"""Apply validated evidence without giving model prose provider privileges."""

from .domain import fact


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
        d["facts"]["security"] = fact(data["securityQuote"], "user", data["evidenceIds"])
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
        d["reasons"].append("model-extracted-security-evidence")
    return d
