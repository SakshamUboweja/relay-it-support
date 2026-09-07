"""Compare the routing arms on the scenario corpus; model results are cached, calibration is
fitted on the dev split only. The legacy `evaluate()` is untouched and runs separately."""

import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .agents import budget_for, confidence
from .agents.compose import compose_decision, prompt_versions
from .agents.confidence import fit_isotonic, interpolate
from .agents.orchestrator import run_multi_agent
from .agents.pricing import pricing_version
from .agents.runtime import ModelRuntime
from .agents.schemas import PipelineContext, PipelineResult
from .agents.single import run_single_agent
from .agents.tools import TOOL_SCHEMA_VERSION
from .config import ROOT
from .db import mode, query
from .eval_cache import (
    cache_key,
    cache_path,
    read_entry,
    replay,
    serialize_result,
    sources_hash,
    write_entry,
)
from .eval_metrics import arm_metrics, correct
from .evaluate import _metric, policy_hash
from .fixtures import users
from .model import live_client, model_settings
from .policy import decide, policy

ARMS = ("rules-v1", "rules-v2", "single", "multi")
SPLITS = ("dev", "heldout")
RULES_SCORING = {"rules-v1": "v1", "rules-v2": "v2"}
MODEL_ARMS = {"single": run_single_agent, "multi": run_multi_agent}
MODEL_SCORING = "v2"
FIT_ERROR = "Calibration is fitted on the dev split only."
SCENARIOS_PATH = ROOT / "evaluation/scenarios.json"
CACHE_DIR = ROOT / "evaluation/cache"
RESULTS_PATH = ROOT / "evaluation/arms-results.json"
MARKDOWN_PATH = ROOT / "evaluation/ARMS-RESULTS.md"
TABLE_HEADER = (
    "| Arm | Split | Route acc | Accepted prec | Coverage | Security recall | Escalation recall"
    " | ECE | Brier | AUROC | p50 / p95 ms | Tokens in/out | Est. cost |"
)


@dataclass
class EvalOptions:
    arm: str = "rules-v1"
    split: str = "all"
    effort: str = "medium"
    limit: int | None = None
    ids: list[str] | None = None
    resume: bool = True
    concurrency: int = 4
    max_usd: float | None = None
    fit_calibration: bool = False
    write: bool = True

    @property
    def arms(self) -> list[str]:
        return list(ARMS) if self.arm == "all" else [self.arm]

    @property
    def splits(self) -> list[str]:
        return list(SPLITS) if self.split == "all" else [self.split]


def _select_cases(scenarios: list[dict], options: EvalOptions) -> list[dict]:
    if options.ids:
        unknown = sorted(set(options.ids) - {c["id"] for c in scenarios})
        if unknown:
            raise ValueError("Unknown scenario ids: " + ", ".join(unknown))
    chosen = []
    for split in options.splits:
        cases = [c for c in scenarios if c["split"] == split]
        if options.ids:
            cases = [c for c in cases if c["id"] in options.ids]
        chosen.extend(cases[: options.limit] if options.limit is not None else cases)
    return chosen


def _expected(case: dict) -> dict:
    return {
        "team": case["team"],
        "escalation": case["escalation"],
        "clarification": case["expectedClarification"],
    }


def _run_summary(result: PipelineResult) -> dict:
    calls = [s for s in result.run.steps if s.kind == "model_call"]
    return {
        "status": result.run.status,
        "usage": result.run.usage.model_dump(),
        "costUsd": result.run.costUsd,
        "modelCalls": len(calls),
        "firstCallOk": calls[0].status == "ok" if calls else None,
        "verdict": result.reviewer["verdict"] if result.reviewer else None,
    }


def _evidence(result: PipelineResult, decision: dict) -> dict | None:
    extraction = result.extraction
    if extraction is None:
        return None
    return {
        "summary": result.summary,
        "attemptedSteps": bool(
            extraction.get("attemptedStepsQuotes") or extraction.get("procedureAttemptedQuote")
        ),
        "impact": decision["facts"]["impact"]["value"],
        "urgency": decision["facts"]["urgency"]["value"],
    }


