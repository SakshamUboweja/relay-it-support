"""Budgeted Responses calls that record every attempt as a trace step."""

import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, APITimeoutError

from ..model import live_client
from . import pricing
from .schemas import AgentRun, AgentStep, Budget, Usage


class BudgetExceeded(Exception):
    """The run would exceed its call, token or time budget; the caller degrades gracefully."""


class Rejected(ValueError):
    """The model answered, but unusably: incomplete, refused, or failed validation."""


@dataclass
class CallResult:
    parsed: Any
    usage: Usage
    latency_ms: int
    cost_usd: float | None


def _usage(response) -> Usage:
    usage = getattr(response, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return Usage(
        input=getattr(usage, "input_tokens", 0) or 0,
        output=getattr(usage, "output_tokens", 0) or 0,
        cached=getattr(input_details, "cached_tokens", 0) or 0,
        reasoning=getattr(output_details, "reasoning_tokens", 0) or 0,
    )


def _retryable(error: Exception) -> bool:
    """Only a transient failure or an unusable answer earns a second paid call."""
    if isinstance(error, APIStatusError):
        return error.status_code >= 500 or error.status_code == 429
    return isinstance(error, (APIConnectionError, TimeoutError, Rejected))


def _status(error: Exception) -> str:
    if isinstance(error, Rejected):
        return "rejected"
    if isinstance(error, (TimeoutError, APITimeoutError)):
        return "timeout"
    return "error"


def _describe(parsed) -> str:
    if hasattr(parsed, "model_dump_json"):
        return parsed.model_dump_json()
    return json.dumps(parsed, default=str)


class ModelRuntime:
    def __init__(
        self,
        settings: dict,
        budget: Budget,
        *,
        model: str,
        client_factory=live_client,
        clock=time.perf_counter,
    ):
        self.id = str(uuid4())
        self.settings = settings
        self.budget = budget
        self.model = model
        self.client_factory = client_factory
        self.clock = clock
        self.steps: list[AgentStep] = []
        self.usage = Usage()
        self.cost_usd: float | None = 0.0
        self.model_calls = 0
        self.tool_calls = 0
        self.started = clock()

    def elapsed_ms(self, since: float | None = None) -> int:
        return int((self.clock() - (self.started if since is None else since)) * 1000)

    def record(self, **fields) -> AgentStep:
        """Append a step; its usage, cost and call kind accrue to the run totals."""
        step = AgentStep(seq=len(self.steps) + 1, **fields)
        self.steps.append(step)
        self.usage = self.usage + step.usage
        if step.costUsd is None:
            self.cost_usd = None
        elif self.cost_usd is not None:
            self.cost_usd = round(self.cost_usd + step.costUsd, 6)
        if step.kind == "model_call":
            self.model_calls += 1
        elif step.kind == "tool_call":
            self.tool_calls += 1
        return step

    def check_budget(self) -> None:
        if self.model_calls + 1 > self.budget.maxModelCalls:
            raise BudgetExceeded(f"Model call budget of {self.budget.maxModelCalls} reached")
        if self.usage.total > self.budget.maxTotalTokens:
            raise BudgetExceeded(f"Token budget of {self.budget.maxTotalTokens} exceeded")
        if self.clock() - self.started > self.budget.maxSeconds:
            raise BudgetExceeded(f"Time budget of {self.budget.maxSeconds}s exceeded")

    async def call(
        self,
        *,
        role: str,
        prompt_version: str,
        system: str,
        user_json: dict,
        text_format,
        validator=None,
        image: dict | None = None,
        tools=None,
        tool_impls=None,
        timeout: float = 60.0,
        input_summary: str,
    ) -> CallResult:
        """One structured call with a single retry; `validator` may return a replacement value."""
        if tools or tool_impls:
            raise NotImplementedError("tool loop arrives in a later task")
        content = [{"type": "input_text", "text": json.dumps(user_json)}]
        if image:
            content.append(
                {
                    "type": "input_image",
                    "image_url": image["data_url"],
                    "detail": image.get("detail", "auto"),
                }
            )
        args = {
            "model": self.model,
            "store": False,
            "max_output_tokens": self.settings["maxOutputTokens"],
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "text_format": text_format,
            "timeout": timeout,
        }
        if self.settings.get("effort"):
            args["reasoning"] = {"effort": self.settings["effort"]}
        step = dict(role=role, kind="model_call", model=self.model, promptVersion=prompt_version)
        last: Exception | None = None
        for _ in range(2):
            self.check_budget()
            started = self.clock()
            usage = Usage()
            try:
                async with self.client_factory() as client:
                    response = await client.responses.parse(**args)
                usage = _usage(response)
                if response.status != "completed" or response.output_parsed is None:
                    details = getattr(response, "incomplete_details", None)
                    raise Rejected(
                        "Model response incomplete or refused "
                        f"(status={getattr(response, 'status', None)}, "
                        f"reason={getattr(details, 'reason', None)})"
                    )
                parsed = response.output_parsed
                if validator is not None:
                    try:
                        checked = validator(parsed)
                    except ValueError as error:
                        raise Rejected(str(error)) from error
                    parsed = parsed if checked is None else checked
            except Exception as error:
                last = error
                self.record(
                    **step,
                    inputSummary=input_summary,
                    outputSummary="",
                    usage=usage,
                    costUsd=pricing.cost(self.model, usage),
                    latencyMs=self.elapsed_ms(started),
                    status=_status(error),
                    error=str(error) or type(error).__name__,
                )
                if not _retryable(error):
                    break
                continue
            cost_usd = pricing.cost(self.model, usage)
            latency_ms = self.elapsed_ms(started)
            self.record(
                **step,
                inputSummary=input_summary,
                outputSummary=_describe(parsed),
                usage=usage,
                costUsd=cost_usd,
                latencyMs=latency_ms,
            )
            return CallResult(parsed, usage, latency_ms, cost_usd)
        raise last

    def policy_step(self, *, input_summary: str, output_summary: str, detail=None) -> AgentStep:
        return self.record(
            role="policy",
            kind="policy",
            inputSummary=input_summary,
            outputSummary=output_summary,
            costUsd=0.0,
            detail=detail or {},
        )

    def finish(self, *, pipeline: str, scoring: str, status: str, outcome: dict) -> AgentRun:
        """Snapshot the run so far; call again after later steps to refresh the snapshot."""
        return AgentRun(
            id=self.id,
            pipeline=pipeline,
            scoring=scoring,
            model=self.model if self.model_calls else None,
            reasoningEffort=self.settings.get("effort"),
            status=status,
            budget=self.budget.model_dump(),
            usage=self.usage,
            costUsd=self.cost_usd,
            pricingVersion=pricing.pricing_version(),
            latencyMs=self.elapsed_ms(),
            outcome=outcome,
            steps=list(self.steps),
        )
