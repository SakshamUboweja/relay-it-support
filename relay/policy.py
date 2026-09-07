"""Deterministic routing policy; model output never grants provider privileges."""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .domain import fact
from .fixtures import catalog


class AgentBudget(BaseModel):
    maxModelCalls: int = Field(ge=0)
    maxToolCalls: int = Field(ge=0)
    maxTotalTokens: int = Field(gt=0)
    maxSeconds: float = Field(gt=0)


class Policy(BaseModel):
    version: str
    defaultPriority: str
    reviewMinutes: float = Field(gt=0)
    criticalServices: list[str]
    routingMinimum: float = Field(ge=0)
    routingMargin: float = Field(ge=0)
    routingScoring: Literal["v1", "v2"] = "v1"
    proposalMinConfidence: float = Field(default=0.6, ge=0, le=1)
    contextMaxAgeHours: float = Field(gt=0)
    relatedWindowHours: float = Field(gt=0)
    retentionDays: float = Field(gt=0)
    maxOutputTokens: int = Field(gt=0)
    maxModelRetries: int = Field(ge=0, le=1)
    agentBudget: dict[str, AgentBudget]


policy = Policy.model_validate(
    json.loads((Path(__file__).resolve().parent.parent / "config/policy.json").read_text())
).model_dump()
if policy["defaultPriority"] != "normal":
    raise ValueError("Default priority must remain normal.")


def _matches(pattern, text):
    return re.search(pattern, text) is not None


def _date(value):
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def security_evidence(text: str) -> bool:
    for clause in re.split(r"[.!?;\n]+", text.lower()):
        if _matches(
            r"\b(how (do|can|should)|what (is|are)|training|example of|simulation|drill)\b", clause
        ):
            continue
        if _matches(
            r"\b(no|not|never|haven't|didn't|wasn't|isn't)\b.{0,35}\b(compromis|phish|suspicious|expos|leak|click)",
            clause,
        ):
            continue
        if _matches(
            r"\b(unexpected|unsolicited|unrequested|suspicious|unfamiliar)\b.{0,35}\b(mfa|approval|login|sign.in|prompt)|\b(mfa|approval|login)\b.{0,35}\b(didn.t request|did not request|didn.t initiate|don.t recognize|unexpected|unsolicited)|\b(account|credentials?)\b.{0,25}\b(compromised|hacked|stolen)|\b(phishing|phish)\b.{0,50}\b(clicked|entered|opened)|\b(clicked|entered|approved)\b.{0,50}\b(phishing|suspicious|fake|didn.t request)|\b(data|customer (data|records)|confidential (data|file))\b.{0,30}\b(exposed|leaked|public|sent outside)|\b(ransomware|encrypted my files)\b",
            clause,
        ):
            return True
    return False


def catalog_candidates(text: str, supplemental: str = "") -> list[str]:
    """Catalog service ids whose aliases appear in the message; VPN outranks a bare sign-in hit."""
    scan = text.lower() + "\n" + supplemental.lower() if supplemental else text.lower()
    found = [s["id"] for s in catalog if any(alias in scan for alias in s["aliases"])]
    return [i for i in found if i != "sso"] if "vpn" in found else found


def vpn_auth(text: str) -> bool:
    """Catalog exception: a VPN report whose evidence points at credentials, not the tunnel."""
    t = text.lower()
    return "vpn" in t and (
        ("password" in t and _matches(r"chang|reset|reject|auth", t))
        or _matches(r"authentication failed|invalid credentials", t)
    )


_DEVICE_CONTAINERS = {"laptop", "computer", "macbook", "thinkpad"}
_CONNECTIVITY = ("wifi", "vpn")
_CONNECTIVITY_FAILURE = r"\b(cannot|can.t|unable|won.t|fails?|failed|failing|never completes|drops?|dropped|disconnect\w*|times? out|timeout|unreachable)\b"