class _Run:
    """One harness run: shared sources, the spend cap, the cache and the semaphore."""

    def __init__(self, options: EvalOptions, sources: list[dict], model, client_factory):
        self.options = options
        self.sources = sources
        self.sources_hash = sources_hash(sources)
        self.user = users[0]
        self.model = model
        self.client_factory = client_factory
        self.settings = {
            "effort": options.effort,
            "maxOutputTokens": model_settings()["maxOutputTokens"],
        }
        # The arms rank their candidates with the configured scoring, whatever compose uses.
        self.candidate_scoring = policy["routingScoring"]
        self.semaphore = asyncio.Semaphore(max(1, options.concurrency))
        self.spent = 0.0
        self.aborted = False

    def _row(self, case, decision, *, latency_ms, cache_hit, run, evidence) -> dict:
        return {
            "id": case["id"],
            "family": case["family"],
            "text": case["text"],
            "expected": _expected(case),
            "actual": {
                "team": decision["team"],
                "accepted": decision["accepted"],
                "escalation": decision["escalation"],
                "clarification": bool(decision["question"]),
                "reasons": decision["reasons"],
            },
            "confidence": {
                "raw": decision["confidence"]["raw"],
                "value": None,
                "calibrated": False,
            },
            "latencyMs": latency_ms,
            "cacheHit": cache_hit,
            "failed": False,
            "error": None,
            "run": run,
            "evidence": evidence,
        }

    def _failed_row(self, case, error, rt) -> dict:
        """A case the harness could not score: recorded as a failure, never as a guess."""
        run = None
        if rt is not None:
            calls = [s for s in rt.steps if s.kind == "model_call"]
            run = {
                "status": "failed",
                "usage": rt.usage.model_dump(),
                "costUsd": rt.cost_usd,
                "modelCalls": len(calls),
                "firstCallOk": calls[0].status == "ok" if calls else None,
                "verdict": None,
            }
        return {
            "id": case["id"],
            "family": case["family"],
            "text": case["text"],
            "expected": _expected(case),
            "actual": {
                "team": "Service Desk",
                "accepted": False,
                "escalation": "none",
                "clarification": False,
                "reasons": ["failed"],
            },
            "confidence": {"raw": 0.0, "value": 0.0, "calibrated": False},
            "latencyMs": None,
            "cacheHit": False,
            "failed": True,
            "error": str(error) or type(error).__name__,
            "run": run,
            "evidence": None,
        }

    def _rules_case(self, arm: str, case: dict) -> dict:
        started = time.perf_counter()
        decision = decide(case["text"], self.sources, self.user, scoring=RULES_SCORING[arm])
        decision["confidence"] = confidence.build(
            pipeline=arm,
            decision=decision,
            ranked=decision["alternatives"],
            sources=self.sources,
            extraction_ok=None,
        )
        latency = (time.perf_counter() - started) * 1000
        return self._row(
            case, decision, latency_ms=latency, cache_hit=False, run=None, evidence=None
        )

    def _spend(self, cost) -> None:
        if cost is not None:
            self.spent = round(self.spent + cost, 6)

    async def _model_case(self, arm: str, case: dict, holder: dict) -> dict:
        ctx = PipelineContext(
            report_id=f"eval:{case['id']}",
            text=case["text"],
            message_ids=["eval-message"],
            sources=self.sources,
            user=self.user,
            clarifications=0,
            procedure=None,
            settings=self.settings,
            pipeline=arm,
        )
        versions = prompt_versions(arm)
        key = cache_key(
            arm=arm,
            case_id=case["id"],
            text=case["text"],
            model=self.model,
            effort=self.options.effort,
            prompt_versions=versions,
            tool_schema_version=TOOL_SCHEMA_VERSION,
            scoring=MODEL_SCORING,
            sources_hash=self.sources_hash,
            candidate_scoring=self.candidate_scoring,
        )
        path = cache_path(CACHE_DIR, arm, case["id"], key)
        entry = read_entry(path) if self.options.resume else None
        rt = ModelRuntime(
            self.settings, budget_for(arm), model=self.model, client_factory=self.client_factory
        )
        holder["rt"] = rt
        if entry is not None:
            result = replay(entry, rt)
            decision = compose_decision(ctx, result, rt, scoring=MODEL_SCORING)
            latency = None
        else:
            started = time.perf_counter()
            try:
                result = await MODEL_ARMS[arm](ctx, rt)
            finally:
                self._spend(rt.cost_usd)
            decision = compose_decision(ctx, result, rt, scoring=MODEL_SCORING)
            latency = (time.perf_counter() - started) * 1000
            write_entry(
                path,
                {
                    "arm": arm,
                    "id": case["id"],
                    "key": key,
                    "model": self.model,
                    "effort": self.options.effort,
                    "promptVersions": versions,
                    "toolSchemaVersion": TOOL_SCHEMA_VERSION,
                    "scoring": MODEL_SCORING,
                    "candidateScoring": self.candidate_scoring,
                    "sourcesHash": self.sources_hash,
                    "cachedAt": datetime.now(timezone.utc).isoformat(),
                    "result": serialize_result(result),
                    "decision": decision,
                },
            )
        return self._row(
            case,
            decision,
            latency_ms=latency,
            cache_hit=entry is not None,
            run=_run_summary(result),
            evidence=_evidence(result, decision),
        )

    async def _guarded(self, arm: str, case: dict) -> dict | None:
        async with self.semaphore:
            cap = self.options.max_usd
            if cap is not None and self.spent > cap:
                self.aborted = True
                return None
            holder: dict = {}
            try:
                if arm in MODEL_ARMS:
                    row = await self._model_case(arm, case, holder)
                else:
                    row = self._rules_case(arm, case)
            except Exception as error:
                row = self._failed_row(case, error, holder.get("rt"))
            status = "failed" if row["failed"] else "cached" if row["cacheHit"] else "ran"
            print(
                f"{arm} {case['id']} {status} team={row['actual']['team']} spent=${self.spent:.4f}",
                file=sys.stderr,
            )
            return row

    async def arm_split(self, arm: str, cases: list[dict]) -> list[dict]:
        rows = await asyncio.gather(*(self._guarded(arm, case) for case in cases))
        return [row for row in rows if row is not None]


