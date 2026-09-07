"""Metrics over per-case rows of the arm comparison: routing (shared with the legacy runner),
calibration, latency, tokens and cost, and extraction fidelity."""

import re
from collections import Counter

from .agents.confidence import auroc, brier, ece
from .evaluate import _count, percentiles, routing_metrics
from .policy import broad_impact, work_blocked

ATTEMPT_VERB = re.compile(
    r"\b(tried|restarted|reconnected|rebooted|reset|reinstalled|checked|cleared|updated)\b", re.I
)
BINS = 5
SELECTIVE_THRESHOLD = 0.8
SUMMARY_LIMIT = 120
LATENCY_KIND = "pipeline wall-clock including model calls; cache hits excluded from latency"
TOKEN_KEYS = ("input", "output", "cached", "reasoning")


def correct(row: dict) -> int:
    return int(row["actual"]["team"] == row["expected"]["team"])


def confidence_metrics(rows: list[dict]) -> dict:
    """Calibration of the calibrated `confidence.value` against routing correctness."""
    probs = [r["confidence"]["value"] for r in rows]
    hits = [correct(r) for r in rows]
    bins = []
    for index in range(BINS):
        members = [(p, y) for p, y in zip(probs, hits) if min(int(p * BINS), BINS - 1) == index]
        bins.append(
            {
                "lo": index / BINS,
                "hi": (index + 1) / BINS,
                "count": len(members),
                "meanConfidence": sum(p for p, _ in members) / len(members) if members else None,
                "accuracy": sum(y for _, y in members) / len(members) if members else None,
            }
        )
    selected = [y for p, y in zip(probs, hits) if p >= SELECTIVE_THRESHOLD]
    return {
        "ece": ece(probs, hits, bins=BINS),
        "brier": brier(probs, hits),
        "auroc": auroc(probs, hits),
        "selectiveAccuracy": sum(selected) / len(selected) if selected else None,
        "coverage": len(selected) / len(probs) if probs else None,
        "threshold": SELECTIVE_THRESHOLD,
        "bins": bins,
    }


def latency_metrics(rows: list[dict]) -> dict:
    p50, p95 = percentiles([r["latencyMs"] for r in rows if r["latencyMs"] is not None])
    return {"p50Ms": p50, "p95Ms": p95, "kind": LATENCY_KIND}


def usage_metrics(rows: list[dict]) -> dict:
    """Tokens and cost over every run the rows carry; an unknown price makes the cost null."""
    runs = [r["run"] for r in rows if r.get("run")]
    costs = [run["costUsd"] for run in runs]
    return {
        "tokens": {key: sum(run["usage"][key] for run in runs) for key in TOKEN_KEYS},
        "estimatedCostUSD": None if any(c is None for c in costs) else round(sum(costs), 6),
        "budgetExhausted": sum(run["status"] == "budget_exhausted" for run in runs),
        "reviewerVerdicts": dict(Counter(run["verdict"] for run in runs if run.get("verdict"))),
        "cacheHits": sum(bool(r.get("cacheHit")) for r in rows),
    }


def fidelity(rows: list[dict]) -> dict:
    """How faithfully the model arms quoted, abstained from inventing, and kept unknowns."""
    called = [r for r in rows if r.get("run") and r["run"].get("firstCallOk") is not None]
    extracted = [r for r in rows if r.get("evidence")]
    no_attempt_verb = [r for r in extracted if not ATTEMPT_VERB.search(r["text"])]
    no_phrase = [
        r for r in extracted if not broad_impact(r["text"]) and not work_blocked(r["text"])
    ]
    return {
        "quoteValidityRate": _count(sum(r["run"]["firstCallOk"] for r in called), len(called)),
        "inventedAttemptsRate": _count(
            sum(r["evidence"]["attemptedSteps"] for r in no_attempt_verb), len(no_attempt_verb)
        ),
        "unknownsPreservedRate": _count(
            sum(
                r["evidence"]["impact"] is None and r["evidence"]["urgency"] is None
                for r in no_phrase
            ),
            len(no_phrase),
        ),
        "summaryBoundedRate": _count(
            sum(
                bool(r["evidence"]["summary"]) and len(r["evidence"]["summary"]) <= SUMMARY_LIMIT
                for r in extracted
            ),
            len(extracted),
        ),
    }


def arm_metrics(rows: list[dict], *, arm: str, split: str, scoring: str) -> dict:
    routing = routing_metrics(rows)
    failures = routing.pop("failures")
    return {
        "arm": arm,
        "split": split,
        "scoring": scoring,
        "cases": len(rows),
        **routing,
        "confidence": confidence_metrics(rows),
        "latency": latency_metrics(rows),
        **usage_metrics(rows),
        "fidelity": fidelity(rows),
        "failures": failures,
    }
