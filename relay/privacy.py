from copy import deepcopy

from .db import query


async def requester_report(report: dict, user: dict) -> dict:
    """Re-resolve embedded evidence; historical snapshots never grant access."""
    if user["role"] == "operator":
        return report
    safe = deepcopy(report)
    decision = safe["decision"]
    ids = [decision[key]["id"] for key in ("procedure", "related") if decision.get(key)]
    allowed = (
        await query(
            "SELECT * FROM sources WHERE id=ANY($1) AND (visibility='all' OR visibility=$2) AND (kind<>'incident' OR location=$3)",
            [ids, user["scope"], user["location"]],
        )
    ).rows
    for key in ("procedure", "related"):
        original = decision.get(key)
        decision[key] = next((s for s in allowed if original and s["id"] == original["id"]), None)
    return safe
