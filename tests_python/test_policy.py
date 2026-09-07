import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from relay.fixtures import users
from relay.policy import decide, policy, security_evidence, vpn_auth
from relay.sanitize import sanitize


def test_vpn_password_routes_without_fabricating_facts():
    d = decide("VPN broke after I changed my password", [], users[0])
    assert d["team"] == "Identity & Access"
    assert d["question"] is None
    assert d["facts"]["impact"]["value"] is None
    assert d["facts"]["urgency"]["value"] is None
    assert d["facts"]["rootCause"]["origin"] == "hypothesis"


def test_ambiguity_asks_at_most_one_question():
    assert decide("I cannot get in", [], users[0])["question"]
    d = decide("I cannot get in, it is still broken", [], users[0], 1)
    assert d["question"] is None
    assert d["team"] == "Service Desk"
    assert not d["accepted"]


@pytest.mark.parametrize(
    "text",
    [
        "How do I report phishing?",
        "My account is not compromised. VPN is down.",
        "This is a phishing training example.",
        "I did not click a phishing link.",
    ],
)
def test_security_negation_and_hypothetical_do_not_escalate(text):
    assert not security_evidence(text)


def test_security_bypasses_help_and_survives_separate_negation():
    d = decide("VPN is not working. I approved an unexpected MFA prompt.", [], users[0])
    assert d["team"] == "Security Review"
    assert d["visibility"] == "restricted"
    assert d["procedure"] is None
    assert d["question"] is None


def test_impact_priority_preserves_provenance():
    d = decide("SSO is down for everyone", [], users[0])
    assert d["priority"] == "urgent"
    assert d["facts"]["impact"]["origin"] == "user"
    assert d["related"] is None
    assert (
        decide("VPN fails; work is blocked with no workaround", [], users[0])["priority"]
        == "elevated"
    )
    assert decide("VPN fails", [], users[0])["priority"] == "normal"


def test_known_secrets_removed():
    text = sanitize(
        "my password is Secret123 and MFA code 123456. sk-abcdefghijklmnopqrstuvwx Bearer abcdef"
    )
    for secret in ("Secret123", "123456", "sk-", "abcdef"):
        assert secret not in text


def test_incident_requires_visible_recent_unique_local_curated_evidence():
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    source = {
        "id": "incident-1",
        "kind": "incident",
        "title": "SSO incident",
        "body": "Known disruption",
        "service": "sso",
        "visibility": "all",
        "location": users[0]["location"],
        "status": "open",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "metadata": {"curated": True},
    }
    assert decide("SSO fails", [source], users[0], now=now)["related"] == source
    for changes in (
        {"visibility": "london"},
        {"location": "London"},
        {"status": "closed"},
        {"metadata": {"curated": False}},
        {"updated_at": (now - timedelta(days=2)).isoformat()},
        {"created_at": (now + timedelta(days=1)).isoformat()},
    ):
        assert decide("SSO fails", [{**source, **changes}], users[0], now=now)["related"] is None
    assert (
        decide("SSO fails", [source, {**source, "id": "incident-2"}], users[0], now=now)["related"]
        is None
    )


NOW = "2026-09-05T12:00:00Z"


def _scenario_hash(scoring):
    scenarios = json.loads(
        (Path(__file__).resolve().parents[1] / "evaluation/scenarios.json").read_text()
    )
    rows = []
    for case in scenarios:
        decision = decide(case["text"], [], users[0], now=NOW, scoring=scoring)
        del decision["latencyMs"]
        rows.append({"id": case["id"], "decision": decision})
    assert len(rows) == 120
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def test_frozen_typescript_policy_parity_for_all_120_scenarios():
    """Fingerprint captured from original TypeScript before migration, not labels.

    Includes every decision field except timing, with fixed date and no sources.
    Additional evidence visibility/incident behavior is tested above.
    """
    expected = "dadc8ba58a87f7067afed632c15deb84f24adae13ceffc9b4ce21a35873cd94e"
    assert _scenario_hash("v1") == expected


def test_scoring_v2_frozen_hash():
    """Companion fingerprint for the shipped scoring; v1 above stays reachable for comparison."""
    expected = "a9461301232006a5ee219d5ed38cd6fe54185285b17e5d7722d3dd503001e7a7"
    assert _scenario_hash("v2") == expected


def test_the_configured_default_scoring_is_v2():
    """The measured winner is the live default; `decide()` without `scoring` must resolve to it."""
    assert policy["routingScoring"] == "v2"
    assert _scenario_hash(None) == _scenario_hash("v2")


@pytest.mark.parametrize(
    "text",
    [
        "My managed laptop cannot join the office Wi-Fi; it says unable to connect.",
        "Office Wi-Fi is visible but joining the network fails on my laptop.",
        "My laptop lists office Wi-Fi but the connection never completes."
        " I have not tried any troubleshooting.",
    ],
)
def test_v2_demotes_device_context_of_a_connectivity_failure(text):
    d = decide(text, [], users[0], now=NOW, scoring="v2")
    assert d["team"] == "Network"
    assert d["accepted"]
    assert "device-context-demoted" in d["reasons"]
    assert {"team": "Endpoint", "score": 1} in d["alternatives"]
    assert not decide(text, [], users[0], now=NOW, scoring="v1")["accepted"]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "My laptop freezes after login. I am completely blocked with no usable workaround.",
            id="no-connectivity-candidate",
        ),
        pytest.param(
            "I saw a Wi-Fi incident yesterday, but today wireless works"
            " and my laptop speakers produce no sound.",
            id="no-failure-term-beside-the-wifi-alias",
        ),
        pytest.param(
            "The corporate VPN gateway times out before the sign-in screen appears.",
            id="laptop-hit-is-not-a-container-word",
        ),
    ],
)
def test_v2_leaves_decisions_without_device_context_untouched(text):
    v1 = decide(text, [], users[0], now=NOW, scoring="v1")
    v2 = decide(text, [], users[0], now=NOW, scoring="v2")
    del v1["latencyMs"], v2["latencyMs"]
    assert v2 == v1


@pytest.mark.parametrize("scoring", ["v1", "v2"])
def test_supplemental_text_routes_without_entering_the_symptom(scoring):
    text = "My connection drops every few minutes."
    d = decide(
        text, [], users[0], now=NOW, scoring=scoring, supplemental="GlobalProtect: tunnel failed"
    )
    assert {"team": "Network", "score": 3} in d["alternatives"]
    assert d["facts"]["symptom"]["value"] == text
    assert decide(text, [], users[0], now=NOW, scoring=scoring)["alternatives"] == []


def test_supplemental_text_can_carry_the_security_evidence():
    text = "Please look at the attached screenshot from this morning."
    d = decide(
        text,
        [],
        users[0],
        now=NOW,
        supplemental="Approve sign-in? I got an unexpected MFA prompt I did not request",
    )
    assert d["visibility"] == "restricted"
    assert d["team"] == "Security Review"
    assert decide(text, [], users[0], now=NOW)["visibility"] == "private"


def test_vpn_auth_helper_reports_the_password_exception_only():
    assert vpn_auth("Corporate VPN rejects my new password after I changed my password")
    assert not vpn_auth("My VPN is slow today")


def test_unknown_scoring_is_rejected():
    with pytest.raises(ValueError, match="Unknown routing scoring"):
        decide("hello", [], users[0], scoring="v3")
