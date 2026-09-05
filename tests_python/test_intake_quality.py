import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from relay.fixtures import users
from relay.intake_evidence import apply_extraction
from relay.model import Extraction, extract_live, model_settings, validate_extraction
from relay.policy import decide
from relay.ticket_description import ticket_description


TEXT = "My external monitor flickers through the USB-C dock. It started this morning. I restarted the laptop, reconnected the cables, and checked the monitor input, but it still happens. Only I am affected. I can work on the laptop screen. This is not urgent. Please send this to IT support."
EXTRACTED = {
    "summary": "External monitor flickers through USB-C dock",
    "service": "laptop",
    "serviceQuote": "external monitor",
    "symptomQuote": "My external monitor flickers through the USB-C dock.",
    "impactQuote": "Only I am affected.",
    "urgencyQuote": "This is not urgent.",
    "deviceQuote": "external monitor",
    "startedQuote": "It started this morning.",
    "workaroundQuote": "I can work on the laptop screen.",
    "attemptedStepsQuotes": [
        "I restarted the laptop, reconnected the cables, and checked the monitor input, but it still happens."
    ],
    "supportRequestQuote": "Please send this to IT support.",
    "procedureAttemptedQuote": "I restarted the laptop, reconnected the cables, and checked the monitor input, but it still happens.",
    "securityQuote": None,
    "evidenceIds": ["test-message"],
}


def test_known_low_impact_and_attempts_are_preserved_as_user_evidence():
    data = validate_extraction(EXTRACTED, TEXT, ["test-message"])
    d = apply_extraction(decide(TEXT, [], users[0]), data)
    assert d["team"] == "Endpoint"
    assert d["priority"] == "normal"
    for key in (
        "impact",
        "urgency",
        "workaround",
        "device",
        "started",
        "supportRequest",
        "procedureAttempted",
    ):
        assert d["facts"][key] == {
            "value": EXTRACTED[f"{key}Quote"],
            "origin": "user",
            "evidenceIds": ["test-message"],
        }
    assert "provisional-priority-impact-unknown" not in d["reasons"]
    description = ticket_description({"decision": d, "offered": [], "attempted": []})
    assert TEXT in description
    assert "Impact: Only I am affected." in description
    assert "Troubleshooting reported by the requester\nI restarted" in description
    assert "None confirmed in Relay" in description
    assert '"evidenceIds"' not in description


@pytest.mark.parametrize(
    "change",
    [
        {"symptomQuote": "invented"},
        {"attemptedStepsQuotes": ["I replaced the dock"]},
        {"supportRequestQuote": "Escalate to the CEO"},
        {"workaroundQuote": "I have a spare monitor"},
    ],
)
def test_quotes_cannot_invent_facts(change):
    with pytest.raises(ValueError, match="unsupported evidence"):
        validate_extraction({**EXTRACTED, **change}, TEXT, ["test-message"])


def test_evidence_must_be_allowed_and_present():
    with pytest.raises(ValueError, match="disallowed"):
        validate_extraction(
            {**EXTRACTED, "evidenceIds": ["private-secret"]}, TEXT, ["test-message"]
        )
    with pytest.raises(ValueError, match="message evidence"):
        validate_extraction({**EXTRACTED, "evidenceIds": []}, TEXT, ["test-message"])
    with pytest.raises(ValueError, match="source quote"):
        validate_extraction({**EXTRACTED, "serviceQuote": None}, TEXT, ["test-message"])


def test_security_override_remains_restricted_despite_routine_support_request():
    quote = "I approved an MFA prompt I did not initiate."
    text = TEXT + " " + quote
    data = validate_extraction({**EXTRACTED, "securityQuote": quote}, text, ["test-message"])
    d = apply_extraction(decide(text, [], users[0]), data)
    assert (d["team"], d["priority"], d["visibility"]) == (
        "Security Review",
        "urgent",
        "restricted",
    )
    assert d["facts"]["priority"]["value"] == "urgent"
    assert d["procedure"] is d["question"] is d["related"] is None


def test_conflicting_service_abstains_instead_of_accepting_model_routing():
    data = {**EXTRACTED, "service": "vpn"}
    d = apply_extraction(decide(TEXT, [], users[0]), data)
    assert not d["accepted"]
    assert d["team"] == "Service Desk"
    assert "model-catalog-conflict" in d["reasons"]


def test_terra_settings_preserve_high_reasoning_and_bounded_budget(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "8192")
    assert model_settings() == {"effort": "high", "maxOutputTokens": 8192}
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "99999")
    with pytest.raises(ValueError):
        model_settings()


@pytest.mark.asyncio
async def test_python_responses_adapter_sends_pydantic_schema_and_validates_result(monkeypatch):
    """No provider call: exercise SDK boundary, not merely extraction helpers."""
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "8192")
    parse = AsyncMock(
        return_value=SimpleNamespace(
            status="completed",
            output_parsed=Extraction.model_validate(EXTRACTED),
            usage=SimpleNamespace(input_tokens=123, output_tokens=45),
        )
    )
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    monkeypatch.setattr("relay.model.live_client", lambda: client)
    result = await extract_live(TEXT, ["test-message"])
    args = parse.call_args.kwargs
    assert args["text_format"] is Extraction
    assert args["model"] == "gpt-5.6-terra"
    assert args["reasoning"] == {"effort": "high"}
    assert args["max_output_tokens"] == 8192
    assert args["store"] is False
    assert json.loads(args["input"][1]["content"])["evidenceIds"] == ["test-message"]
    assert result == {"data": EXTRACTED, "usage": {"input": 123, "output": 45}}


@pytest.mark.asyncio
async def test_incomplete_extraction_retries_once_then_fails_closed(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    parse = AsyncMock(return_value=SimpleNamespace(status="incomplete", output_parsed=None))
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    monkeypatch.setattr("relay.model.live_client", lambda: client)
    with pytest.raises(ValueError, match="incomplete or refused"):
        await extract_live(TEXT, ["test-message"])
    assert parse.await_count == 2
