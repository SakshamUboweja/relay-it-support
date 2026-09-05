def ticket_description(report: dict) -> str:
    d = report["decision"]

    def value(key):
        item = d["facts"].get(key, {}).get("value")
        return "Not reported" if item is None else item

    def procedure(identifier):
        p = d.get("procedure")
        return (
            f"{p['title']}: {p['body']}"
            if p and p["id"] == identifier
            else f"Approved procedure {identifier}"
        )

    lines = [
        "Reported issue",
        value("symptom"),
        "",
        f"Started: {value('started')}",
        f"Affected device: {value('device')}",
        f"Impact: {value('impact')}",
        f"Urgency: {value('urgency')}",
        f"Workaround: {value('workaround')}",
        "",
        "Troubleshooting reported by the requester",
        value("attemptedSteps"),
        "",
        "Troubleshooting offered by Relay",
        *([procedure(p) for p in report["offered"]] or ["None"]),
        "",
        "Requester-confirmed attempts of Relay procedures",
        *([procedure(p) for p in report["attempted"]] or ["None confirmed in Relay"]),
        "",
        f"Support team: {d['team']}",
        f"Priority: {d['priority']} (application policy)",
    ]
    if d["facts"].get("security", {}).get("value"):
        lines.append(f"Security evidence: {value('security')}")
    if d["facts"].get("rootCause", {}).get("value"):
        lines.append(f"Possible cause (unconfirmed): {value('rootCause')}")
    return "\n".join(lines)
