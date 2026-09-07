"""Pipeline arms: the deterministic arm traces today's extraction; the multi arm adds
triage with tools and an independent review, all under one budget."""

from ..db import mode
from ..intake_prompt import INTAKE_PROMPT_VERSION
from ..policy import policy, rank_candidates
from ..sanitize import sanitize
from . import pricing
from .intake import run_intake
from .reviewer import run_reviewer
from .runtime import BudgetExceeded, ModelRuntime
from .schemas import PipelineContext, PipelineResult, Usage
from .tools import cited_sources
from .triage import run_triage

QUOTE_KEYS = (
    "serviceQuote",
    "symptomQuote",
    "impactQuote",
    "urgencyQuote",
    "deviceQuote",
    "startedQuote",
    "workaroundQuote",
    "attemptedStepsQuotes",
    "supportRequestQuote",
    "procedureAttemptedQuote",
    "securityQuote",
)


async def run_deterministic(ctx: PipelineContext, rt: ModelRuntime, extract) -> PipelineResult:
    """Demo: no model call. Live: one traced extraction; a failure yields a rules-only result."""
    scoring = policy["routingScoring"]
    if mode() == "demo":
        run = rt.finish(
            pipeline=ctx.pipeline,
            scoring=scoring,
            status="skipped",
            outcome={"extraction": "skipped"},
        )
        return PipelineResult(run=run)
    started = rt.clock()
    step = dict(
        role="extractor",
        kind="model_call",
        model=rt.model,
        promptVersion=INTAKE_PROMPT_VERSION,
        inputSummary=f"{sanitize(ctx.text)[:200]} [{len(ctx.message_ids)} evidence ids]",
    )
    try:
        # The screenshot rides along only when there is one, so plain extractors still fit.
        result = await extract(
            ctx.text, ctx.message_ids, ctx.procedure, **({"image": ctx.image} if ctx.image else {})
        )
    except Exception as error:
        rt.record(
            **step,
            outputSummary="",
            latencyMs=rt.elapsed_ms(started),
            status="error",
            error=str(error) or type(error).__name__,
        )
        run = rt.finish(
            pipeline=ctx.pipeline,
            scoring=scoring,
            status="failed",
            outcome={"extraction": "failed"},
        )
        return PipelineResult(run=run)
    data, usage = result["data"], Usage(**result["usage"])
    quotes = ", ".join(key for key in QUOTE_KEYS if data.get(key)) or "none"
    rt.record(
        **step,
        outputSummary=f"{data['summary']} [quotes: {quotes}]",
        usage=usage,
        costUsd=pricing.cost(rt.model, usage),
        latencyMs=rt.elapsed_ms(started),
    )
    run = rt.finish(
        pipeline=ctx.pipeline,
        scoring=scoring,
        status="completed",
        outcome={"extraction": "validated", "service": data.get("service")},
    )
    return PipelineResult(
        run=run,
        extraction=data,
        summary=data["summary"],
        requested_support=bool(data.get("supportRequestQuote")),
        procedure_tried=bool(data.get("procedureAttemptedQuote")),
    )


def _text(error: Exception) -> str:
    return str(error) or type(error).__name__


def _final_review(reviews: list[dict]) -> dict | None:
    """The verdict compose sees. A revision the reviewer then accepted reads as `revise`;
    a proposal that still needed revising, or never received its revision, needs a person."""
    if not reviews:
        return None
    review = dict(reviews[-1])
    if review["verdict"] == "revise":
        review["verdict"] = "human_review"
    elif len(reviews) == 2 and review["verdict"] == "accept":
        review["verdict"] = "revise"
    return review


async def run_multi_agent(ctx: PipelineContext, rt: ModelRuntime) -> PipelineResult:
    """Intake, then triage with tools and an independent review, with at most one revision.

    Intake failure yields a rules-only result. Triage or reviewer failure keeps the
    extraction and drops the proposal. Budget exhaustion keeps whatever validated so far.
    """
    scoring = policy["routingScoring"]

    def snapshot(status: str, outcome: dict, **fields) -> PipelineResult:
        run = rt.finish(pipeline=ctx.pipeline, scoring=scoring, status=status, outcome=outcome)
        return PipelineResult(run=run, **fields)

    if mode() == "demo":
        return snapshot("skipped", {"extraction": "skipped", "proposal": "skipped"})
    try:
        extraction = await run_intake(ctx, rt, image=ctx.image)
    except BudgetExceeded as error:
        return snapshot("budget_exhausted", {"extraction": "skipped", "error": str(error)})
    except Exception as error:
        return snapshot("failed", {"extraction": "failed", "error": _text(error)})
    fields = dict(
        extraction=extraction,
        summary=extraction["summary"],
        requested_support=bool(extraction["supportRequestQuote"]),
        procedure_tried=bool(extraction["procedureAttemptedQuote"]),
    )
    # Screenshot text widens the candidate list exactly as it does in compose.
    candidates = rank_candidates(
        ctx.text,
        ctx.sources,
        ctx.user,
        scoring=scoring,
        supplemental=extraction.get("imageText") or "",
    )
    status, proposal, reviews, error = "completed", None, [], None
    try:
        for _ in range(2):
            proposal = await run_triage(
                ctx,
                rt,
                extraction=extraction,
                candidates=candidates,
                reviewer_notes=reviews[-1]["issues"] if reviews else None,
            )
            reviews.append(
                await run_reviewer(
                    ctx,
                    rt,
                    extraction=extraction,
                    proposal=proposal,
                    cited_sources=cited_sources(rt.seen_sources, proposal["citedSourceIds"]),
                )
            )
            if reviews[-1]["verdict"] != "revise":
                break
    except BudgetExceeded as failure:
        status, error = "budget_exhausted", str(failure)
    except Exception as failure:
        proposal, reviews, error = None, [], _text(failure)
    review = _final_review(reviews)
    if review is not None and review["verdict"] == "human_review":
        proposal["abstain"] = True
    outcome = {
        "extraction": "validated",
        "service": extraction["service"],
        "proposal": "validated" if proposal else "failed",
        "team": proposal["team"] if proposal else None,
        "abstain": proposal["abstain"] if proposal else None,
        "verdict": review["verdict"] if review else None,
    }
    if error:
        outcome["error"] = error
    return snapshot(status, outcome, **fields, proposal=proposal, reviewer=review)