def _dataset_hash(rows: list[dict]) -> str:
    labelled = sorted((r["id"], r["text"], json.dumps(r["expected"], sort_keys=True)) for r in rows)
    return hashlib.sha256(json.dumps(labelled).encode()).hexdigest()


def _calibration_tables(results: dict, options: EvalOptions, model) -> dict:
    """Breakpoints per arm: freshly fitted on dev when asked, else whatever the file holds."""
    path = confidence.CALIBRATION_PATH
    table = json.loads(path.read_text()) if path.exists() else {}
    points = {arm: (table.get(arm) or {}).get("breakpoints") or None for arm in results}
    if not options.fit_calibration:
        return points
    fitted_at = datetime.now(timezone.utc).isoformat()
    for arm, splits in results.items():
        rows = [r for r in splits["dev"] if not r["failed"]]
        fit = fit_isotonic([(r["confidence"]["raw"], correct(r)) for r in rows])
        is_model = arm in MODEL_ARMS
        table[arm] = {
            "breakpoints": fit,
            "fitOn": "dev",
            "n": len(rows),
            "datasetHash": _dataset_hash(rows),
            "promptVersions": prompt_versions(arm) if is_model else {},
            "model": model if is_model else None,
            "effort": options.effort if is_model else None,
            "scoring": MODEL_SCORING if is_model else RULES_SCORING[arm],
            "fittedAt": fitted_at,
        }
        points[arm] = fit or None
    if options.write:
        path.write_text(json.dumps(table, indent=2) + "\n")
    return points


def _apply_calibration(results: dict, tables: dict) -> None:
    for arm, splits in results.items():
        points = tables.get(arm)
        for rows in splits.values():
            for row in rows:
                if row["failed"]:
                    continue
                raw = row["confidence"]["raw"]
                value = interpolate(points, raw) if points else raw
                row["confidence"] = {
                    "raw": raw,
                    "value": round(value, 4),
                    "calibrated": bool(points),
                }


def _calibration_hash() -> str | None:
    path = confidence.CALIBRATION_PATH
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _caveats(options: EvalOptions, model_arms: list[str]) -> list[str]:
    production = os.getenv("OPENAI_REASONING_EFFORT") or "unset"
    caveats = [
        "Labels are agent-authored and not human reviewed.",
        "The heldout split was already inspected during development (its failures are in"
        " python-results.json); this is a holdout-informed regression comparison, not a clean"
        " holdout claim.",
        f"Costs are estimates from config/pricing.json (version {pricing_version()}), not"
        " billing records.",
    ]
    if model_arms:
        caveats.append(
            f"Model arms ran at reasoning effort {options.effort}; production uses {production}."
        )
    if "single" in model_arms:
        caveats.append(
            "The single arm saw the first eight seeded sources by id (no retrieval on this"
            " text-only corpus)."
        )
    return caveats


def _number(value, digits=3) -> str:
    return f"{value:.{digits}f}" if value is not None else "n/a"


