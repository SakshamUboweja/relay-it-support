"""Bounded proposal lanes: a model may break a tie, never bypass a policy gate."""

import json
from pathlib import Path

import pytest

from relay.agents.schemas import RoutingProposal, validate_proposal
from relay.fixtures import catalog, users
from relay.intake_evidence import apply_extraction, apply_proposal
from relay.policy import catalog_candidates, decide, vpn_auth

TIE = "My managed laptop cannot join the office Wi-Fi; it says unable to connect."
CLEAR = "Corporate VPN connection failure since this morning."


def proposal(**overrides):
    return {
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


def extraction(**overrides):
    return {
        "summary": "Wi-Fi will not join",
        "service": None,
        "serviceQuote": None,
        "symptomQuote": None,
        "impactQuote": None,
        "urgencyQuote": None,
        "deviceQuote": None,
        "startedQuote": None,
        "workaroundQuote": None,
        "attemptedStepsQuotes": [],
        "supportRequestQuote": None,
        "procedureAttemptedQuote": None,
        "securityQuote": None,
        "evidenceIds": ["m1"],
        **overrides,
    }


def applied(text, /, raw=0.9, **overrides):
    d = decide(text, [], users[0])
    return d, apply_proposal(
        d,
        proposal(**overrides),
        candidates=catalog_candidates(text),
        text=text,
        raw_confidence=raw,
    )


def test_validate_proposal_checks_quotes_team_and_cited_sources():
    text = "Office Wi-Fi keeps dropping."
    assert validate_proposal(proposal(), text, set())["team"] == "Network"
    assert validate_proposal(RoutingProposal(**proposal()), text, set())["probability"] == 0.9
    with pytest.raises(ValueError, match="Proposal asserted unsupported evidence."):
        validate_proposal(proposal(blockedQuote="I cannot work at all"), text, set())
    with pytest.raises(ValueError, match="Proposal named an unknown team."):
        validate_proposal(proposal(team="Platform Ops"), text, set())
    with pytest.raises(ValueError, match="Proposal cited a source that was not provided."):
        validate_proposal(proposal(citedSourceIds=["src-9"]), text, {"src-1"})
    assert validate_proposal(proposal(citedSourceIds=["src-1"]), text, {"src-1"})


def test_unknown_or_security_review_team_is_rejected_without_other_change():
    d, out = applied(TIE, team="Platform Ops")
    assert "proposal-rejected-unknown-team" in out["reasons"]
    assert (out["team"], out["accepted"]) == ("Service Desk", False)
    d, out = applied(TIE, team="Security Review", service=None)
    assert "proposal-rejected-unknown-team" in out["reasons"]
    assert (out["team"], out["escalation"], out["visibility"]) == (
        "Service Desk",
        "none",
        "private",
    )


def test_restricted_decisions_ignore_the_proposal():
    text = "VPN is down. I approved an unexpected MFA prompt I did not request."
    d, out = applied(text, service="vpn", team="Network")
    assert out["visibility"] == "restricted" and out["team"] == "Security Review"
    assert out["reasons"][-1] == "proposal-ignored-restricted"
    assert "model-tie-break" not in out["reasons"]

    quoted = "The VPN is down and someone signed into my account from Brazil."
    d = decide(quoted, [], users[0])
    apply_extraction(d, extraction(securityQuote="someone signed into my account from Brazil"))
    assert d["visibility"] == "restricted"
    out = apply_proposal(
        d,
        proposal(service="vpn", team="Network"),
        candidates=catalog_candidates(quoted),
        text=quoted,
        raw_confidence=0.95,
    )
    assert out["team"] == "Security Review"
    assert out["reasons"][-1] == "proposal-ignored-restricted"


def test_team_must_match_the_catalog_team_of_the_named_service():
    d, out = applied(TIE, service="wifi", team="Endpoint")
    assert "proposal-team-service-mismatch" in out["reasons"]
    assert (out["team"], out["accepted"]) == ("Service Desk", False)
    password = "Corporate VPN rejects my new password after I changed my password."
    assert vpn_auth(password)
    d, out = applied(password, service="vpn", team="Network")
    assert "proposal-team-service-mismatch" in out["reasons"]
    d, out = applied(password, service="vpn", team="Identity & Access")
    assert "proposal-team-service-mismatch" not in out["reasons"]


def test_tie_break_routes_only_when_every_gate_passes():
    d, out = applied(TIE)
    assert (out["team"], out["service"], out["accepted"]) == ("Network", "wifi", True)
    assert out["question"] is None
    assert out["reasons"][-1] == "model-tie-break"
    assert out["facts"]["service"] == {
        "value": "wifi",
        "origin": "user",
        "evidenceIds": ["current-message"],
    }
    # Already accepted deterministically: the model does not get to re-route.
    d, out = applied(CLEAR, service="vpn", team="Network")
    assert "model-tie-break" not in out["reasons"] and out["team"] == "Network"
    # A service the deterministic scan never saw is not a candidate.
    d, out = applied(TIE, service="atlas", team="Business Applications")
    assert "model-tie-break" not in out["reasons"] and out["team"] == "Service Desk"
    # Below the configured minimum confidence.
    d, out = applied(TIE, raw=0.59)
    assert "model-tie-break" not in out["reasons"] and out["team"] == "Service Desk"
    d, out = applied(TIE, raw=0.6)
    assert "model-tie-break" in out["reasons"]
    # The model abstained.
    d, out = applied(TIE, abstain=True)
    assert "model-tie-break" not in out["reasons"]


def test_unsupported_workflow_blocks_the_tie_break():
    text = "I need to request access to Atlas and the office Wi-Fi for a new laptop."
    d = decide(text, [], users[0])
    assert "unsupported-workflow" in d["reasons"]
    out = apply_proposal(
        d,
        proposal(service="wifi", team="Network"),
        candidates=catalog_candidates(text),
        text=text,
        raw_confidence=0.95,
    )
    assert "model-tie-break" not in out["reasons"]
    assert (out["team"], out["accepted"]) == ("Service Desk", False)


def test_abstention_vetoes_an_accepted_route_but_never_a_security_route():
    d = decide(CLEAR, [], users[0])
    assert d["accepted"] and d["team"] == "Network"
    d["related"] = {"id": "inc-1", "title": "VPN advisory"}
    out = apply_proposal(
        d,
        proposal(service=None, team="Service Desk", abstain=True),
        candidates=catalog_candidates(CLEAR),
        text=CLEAR,
        raw_confidence=0.9,
    )
    assert (out["accepted"], out["team"], out["service"], out["procedure"]) == (
        False,
        "Service Desk",
        None,
        None,
    )
    assert out["reasons"][-1] == "model-abstain"
    assert out["related"] == {"id": "inc-1", "title": "VPN advisory"}
    secure = "I approved an unexpected MFA prompt I did not request."
    d, out = applied(secure, service=None, team="Service Desk", abstain=True)
    assert out["team"] == "Security Review" and "model-abstain" not in out["reasons"]


def test_blocked_quote_elevates_priority_with_provenance():
    text = "Office Wi-Fi keeps dropping and I am stuck with no way to work."
    assert decide(text, [], users[0])["escalation"] == "none"
    d, out = applied(text, blockedQuote="I am stuck with no way to work")
    assert (out["priority"], out["escalation"]) == ("elevated", "elevated")
    assert out["facts"]["urgency"]["value"] == "I am stuck with no way to work"
    assert out["facts"]["urgency"]["origin"] == "user"
    assert out["facts"]["priority"] == {
        "value": "elevated",
        "origin": "policy_default",
        "evidenceIds": [out["version"]],
    }
    assert out["reasons"][-1] == "model-cited-work-blocked"


def test_broad_impact_records_the_fact_and_escalates_only_critical_services():
    text = "Office Wi-Fi is failing for lots of people in my office."
    d, out = applied(text, broadImpactQuote="failing for lots of people in my office")
    assert out["priority"] == "normal" and out["escalation"] == "none"
    assert out["facts"]["impact"]["value"] == "failing for lots of people in my office"
    assert "user-reported-broad-critical-loss" not in out["reasons"]
    critical = "Single sign-on is failing for lots of people in my office."
    d, out = applied(
        critical,
        service="sso",
        team="Identity & Access",
        broadImpactQuote="failing for lots of people in my office",
    )
    assert (out["priority"], out["escalation"]) == ("urgent", "urgent")
    assert out["facts"]["priority"]["value"] == "urgent"
    assert out["reasons"][-1] == "user-reported-broad-critical-loss"


def test_security_quote_restricts_the_report_like_extraction_does():
    text = "Office Wi-Fi drops and someone else is using my account without permission."
    d = decide(text, [], users[0])
    assert d["visibility"] == "private"
    out = apply_proposal(
        d,
        proposal(securityQuote="someone else is using my account without permission"),
        candidates=catalog_candidates(text),
        text=text,
        raw_confidence=0.9,
    )
    assert (out["team"], out["escalation"], out["visibility"], out["priority"]) == (
        "Security Review",
        "security",
        "restricted",
        "urgent",
    )
    assert out["procedure"] is None and out["question"] is None
    assert out["facts"]["security"]["origin"] == "user"
    assert out["reasons"][-1] == "model-extracted-security-evidence"


def _expected_candidates(text: str) -> list[str]:
    scan = text.lower()
    found = [s["id"] for s in catalog if any(alias in scan for alias in s["aliases"])]
    return [i for i in found if i != "sso"] if "vpn" in found else found


def test_catalog_candidates_matches_the_deterministic_scan_on_every_scenario():
    scenarios = json.loads(
        (Path(__file__).resolve().parents[1] / "evaluation/scenarios.json").read_text()
    )
    assert len(scenarios) == 120
    teams = {s["id"]: s["team"] for s in catalog}
    for case in scenarios:
        text = case["text"]
        found = catalog_candidates(text)
        assert found == _expected_candidates(text), text
        d = decide(text, [], users[0], now="2026-09-05T12:00:00Z")
        expected = [
            "Identity & Access" if vpn_auth(text) and i == "vpn" else teams[i] for i in found
        ]
        assert sorted(a["team"] for a in d["alternatives"]) == sorted(expected), text
    assert catalog_candidates("Nothing here") == []
    assert catalog_candidates("Nothing here", "the wifi is down") == ["wifi"]
