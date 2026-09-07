"""The triage role: a routing proposal grounded in tool results the model actually saw."""

from ..sanitize import sanitize
from .prompts import TRIAGE_PROMPT, TRIAGE_PROMPT_VERSION
from .runtime import ModelRuntime
from .schemas import PipelineContext, RoutingProposal, validate_proposal
from .tools import ToolContext, build_tools


async def run_triage(
    ctx: PipelineContext,
    rt: ModelRuntime,
    *,
    extraction: dict,
    candidates: list[dict],
    reviewer_notes: list[dict] | None = None,
) -> dict:
    """One tool-assisted proposal; cited sources must have come back from a tool."""
    tools, impls = build_tools(ToolContext(sources=ctx.sources, user=ctx.user))
    notes = f", {len(reviewer_notes)} reviewer notes" if reviewer_notes else ""
    result = await rt.call(
        role="triage",
        prompt_version=TRIAGE_PROMPT_VERSION,
        system=TRIAGE_PROMPT,
        user_json={
            "message": ctx.text,
            "extraction": extraction,
            "candidates": candidates,
            "reviewerNotes": reviewer_notes,
        },
        text_format=RoutingProposal,
        validator=lambda parsed: validate_proposal(parsed, ctx.text, rt.seen_source_ids),
        tools=tools,
        tool_impls=impls,
        input_summary=f"{sanitize(ctx.text)[:200]} [{len(candidates)} candidates{notes}]",
    )
    return result.parsed