def _table_row(row: dict) -> str:
    latency = row["latency"]
    p50 = f"{latency['p50Ms']:.0f}" if latency["p50Ms"] is not None else "n/a"
    p95 = f"{latency['p95Ms']:.0f}" if latency["p95Ms"] is not None else "n/a"
    cost = row["estimatedCostUSD"]
    cells = [
        row["arm"],
        row["split"],
        _metric(row["routingAccuracy"]),
        _metric(row["acceptedPrecision"]),
        _metric(row["eligibleCoverage"]),
        _metric(row["securityRecall"]),
        _metric(row["escalationRecall"]),
        _number(row["confidence"]["ece"]),
        _number(row["confidence"]["brier"]),
        _number(row["confidence"]["auroc"]),
        f"{p50} / {p95}",
        f"{row['tokens']['input']}/{row['tokens']['output']}",
        f"${cost:.4f}" if cost is not None else "n/a",
    ]
    return "| " + " | ".join(cells) + " |"


def _markdown(report: dict) -> str:
    prompts = ", ".join(f"{k}={v}" for k, v in report["promptVersions"].items()) or "none"
    lines = [
        "# Arm comparison results",
        "",
        f"Run: {report['runDate']}. Model: {report['model'] or 'none (rules arms only)'}."
        f" Effort: {report['effort']}. Scoring: {report['scoring']} for the model arms,"
        f" candidates ranked with {report['candidateScoring']} (rules arms carry their own).",
        "",
        f"Prompts: {prompts}. Policy SHA-256: {report['policyHash']}."
        f" Calibration SHA-256: {report['calibrationHash'] or 'none'}."
        f" Pricing: {report['pricingVersion']}. Cache hits: {report['cacheHits']}."
        f" Aborted at the spend cap: {'yes' if report['aborted'] else 'no'}.",
        "",
        TABLE_HEADER,
        "|" + "---|" * 13,
        *(_table_row(row) for row in report["rows"]),
        "",
        "Caveats:",
        "",
        *(f"{number}. {caveat}" for number, caveat in enumerate(report["caveats"], 1)),
        "",
    ]
    return "\n".join(lines)


async def _load_sources() -> list[dict]:
    return (
        await query(
            "SELECT * FROM sources WHERE kind IN ('article','case') AND created_at<=now() ORDER BY id",
            [],
        )
    ).rows


async def evaluate_arms(options: EvalOptions, *, client_factory=None) -> dict:
    if options.fit_calibration and options.split != "dev":
        raise ValueError(FIT_ERROR)
    unknown = [arm for arm in options.arms if arm not in ARMS]
    if unknown:
        raise ValueError("Unknown arm: " + ", ".join(unknown))
    model_arms = [arm for arm in options.arms if arm in MODEL_ARMS]
    model = os.getenv("OPENAI_MODEL") if model_arms else None
    if model_arms and (mode() != "live" or not os.getenv("OPENAI_API_KEY") or not model):
        raise ValueError("Model arms require APP_MODE=live, OPENAI_API_KEY and OPENAI_MODEL.")
    cases = _select_cases(json.loads(SCENARIOS_PATH.read_text()), options)
    run = _Run(options, await _load_sources(), model, client_factory or live_client)
    results: dict[str, dict[str, list[dict]]] = {}
    for arm in options.arms:
        for split in options.splits:
            rows = await run.arm_split(arm, [c for c in cases if c["split"] == split])
            results.setdefault(arm, {})[split] = rows
    _apply_calibration(results, _calibration_tables(results, options, model))
    rows = [
        arm_metrics(
            results[arm][split],
            arm=arm,
            split=split,
            scoring=MODEL_SCORING if arm in MODEL_ARMS else RULES_SCORING[arm],
        )
        for arm in options.arms
        for split in options.splits
    ]
    versions = {}
    for arm in model_arms:
        versions.update(prompt_versions(arm))
    report = {
        "runDate": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "effort": options.effort,
        "scoring": MODEL_SCORING,
        "candidateScoring": run.candidate_scoring,
        "promptVersions": versions,
        "policyHash": policy_hash(ROOT),
        "calibrationHash": _calibration_hash(),
        "pricingVersion": pricing_version(),
        "cacheHits": sum(row["cacheHits"] for row in rows),
        "aborted": run.aborted,
        "options": {
            "arms": options.arms,
            "splits": options.splits,
            "limit": options.limit,
            "ids": options.ids,
            "resume": options.resume,
            "concurrency": options.concurrency,
            "maxUsd": options.max_usd,
            "fitCalibration": options.fit_calibration,
        },
        "caveats": _caveats(options, model_arms),
        "rows": rows,
        "cases": results,
    }
    markdown = _markdown(report)
    if options.write:
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n")
        MARKDOWN_PATH.write_text(markdown)
    print(markdown)
    return report
