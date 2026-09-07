"""The single-agent arm: one structured call that extracts evidence and proposes a route."""

from ..db import mode
from ..model import Extraction, validate_extraction
from ..policy import policy
from ..sanitize import sanitize
from .prompts import SINGLE_PROMPT, SINGLE_PROMPT_VERSION
from .runtime import BudgetExceeded, ModelRuntime
from .schemas import (
    PipelineContext,
    PipelineResult,
    RoutingProposal,
    SingleAgentOutput,
    validate_proposal,
)

SOURCE_LIMIT = 8


def _sources(sources: list[dict]) -> list[dict]:
    """Only what a router needs: no bodies, no visibility metadata, no unretrieved sources."""
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "service": s["service"],
            "kind": s["kind"],
            "team": (s.get("metadata") or {}).get("team"),
        }
        for s in sources[:SOURCE_LIMIT]
    ]


async def run_single_agent(ctx: PipelineContext, rt: ModelRuntime) -> PipelineResult:
    """Demo: no model call. Live: one validated call; any failure yields a rules-only result."""
    scoring = policy["routingScoring"]

    def snapshot(status: str, outcome: dict) -> PipelineResult:
        return PipelineResult(
            run=rt.finish(pipeline=ctx.pipeline, scoring=scoring, status=status, outcome=outcome)
        )

    if mode() == "demo":
        return snapshot("skipped", {"extraction": "skipped", "proposal": "skipped"})
    sources = _sources(ctx.sources)
    allowed_source_ids = {s["id"] for s in sources}

    def validator(parsed: SingleAgentOutput) -> dict:
        fields = parsed.model_dump()
        return {
            "extraction": validate_extraction(
                {key: fields[key] for key in Extraction.model_fields}, ctx.text, ctx.message_ids
            ),
            "proposal": validate_proposal(
                {key: fields[key] for key in RoutingProposal.model_fields},
                ctx.text,
                allowed_source_ids,
            ),
        }

    try:
        result = await rt.call(
            role="intake",
            prompt_version=SINGLE_PROMPT_VERSION,
            system=SINGLE_PROMPT,
            user_json={
                "message": ctx.text,
                "evidenceIds": ctx.message_ids,
                "approvedProcedure": ctx.procedure,
                "sources": sources,
            },
            text_format=SingleAgentOutput,
            validator=validator,
            image=ctx.image,
            input_summary=f"{sanitize(ctx.text)[:200]} [{len(ctx.message_ids)} evidence ids, {len(sources)} sources]",
        )
    except BudgetExceeded as error:
        return snapshot("budget_exhausted", {"extraction": "skipped", "error": str(error)})
    except Exception:
        return snapshot("failed", {"extraction": "failed"})
    extraction, proposal = result.parsed["extraction"], result.parsed["proposal"]
    run = rt.finish(
        pipeline=ctx.pipeline,
        scoring=scoring,
        status="completed",
        outcome={
            "extraction": "validated",
            "service": extraction["service"],
            "team": proposal["team"],
            "abstain": proposal["abstain"],
        },
    )
    return PipelineResult(
        run=run,
        extraction=extraction,
        proposal=proposal,
        summary=extraction["summary"],
        requested_support=bool(extraction["supportRequestQuote"]),
        procedure_tried=bool(extraction["procedureAttemptedQuote"]),
    )
