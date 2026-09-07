"""The arm comparison harness: cached model results, shared metrics, calibration fitted on dev only."""

import hashlib
import json
import re
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from relay import cli, evaluate_arms as harness
from relay.agents import compose, confidence
from relay.agents.confidence import ece
from relay.agents.prompts import (
    REVIEWER_PROMPT,
    REVIEWER_PROMPT_VERSION,
    SINGLE_PROMPT_VERSION,
    TRIAGE_PROMPT,
    TRIAGE_PROMPT_VERSION,
)
from relay.agents.schemas import ReviewerOutput, RoutingProposal, SingleAgentOutput
from relay.agents.tools import CasesArgs
from relay.cli import seed_demo
from relay.eval_metrics import confidence_metrics, fidelity
from relay.evaluate import evaluate, policy_hash
from relay.evaluate_arms import EvalOptions, evaluate_arms
from relay.intake_prompt import INTAKE_PROMPT, INTAKE_PROMPT_VERSION
from relay.model import Extraction
from relay.policy import policy

pytestmark = pytest.mark.usefixtures("isolated_db")

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ("evaluation/python-results.json", "evaluation/python-RESULTS.md")
ROUTING_KEYS = (
    "routingAccuracy",
    "perTeam",
    "acceptedPrecision",
    "eligibleCoverage",
    "automaticRoutingFrequency",
    "abstentions",
    "escalationRecall",
    "escalationFalsePositives",
    "securityRecall",
    "securityFalsePositives",
    "clarificationAgreement",
    "firstTurnQuestionRate",
)
TABLE_HEADER = (
    "| Arm | Split | Route acc | Accepted prec | Coverage | Security recall | Escalation recall"
    " | ECE | Brier | AUROC | p50 / p95 ms | Tokens in/out | Est. cost | Tool calls / cited |"
)


