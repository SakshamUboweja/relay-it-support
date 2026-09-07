"""Confidence arithmetic is pure and hand-checkable; calibration is optional and monotone."""

import json
from datetime import datetime, timezone

import pytest

from relay.agents import confidence
from relay.agents.confidence import (
    auroc,
    band,
    brier,
    build,
    calibrate,
    ece,
    features,
    fit_isotonic,
    load_weights,
    proposal_gate,
    raw_score,
    signals,
    why,
)


def case(service, team, reviewed=True):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": f"{service}-{team}-{reviewed}",
        "kind": "case",
        "service": service,
        "visibility": "all",
        "metadata": {"reviewed": reviewed, "team": team},
        "created_at": now,
        "updated_at": now,
    }


def decision(**overrides):
    return {"escalation": "none", "service": "vpn", "team": "Network", **overrides}


def test_fit_isotonic_is_monotone_non_decreasing_and_bounded():
    pairs = [
        (0.1, 0),
        (0.2, 1),
        (0.3, 0),
        (0.4, 0),
        (0.5, 1),
        (0.6, 0),
        (0.7, 1),
        (0.8, 1),
        (0.9, 0),
        (0.95, 1),
    ]
    points = fit_isotonic(pairs)
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    assert xs == sorted(xs)
    assert all(a <= b for a, b in zip(ys, ys[1:]))
    assert all(0.0 <= y <= 1.0 for y in ys)
    assert points[0][1] < points[-1][1]
    assert fit_isotonic([]) == []


def test_ece_brier_and_auroc_on_hand_computed_vectors():
    probs, correct = [0.9, 0.8, 0.3, 0.2], [1, 0, 1, 0]
    assert brier(probs, correct) == pytest.approx(0.295)
    assert auroc(probs, correct) == pytest.approx(0.75)
    # Bins of width 0.2: {0.9, 0.8} -> conf 0.85 / acc 0.5; {0.3, 0.2} -> conf 0.25 / acc 0.5.
    assert ece(probs, correct, bins=5) == pytest.approx(0.5 * 0.35 + 0.5 * 0.25)
    assert auroc([0.5, 0.5], [1, 0]) == pytest.approx(0.5)
    assert auroc([0.4, 0.9], [1, 1]) is None
    assert brier([], []) is None and ece([], []) is None


def test_band_thresholds():
    assert band(0.8) == "high"
    assert band(0.79) == "medium"
    assert band(0.5) == "medium"
    assert band(0.49) == "low"


def test_raw_score_renormalises_over_the_features_that_exist():
    weights = load_weights()
    partial = {
        "agentProbability": None,
        "agreement": None,
        "retrievalSupport": 1.0,
        "deterministicMargin": 0.5,
        "evidenceFidelity": None,
    }
    assert raw_score(partial, weights) == pytest.approx((0.2 * 1.0 + 0.15 * 0.5) / 0.35)
    assert raw_score({k: None for k in partial}, weights) == 0.0
    assert raw_score({k: 1.0 for k in partial}, weights) == pytest.approx(1.0)


def test_calibrate_without_a_file_and_with_breakpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(confidence, "CALIBRATION_PATH", tmp_path / "missing.json")
    assert calibrate("deterministic", 0.4) == (0.4, False)
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps({"deterministic": {"breakpoints": [[0.0, 0.0], [0.5, 0.25], [1.0, 1.0]]}})
    )
    monkeypatch.setattr(confidence, "CALIBRATION_PATH", path)
    assert calibrate("deterministic", 0.25) == (pytest.approx(0.125), True)
    assert calibrate("deterministic", 0.75) == (pytest.approx(0.625), True)
    assert calibrate("deterministic", 1.5) == (1.0, True)
    assert calibrate("deterministic", -1.0) == (0.0, True)
    assert calibrate("single", 0.4) == (0.4, False)


def test_features_margin_support_and_fidelity():
    ranked = [{"team": "Network", "score": 4}, {"team": "Endpoint", "score": 3}]
    sources = [case("vpn", "Network"), case("vpn", "Network"), case("vpn", "Identity & Access")]
    sources += [case("vpn", "Endpoint", reviewed=False), case("wifi", "Network")]
    got = features(
        pipeline="deterministic",
        decision=decision(),
        ranked=ranked,
        sources=sources,
        extraction_ok="retried",
    )
    assert got == {
        "agentProbability": None,
        "agreement": None,
        "retrievalSupport": pytest.approx(2 / 3),
        "deterministicMargin": 0.5,
        "evidenceFidelity": 0.5,
    }
    security = features(
        pipeline="deterministic",
        decision=decision(escalation="security", service=None, team="Security Review"),
        ranked=[],
        sources=sources,
        extraction_ok=None,
    )
    assert security["deterministicMargin"] == 1.0
    assert security["retrievalSupport"] is None
    assert security["evidenceFidelity"] is None
    empty = features(
        pipeline="deterministic",
        decision=decision(service=None, team="Service Desk"),
        ranked=[],
        sources=[],
        extraction_ok=False,
    )
    assert empty["deterministicMargin"] == 0.0 and empty["evidenceFidelity"] == 0.0
    single = features(
        pipeline="deterministic",
        decision=decision(),
        ranked=[{"team": "Network", "score": 3}],
        sources=[],
        extraction_ok=True,
    )
    assert single["deterministicMargin"] == 1.0 and single["evidenceFidelity"] == 1.0
    assert single["retrievalSupport"] is None


