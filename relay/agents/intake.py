"""The intake role of the multi-agent arm: the existing extraction contract, traced."""

from ..intake_prompt import INTAKE_PROMPT, INTAKE_PROMPT_VERSION
from ..model import Extraction, validate_extraction
from ..sanitize import sanitize
from .runtime import ModelRuntime
from .schemas import PipelineContext


async def run_intake(ctx: PipelineContext, rt: ModelRuntime, *, image: dict | None = None) -> dict:
    """One validated extraction call; the runtime's retry and budget rules apply."""
    result = await rt.call(
        role="intake",
        prompt_version=INTAKE_PROMPT_VERSION,
        system=INTAKE_PROMPT,
        user_json={
            "message": ctx.text,
            "evidenceIds": ctx.message_ids,
            "approvedProcedure": ctx.procedure,
        },
        text_format=Extraction,
        validator=lambda parsed: validate_extraction(parsed, ctx.text, ctx.message_ids),
        image=image,
        input_summary=f"{sanitize(ctx.text)[:200]} [{len(ctx.message_ids)} evidence ids]",
    )
    return result.parsed