def _device_context_row(ranked: list[dict], scan: str) -> dict | None:
    """The laptop row when the laptop is only the container of a failing connection."""
    row = next((r for r in ranked if r["service"] == "laptop"), None)
    if row is None or not any(r["service"] in _CONNECTIVITY for r in ranked):
        return None
    hits = {a for s in catalog if s["id"] == "laptop" for a in s["aliases"] if a in scan}
    if not hits or not hits <= _DEVICE_CONTAINERS:
        return None
    aliases = [a for s in catalog if s["id"] in _CONNECTIVITY for a in s["aliases"]]
    for clause in re.split(r"[.!?;\n]+", scan):
        if any(a in clause for a in aliases) and _matches(_CONNECTIVITY_FAILURE, clause):
            return row
    return None


def _allowed(sources: list[dict], user: dict, now: datetime) -> list[dict]:
    return [
        s
        for s in sources
        if (s["visibility"] in ("all", user["scope"]) or user["role"] == "operator")
        and _date(s["created_at"]) <= now
    ]


def _rank(text: str, supplemental: str, allowed: list[dict], scoring: str) -> tuple[list, bool]:
    """Catalog candidates scored by reviewed-case support, best first; whether v2 demoted one."""
    scan = text.lower() + "\n" + supplemental.lower() if supplemental else text.lower()
    found = catalog_candidates(text, supplemental)
    vpn_password = vpn_auth(text)
    ranked = []
    for s in [s for s in catalog if s["id"] in found]:
        team = "Identity & Access" if vpn_password and s["id"] == "vpn" else s["team"]
        matches = [
            x
            for x in allowed
            if x["kind"] == "case"
            and x["service"] == s["id"]
            and x["metadata"].get("reviewed") is True
            and x["metadata"].get("team") == team
        ]
        ranked.append({"service": s["id"], "team": team, "score": 3 + bool(matches)})
    demoted = _device_context_row(ranked, scan) if scoring == "v2" else None
    if demoted:
        demoted["score"] = 1
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked, demoted is not None


def rank_candidates(
    text: str,
    sources: list[dict],
    user: dict,
    now=None,
    *,
    scoring: str | None = None,
    supplemental: str = "",
) -> list[dict]:
    """The deterministic candidate rows behind `decide`: [{service, team, score}], best first."""
    now = _date(now) if now else datetime.now(timezone.utc)
    scoring = policy["routingScoring"] if scoring is None else scoring
    return _rank(text, supplemental, _allowed(sources, user, now), scoring)[0]


