"""Budgeted Responses calls that record every attempt as a trace step."""

import inspect
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, APITimeoutError

from ..model import input_image, live_client
from . import pricing
from .schemas import AgentRun, AgentStep, Budget, Usage, summary


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


def _answer(response, validator):
    """The structured answer of a turn without tool calls, validated; else `Rejected`."""
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
    return parsed


def _function_calls(response) -> list:
    output = getattr(response, "output", None) or []
    return [item for item in output if getattr(item, "type", None) == "function_call"]


# The SDK adds `parsed_arguments` to function calls and `parsed` to message text; the API
# rejects both when they are echoed back.
_SDK_ONLY = {"parsed_arguments": True, "content": {"__all__": {"parsed"}}}


def _echo(output) -> list[dict]:
    """Output items go back verbatim, minus the SDK-only fields the API rejects."""
    return [item.model_dump(exclude_none=True, exclude=_SDK_ONLY) for item in output]


def _tool_models(tools) -> dict:
    """Argument models by tool name, from the `pydantic_function_tool` dicts."""
    found = {}
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if function is not None and hasattr(function, "model"):
            found[function["name"]] = function.model
    return found


def _arguments(call, parsed) -> dict:
    if hasattr(parsed, "model_dump"):
        return parsed.model_dump()
    try:
        loaded = json.loads(getattr(call, "arguments", None) or "{}")
    except ValueError:
        loaded = None
    return loaded if isinstance(loaded, dict) else {"arguments": call.arguments}


def _sources(value, found: dict[str, dict]) -> dict[str, dict]:
    """Every object with a string `id` that a tool result carries, at any depth."""
    if isinstance(value, dict):
        if isinstance(value.get("id"), str):
            found[value["id"]] = value
        for item in value.values():
            _sources(item, found)
    elif isinstance(value, list):
        for item in value:
            _sources(item, found)
    return found


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
        self.seen_sources: dict[str, dict] = {}
        self.started = clock()

    @property
    def seen_source_ids(self) -> set[str]:
        """IDs the model has actually been shown by a tool; the only IDs it may cite."""
        return set(self.seen_sources)

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
        """One structured call with a single retry; `validator` may return a replacement value.

        With `tools`, the model may call them between turns: each tool call is executed
        locally under `maxToolCalls`, echoed back, and the model is called again.
        """
        content = [{"type": "input_text", "text": json.dumps(user_json)}]
        if image:
            content.append(input_image(image))
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
        if tools:
            # The tool-call cap is enforced locally, so `max_tool_calls` is never sent.
            args.update(tools=tools, tool_choice="auto", parallel_tool_calls=True)
        step = dict(role=role, kind="model_call", model=self.model, promptVersion=prompt_version)
        toolbox = (_tool_models(tools), tool_impls or {})
        last: Exception | None = None
        for _ in range(2):
            try:
                return await self._converse(args, step, input_summary, validator, toolbox)
            except BudgetExceeded:
                raise
            except Exception as error:
                last = error
                if not _retryable(error):
                    break
        raise last

    async def _converse(self, args, step, input_summary, validator, toolbox) -> CallResult:
        """Model turns until one answers; tool calls in between are run and echoed back."""
        while True:
            self.check_budget()
            started = self.clock()
            usage = Usage()
            try:
                async with self.client_factory() as client:
                    response = await client.responses.parse(**args)
                usage = _usage(response)
                calls = _function_calls(response)
                if not calls:
                    parsed = _answer(response, validator)
            except Exception as error:
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
                raise
            cost_usd = pricing.cost(self.model, usage)
            latency_ms = self.elapsed_ms(started)
            if calls:
                self.record(
                    **step,
                    inputSummary=input_summary,
                    outputSummary="tool calls: " + ", ".join(c.name for c in calls),
                    usage=usage,
                    costUsd=cost_usd,
                    latencyMs=latency_ms,
                )
                outputs = [await self._tool_call(call, step["role"], toolbox) for call in calls]
                args["input"].extend([*_echo(response.output), *outputs])
                continue
            self.record(
                **step,
                inputSummary=input_summary,
                outputSummary=_describe(parsed),
                usage=usage,
                costUsd=cost_usd,
                latencyMs=latency_ms,
            )
            return CallResult(parsed, usage, latency_ms, cost_usd)

    async def _tool_call(self, call, role: str, toolbox) -> dict:
        """Run one tool under the tool budget; failures answer the model as an error object."""
        if self.tool_calls + 1 > self.budget.maxToolCalls:
            raise BudgetExceeded(f"Tool call budget of {self.budget.maxToolCalls} reached")
        models, impls = toolbox
        started = self.clock()
        parsed = getattr(call, "parsed_arguments", None)
        error: Exception | None = None
        try:
            if call.name not in impls:
                raise ValueError(f"Unknown tool '{call.name}'")
            if parsed is None:
                parsed = models[call.name].model_validate_json(call.arguments)
            result = impls[call.name](parsed)
            if inspect.isawaitable(result):
                result = await result
        except Exception as failure:
            error = failure
            result = {"error": str(failure) or type(failure).__name__}
        _sources(result, self.seen_sources)
        tool_args = _arguments(call, parsed)
        output = json.dumps(result, default=str)
        self.record(
            role=role,
            kind="tool_call",
            toolName=call.name,
            toolArgs=tool_args,
            inputSummary=json.dumps(tool_args),
            outputSummary=summary(output),
            toolResultSummary=output,
            costUsd=0.0,
            latencyMs=self.elapsed_ms(started),
            status="error" if error else "ok",
            error=None if error is None else result["error"],
        )
        return {"type": "function_call_output", "call_id": call.call_id, "output": output}

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
