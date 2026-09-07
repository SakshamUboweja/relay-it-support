"""Pure confidence arithmetic: weighted features, optional calibration, evaluation metrics."""

import json

from ..config import ROOT
from ..fixtures import catalog
from ..policy import policy

CONFIDENCE_PATH = ROOT / "config/confidence.json"
CALIBRATION_PATH = ROOT / "config/calibration.json"


def _config() -> dict:
    return json.loads(CONFIDENCE_PATH.read_text())


def load_weights() -> dict[str, float]:
    return _config()["weights"]


def bands() -> dict[str, float]:
    return _config()["bands"]


def has_calibration() -> bool:
    return CALIBRATION_PATH.exists()


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _reviewed_cases(sources: list[dict], service: str | None) -> list[dict]:
    return [
        s
        for s in sources
        if s.get("kind") == "case"
        and s.get("metadata", {}).get("reviewed") is True
        and s.get("service") == service
    ]


def _service_name(service: str | None) -> str | None:
    return next((s["name"] for s in catalog if s["id"] == service), service)


def features(
    *,
    pipeline: str,
    decision: dict,
    ranked: list[dict],
    sources: list[dict],
    extraction_ok,
    proposal=None,
    reviewer=None,
) -> dict[str, float | None]:
    """Per-signal values in [0, 1]; None means the signal does not apply to this run."""
    if decision["escalation"] == "security" or len(ranked) == 1:
        margin = 1.0
    elif not ranked:
        margin = 0.0
    else:
        gap = ranked[0]["score"] - ranked[1]["score"]
        limit = policy["routingMargin"]
        margin = _clip(gap / limit) if limit else float(gap > 0)
    cases = _reviewed_cases(sources, decision["service"]) if decision["service"] else []
    support = (
        sum(c["metadata"].get("team") == decision["team"] for c in cases) / len(cases)
        if cases
        else None
    )
    fidelity = {None: None, True: 1.0, False: 0.0, "retried": 0.5}[extraction_ok]
    return {
        "agentProbability": None,
        "agreement": None,
        "retrievalSupport": support,
        "deterministicMargin": margin,
        "evidenceFidelity": fidelity,
    }


def raw_score(features: dict, weights: dict) -> float:
    """Weighted mean over the signals that exist, so a missing signal does not drag the score."""
    present = [(weights[k], v) for k, v in features.items() if v is not None and k in weights]
    total = sum(w for w, _ in present)
    return sum(w * v for w, v in present) / total if total else 0.0


def band(value: float) -> str:
    limits = bands()
    if value >= limits["high"]:
        return "high"
    return "medium" if value >= limits["medium"] else "low"


def signals(features: dict, *, decision: dict, ranked: list[dict], sources: list[dict]) -> list:
    found = []
    margin = features.get("deterministicMargin")
    if margin is not None:
        if decision["escalation"] == "security":
            label = "Security indicators decided the route"
        elif not ranked:
            label = "No catalog match in the message"
        elif margin >= 1.0:
            name = _service_name(decision["service"]) or ranked[0]["team"]
            label = f"Catalog evidence favoured {name} by a clear margin"
        else:
            label = f"Catalog evidence was tied between {ranked[0]['team']} and {ranked[1]['team']}"
        found.append({"kind": "deterministicMargin", "label": label, "value": margin})
    support = features.get("retrievalSupport")
    if support is not None:
        cases = _reviewed_cases(sources, decision["service"])
        hits = sum(c["metadata"].get("team") == decision["team"] for c in cases)
        label = f"{hits} of {len(cases)} similar reviewed cases went to {decision['team']}"
        found.append({"kind": "retrievalSupport", "label": label, "value": support})
    fidelity = features.get("evidenceFidelity")
    if fidelity is not None:
        label = (
            "Extraction validated on the first attempt"
            if fidelity >= 1.0
            else "Extraction needed a retry"
            if fidelity > 0.0
            else "Extraction failed; rules-only decision"
        )
        found.append({"kind": "evidenceFidelity", "label": label, "value": fidelity})
    return found


def why(band_name: str, signals: list[dict]) -> str:
    clauses = "; ".join(s["label"][:1].lower() + s["label"][1:] for s in signals)
    return f"{band_name.capitalize()} confidence: {clauses}."


def _interpolate(points: list, x: float) -> float:
    points = sorted(points)
    if x <= points[0][0]:
        return _clip(points[0][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            share = (x - x0) / (x1 - x0) if x1 > x0 else 1.0
            return _clip(y0 + share * (y1 - y0))
    return _clip(points[-1][1])


def calibrate(arm: str, raw: float) -> tuple[float, bool]:
    """Piecewise-linear calibration when a fitted table exists for the arm; else the raw score."""
    if not CALIBRATION_PATH.exists():
        return raw, False
    table = json.loads(CALIBRATION_PATH.read_text()).get(arm) or {}
    points = table.get("breakpoints")
    if not points:
        return raw, False
    return _interpolate(points, raw), True


def fit_isotonic(pairs: list[tuple[float, int]]) -> list[list[float]]:
    """Pool-adjacent-violators: monotone breakpoints [[raw, calibrated], ...] for `calibrate`."""
    blocks = []
    for x, y in sorted(pairs):
        blocks.append([x, x, float(y), 1])
        while len(blocks) > 1 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            low, high = blocks[-2], blocks.pop()
            low[1], low[2], low[3] = high[1], low[2] + high[2], low[3] + high[3]
    points = []
    for x_min, x_max, total, count in blocks:
        y = _clip(total / count)
        points.append([x_min, y])
        if x_max != x_min:
            points.append([x_max, y])
    return points


def ece(probs: list[float], correct: list[int], bins: int = 5) -> float | None:
    """Expected calibration error over equal-width bins; None without observations."""
    if not probs:
        return None
    buckets = [[0.0, 0.0, 0] for _ in range(bins)]
    for p, y in zip(probs, correct):
        bucket = buckets[min(int(p * bins), bins - 1)]
        bucket[0] += p
        bucket[1] += y
        bucket[2] += 1
    return sum(abs(hits / n - conf / n) * n / len(probs) for conf, hits, n in buckets if n)


def brier(probs: list[float], correct: list[int]) -> float | None:
    if not probs:
        return None
    return sum((p - y) ** 2 for p, y in zip(probs, correct)) / len(probs)


def auroc(probs: list[float], correct: list[int]) -> float | None:
    """Mann-Whitney AUROC with ties counted as half; None when one class is empty."""
    positives = [p for p, y in zip(probs, correct) if y]
    negatives = [p for p, y in zip(probs, correct) if not y]
    if not positives or not negatives:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def build(
    *,
    pipeline: str,
    decision: dict,
    ranked: list[dict],
    sources: list[dict],
    extraction_ok,
    proposal=None,
    reviewer=None,
    degraded: bool = False,
) -> dict:
    found = features(
        pipeline=pipeline,
        decision=decision,
        ranked=ranked,
        sources=sources,
        extraction_ok=extraction_ok,
        proposal=proposal,
        reviewer=reviewer,
    )
    raw = round(raw_score(found, load_weights()), 4)
    value, calibrated = calibrate(pipeline, raw)
    value = round(value, 4)
    band_name = band(value)
    found_signals = signals(found, decision=decision, ranked=ranked, sources=sources)
    return {
        "value": value,
        "band": band_name,
        "raw": raw,
        "calibrated": calibrated,
        "degraded": degraded,
        "signals": found_signals,
        "why": why(band_name, found_signals),
        "agentRationale": None,
    }
