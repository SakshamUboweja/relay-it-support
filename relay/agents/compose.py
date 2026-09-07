"""Compose the final decision: policy first, then validated evidence, confidence, run metadata."""

from ..db import mode
from ..domain import fact
from ..intake_evidence import apply_extraction
from ..intake_prompt import INTAKE_PROMPT_VERSION
from ..policy import decide, policy
from . import confidence
from .runtime import ModelRuntime
from .schemas import PipelineContext, PipelineResult


def _candidates(d: dict, scoring: str) -> str:
    ranked = ", ".join(f"{a['team']}={a['score']}" for a in d["alternatives"]) or "none"
    return f"candidates: {ranked}; scoring {scoring}"


def _outcome(d: dict) -> str:
    return f"team={d['team']}; accepted={d['accepted']}; reasons={', '.join(d['reasons'])}"


def _extraction_ok(result: PipelineResult, rt: ModelRuntime, live: bool):
    if not live:
        return None
    if result.extraction is None:
        return False
    retried = any(s.kind == "model_call" and s.status != "ok" for s in rt.steps)
    return "retried" if retried else True


def compose_decision(
    ctx: PipelineContext, result: PipelineResult, rt: ModelRuntime, *, scoring=None
) -> dict:
    extraction = result.extraction
    d = decide(
        ctx.text,
        ctx.sources,
        ctx.user,
        ctx.clarifications,
        scoring=scoring,
        supplemental=(extraction or {}).get("imageText") or "",
    )
    scoring = policy["routingScoring"] if scoring is None else scoring
    live = mode() == "live"
    if live:
        d["facts"].update(device=fact(None), location=fact(None))
        if extraction is not None:
            d.update(
                model=rt.model,
                usage={"input": rt.usage.input, "output": rt.usage.output},
                promptVersion=INTAKE_PROMPT_VERSION,
                reasoningEffort=rt.settings["effort"],
            )
            apply_extraction(d, extraction)
        else:
            d["model"] = "live-failed"
            if d["visibility"] != "restricted":
                d.update(team="Service Desk", accepted=False)
            d.update(procedure=None, question=None)
            d["reasons"].append("model-unavailable")
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
        promptVersions={"intake": INTAKE_PROMPT_VERSION} if live else {},
        costUsd=rt.cost_usd,
        agentRunId=rt.id,
    )
    # Refresh the snapshot so the policy step above is part of the persisted run.
    result.run = rt.finish(
        pipeline=ctx.pipeline, scoring=scoring, status=result.run.status, outcome=result.run.outcome
    )
    return d
