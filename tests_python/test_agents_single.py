"""The single-agent arm: one structured call, validated at the boundary, then bounded by lanes."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from relay.agents import build_pipeline
from relay.agents.compose import compose_decision
from relay.agents.prompts import SINGLE_PROMPT, SINGLE_PROMPT_VERSION
from relay.agents.runtime import ModelRuntime
from relay.agents.schemas import Budget, PipelineContext, SingleAgentOutput
from relay.agents.single import run_single_agent

TIE = "My managed laptop cannot join the office Wi-Fi; it says unable to connect."
SETTINGS = {"effort": "high", "maxOutputTokens": 4096}
USER = {
    "id": "maya",
    "role": "employee",
    "location": "San Francisco",
    "device": "MacBook",
    "scope": "sf",
}


def output(**overrides):
    return SingleAgentOutput(
        **{
            "summary": "Managed laptop cannot join the office Wi-Fi",
            "service": "wifi",
            "serviceQuote": "office Wi-Fi",
            "symptomQuote": "cannot join the office Wi-Fi",
            "impactQuote": None,
            "urgencyQuote": None,
            "deviceQuote": "managed laptop",
            "startedQuote": None,
            "workaroundQuote": None,
            "attemptedStepsQuotes": [],
            "supportRequestQuote": None,
            "procedureAttemptedQuote": None,
            "securityQuote": None,
            "evidenceIds": ["m1"],
            "team": "Network",
            "abstain": False,
            "blockedQuote": None,
            "broadImpactQuote": None,
            "rationale": "The message names the office Wi-Fi.",
            "probability": 0.9,
            "citedSourceIds": [],
            **overrides,
        }
    )


def completed(parsed):
    return SimpleNamespace(
        status="completed",
        output_parsed=parsed,
        usage=SimpleNamespace(input_tokens=30, output_tokens=40),
    )


def runtime(parse, *, budget=None):
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return ModelRuntime(
        SETTINGS,
        budget or Budget(maxModelCalls=2, maxToolCalls=0, maxTotalTokens=25000, maxSeconds=75),
        model="gpt-test",
        client_factory=lambda: client,
    )


def context(text=TIE, sources=None):
    return PipelineContext(
        report_id="r1",
        text=text,
        message_ids=["m1"],
        sources=sources or [],
        user=USER,
        clarifications=0,
        procedure=None,
        settings=SETTINGS,
        pipeline="single",
    )


def article(id="kb-1"):
    return {
        "id": id,
        "title": "Office Wi-Fi will not join",
        "service": "wifi",
        "kind": "article",
        "body": "Reconnect to the network.",
        "metadata": {"team": "Network", "procedure": None},
    }


async def test_the_request_carries_the_single_prompt_message_and_trimmed_sources(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    parse = AsyncMock(return_value=completed(output()))
    rt = runtime(parse)
    sources = [article(f"kb-{n}") for n in range(10)]
    result = await run_single_agent(context(sources=sources), rt)
    args = parse.call_args.kwargs
    assert args["text_format"] is SingleAgentOutput
    assert args["input"][0] == {"role": "system", "content": SINGLE_PROMPT}
    sent = json.loads(args["input"][1]["content"][0]["text"])
    assert sent["message"] == TIE
    assert sent["evidenceIds"] == ["m1"] and sent["approvedProcedure"] is None
    assert len(sent["sources"]) == 8
    assert sent["sources"][0] == {
        "id": "kb-0",
        "title": "Office Wi-Fi will not join",
        "service": "wifi",
        "kind": "article",
        "team": "Network",
    }
    assert rt.steps[0].promptVersion == SINGLE_PROMPT_VERSION
    assert rt.steps[0].role == "intake"
    assert result.run.status == "completed"
    assert result.summary == "Managed laptop cannot join the office Wi-Fi"
    assert result.extraction["deviceQuote"] == "managed laptop"
    assert result.proposal["team"] == "Network"
    assert "team" not in result.extraction and "deviceQuote" not in result.proposal


async def test_a_confident_proposal_breaks_the_deterministic_tie(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    rt = runtime(AsyncMock(return_value=completed(output())))
    ctx = context()
    result = await run_single_agent(ctx, rt)
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert (d["team"], d["service"], d["accepted"]) == ("Network", "wifi", True)
    assert "model-tie-break" in d["reasons"]
    assert d["pipeline"] == "single"
    assert d["promptVersions"]["single"] == SINGLE_PROMPT_VERSION
    assert d["proposal"] == {
        "team": "Network",
        "service": "wifi",
        "abstain": False,
        "probability": 0.9,
        "rationale": "The message names the office Wi-Fi.",
        "citedSourceIds": [],
    }
    assert d["confidence"]["signals"][0] == {
        "kind": "agentProbability",
        "label": "Agent estimated 90% that Network is right",
        "value": 0.9,
    }
    assert d["confidence"]["agentRationale"] == "The message names the office Wi-Fi."
    assert [s.kind for s in result.run.steps] == ["model_call", "policy"]


async def test_a_security_review_proposal_without_a_quote_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    rt = runtime(
        AsyncMock(
            return_value=completed(output(team="Security Review", service=None, serviceQuote=None))
        )
    )
    ctx = context()
    result = await run_single_agent(ctx, rt)
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert (d["team"], d["visibility"], d["escalation"]) == ("Service Desk", "private", "none")
    assert "proposal-rejected-unknown-team" in d["reasons"]
    assert d["confidence"]["signals"][0]["label"] == (
        "Agent proposed Security Review, overruled by policy"
    )
    assert d["confidence"]["signals"][0]["value"] == pytest.approx(0.1)


async def test_a_quote_outside_the_message_is_rejected_and_retried_once(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    parse = AsyncMock(
        side_effect=[completed(output(blockedQuote="I cannot work at all")), completed(output())]
    )
    rt = runtime(parse)
    result = await run_single_agent(context(), rt)
    assert parse.await_count == 2
    assert [s.status for s in rt.steps] == ["rejected", "ok"]
    assert "Proposal asserted unsupported evidence." in rt.steps[0].error
    assert result.run.status == "completed"
    assert result.proposal["blockedQuote"] is None


async def test_a_failed_call_leaves_a_rules_only_decision(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    parse = AsyncMock(side_effect=RuntimeError("Provider unavailable"))
    rt = runtime(parse)
    ctx = context()
    result = await run_single_agent(ctx, rt)
    assert parse.await_count == 1
    assert result.run.status == "failed"
    assert result.extraction is None and result.proposal is None
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert (d["team"], d["model"]) == ("Service Desk", "live-failed")
    assert "model-unavailable" in d["reasons"] and "proposal" not in d


async def test_budget_exhaustion_degrades_to_service_desk(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    parse = AsyncMock(return_value=completed(output()))
    rt = runtime(
        parse,
        budget=Budget(maxModelCalls=0, maxToolCalls=0, maxTotalTokens=25000, maxSeconds=75),
    )
    ctx = context()
    result = await run_single_agent(ctx, rt)
    assert parse.await_count == 0
    assert result.run.status == "budget_exhausted"
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert d["team"] == "Service Desk" and d["model"] == "live-failed"
    assert "agent-budget-exhausted" in d["reasons"]
    assert d["confidence"]["degraded"] is True


async def test_demo_mode_makes_no_model_call(monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    parse = AsyncMock(return_value=completed(output()))
    rt = runtime(parse)
    result = await run_single_agent(context(), rt)
    assert parse.await_count == 0
    assert result.run.status == "skipped"
    assert result.run.model is None
    assert rt.steps == []
    assert result.extraction is None and result.proposal is None


def test_build_pipeline_names_the_single_arm_and_keeps_multi_guarded():
    pipeline = build_pipeline("single", extract=None)
    assert pipeline.name == "single"
    assert build_pipeline("deterministic", extract=None).name == "deterministic"
    with pytest.raises(ValueError, match="Pipeline 'multi' is not available"):
        build_pipeline("multi", extract=None)
