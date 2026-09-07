"""The reviewer role: an independent critic with no tools and no power to route."""

from ..sanitize import sanitize
from .prompts import REVIEWER_PROMPT, REVIEWER_PROMPT_VERSION
from .runtime import ModelRuntime
from .schemas import PipelineContext, ReviewerOutput, validate_reviewer


async def run_reviewer(
    ctx: PipelineContext,
    rt: ModelRuntime,
    *,
    extraction: dict,
    proposal: dict,
    cited_sources: list[dict],
) -> dict:
    """One verdict over the proposal; every quote it gives is checked against the message."""
    result = await rt.call(
        role="reviewer",
        prompt_version=REVIEWER_PROMPT_VERSION,
        system=REVIEWER_PROMPT,
        user_json={
            "message": ctx.text,
            "extraction": extraction,
            "proposal": proposal,
            "citedSources": cited_sources,
        },
        text_format=ReviewerOutput,
        validator=lambda parsed: validate_reviewer(parsed, ctx.text),
        input_summary=(
            f"{sanitize(ctx.text)[:200]} [proposal {proposal['team']}, "
            f"{len(cited_sources)} cited sources]"
        ),
    )
    return result.parsed
