"""Async Responses structured extraction with exact-evidence validation."""

import json
import math
import os
from typing import Literal

from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from .intake_prompt import INTAKE_PROMPT
from .policy import policy


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(min_length=1, max_length=120)
    service: Literal["vpn", "sso", "wifi", "laptop", "atlas"] | None
    serviceQuote: str | None
    symptomQuote: str | None
    impactQuote: str | None
    urgencyQuote: str | None
    deviceQuote: str | None
    startedQuote: str | None
    workaroundQuote: str | None
    attemptedStepsQuotes: list[str]
    supportRequestQuote: str | None
    procedureAttemptedQuote: str | None
    securityQuote: str | None = Field(
        description="Exact evidence of suspected compromise, phishing, unauthorized access, unexpected MFA, malware, or data exposure. Null for ordinary login failures, user-initiated password changes, or routine access requests without a threat indicator."
    )
    evidenceIds: list[str]


def model_settings() -> dict:
    effort = os.getenv("OPENAI_REASONING_EFFORT") or None
    if effort is not None and effort not in ("none", "low", "medium", "high", "xhigh", "max"):
        raise ValueError("Invalid OPENAI_REASONING_EFFORT.")
    try:
        budget = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS") or policy["maxOutputTokens"])
    except ValueError:
        raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be an integer.") from None
    if not 512 <= budget <= 16384:
        raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be between 512 and 16384.")
    return {"effort": effort, "maxOutputTokens": budget}


def live_client() -> AsyncOpenAI:
    if not all(
        os.getenv(key) for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_EMBEDDING_MODEL")
    ):
        raise ValueError(
            "Live mode requires OPENAI_API_KEY, OPENAI_MODEL and OPENAI_EMBEDDING_MODEL."
        )
    return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=20.0)


def validate_extraction(data: dict | Extraction, text: str, allowed_ids: list[str]) -> dict:
    parsed = data if isinstance(data, Extraction) else Extraction.model_validate(data)
    data = parsed.model_dump()
    quotes = [
        data[key]
        for key in (
            "serviceQuote",
            "symptomQuote",
            "impactQuote",
            "urgencyQuote",
            "securityQuote",
            "deviceQuote",
            "startedQuote",
            "workaroundQuote",
            "supportRequestQuote",
            "procedureAttemptedQuote",
        )
    ]
    for quote in [*quotes, *data["attemptedStepsQuotes"]]:
        if quote and quote not in text:
            raise ValueError("Model output asserted unsupported evidence.")
    if any(identifier not in allowed_ids for identifier in data["evidenceIds"]):
        raise ValueError("Model output referenced disallowed evidence.")
    if data["service"] and not data["serviceQuote"]:
        raise ValueError("Service requires a source quote.")
    if not data["evidenceIds"]:
        raise ValueError("Extracted facts require message evidence IDs.")
    return data


async def extract_live(
    text: str, source_ids: list[str], approved_procedure: dict | None = None
) -> dict:
    settings = model_settings()
    last = None
    async with live_client() as client:
        for _ in range(policy["maxModelRetries"] + 1):
            try:
                args = {
                    "model": os.environ["OPENAI_MODEL"],
                    "store": False,
                    "max_output_tokens": settings["maxOutputTokens"],
                    "input": [
                        {"role": "system", "content": INTAKE_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "message": text,
                                    "evidenceIds": source_ids,
                                    "approvedProcedure": approved_procedure,
                                }
                            ),
                        },
                    ],
                    "text_format": Extraction,
                    "timeout": 60.0,
                }
                if settings["effort"]:
                    args["reasoning"] = {"effort": settings["effort"]}
                response = await client.responses.parse(**args)
                if response.status != "completed" or not response.output_parsed:
                    raise ValueError(
                        "Model response incomplete or refused; saved for general intake."
                    )
                return {
                    "data": validate_extraction(response.output_parsed, text, source_ids),
                    "usage": {
                        "input": response.usage.input_tokens if response.usage else 0,
                        "output": response.usage.output_tokens if response.usage else 0,
                    },
                }
            except Exception as error:
                last = error
                if (
                    isinstance(error, APIStatusError)
                    and error.status_code < 500
                    and error.status_code != 429
                ):
                    break
    raise last


async def embed(text: str) -> list[float]:
    async with live_client() as client:
        response = await client.embeddings.create(
            model=os.environ["OPENAI_EMBEDDING_MODEL"], input=text[:14000], dimensions=256
        )
    values = response.data[0].embedding if response.data else None
    if not values or len(values) != 256 or any(not math.isfinite(value) for value in values):
        raise ValueError("Embedding model must support 256 dimensions.")
    return values
