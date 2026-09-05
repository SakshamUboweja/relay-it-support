import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from relay.fixtures import users
from relay.policy import decide, security_evidence
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


def test_frozen_typescript_policy_parity_for_all_120_scenarios():
    """Fingerprint captured from original TypeScript before migration, not labels.

    Includes every decision field except timing, with fixed date and no sources.
    Additional evidence visibility/incident behavior is tested above.
    """
    scenarios = json.loads(
        (Path(__file__).resolve().parents[1] / "evaluation/scenarios.json").read_text()
    )
    rows = []
    for case in scenarios:
        decision = decide(case["text"], [], users[0], now="2026-09-05T12:00:00Z")
        del decision["latencyMs"]
        rows.append({"id": case["id"], "decision": decision})
    assert len(rows) == 120
    actual = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert actual == "dadc8ba58a87f7067afed632c15deb84f24adae13ceffc9b4ce21a35873cd94e"