def decide(
    text: str,
    sources: list[dict],
    user: dict,
    clarifications: int = 0,
    now=None,
    *,
    scoring: str | None = None,
    supplemental: str = "",
) -> dict:
    start = time.perf_counter()
    scoring = policy["routingScoring"] if scoring is None else scoring
    if scoring not in ("v1", "v2"):
        raise ValueError("Unknown routing scoring")
    now = _date(now) if now else datetime.now(timezone.utc)
    t, msg = text.lower(), "current-message"
    scan = t + "\n" + supplemental.lower() if supplemental else t
    security = security_evidence(scan)
    unsupported = _matches(
        r"\b(new (access|account|laptop)|request access|access (to|approval)|grant|permission to|procure|purchase|buy |onboard|payroll|vacation|hr request|how (do|can|should) i report|what is phishing)\b",
        t,
    )
    vpn_password = vpn_auth(text)
    allowed = _allowed(sources, user, now)
    ranked, demoted = _rank(text, supplemental, allowed, scoring)
    top = ranked[0] if ranked else None
    accepted = bool(
        top
        and not unsupported
        and (len(ranked) == 1 or top["score"] - ranked[1]["score"] >= policy["routingMargin"])
        and top["score"] >= policy["routingMinimum"]
    )
    service = top["service"] if accepted else None
    team = top["team"] if accepted else "Service Desk"
    reasons = [
        "unsupported-workflow"
        if unsupported
        else ("catalog-vpn-password-exception" if vpn_password else "catalog-and-evidence")
        if accepted
        else "insufficient-or-conflicting-evidence"
    ]
    if demoted:
        reasons.insert(1, "device-context-demoted")
    broad = _matches(
        r"\b(everyone|entire (team|office|company)|all (employees|users|staff)|whole (team|office)|company.wide|organization.wide)\b",
        t,
    ) and not _matches(r"not (everyone|the entire|all)", t)
    blocked = _matches(
        r"\b(blocked|cannot work|can.t work|unable to work|work has stopped)\b", t
    ) and _matches(
        r"no (usable )?workaround|no (other|alternative)|nothing else|without a workaround", t
    )
    incidents = [
        s
        for s in allowed
        if s["kind"] == "incident"
        and s["status"] == "open"
        and s["service"] == service
        and s["location"] == user["location"]
        and s["metadata"].get("curated") is True
        and (now - _date(s["updated_at"])).total_seconds() <= policy["relatedWindowHours"] * 3600
    ]
    related = incidents[0] if not security and not unsupported and len(incidents) == 1 else None
    priority, escalation = "normal", "none"
    if security:
        team, priority, escalation = "Security Review", "urgent", "security"
        reasons.append("possible-compromise-restricted-review")
    elif service and service in policy["criticalServices"] and (related or broad):
        priority, escalation = "urgent", "urgent"
        reasons.append(
            "authoritative-active-advisory" if related else "user-reported-broad-critical-loss"
        )
    elif blocked:
        priority, escalation = "elevated", "elevated"
        reasons.append("explicit-work-blocked-no-workaround")
    else:
        reasons.append("provisional-priority-impact-unknown")
    procedure = None
    if not unsupported and not security and escalation == "none" and accepted:
        procedure = next(
            (
                s
                for s in allowed
                if s["kind"] == "article"
                and s["service"] == service
                and s["metadata"].get("procedure")
                and _matches(
                    r"disconnect|won.t connect|cannot connect|can.t connect"
                    if s["metadata"]["procedure"] == "wifi"
                    else r"external (display|monitor)|monitor|display cable",
                    t,
                )
            ),
            None,
        )
    question = (
        "Which service is affected: VPN, sign-in, Wi-Fi, your laptop, or Atlas?"
        if not accepted and not unsupported and not security and clarifications == 0
        else None
    )
    facts = {
        "symptom": fact(text, "user", [msg]),
        "service": fact(service, "user" if service else "unknown", [msg] if service else []),
        "impact": fact(
            "broad loss reported" if broad else None,
            "user" if broad else "unknown",
            [msg] if broad else [],
        ),
        "urgency": fact(
            "work blocked; no workaround reported" if blocked else None,
            "user" if blocked else "unknown",
            [msg] if blocked else [],
        ),
        "device": fact(user["device"], "context", ["demo-device"]),
        "location": fact(user["location"], "context", ["demo-directory"]),
        "passwordChange": fact(
            "password change reported"
            if _matches(r"(?:changed|reset).{0,20}password|password.{0,20}(?:changed|reset)", t)
            else None,
            "user" if "password" in t and vpn_password else "unknown",
            [msg] if vpn_password else [],
        ),
        "rootCause": fact(
            "cached credentials may be involved" if vpn_password else None,
            "hypothesis" if vpn_password else "unknown",
            ["catalog-vpn-password-exception"] if vpn_password else [],
        ),
        "priority": fact(priority, "policy_default", [policy["version"]]),
    }
    return {
        "team": team,
        "service": service,
        "accepted": security or accepted,
        "reasons": reasons,
        "alternatives": [{"team": r["team"], "score": r["score"]} for r in ranked],
        "priority": priority,
        "escalation": escalation,
        "visibility": "restricted" if security else "private",
        "facts": facts,
        "sources": [
            s["id"] for s in allowed if s["service"] == service and s["kind"] != "incident"
        ][:5],
        "procedure": procedure,
        "related": related,
        "question": question,
        "model": "deterministic-demo-v1",
        "version": policy["version"],
        "latencyMs": (time.perf_counter() - start) * 1000,
        "usage": {"input": 0, "output": 0},
    }