@pytest.fixture(autouse=True)
async def seeded(isolated_db, monkeypatch, tmp_path):
    """`isolated_db` first: seeding before it would write to the configured database."""
    await seed_demo()
    monkeypatch.setattr(harness, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(harness, "RESULTS_PATH", tmp_path / "arms-results.json")
    monkeypatch.setattr(harness, "MARKDOWN_PATH", tmp_path / "ARMS-RESULTS.md")
    monkeypatch.setattr(confidence, "CALIBRATION_PATH", tmp_path / "calibration.json")
    for name in (
        "OPENAI_PRICE_INPUT_PER_M",
        "OPENAI_PRICE_CACHED_INPUT_PER_M",
        "OPENAI_PRICE_OUTPUT_PER_M",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def legacy_outputs():
    """A test may run the legacy `evaluate()`; its two files go back to their committed bytes."""
    saved = {name: (ROOT / name).read_bytes() for name in LEGACY}
    yield saved
    for name, content in saved.items():
        (ROOT / name).write_bytes(content)


def live(monkeypatch, model="gpt-test"):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", model)


def rate(n, d):
    return {"numerator": n, "denominator": d, "rate": n / d if d else None}


def extraction_fields(message, **overrides):
    return {
        "summary": message[:120],
        "service": None,
        "serviceQuote": None,
        "symptomQuote": message[:20],
        "impactQuote": None,
        "urgencyQuote": None,
        "deviceQuote": None,
        "startedQuote": None,
        "workaroundQuote": None,
        "attemptedStepsQuotes": [],
        "supportRequestQuote": None,
        "procedureAttemptedQuote": None,
        "securityQuote": None,
        "evidenceIds": ["eval-message"],
        **overrides,
    }


def proposal_fields(**overrides):
    return {
        "service": None,
        "team": "Service Desk",
        "abstain": True,
        "blockedQuote": None,
        "broadImpactQuote": None,
        "securityQuote": None,
        "rationale": "No service is named.",
        "probability": 0.5,
        "citedSourceIds": [],
        **overrides,
    }


def completed(parsed):
    return SimpleNamespace(
        status="completed",
        output=[],
        output_parsed=parsed,
        usage=SimpleNamespace(input_tokens=30, output_tokens=40),
    )


def mock_client(parse):
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return client


def message_of(kwargs):
    return json.loads(kwargs["input"][1]["content"][0]["text"])["message"]


def single_parse():
    """A valid single-agent answer for whichever scenario text the call carries."""

    async def parse(**kwargs):
        message = message_of(kwargs)
        fields = {**extraction_fields(message), **proposal_fields()}
        return completed(SingleAgentOutput(**fields))

    return AsyncMock(side_effect=parse)


def never():
    raise AssertionError("no client may be built for this run")


def options(**overrides):
    return EvalOptions(
        **{"arm": "single", "split": "dev", "ids": ["dev-001", "dev-002"], "concurrency": 2}
        | overrides
    )


def cache_files(tmp_path, arm="single"):
    return sorted(p.name for p in (tmp_path / "cache" / arm).glob("*.json"))


# --- cache ------------------------------------------------------------------------------


async def test_a_cache_hit_skips_the_model_call_and_the_key_follows_effort_and_prompts(
    monkeypatch, tmp_path
):
    live(monkeypatch)
    parse = single_parse()
    factory = lambda: mock_client(parse)  # noqa: E731
    first = await evaluate_arms(options(), client_factory=factory)
    assert parse.await_count == 2 and first["cacheHits"] == 0
    names = cache_files(tmp_path)
    assert len(names) == 2
    assert all(re.fullmatch(r"dev-00[12]\.[0-9a-f]{16}\.json", name) for name in names)
    entry = json.loads((tmp_path / "cache/single" / names[0]).read_text())
    assert entry["result"]["run"]["status"] == "completed"
    assert entry["result"]["extraction"]["evidenceIds"] == ["eval-message"]
    assert entry["result"]["proposal"]["abstain"] is True
    assert entry["decision"]["team"] == "Service Desk"
    second = await evaluate_arms(options(), client_factory=factory)
    assert parse.await_count == 2 and second["cacheHits"] == 2
    [row] = second["rows"]
    # A hit replays the wall-clock recorded when the case actually ran, and says so.
    assert row["cacheHits"] == 2 and row["latency"]["p50Ms"] is not None
    assert (row["latency"]["p50Ms"], row["latency"]["p95Ms"]) == (
        first["rows"][0]["latency"]["p50Ms"],
        first["rows"][0]["latency"]["p95Ms"],
    )
    assert row["latency"]["kind"] == (
        "pipeline wall-clock including model calls; 2 of 2 rows replayed from cache"
    )
    assert first["rows"][0]["latency"]["kind"] == (
        "pipeline wall-clock including model calls; 0 of 2 rows replayed from cache"
    )
    assert [c["latencyMs"] for c in second["cases"]["single"]["dev"]] == [
        c["latencyMs"] for c in first["cases"]["single"]["dev"]
    ]
    assert all(isinstance(c["latencyMs"], int) for c in second["cases"]["single"]["dev"])
    assert row["routingAccuracy"] == first["rows"][0]["routingAccuracy"]
    assert row["tokens"] == first["rows"][0]["tokens"]
    assert [c["cacheHit"] for c in second["cases"]["single"]["dev"]] == [True, True]
    await evaluate_arms(options(effort="high"), client_factory=factory)
    assert parse.await_count == 4
    monkeypatch.setattr(compose, "SINGLE_PROMPT_VERSION", "relay-single-test")
    await evaluate_arms(options(effort="high"), client_factory=factory)
    assert parse.await_count == 6
    assert len(cache_files(tmp_path)) == 6
    await evaluate_arms(options(effort="high", resume=False), client_factory=factory)
    assert parse.await_count == 8


async def test_model_arms_require_live_mode_a_key_and_a_model(monkeypatch):
    with pytest.raises(ValueError, match="APP_MODE=live"):
        await evaluate_arms(options(), client_factory=never)
    live(monkeypatch)
    monkeypatch.delenv("OPENAI_MODEL")
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        await evaluate_arms(options(), client_factory=never)
    with pytest.raises(ValueError, match="bogus"):
        await evaluate_arms(options(arm="bogus"), client_factory=never)
    with pytest.raises(ValueError, match="dev-999"):
        await evaluate_arms(options(arm="rules-v1", ids=["dev-999"]), client_factory=never)


# --- rules arms ---------------------------------------------------------------------------


async def test_rules_arms_never_call_a_model_and_match_the_legacy_numbers(legacy_outputs, tmp_path):
    # The legacy runner is pinned to scoring v1, so its published numbers keep their label
    # after the shipped default moved to v2.
    result = await evaluate_arms(EvalOptions(arm="rules-v1", split="dev"), client_factory=never)
    legacy = await evaluate()
    [row] = result["rows"]
    proposed = legacy["splits"]["dev"]["proposed"]
    for key in ROUTING_KEYS:
        assert row[key] == proposed[key], key
    assert [f["id"] for f in row["failures"]] == [f["id"] for f in proposed["failures"]]
    assert row["scoring"] == "v1" and row["cases"] == 60
    assert row["estimatedCostUSD"] == 0
    assert row["tokens"] == {"input": 0, "output": 0, "cached": 0, "reasoning": 0}
    assert row["cacheHits"] == 0 and not (tmp_path / "cache").exists()
    assert row["latency"]["p50Ms"] is not None
    assert row["confidence"]["brier"] is not None and len(row["confidence"]["bins"]) == 5
    assert row["fidelity"]["quoteValidityRate"] == rate(0, 0)
    assert result["model"] is None and result["promptVersions"] == {}
    assert len(result["caveats"]) == 3 and not any("effort" in c for c in result["caveats"])
    v2 = await evaluate_arms(EvalOptions(arm="rules-v2", split="dev"), client_factory=never)
    assert v2["rows"][0]["scoring"] == "v2"
    assert v2["rows"][0]["routingAccuracy"] != row["routingAccuracy"]


# --- calibration --------------------------------------------------------------------------


async def test_fit_calibration_refuses_heldout_and_writes_monotone_breakpoints_on_dev(tmp_path):
    path = tmp_path / "calibration.json"
    for split in ("heldout", "all"):
        with pytest.raises(ValueError, match="Calibration is fitted on the dev split only."):
            await evaluate_arms(EvalOptions(arm="rules-v1", split=split, fit_calibration=True))
    assert not path.exists()
    result = await evaluate_arms(EvalOptions(arm="rules-v1", split="dev", fit_calibration=True))
    table = json.loads(path.read_text())
    entry = table["rules-v1"]
    assert set(entry) == {
        "breakpoints",
        "fitOn",
        "n",
        "datasetHash",
        "promptVersions",
        "model",
        "effort",
        "scoring",
        "fittedAt",
    }
    xs = [x for x, _ in entry["breakpoints"]]
    ys = [y for _, y in entry["breakpoints"]]
    assert xs == sorted(xs) and len(xs) == len(set(xs))
    assert all(a <= b for a, b in zip(ys, ys[1:])) and all(0 <= y <= 1 for y in ys)
    assert (entry["fitOn"], entry["n"], entry["scoring"]) == ("dev", 60, "v1")
    assert entry["model"] is None and entry["effort"] is None and entry["promptVersions"] == {}
    assert len(entry["datasetHash"]) == 64
    assert result["calibrationHash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    # The metrics of the same run already use the fresh fit.
    case = result["cases"]["rules-v1"]["dev"][0]
    assert case["confidence"]["calibrated"] is True
    expected, _ = confidence.calibrate("rules-v1", case["confidence"]["raw"])
    assert case["confidence"]["value"] == pytest.approx(expected, abs=1e-4)
    # Another arm's fit merges in; the first entry stays.
    await evaluate_arms(EvalOptions(arm="rules-v2", split="dev", fit_calibration=True))
    merged = json.loads(path.read_text())
    assert merged["rules-v1"] == entry and merged["rules-v2"]["scoring"] == "v2"
    # A run without the flag reads the table it finds and reports its hash.
    plain = await evaluate_arms(EvalOptions(arm="rules-v1", split="heldout"))
    assert plain["calibrationHash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert plain["cases"]["rules-v1"]["heldout"][0]["confidence"]["calibrated"] is True


# --- outputs ------------------------------------------------------------------------------


async def test_results_json_and_markdown_have_the_documented_shape(monkeypatch, tmp_path, capsys):
    live(monkeypatch)
    # Pin the production effort the caveat quotes; the ambient value must not decide the assertion.
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    parse = single_parse()
    result = await evaluate_arms(
        EvalOptions(arm="single", split="all", ids=["dev-001", "heldout-001"]),
        client_factory=lambda: mock_client(parse),
    )
    assert set(result) >= {
        "runDate",
        "model",
        "effort",
        "scoring",
        "promptVersions",
        "policyHash",
        "calibrationHash",
        "cacheHits",
        "aborted",
        "caveats",
        "rows",
        "cases",
    }
    assert (result["model"], result["effort"], result["scoring"]) == ("gpt-test", "medium", "v2")
    assert result["promptVersions"] == {
        "intake": INTAKE_PROMPT_VERSION,
        "single": SINGLE_PROMPT_VERSION,
    }
    assert result["policyHash"] == policy_hash(ROOT)
    assert result["calibrationHash"] is None and result["aborted"] is False
    assert [(r["arm"], r["split"]) for r in result["rows"]] == [
        ("single", "dev"),
        ("single", "heldout"),
    ]
    row = result["rows"][0]
    assert set(row) >= {
        *ROUTING_KEYS,
        "failures",
        "failedCases",
        "confidence",
        "latency",
        "tokens",
        "estimatedCostUSD",
        "budgetExhausted",
        "reviewerVerdicts",
        "cacheHits",
        "fidelity",
        "toolCalls",
        "citedSources",
    }
    assert row["estimatedCostUSD"] is None
    assert row["tokens"] == {"input": 30, "output": 40, "cached": 0, "reasoning": 0}
    assert (row["toolCalls"], row["citedSources"]) == (0, 0)
    assert row["latency"]["kind"] == (
        "pipeline wall-clock including model calls; 0 of 1 rows replayed from cache"
    )
    assert row["latency"]["p50Ms"] is not None and row["latency"]["p95Ms"] is not None
    assert result["cases"]["single"]["dev"][0]["run"]["toolCalls"] == 0
    assert result["cases"]["single"]["dev"][0]["run"]["citedSources"] == 0
    assert set(row["confidence"]) >= {
        "ece",
        "brier",
        "auroc",
        "selectiveAccuracy",
        "coverage",
        "bins",
    }
    assert [(b["lo"], b["hi"]) for b in row["confidence"]["bins"]] == [
        (0.0, 0.2),
        (0.2, 0.4),
        (0.4, 0.6),
        (0.6, 0.8),
        (0.8, 1.0),
    ]
    assert row["fidelity"]["quoteValidityRate"] == rate(1, 1)
    assert row["fidelity"]["summaryBoundedRate"] == rate(1, 1)
    assert row["fidelity"]["inventedAttemptsRate"] == rate(0, 1)
    assert row["fidelity"]["unknownsPreservedRate"] == rate(1, 1)
    assert row["reviewerVerdicts"] == {} and row["budgetExhausted"] == 0
    assert result["cases"]["single"]["heldout"][0]["id"] == "heldout-001"
    assert result["caveats"] == [
        "Labels are agent-authored and not human reviewed.",
        "The heldout split was already inspected during development (its failures are in"
        " python-results.json); this is a holdout-informed regression comparison, not a clean"
        " holdout claim.",
        "Costs are estimates from config/pricing.json (version 2026-09-06-openrouter), not"
        " billing records.",
        "Model arms ran at reasoning effort medium; production uses high.",
        "The single arm saw the first eight seeded sources by id (no retrieval on this text-only"
        " corpus).",
    ]
    assert (result["scoring"], result["candidateScoring"]) == ("v2", "v2")
    saved = json.loads((tmp_path / "arms-results.json").read_text())
    assert saved["rows"] == result["rows"] and saved["cases"] == result["cases"]
    markdown = (tmp_path / "ARMS-RESULTS.md").read_text()
    assert TABLE_HEADER in markdown
    assert "\n|" + "---|" * 14 + "\n" in markdown
    single_lines = [line for line in markdown.splitlines() if line.startswith("| single | ")]
    assert len(single_lines) == 2 and all(line.endswith("| 0 / 0 |") for line in single_lines)
    assert not any("multi arm" in caveat for caveat in result["caveats"])
    for number, caveat in enumerate(result["caveats"], 1):
        assert f"{number}. {caveat}" in markdown
    assert "gpt-test" in markdown and result["policyHash"] in markdown
    assert markdown in capsys.readouterr().out


async def test_no_write_keeps_the_outputs_and_the_calibration_off_disk(monkeypatch, tmp_path):
    result = await evaluate_arms(
        EvalOptions(arm="rules-v1", split="dev", fit_calibration=True, write=False)
    )
    assert result["rows"][0]["routingAccuracy"]["denominator"] == 60
    assert result["cases"]["rules-v1"]["dev"][0]["confidence"]["calibrated"] is True
    assert sorted(p.name for p in tmp_path.iterdir()) == []
    # Cache entries are the record of paid calls, so they are written even without outputs.
    live(monkeypatch)
    parse = single_parse()
    await evaluate_arms(options(write=False), client_factory=lambda: mock_client(parse))
    assert len(cache_files(tmp_path)) == 2 and sorted(p.name for p in tmp_path.iterdir()) == [
        "cache"
    ]


# --- spend cap and failures -----------------------------------------------------------------


async def test_max_usd_zero_aborts_after_the_first_case_with_a_priced_model(monkeypatch):
    live(monkeypatch, model="gpt-5.6-terra")
    parse = single_parse()
    result = await evaluate_arms(
        options(ids=["dev-001", "dev-002", "dev-003"], concurrency=1, max_usd=0),
        client_factory=lambda: mock_client(parse),
    )
    assert result["aborted"] is True and parse.await_count == 1
    [row] = result["rows"]
    assert row["routingAccuracy"]["denominator"] == 1
    # (30 input * 2.0 + 40 output * 12.0) per million tokens.
    assert row["estimatedCostUSD"] == pytest.approx(0.00054)
    assert [c["id"] for c in result["cases"]["single"]["dev"]] == ["dev-001"]


async def test_an_unpriced_model_never_trips_the_cap(monkeypatch):
    live(monkeypatch)
    parse = single_parse()
    result = await evaluate_arms(
        options(concurrency=1, max_usd=0), client_factory=lambda: mock_client(parse)
    )
    assert result["aborted"] is False and parse.await_count == 2
    assert result["rows"][0]["estimatedCostUSD"] is None


async def test_a_case_that_raises_becomes_a_failed_row_never_a_fabricated_one(
    monkeypatch, tmp_path
):
    live(monkeypatch)
    real = harness.compose_decision

    def explode(ctx, result, rt, *, scoring=None):
        if ctx.report_id == "eval:dev-002":
            raise RuntimeError("boom")
        return real(ctx, result, rt, scoring=scoring)

    monkeypatch.setattr(harness, "compose_decision", explode)
    parse = single_parse()
    result = await evaluate_arms(options(), client_factory=lambda: mock_client(parse))
    cases = result["cases"]["single"]["dev"]
    failed = next(c for c in cases if c["id"] == "dev-002")
    assert failed["failed"] is True and failed["error"] == "boom"
    assert failed["actual"] == {
        "team": "Service Desk",
        "accepted": False,
        "escalation": "none",
        "clarification": False,
        "reasons": ["failed"],
    }
    assert failed["confidence"]["value"] == 0.0 and failed["cacheHit"] is False
    assert failed["run"]["usage"]["input"] == 30
    assert failed in result["rows"][0]["failures"]
    assert result["rows"][0]["failedCases"] == 1
    assert result["rows"][0]["routingAccuracy"]["denominator"] == 2
    assert not list((tmp_path / "cache/single").glob("dev-002.*"))
    assert len(list((tmp_path / "cache/single").glob("dev-001.*"))) == 1


async def test_a_failed_case_is_listed_even_when_its_labels_match_the_fallback(monkeypatch):
    # dev-036 expects Service Desk, no escalation and no question: exactly the failed-row shape.
    live(monkeypatch)

    def explode(ctx, result, rt, *, scoring=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(harness, "compose_decision", explode)
    parse = single_parse()
    result = await evaluate_arms(
        options(ids=["dev-036"]), client_factory=lambda: mock_client(parse)
    )
    [row] = result["rows"]
    # Its labels match the fallback shape, but a case the harness could not score is never
    # counted as a correct route, a correct abstention or an agreeing clarification.
    assert row["routingAccuracy"] == rate(0, 1) and row["failedCases"] == 1
    assert row["perTeam"]["Service Desk"] == rate(0, 1)
    assert row["acceptedPrecision"] == rate(0, 0)
    assert row["clarificationAgreement"] == rate(0, 1)
    assert row["confidence"]["brier"] == 0.0
    assert [f["id"] for f in row["failures"]] == ["dev-036"]
    assert row["failures"][0]["failed"] is True


# --- the multi arm through the harness ----------------------------------------------------


class FunctionCall(SimpleNamespace):
    def model_dump(self, *, exclude_none=False, exclude=()):
        return {
            key: value
            for key, value in vars(self).items()
            if key not in exclude and not (exclude_none and value is None)
        }


def multi_parse():
    """Intake, one tool turn, a proposal and an accepting review, for any scenario text."""

    async def parse(**kwargs):
        system = kwargs["input"][0]["content"]
        message = message_of(kwargs)
        if system == INTAKE_PROMPT:
            return completed(Extraction(**extraction_fields(message)))
        if system == TRIAGE_PROMPT:
            if not any(item.get("type") == "function_call_output" for item in kwargs["input"]):
                call = FunctionCall(
                    type="function_call",
                    id="fc_1",
                    name="similar_cases",
                    call_id="call_1",
                    arguments=json.dumps({"service": "vpn"}),
                    parsed_arguments=CasesArgs(service="vpn"),
                    status="completed",
                )
                return SimpleNamespace(
                    status="completed",
                    output=[call],
                    output_parsed=None,
                    usage=SimpleNamespace(input_tokens=30, output_tokens=40),
                )
            return completed(RoutingProposal(**proposal_fields()))
        assert system == REVIEWER_PROMPT
        return completed(
            ReviewerOutput(
                verdict="accept", agreementProbability=0.7, issues=[], securityQuote=None
            )
        )

    return AsyncMock(side_effect=parse)


async def test_the_multi_arm_runs_and_replays_from_the_cache(monkeypatch, tmp_path):
    live(monkeypatch)
    parse = multi_parse()
    factory = lambda: mock_client(parse)  # noqa: E731
    result = await evaluate_arms(options(arm="multi", ids=["dev-001"]), client_factory=factory)
    assert parse.await_count == 4
    assert result["promptVersions"] == {
        "intake": INTAKE_PROMPT_VERSION,
        "triage": TRIAGE_PROMPT_VERSION,
        "reviewer": REVIEWER_PROMPT_VERSION,
    }
    [row] = result["rows"]
    assert row["reviewerVerdicts"] == {"accept": 1}
    assert row["tokens"] == {"input": 120, "output": 160, "cached": 0, "reasoning": 0}
    assert (row["toolCalls"], row["citedSources"]) == (1, 0)
    assert result["caveats"][-1] == (
        "The multi arm made 1 tool call and cited sources in 0 of 1 cases; its routing was"
        " not tool-grounded in this run."
    )
    markdown = (tmp_path / "ARMS-RESULTS.md").read_text()
    assert next(line for line in markdown.splitlines() if line.startswith("| multi | ")).endswith(
        "| 1 / 0 |"
    )
    [case] = result["cases"]["multi"]["dev"]
    assert case["run"]["status"] == "completed" and case["run"]["modelCalls"] == 4
    assert (case["run"]["toolCalls"], case["run"]["citedSources"]) == (1, 0)
    entry = json.loads(next((tmp_path / "cache/multi").glob("dev-001.*")).read_text())
    assert [s["kind"] for s in entry["result"]["run"]["steps"]] == [
        "model_call",
        "model_call",
        "tool_call",
        "model_call",
        "model_call",
        "policy",
    ]
    again = await evaluate_arms(options(arm="multi", ids=["dev-001"]), client_factory=factory)
    assert parse.await_count == 4 and again["cacheHits"] == 1
    [replayed] = again["cases"]["multi"]["dev"]
    assert replayed["actual"] == case["actual"]
    assert replayed["run"]["usage"] == case["run"]["usage"]
    assert replayed["run"]["modelCalls"] == 4
    assert (again["rows"][0]["toolCalls"], again["rows"][0]["citedSources"]) == (1, 0)
    assert again["caveats"][-1] == result["caveats"][-1]
    assert replayed["latencyMs"] == case["latencyMs"]
    assert replayed["confidence"]["raw"] == case["confidence"]["raw"]
    # Triage ranked its candidates with the configured scoring; flipping it misses the cache.
    assert entry["candidateScoring"] == "v2" and again["candidateScoring"] == "v2"
    monkeypatch.setitem(policy, "routingScoring", "v1")
    flipped = await evaluate_arms(options(arm="multi", ids=["dev-001"]), client_factory=factory)
    assert parse.await_count == 8 and flipped["cacheHits"] == 0
    assert flipped["candidateScoring"] == "v1" and flipped["scoring"] == "v2"
    assert len(list((tmp_path / "cache/multi").glob("dev-001.*"))) == 2


# --- the legacy runner stays untouched ----------------------------------------------------


def _without_run_date(text):
    return "\n".join(line for line in text.splitlines() if not line.startswith("Run: "))


def _stable(result):
    """The legacy result minus what changes between runs: the date and wall-clock latencies."""
    stable = json.loads(json.dumps(result))
    stable.pop("runDate")
    for methods in stable["splits"].values():
        for metrics in methods.values():
            metrics.pop("latency")
            for failure in metrics["failures"]:
                failure.pop("latencyMs")
    return stable


async def test_the_legacy_evaluate_output_is_unchanged_by_the_harness(legacy_outputs, tmp_path):
    before = await evaluate()
    files_before = {name: (ROOT / name).read_text() for name in LEGACY}
    await evaluate_arms(EvalOptions(arm="rules-v2", split="all", fit_calibration=False))
    after = await evaluate()
    files_after = {name: (ROOT / name).read_text() for name in LEGACY}
    assert _stable(before) == _stable(after)
    assert _stable(json.loads(files_before[LEGACY[0]])) == _stable(
        json.loads(files_after[LEGACY[0]])
    )
    assert _stable(json.loads(files_after[LEGACY[0]])) == _stable(after)
    assert _without_run_date(files_before[LEGACY[1]]) == _without_run_date(files_after[LEGACY[1]])
    assert not (tmp_path / "cache").exists()


# --- metric arithmetic --------------------------------------------------------------------


def case_row(text, *, correct=True, value=0.5, evidence=None, first_call_ok=None):
    return {
        "id": "x",
        "text": text,
        "expected": {"team": "Network"},
        "actual": {"team": "Network" if correct else "Endpoint"},
        "confidence": {"raw": value, "value": value},
        "run": None if first_call_ok is None else {"firstCallOk": first_call_ok},
        "evidence": evidence,
    }


def evidence(**overrides):
    return {
        "summary": "Wi-Fi drops",
        "attemptedSteps": False,
        "impact": None,
        "urgency": None,
    } | overrides


def test_fidelity_rates_from_hand_built_rows():
    rows = [
        case_row(
            "No verb here, Wi-Fi drops.", evidence=evidence(attemptedSteps=True), first_call_ok=True
        ),
        case_row(
            "I restarted the laptop and Wi-Fi drops.",
            evidence=evidence(attemptedSteps=True),
            first_call_ok=False,
        ),
        case_row(
            "Everyone in the office lost Wi-Fi.",
            evidence=evidence(impact="Everyone in the office lost Wi-Fi"),
            first_call_ok=True,
        ),
        case_row(
            "Wi-Fi drops.", evidence=evidence(impact="only me", summary=""), first_call_ok=True
        ),
        case_row("Wi-Fi drops."),
    ]
    found = fidelity(rows)
    assert found["quoteValidityRate"] == rate(3, 4)
    assert found["inventedAttemptsRate"] == rate(1, 3)
    assert found["unknownsPreservedRate"] == rate(2, 3)
    assert found["summaryBoundedRate"] == rate(3, 4)
    assert fidelity([]) == {
        "quoteValidityRate": rate(0, 0),
        "inventedAttemptsRate": rate(0, 0),
        "unknownsPreservedRate": rate(0, 0),
        "summaryBoundedRate": rate(0, 0),
    }


def test_confidence_metrics_bins_and_selective_accuracy():
    rows = [
        case_row("a", correct=True, value=0.9),
        case_row("b", correct=False, value=0.85),
        case_row("c", correct=True, value=0.3),
        case_row("d", correct=False, value=0.1),
    ]
    found = confidence_metrics(rows)
    assert found["coverage"] == 0.5 and found["selectiveAccuracy"] == 0.5
    assert found["ece"] == pytest.approx(ece([0.9, 0.85, 0.3, 0.1], [1, 0, 1, 0]))
    assert found["brier"] == pytest.approx((0.01 + 0.85**2 + 0.49 + 0.01) / 4)
    # Positives 0.9 and 0.3 against negatives 0.85 and 0.1: three of four pairs ordered.
    assert found["auroc"] == pytest.approx(0.75)
    assert found["bins"][4] == {
        "lo": 0.8,
        "hi": 1.0,
        "count": 2,
        "meanConfidence": pytest.approx(0.875),
        "accuracy": 0.5,
    }
    assert found["bins"][1] == {
        "lo": 0.2,
        "hi": 0.4,
        "count": 1,
        "meanConfidence": 0.3,
        "accuracy": 1.0,
    }
    assert found["bins"][2] == {
        "lo": 0.4,
        "hi": 0.6,
        "count": 0,
        "meanConfidence": None,
        "accuracy": None,
    }
    empty = confidence_metrics([])
    assert empty["ece"] is None and empty["coverage"] is None and len(empty["bins"]) == 5


# --- CLI ------------------------------------------------------------------------------------


def test_eval_flags_parse_with_the_documented_defaults():
    parser = cli.build_parser()
    args = parser.parse_args(["eval"])
    assert (args.arm, args.split, args.effort, args.limit, args.ids) == (
        "rules-v1",
        "all",
        "medium",
        None,
        None,
    )
    assert (args.resume, args.concurrency, args.max_usd, args.fit_calibration, args.no_write) == (
        True,
        4,
        None,
        False,
        False,
    )
    args = parser.parse_args(
        "eval --arm all --split dev --effort high --limit 5 --ids a,b --no-resume"
        " --concurrency 2 --max-usd 1.5 --fit-calibration --no-write".split()
    )
    assert (args.arm, args.split, args.effort, args.limit, args.ids) == (
        "all",
        "dev",
        "high",
        5,
        ["a", "b"],
    )
    assert (args.resume, args.concurrency, args.max_usd, args.fit_calibration, args.no_write) == (
        False,
        2,
        1.5,
        True,
        True,
    )
    no_write = next(
        a
        for a in parser._subparsers._group_actions[0].choices["eval"]._actions
        if a.dest == "no_write"
    )
    assert "cache entries are still written" in no_write.help
    with pytest.raises(SystemExit):
        parser.parse_args(["eval", "--arm", "bogus"])
    with pytest.raises(SystemExit):
        parser.parse_args(["eval", "--effort", "extreme"])


async def test_eval_dispatches_rules_v1_to_the_legacy_runner_and_other_arms_to_the_harness(
    monkeypatch,
):
    seen = []

    async def legacy():
        seen.append("legacy")

    async def arms(options):
        seen.append(options)

    monkeypatch.setattr("relay.evaluate.evaluate", legacy)
    monkeypatch.setattr("relay.evaluate_arms.evaluate_arms", arms)
    defaults = vars(cli.build_parser().parse_args(["eval"]))
    await cli.run(Namespace(**defaults))
    assert seen == ["legacy"]
    flags = "eval --arm all --split dev --effort high --limit 5 --ids a,b --no-resume"
    flags += " --concurrency 2 --max-usd 1.5 --fit-calibration --no-write"
    await cli.run(Namespace(**vars(cli.build_parser().parse_args(flags.split()))))
    assert seen[1] == EvalOptions(
        arm="all",
        split="dev",
        effort="high",
        limit=5,
        ids=["a", "b"],
        resume=False,
        concurrency=2,
        max_usd=1.5,
        fit_calibration=True,
        write=False,
    )
    with pytest.raises(ValueError, match="legacy"):
        await cli.run(Namespace(**defaults | {"fit_calibration": True, "split": "dev"}))
    assert seen == ["legacy", seen[1]]
