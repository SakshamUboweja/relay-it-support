"""Synthetic migration regression evaluation; never calls a live model."""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .db import query
from .domain import TEAMS
from .fixtures import catalog, users
from .policy import decide, policy


def _count(n, d):
    return {"numerator": n, "denominator": d, "rate": n / d if d else None}


def _metric(value):
    rate = f"{value['rate'] * 100:.1f}%" if value["rate"] is not None else "n/a"
    return f"{value['numerator']}/{value['denominator']} ({rate})"


def policy_hash(root: Path) -> str:
    """One digest over the routing implementation and its configuration."""
    digest = hashlib.sha256()
    for filename in ("relay/policy.py", "config/policy.json", "config/fixtures.json"):
        digest.update((root / filename).read_bytes())
    return digest.hexdigest()


def percentiles(times: list[float]) -> tuple:
    """(p50, p95) by rank over the sorted times; (None, None) without observations."""
    times = sorted(times)
    if not times:
        return None, None
    return times[len(times) // 2], times[int(len(times) * 0.95)]


def routing_metrics(rows: list[dict]) -> dict:
    """Routing, escalation and clarification counts over per-case rows; shared by both runners.

    Each row carries `expected` and `actual` with `team`, `escalation` and `clarification`,
    plus `actual.accepted`.
    """
    # A row the arm comparison could not score (`failed`) stays in every denominator and is
    # always listed as a failure, but its fallback labels never count as correct.
    scored = [r for r in rows if not r.get("failed")]
    eligible = [r for r in rows if r["expected"]["team"] != "Service Desk"]
    accepted = [r for r in scored if r["actual"]["accepted"]]
    security = [r for r in rows if r["expected"]["escalation"] == "security"]
    non_security = [r for r in rows if r["expected"]["escalation"] != "security"]
    escalated = [r for r in rows if r["expected"]["escalation"] != "none"]
    non_escalated = [r for r in rows if r["expected"]["escalation"] == "none"]
    failures = [
        r
        for r in rows
        if r.get("failed")
        or any(
            r["actual"][key] != r["expected"][key]
            for key in ("team", "escalation", "clarification")
        )
    ]
    per_team = {}
    for team in TEAMS:
        group = [r for r in rows if r["expected"]["team"] == team]
        hits = sum(r["actual"]["team"] == team for r in group if not r.get("failed"))
        per_team[team] = _count(hits, len(group))
    return {
        "routingAccuracy": _count(
            sum(r["expected"]["team"] == r["actual"]["team"] for r in scored), len(rows)
        ),
        "perTeam": per_team,
        "acceptedPrecision": _count(
            sum(r["actual"]["team"] == r["expected"]["team"] for r in accepted),
            len(accepted),
        ),
        "eligibleCoverage": _count(sum(r["actual"]["accepted"] for r in eligible), len(eligible)),
        "automaticRoutingFrequency": _count(len(accepted), len(rows)),
        "abstentions": len(rows) - len(accepted),
        "escalationRecall": _count(
            sum(
                r["actual"]["escalation"] == r["expected"]["escalation"]
                for r in escalated
                if not r.get("failed")
            ),
            len(escalated),
        ),
        "escalationFalsePositives": _count(
            sum(r["actual"]["escalation"] != "none" for r in non_escalated),
            len(non_escalated),
        ),
        "securityRecall": _count(
            sum(r["actual"]["escalation"] == "security" for r in security), len(security)
        ),
        "securityFalsePositives": _count(
            sum(r["actual"]["escalation"] == "security" for r in non_security),
            len(non_security),
        ),
        "clarificationAgreement": _count(
            sum(r["actual"]["clarification"] == r["expected"]["clarification"] for r in scored),
            len(rows),
        ),
        "firstTurnQuestionRate": _count(sum(r["actual"]["clarification"] for r in rows), len(rows)),
        "failures": failures,
    }


async def evaluate() -> dict:
    root = Path(__file__).resolve().parents[1]
    scenarios = json.loads((root / "evaluation/scenarios.json").read_text())
    digest = policy_hash(root)
    sources = (
        await query(
            "SELECT * FROM sources WHERE kind IN ('article','case') AND created_at<=now() ORDER BY id",
            [],
        )
    ).rows
    result = {
        "runDate": datetime.now(timezone.utc).isoformat(),
        "mode": "deterministic-demo",
        "runtime": "python",
        "model": "deterministic-demo-v1",
        "promptVersion": "intake-v1",
        "policyVersion": policy["version"],
        "policyHash": digest,
        "dataset": "120 synthetic cases; historical 60/60 family split, already observed during development; agent-authored labels NOT human reviewed. Migration regression corpus, not a new untouched holdout.",
        "allowedInformation": {
            "keyword": "Text and catalog; first alias match; no policy, history, model or incidents",
            "proposed": "Same text and catalog, simulated Maya profile, approved pre-existing synthetic articles/cases, deterministic policy; no active incident context in this text-only corpus",
        },
        "liveLLMBaseline": {
            "status": "skipped",
            "reason": "This command measures deterministic migration regression only and does not call a live model.",
        },
        "splits": {},
    }
    for split in ("dev", "heldout"):
        cases = [case for case in scenarios if case["split"] == split]
        result["splits"][split] = {}
        for method in ("keyword", "proposed"):
            rows = []
            for case in cases:
                start = time.perf_counter()
                # Pinned to v1: these published numbers are the scoring-v1 regression baseline
                # and must not move when the shipped default changes.
                d = decide(case["text"], sources, users[0], scoring="v1")
                keyword = next(
                    (
                        s
                        for s in catalog
                        if any(alias in case["text"].lower() for alias in s["aliases"])
                    ),
                    None,
                )
                team = (
                    (keyword["team"] if keyword else "Service Desk")
                    if method == "keyword"
                    else d["team"]
                )
                accepted = bool(keyword) if method == "keyword" else d["accepted"]
                rows.append(
                    {
                        "id": case["id"],
                        "family": case["family"],
                        "text": case["text"],
                        "expected": {
                            "team": case["team"],
                            "escalation": case["escalation"],
                            "clarification": case["expectedClarification"],
                        },
                        "actual": {
                            "team": team,
                            "accepted": accepted,
                            "escalation": "none" if method == "keyword" else d["escalation"],
                            "clarification": False if method == "keyword" else bool(d["question"]),
                            "reasons": ["first-keyword-match"]
                            if method == "keyword"
                            else d["reasons"],
                        },
                        "latencyMs": (time.perf_counter() - start) * 1000,
                    }
                )
            metrics = routing_metrics(rows)
            p50, p95 = percentiles([r["latencyMs"] for r in rows])
            result["splits"][split][method] = {
                **{key: value for key, value in metrics.items() if key != "failures"},
                "latency": {
                    "p50Ms": p50,
                    "p95Ms": p95,
                    "kind": "In-process deterministic route evaluation; includes proposed decision computation for both methods. Not UI or provider latency.",
                },
                "tokens": {"input": 0, "output": 0},
                "estimatedModelCostUSD": 0,
                "failures": metrics["failures"],
            }
    result["unmeasured"] = [
        "Live model performance/baseline/cost",
        "Human-reviewed label validity",
        "Related-incident precision/recall: text corpus lacks association labels",
        "Fact fidelity across broad language; targeted assertions exist in tests",
        "Real-world resolution effectiveness",
        "Browser and provider-confirmed latency",
    ]
    (root / "evaluation/python-results.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        "# Python migration regression results",
        "",
        f"Run: {result['runDate']}. Policy: {policy['version']}. Implementation SHA-256: {digest}.",
        "",
        "Labels are agent-authored and **not human reviewed**. Both historical split names are retained for comparison; their cases have already been observed. This is migration regression evidence, not a new untouched holdout or proof of improved real-world accuracy.",
        "",
        "| Split / method | Route accuracy | Accepted precision | Eligible coverage | Security recall |",
        "|---|---|---|---|---|",
    ]
    for split in ("dev", "heldout"):
        for method in ("keyword", "proposed"):
            metrics = result["splits"][split][method]
            values = " | ".join(
                _metric(metrics[key])
                for key in (
                    "routingAccuracy",
                    "acceptedPrecision",
                    "eligibleCoverage",
                    "securityRecall",
                )
            )
            lines.append(f"| {split} / {method} | {values} |")
    lines.extend(
        [
            "",
            "All failure cases and per-team counts are recorded in python-results.json. Historical TypeScript frozen.json and results.json remain unchanged. No live model calls occur; zero model cost is not an estimate of live costs.",
            "",
            "Limitations: " + "; ".join(result["unmeasured"]) + ".",
            "",
        ]
    )
    markdown = "\n".join(lines)
    (root / "evaluation/python-RESULTS.md").write_text(markdown)
    print(markdown)
    return result
