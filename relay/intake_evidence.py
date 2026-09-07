"""Apply validated evidence without giving model prose provider privileges."""

from .domain import TEAMS, fact
from .fixtures import catalog
from .policy import policy, vpn_auth

SECURITY_REASON = "model-extracted-security-evidence"


def _restrict(d: dict, quote: str, evidence_ids: list[str]) -> dict:
    """The one security branch: restricted review, no troubleshooting, no external handoff."""
    d["facts"]["security"] = fact(quote, "user", evidence_ids)
    d.update(
        team="Security Review",
        escalation="security",
        priority="urgent",
        visibility="restricted",
        procedure=None,
        question=None,
        related=None,
    )
    d["facts"]["priority"] = fact("urgent", "policy_default", [d["version"]])
    d["reasons"].append(SECURITY_REASON)
    return d


def apply_extraction(d: dict, data: dict) -> dict:
    quotes = {
        key: data[f"{key}Quote"]
        for key in (
            "impact",
            "urgency",
            "device",
            "started",
            "workaround",
            "supportRequest",
            "procedureAttempted",
        )
    }
    quotes["attemptedSteps"] = "\n".join(data["attemptedStepsQuotes"]) or None
    for key, quote in quotes.items():
        if quote:
            d["facts"][key] = fact(quote, "user", data["evidenceIds"])
    if data["impactQuote"] and d["priority"] == "normal":
        d["reasons"] = [r for r in d["reasons"] if r != "provisional-priority-impact-unknown"]
        d["reasons"].append("normal-priority-reported-impact")
    if data["service"] and d["service"] != data["service"] and d["visibility"] != "restricted":
        d.update(accepted=False, team="Service Desk", procedure=None)
        d["reasons"].append("model-catalog-conflict")
    if data["securityQuote"]:
        _restrict(d, data["securityQuote"], data["evidenceIds"])
    return d


def _catalog_team(service: str, text: str) -> str:
    if service == "vpn" and vpn_auth(text):
        return "Identity & Access"
    return next(s["team"] for s in catalog if s["id"] == service)


def _evidence_ids(d: dict) -> list[str]:
    """The requester evidence already carried by the decision; never invented here."""
    service = d["facts"].get("service", {}).get("evidenceIds")
    return list(service or d["facts"]["symptom"]["evidenceIds"])


def apply_proposal(
    d: dict, proposal: dict, *, candidates: list[str], text: str, raw_confidence: float
) -> dict:
    """Bounded lanes over a validated proposal: it may break a tie, never open a gate."""
    if d["visibility"] == "restricted":
        d["reasons"].append("proposal-ignored-restricted")
        return d
    if proposal["team"] not in TEAMS or proposal["team"] == "Security Review":
        d["reasons"].append("proposal-rejected-unknown-team")
        return d
    service = proposal["service"]
    if service and proposal["team"] != _catalog_team(service, text):
        d["reasons"].append("proposal-team-service-mismatch")
        return d
    if (
        not d["accepted"]
        and "unsupported-workflow" not in d["reasons"]
        and service is not None
        and service in candidates
        and not proposal["abstain"]
        and raw_confidence >= policy["proposalMinConfidence"]
    ):
        ids = _evidence_ids(d)
        d.update(service=service, team=proposal["team"], accepted=True, question=None)
        d["facts"]["service"] = fact(service, "user", ids)
        d["reasons"].append("model-tie-break")
    if proposal["abstain"] and d["accepted"] and d["escalation"] != "security":
        d.update(accepted=False, team="Service Desk", service=None, procedure=None)
        d["reasons"].append("model-abstain")
    if proposal["blockedQuote"] and d["escalation"] == "none":
        d.update(priority="elevated", escalation="elevated")
        d["facts"]["urgency"] = fact(proposal["blockedQuote"], "user", _evidence_ids(d))
        d["facts"]["priority"] = fact("elevated", "policy_default", [d["version"]])
        d["reasons"].append("model-cited-work-blocked")
    if proposal["broadImpactQuote"]:
        d["facts"]["impact"] = fact(proposal["broadImpactQuote"], "user", _evidence_ids(d))
        if d["service"] in policy["criticalServices"] and d["escalation"] == "none":
            d.update(priority="urgent", escalation="urgent")
            d["facts"]["priority"] = fact("urgent", "policy_default", [d["version"]])
            d["reasons"].append("user-reported-broad-critical-loss")
    if proposal["securityQuote"]:
        _restrict(d, proposal["securityQuote"], _evidence_ids(d))
    return d