def test_signals_and_why_read_as_sentences():
    ranked = [{"team": "Network", "score": 3}, {"team": "Endpoint", "score": 3}]
    sources = [case("vpn", "Network"), case("vpn", "Identity & Access")]
    got = features(
        pipeline="deterministic",
        decision=decision(),
        ranked=ranked,
        sources=sources,
        extraction_ok=False,
    )
    labels = [s["label"] for s in signals(got, decision=decision(), ranked=ranked, sources=sources)]
    assert labels == [
        "Catalog evidence was tied between Network and Endpoint",
        "1 of 2 similar reviewed cases went to Network",
        "Extraction failed; rules-only decision",
    ]
    clear = features(
        pipeline="deterministic",
        decision=decision(),
        ranked=[{"team": "Network", "score": 3}],
        sources=[],
        extraction_ok="retried",
    )
    labels = [
        s["label"]
        for s in signals(
            clear, decision=decision(), ranked=[{"team": "Network", "score": 3}], sources=[]
        )
    ]
    assert labels == [
        "Catalog evidence favoured Corporate VPN by a clear margin",
        "Extraction needed a retry",
    ]
    assert why("medium", [{"kind": "x", "label": "Extraction needed a retry", "value": 0.5}]) == (
        "Medium confidence: extraction needed a retry."
    )


def test_build_returns_the_exact_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(confidence, "CALIBRATION_PATH", tmp_path / "missing.json")
    ranked = [{"team": "Network", "score": 3}]
    result = build(
        pipeline="deterministic",
        decision=decision(),
        ranked=ranked,
        sources=[case("vpn", "Network")],
        extraction_ok=True,
    )
    assert set(result) == {
        "value",
        "band",
        "raw",
        "calibrated",
        "degraded",
        "signals",
        "why",
        "agentRationale",
    }
    assert result["value"] == result["raw"] == 1.0
    assert result["band"] == "high" and result["calibrated"] is False
    assert result["degraded"] is False and result["agentRationale"] is None
    assert [s["kind"] for s in result["signals"]] == [
        "deterministicMargin",
        "retrievalSupport",
        "evidenceFidelity",
    ]
    assert result["why"].startswith("High confidence: catalog evidence favoured Corporate VPN")
    degraded = build(
        pipeline="deterministic",
        decision=decision(),
        ranked=ranked,
        sources=[],
        extraction_ok=None,
        degraded=True,
    )
    assert degraded["degraded"] is True


PROPOSAL = {"team": "Network", "probability": 0.9, "rationale": "The message names the VPN."}
TIED = [{"team": "Network", "score": 3}, {"team": "Endpoint", "score": 3}]


def test_agent_probability_flips_when_the_lanes_overruled_the_proposal():
    agreed = features(
        pipeline="single",
        decision=decision(),
        ranked=TIED,
        sources=[],
        extraction_ok=True,
        proposal=PROPOSAL,
    )
    assert agreed["agentProbability"] == 0.9
    overruled = features(
        pipeline="single",
        decision=decision(team="Service Desk", service=None),
        ranked=TIED,
        sources=[],
        extraction_ok=True,
        proposal=PROPOSAL,
    )
    assert overruled["agentProbability"] == pytest.approx(0.1)


def test_proposal_gate_scores_the_proposal_at_face_value():
    weights = load_weights()
    raw = proposal_gate(
        pipeline="single",
        decision=decision(team="Service Desk", service=None),
        ranked=TIED,
        sources=[],
        extraction_ok=True,
        proposal=PROPOSAL,
    )
    expected = (weights["agentProbability"] * 0.9 + weights["evidenceFidelity"] * 1.0) / (
        weights["agentProbability"] + weights["deterministicMargin"] + weights["evidenceFidelity"]
    )
    assert raw == pytest.approx(expected)
    assert raw >= 0.6


def test_agent_signal_reads_as_a_sentence_in_both_directions():
    agreed = features(
        pipeline="single",
        decision=decision(),
        ranked=TIED,
        sources=[],
        extraction_ok=True,
        proposal=PROPOSAL,
    )
    labels = [
        s["label"]
        for s in signals(agreed, decision=decision(), ranked=TIED, sources=[], proposal=PROPOSAL)
    ]
    assert labels[0] == "Agent estimated 90% that Network is right"
    lost = decision(team="Service Desk", service=None)
    overruled = features(
        pipeline="single",
        decision=lost,
        ranked=TIED,
        sources=[],
        extraction_ok=True,
        proposal=PROPOSAL,
    )
    labels = [
        s["label"]
        for s in signals(overruled, decision=lost, ranked=TIED, sources=[], proposal=PROPOSAL)
    ]
    assert labels[0] == "Agent proposed Network, overruled by policy"


def test_build_records_a_sanitized_agent_rationale(tmp_path, monkeypatch):
    monkeypatch.setattr(confidence, "CALIBRATION_PATH", tmp_path / "missing.json")
    result = build(
        pipeline="single",
        decision=decision(),
        ranked=TIED,
        sources=[],
        extraction_ok=True,
        proposal={**PROPOSAL, "rationale": "VPN named; password: hunter2"},
    )
    assert result["agentRationale"] == "VPN named; password: [REDACTED]"
    assert [s["kind"] for s in result["signals"]][0] == "agentProbability"
    assert result["why"].startswith("Medium confidence: agent estimated 90% that Network is right;")
