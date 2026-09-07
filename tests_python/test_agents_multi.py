"""The multi-agent arm at the SDK boundary: intake, tool-assisted triage, independent review."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from relay.agents import budget_for, build_pipeline
from relay.agents.compose import compose_decision
from relay.agents.orchestrator import run_multi_agent
from relay.agents.prompts import (
    REVIEWER_PROMPT,
    REVIEWER_PROMPT_VERSION,
    TRIAGE_PROMPT,
    TRIAGE_PROMPT_VERSION,
)
from relay.agents.runtime import ModelRuntime
from relay.agents.schemas import Budget, PipelineContext, ReviewerOutput, RoutingProposal
from relay.agents.tools import CasesArgs
from relay.intake_prompt import INTAKE_PROMPT, INTAKE_PROMPT_VERSION
from relay.model import Extraction

TIE = "My managed laptop cannot join the office Wi-Fi; it says unable to connect."
SETTINGS = {"effort": "high", "maxOutputTokens": 4096}
USER = {
    "id": "maya",
    "role": "employee",
    "location": "San Francisco",
    "device": "MacBook",
    "scope": "sf",
}
MULTI = Budget(maxModelCalls=8, maxToolCalls=4, maxTotalTokens=60000, maxSeconds=150)


def extraction(**overrides):
    return Extraction(
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
            **overrides,
        }
    )


def proposal(**overrides):
    return RoutingProposal(
        **{
            "service": "wifi",
            "team": "Network",
            "abstain": False,
            "blockedQuote": None,
            "broadImpactQuote": None,
            "securityQuote": None,
            "rationale": "The message names the office Wi-Fi.",
            "probability": 0.9,
            "citedSourceIds": [],
            **overrides,
        }
    )


def review(**overrides):
    return ReviewerOutput(
        **{"verdict": "accept", "agreementProbability": 0.85, "issues": [], "securityQuote": None}
        | overrides
    )


ISSUE = {
    "field": "service",
    "message": "The message reports a Wi-Fi join failure, not a laptop fault.",
    "evidenceQuote": "cannot join the office Wi-Fi",
}


def usage():
    return SimpleNamespace(input_tokens=30, output_tokens=40)


def completed(parsed):
    return SimpleNamespace(status="completed", output=[], output_parsed=parsed, usage=usage())


class FunctionCall(SimpleNamespace):
    def model_dump(self, *, exclude_none=False, exclude=()):
        return {
            key: value
            for key, value in vars(self).items()
            if key not in exclude and not (exclude_none and value is None)
        }


def similar_cases_call(service="wifi", call_id="call_1"):
    return FunctionCall(
        type="function_call",
        id="fc_1",
        name="similar_cases",
        call_id=call_id,
        arguments=json.dumps({"service": service}),
        parsed_arguments=CasesArgs(service=service),
        status="completed",
    )


def calling(*calls):
    return SimpleNamespace(
        status="completed", output=list(calls), output_parsed=None, usage=usage()
    )


def script(*, intake=None, triage=(), reviewer=()):
    """A `parse` that answers each role, told apart by its system prompt, from its own queue."""
    queues = {
        INTAKE_PROMPT: list(intake if intake is not None else [completed(extraction())]),
        TRIAGE_PROMPT: list(triage),
        REVIEWER_PROMPT: list(reviewer),
    }
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        item = queues[kwargs["input"][0]["content"]].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    parse.calls = calls
    return parse


def runtime(parse, *, budget=MULTI):
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return ModelRuntime(SETTINGS, budget, model="gpt-test", client_factory=lambda: client)


def case(n, service="wifi", team="Network"):
    return {
        "id": f"case-{n}",
        "kind": "case",
        "title": f"Office Wi-Fi case {n}",
        "body": "Never sent to the model.",
        "service": service,
        "visibility": "all",
        "status": "approved",
        "metadata": {"reviewed": True, "team": team},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def context(text=TIE, sources=None):
    return PipelineContext(
        report_id="r1",
        text=text,
        message_ids=["m1"],
        sources=sources if sources is not None else [case(1), case(2)],
        user=USER,
        clarifications=0,
        procedure=None,
        settings=SETTINGS,
        pipeline="multi",
    )


def sent(call):
    return json.loads(call["input"][1]["content"][0]["text"])


def roles(rt):
    return [(s.role, s.kind, s.status) for s in rt.steps]


@pytest.fixture(autouse=True)
def live(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")


async def test_accept_path_routes_the_tie_through_a_tool_grounded_proposal():
    parse = script(
        triage=[calling(similar_cases_call()), completed(proposal(citedSourceIds=["case-1"]))],
        reviewer=[completed(review())],
    )
    rt = runtime(parse)
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "completed"
    assert roles(rt) == [
        ("intake", "model_call", "ok"),
        ("triage", "model_call", "ok"),
        ("triage", "tool_call", "ok"),
        ("triage", "model_call", "ok"),
        ("reviewer", "model_call", "ok"),
    ]
    intake, triage, again, reviewer = parse.calls
    assert intake["text_format"] is Extraction and "tools" not in intake
    assert sent(intake) == {"message": TIE, "evidenceIds": ["m1"], "approvedProcedure": None}
    assert rt.steps[0].promptVersion == INTAKE_PROMPT_VERSION
    assert triage["text_format"] is RoutingProposal
    assert [t["function"]["name"] for t in triage["tools"]] == [
        "lookup_catalog",
        "similar_cases",
        "search_knowledge",
    ]
    assert sent(triage) == {
        "message": TIE,
        "extraction": extraction().model_dump(),
        "candidates": [
            {"service": "wifi", "team": "Network", "score": 4},
            {"service": "laptop", "team": "Endpoint", "score": 3},
        ],
        "reviewerNotes": None,
    }
    assert rt.steps[1].promptVersion == TRIAGE_PROMPT_VERSION
    echoed = again["input"][2:]
    assert echoed[0]["name"] == "similar_cases" and "parsed_arguments" not in echoed[0]
    assert echoed[1]["type"] == "function_call_output" and echoed[1]["call_id"] == "call_1"
    assert json.loads(echoed[1]["output"]) == [
        {"id": "case-1", "title": "Office Wi-Fi case 1", "team": "Network"},
        {"id": "case-2", "title": "Office Wi-Fi case 2", "team": "Network"},
    ]
    assert reviewer["text_format"] is ReviewerOutput and "tools" not in reviewer
    assert sent(reviewer) == {
        "message": TIE,
        "extraction": extraction().model_dump(),
        "proposal": proposal(citedSourceIds=["case-1"]).model_dump(),
        "citedSources": [{"id": "case-1", "title": "Office Wi-Fi case 1", "team": "Network"}],
    }
    assert rt.steps[4].promptVersion == REVIEWER_PROMPT_VERSION
    assert result.reviewer["verdict"] == "accept" and result.proposal["citedSourceIds"] == [
        "case-1"
    ]
    assert result.summary == "Managed laptop cannot join the office Wi-Fi"

    d = compose_decision(ctx, result, rt, scoring="v1")
    assert (d["team"], d["service"], d["accepted"]) == ("Network", "wifi", True)
    assert "model-tie-break" in d["reasons"]
    assert d["facts"]["service"]["evidenceIds"] == ["m1"]
    assert d["pipeline"] == "multi" and d["model"] == "gpt-test"
    assert d["promptVersions"] == {
        "intake": INTAKE_PROMPT_VERSION,
        "triage": TRIAGE_PROMPT_VERSION,
        "reviewer": REVIEWER_PROMPT_VERSION,
    }
    assert d["proposal"]["citedSourceIds"] == ["case-1"]
    assert d["reviewer"] == {"verdict": "accept", "agreementProbability": 0.85, "issues": []}
    signals = {s["kind"]: s for s in d["confidence"]["signals"]}
    assert signals["agreement"] == {
        "kind": "agreement",
        "label": "Triage and reviewer agree on Network",
        "value": 0.85,
    }
    assert d["confidence"]["degraded"] is False
    assert [s.kind for s in result.run.steps][-1] == "policy"


async def test_a_revise_verdict_runs_one_more_triage_with_the_reviewer_notes():
    parse = script(
        triage=[completed(proposal(service="laptop", team="Endpoint")), completed(proposal())],
        reviewer=[
            completed(review(verdict="revise", agreementProbability=0.3, issues=[ISSUE])),
            completed(review(agreementProbability=0.8)),
        ],
    )
    rt = runtime(parse)
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert [r for r, _, _ in roles(rt)] == ["intake", "triage", "reviewer", "triage", "reviewer"]
    assert sent(parse.calls[3])["reviewerNotes"] == [ISSUE]
    assert result.proposal["team"] == "Network"
    assert result.reviewer["verdict"] == "revise"
    assert result.reviewer["agreementProbability"] == 0.8
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert d["team"] == "Network" and "model-tie-break" in d["reasons"]
    assert d["reviewer"]["verdict"] == "revise"
    signals = {s["kind"]: s for s in d["confidence"]["signals"]}
    assert (
        signals["agreement"]["label"] == "Reviewer asked for one revision; final proposal Network"
    )
    assert signals["agreement"]["value"] == pytest.approx(0.4)


async def test_two_revise_verdicts_hand_the_report_to_a_person():
    parse = script(
        triage=[completed(proposal()), completed(proposal())],
        reviewer=[
            completed(review(verdict="revise", issues=[ISSUE])),
            completed(review(verdict="revise", agreementProbability=0.2, issues=[ISSUE])),
        ],
    )
    rt = runtime(parse)
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "completed"
    assert result.reviewer["verdict"] == "human_review" and result.proposal["abstain"] is True
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert (d["team"], d["accepted"], d["visibility"]) == ("Service Desk", False, "private")
    assert "reviewer-requested-human-review" in d["reasons"]
    assert "model-tie-break" not in d["reasons"]
    assert d["reviewer"] == {
        "verdict": "human_review",
        "agreementProbability": 0.2,
        "issues": [{"field": ISSUE["field"], "message": ISSUE["message"]}],
    }
    signals = {s["kind"]: s for s in d["confidence"]["signals"]}
    assert signals["agreement"] == {
        "kind": "agreement",
        "label": "Reviewer requested human review",
        "value": 0.0,
    }


async def test_the_reviewer_security_quote_restricts_the_report():
    text = "Office Wi-Fi drops and someone else is using my account without permission."
    quote = "someone else is using my account without permission"
    parse = script(
        intake=[
            completed(
                extraction(
                    summary="Office Wi-Fi drops",
                    serviceQuote="Office Wi-Fi",
                    symptomQuote="Office Wi-Fi drops",
                    deviceQuote=None,
                )
            )
        ],
        triage=[completed(proposal())],
        reviewer=[completed(review(verdict="human_review", securityQuote=quote))],
    )
    rt = runtime(parse)
    ctx = context(text=text)
    result = await run_multi_agent(ctx, rt)
    assert result.reviewer["securityQuote"] == quote
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert (d["team"], d["visibility"], d["escalation"], d["priority"]) == (
        "Security Review",
        "restricted",
        "security",
        "urgent",
    )
    assert d["facts"]["security"] == {"value": quote, "origin": "user", "evidenceIds": ["m1"]}
    assert d["reasons"][-2:] == [
        "reviewer-requested-human-review",
        "model-extracted-security-evidence",
    ]
    assert d["procedure"] is None and d["related"] is None
    assert "securityQuote" not in d["reviewer"]


async def test_a_quote_outside_the_message_fails_reviewer_validation_and_retries():
    parse = script(
        triage=[completed(proposal())],
        reviewer=[
            completed(review(securityQuote="my account was hacked")),
            completed(review()),
        ],
    )
    rt = runtime(parse)
    result = await run_multi_agent(context(), rt)
    assert [s for _, _, s in roles(rt)][-2:] == ["rejected", "ok"]
    assert "Reviewer asserted unsupported evidence." in rt.steps[2].error
    assert result.reviewer["securityQuote"] is None


async def test_triage_failure_keeps_the_extraction_and_marks_triage_unavailable():
    parse = script(triage=[RuntimeError("Provider unavailable")])
    rt = runtime(parse)
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "completed"
    assert result.extraction is not None
    assert result.proposal is None and result.reviewer is None
    assert roles(rt) == [("intake", "model_call", "ok"), ("triage", "model_call", "error")]
    assert result.run.outcome["error"] == "Provider unavailable"
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert d["model"] == "gpt-test" and "model-unavailable" not in d["reasons"]
    assert "triage-unavailable" in d["reasons"]
    assert "proposal" not in d and "reviewer" not in d
    assert (d["team"], d["accepted"]) == ("Service Desk", False)
    assert d["facts"]["service"]["value"] == "wifi"


async def test_reviewer_failure_drops_the_proposal_too():
    parse = script(triage=[completed(proposal())], reviewer=[RuntimeError("boom")])
    rt = runtime(parse)
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "completed"
    assert result.proposal is None and result.reviewer is None
    assert result.run.outcome["error"] == "boom"
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert "triage-unavailable" in d["reasons"] and "model-tie-break" not in d["reasons"]


async def test_budget_exhaustion_keeps_what_validated_and_degrades_confidence():
    # Two model calls: intake and triage fit, the reviewer does not.
    parse = script(triage=[completed(proposal())], reviewer=[completed(review())])
    rt = runtime(
        parse, budget=Budget(maxModelCalls=2, maxToolCalls=4, maxTotalTokens=60000, maxSeconds=150)
    )
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "budget_exhausted"
    assert result.extraction is not None and result.proposal is not None
    assert result.reviewer is None
    assert "Model call budget" in result.run.outcome["error"]
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert "agent-budget-exhausted" in d["reasons"] and "triage-unavailable" not in d["reasons"]
    assert d["confidence"]["degraded"] is True
    assert d["proposal"]["team"] == "Network" and "reviewer" not in d


async def test_tool_budget_exhaustion_inside_triage_leaves_no_proposal():
    parse = script(triage=[calling(similar_cases_call()), completed(proposal())])
    rt = runtime(
        parse, budget=Budget(maxModelCalls=5, maxToolCalls=0, maxTotalTokens=60000, maxSeconds=150)
    )
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "budget_exhausted"
    assert result.proposal is None and result.extraction is not None
    assert roles(rt) == [("intake", "model_call", "ok"), ("triage", "model_call", "ok")]
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert "triage-unavailable" in d["reasons"] and "agent-budget-exhausted" in d["reasons"]
    assert d["confidence"]["degraded"] is True


async def test_a_revise_that_never_got_its_revision_becomes_human_review():
    # Three model calls: intake, triage, reviewer (revise); the second triage is over budget.
    parse = script(
        triage=[completed(proposal()), completed(proposal())],
        reviewer=[completed(review(verdict="revise", issues=[ISSUE]))],
    )
    rt = runtime(
        parse, budget=Budget(maxModelCalls=3, maxToolCalls=4, maxTotalTokens=60000, maxSeconds=150)
    )
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "budget_exhausted"
    assert result.reviewer["verdict"] == "human_review" and result.proposal["abstain"] is True
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert d["team"] == "Service Desk"
    assert "reviewer-requested-human-review" in d["reasons"]
    assert "agent-budget-exhausted" in d["reasons"]


async def test_intake_failure_yields_the_live_failed_fallback():
    parse = script(intake=[RuntimeError("Provider unavailable")])
    rt = runtime(parse)
    ctx = context()
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "failed" and result.extraction is None
    assert result.run.outcome == {"extraction": "failed", "error": "Provider unavailable"}
    assert len(parse.calls) == 1
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert (d["team"], d["model"]) == ("Service Desk", "live-failed")
    assert "model-unavailable" in d["reasons"]


async def test_screenshot_text_makes_a_triage_candidate_like_compose():
    message = "It drops every few minutes and I cannot get anything done."
    image = {"data_url": "data:image/png;base64,AAAA", "detail": "auto", "attachmentId": "a1"}
    parse = script(
        intake=[
            completed(
                extraction(
                    summary="Connection drops every few minutes",
                    service=None,
                    serviceQuote=None,
                    symptomQuote="It drops every few minutes",
                    deviceQuote=None,
                    imageObservations=["GlobalProtect window showing Disconnected"],
                    imageText="GlobalProtect\nStatus: Disconnected",
                    imageService="vpn",
                )
            )
        ],
        triage=[completed(proposal(service="vpn", team="Network"))],
        reviewer=[completed(review())],
    )
    rt = runtime(parse)
    ctx = context(text=message, sources=[])
    ctx.image = image
    result = await run_multi_agent(ctx, rt)
    assert result.run.status == "completed"
    intake, triage, reviewer = parse.calls
    assert [part["type"] for part in intake["input"][1]["content"]] == ["input_text", "input_image"]
    assert [part["type"] for part in triage["input"][1]["content"]] == ["input_text"]
    assert sent(triage)["candidates"] == [{"service": "vpn", "team": "Network", "score": 3}]
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert (d["team"], d["service"]) == ("Network", "vpn")
    assert d["facts"]["imageEvidence"]["evidenceIds"] == ["a1"]


def test_the_multi_budget_fits_two_review_rounds_and_tool_turns():
    assert budget_for("multi") == MULTI


async def test_demo_mode_makes_no_call(monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    parse = script(triage=[completed(proposal())], reviewer=[completed(review())])
    rt = runtime(parse)
    result = await run_multi_agent(context(), rt)
    assert parse.calls == [] and rt.steps == []
    assert result.run.status == "skipped" and result.run.model is None
    assert result.extraction is None and result.proposal is None and result.reviewer is None


async def test_a_cited_source_no_tool_returned_is_rejected_and_retried_once():
    parse = script(
        triage=[
            calling(similar_cases_call()),
            completed(proposal(citedSourceIds=["case-9"])),
            completed(proposal(citedSourceIds=["case-2"])),
        ],
        reviewer=[completed(review())],
    )
    rt = runtime(parse)
    result = await run_multi_agent(context(), rt)
    assert roles(rt) == [
        ("intake", "model_call", "ok"),
        ("triage", "model_call", "ok"),
        ("triage", "tool_call", "ok"),
        ("triage", "model_call", "rejected"),
        ("triage", "model_call", "ok"),
        ("reviewer", "model_call", "ok"),
    ]
    assert "Proposal cited a source that was not provided." in rt.steps[3].error
    assert result.proposal["citedSourceIds"] == ["case-2"]
    # The retry continues the same conversation, tool results included.
    assert parse.calls[3]["input"][3]["type"] == "function_call_output"


def test_build_pipeline_names_the_multi_arm():
    assert build_pipeline("multi", extract=None).name == "multi"
