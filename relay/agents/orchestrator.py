"""Pipeline arms. The deterministic arm wraps today's extraction call in a traced run."""

from ..db import mode
from ..intake_prompt import INTAKE_PROMPT_VERSION
from ..policy import policy
from ..sanitize import sanitize
from . import pricing
from .runtime import ModelRuntime
from .schemas import PipelineContext, PipelineResult, Usage

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
        result = await extract(ctx.text, ctx.message_ids, ctx.procedure)
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
