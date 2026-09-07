"""Compose the final decision: policy first, then validated evidence, confidence, run metadata."""

from ..db import mode
from ..domain import fact
from ..intake_evidence import apply_extraction, apply_proposal
from ..intake_prompt import INTAKE_PROMPT_VERSION
from ..policy import catalog_candidates, decide, policy
from ..sanitize import sanitize
from . import confidence
from .prompts import SINGLE_PROMPT_VERSION
from .runtime import ModelRuntime
from .schemas import PipelineContext, PipelineResult


def _candidates(d: dict, scoring: str) -> str:
    ranked = ", ".join(f"{a['team']}={a['score']}" for a in d["alternatives"]) or "none"
    return f"candidates: {ranked}; scoring {scoring}"


def _prompt_version(pipeline: str) -> str:
    """The prompt that produced the evidence on this decision."""
    return SINGLE_PROMPT_VERSION if pipeline == "single" else INTAKE_PROMPT_VERSION


def _prompt_versions(pipeline: str) -> dict:
    versions = {"intake": INTAKE_PROMPT_VERSION}
    if pipeline == "single":
        versions["single"] = SINGLE_PROMPT_VERSION
    return versions


def _outcome(d: dict) -> str:
    return f"team={d['team']}; accepted={d['accepted']}; reasons={', '.join(d['reasons'])}"


def _extraction_ok(result: PipelineResult, rt: ModelRuntime, live: bool):
    if not live:
        return None
    if result.extraction is None:
        return False
    retried = any(s.kind == "model_call" and s.status != "ok" for s in rt.steps)
    return "retried" if retried else True


def _proposal_view(proposal: dict) -> dict:
    """What the UI may show: the route the agent argued for, never its raw prose."""
    return {
        "team": proposal["team"],
        "service": proposal["service"],
        "abstain": proposal["abstain"],
        "probability": proposal["probability"],
        "rationale": sanitize(proposal["rationale"]),
        "citedSourceIds": proposal["citedSourceIds"],
    }


def compose_decision(
    ctx: PipelineContext, result: PipelineResult, rt: ModelRuntime, *, scoring=None
) -> dict:
    extraction = result.extraction
    supplemental = (extraction or {}).get("imageText") or ""
    d = decide(
        ctx.text,
        ctx.sources,
        ctx.user,
        ctx.clarifications,
        scoring=scoring,
        supplemental=supplemental,
    )
    scoring = policy["routingScoring"] if scoring is None else scoring
    live = mode() == "live"
    if live:
        d["facts"].update(device=fact(None), location=fact(None))
        if extraction is not None:
            d.update(
                model=rt.model,
                usage={"input": rt.usage.input, "output": rt.usage.output},
                promptVersion=_prompt_version(ctx.pipeline),
                reasoningEffort=rt.settings["effort"],
            )
            apply_extraction(d, extraction)
            if result.proposal is not None:
                # The gate scores the proposal before the lanes judge it, so a tie-break is
                # weighed on the agent's own confidence rather than on the route it lost.
                gate = confidence.proposal_gate(
                    pipeline=ctx.pipeline,
                    decision=d,
                    ranked=d["alternatives"],
                    sources=ctx.sources,
                    extraction_ok=_extraction_ok(result, rt, live),
                    proposal=result.proposal,
                )
                apply_proposal(
                    d,
                    result.proposal,
                    candidates=catalog_candidates(ctx.text, supplemental),
                    text=ctx.text,
                    raw_confidence=gate,
                )
                d["proposal"] = _proposal_view(result.proposal)
        else:
            d["model"] = "live-failed"
            if d["visibility"] != "restricted":
                d.update(team="Service Desk", accepted=False)
            d.update(procedure=None, question=None)
            d["reasons"].append("model-unavailable")
            if result.run.status == "budget_exhausted":
                d["reasons"].append("agent-budget-exhausted")
    rt.policy_step(
        input_summary=_candidates(d, scoring),
        output_summary=_outcome(d),
        detail={
            "priority": d["priority"],
            "escalation": d["escalation"],
            "visibility": d["visibility"],
        },
    )
    d["confidence"] = confidence.build(
        pipeline=ctx.pipeline,
        decision=d,
        ranked=d["alternatives"],
        sources=ctx.sources,
        extraction_ok=_extraction_ok(result, rt, live),
        proposal=result.proposal,
        reviewer=result.reviewer,
        degraded=result.run.status == "budget_exhausted",
    )
    d.update(
        pipeline=ctx.pipeline,
        scoring=scoring,
        promptVersions=_prompt_versions(ctx.pipeline) if live else {},
        costUsd=rt.cost_usd,
        agentRunId=rt.id,
    )
    # Refresh the snapshot so the policy step above is part of the persisted run.
    result.run = rt.finish(
        pipeline=ctx.pipeline, scoring=scoring, status=result.run.status, outcome=result.run.outcome
    )
    return d
