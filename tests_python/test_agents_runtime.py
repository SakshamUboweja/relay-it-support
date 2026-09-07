"""ModelRuntime budgets, retries, usage accounting and request shape at the SDK boundary."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, pydantic_function_tool
from pydantic import BaseModel, ConfigDict

from relay.agents.runtime import BudgetExceeded, ModelRuntime
from relay.agents.schemas import Budget, Usage

SETTINGS = {"effort": "high", "maxOutputTokens": 4096}


class Output(BaseModel):
    answer: str


def status_error(code):
    request = httpx.Request("POST", "https://api.test/responses")
    return APIStatusError(
        f"status {code}", response=httpx.Response(code, request=request), body=None
    )


def completed(answer="ok", usage=None):
    return SimpleNamespace(
        status="completed",
        output_parsed=Output(answer=answer),
        usage=usage or SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def runtime(parse, *, model="unpriced-model", budget=None, clock=None):
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return ModelRuntime(
        SETTINGS,
        budget or Budget(maxModelCalls=2, maxToolCalls=0, maxTotalTokens=20000, maxSeconds=75),
        model=model,
        client_factory=lambda: client,
        **({"clock": clock} if clock else {}),
    )


async def call(rt, **overrides):
    return await rt.call(
        **{
            "role": "extractor",
            "prompt_version": "test-prompt-v1",
            "system": "Be exact.",
            "user_json": {"message": "VPN fails"},
            "text_format": Output,
            "input_summary": "VPN fails",
            **overrides,
        }
    )


async def test_request_carries_settings_prompt_and_json_user_content():
    parse = AsyncMock(return_value=completed())
    rt = runtime(parse, model="gpt-test")
    result = await call(rt, timeout=30.0)
    assert result.parsed.answer == "ok"
    args = parse.call_args.kwargs
    assert args["model"] == "gpt-test"
    assert args["store"] is False
    assert args["max_output_tokens"] == 4096
    assert args["reasoning"] == {"effort": "high"}
    assert args["timeout"] == 30.0
    assert args["text_format"] is Output
    assert args["input"][0] == {"role": "system", "content": "Be exact."}
    assert args["input"][1] == {
        "role": "user",
        "content": [{"type": "input_text", "text": json.dumps({"message": "VPN fails"})}],
    }
    assert "tools" not in args
    step = rt.steps[0]
    assert (step.kind, step.role, step.promptVersion, step.status) == (
        "model_call",
        "extractor",
        "test-prompt-v1",
        "ok",
    )


async def test_image_adds_input_image_part_after_the_text():
    parse = AsyncMock(return_value=completed())
    rt = runtime(parse)
    await call(rt, image={"data_url": "data:image/png;base64,AAAA", "detail": "low"})
    content = parse.call_args.kwargs["input"][1]["content"]
    assert content[0]["type"] == "input_text"
    assert content[1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,AAAA",
        "detail": "low",
    }


async def test_third_call_exceeds_the_model_call_budget():
    parse = AsyncMock(return_value=completed())
    rt = runtime(parse)
    await call(rt)
    await call(rt)
    with pytest.raises(BudgetExceeded):
        await call(rt)
    assert parse.await_count == 2
    assert rt.model_calls == 2


async def test_token_budget_stops_the_next_call():
    parse = AsyncMock(
        return_value=completed(usage=SimpleNamespace(input_tokens=900, output_tokens=200))
    )
    rt = runtime(
        parse, budget=Budget(maxModelCalls=5, maxToolCalls=0, maxTotalTokens=1000, maxSeconds=75)
    )
    await call(rt)
    with pytest.raises(BudgetExceeded):
        await call(rt)
    assert parse.await_count == 1


async def test_time_budget_uses_the_injected_clock():
    now = [0.0]
    rt = runtime(
        AsyncMock(return_value=completed()),
        budget=Budget(maxModelCalls=5, maxToolCalls=0, maxTotalTokens=20000, maxSeconds=60),
        clock=lambda: now[0],
    )
    await call(rt)
    now[0] = 80.0
    with pytest.raises(BudgetExceeded):
        await call(rt)
    assert rt.model_calls == 1


async def test_server_error_retries_once_and_records_both_attempts():
    parse = AsyncMock(side_effect=[status_error(500), completed("second")])
    rt = runtime(parse)
    result = await call(rt)
    assert result.parsed.answer == "second"
    assert parse.await_count == 2
    assert [s.kind for s in rt.steps] == ["model_call", "model_call"]
    assert rt.steps[0].status == "error" and "500" in rt.steps[0].error
    assert rt.steps[1].status == "ok" and rt.steps[1].error is None
    assert [s.seq for s in rt.steps] == [1, 2]


async def test_rate_limit_retries_but_other_client_errors_do_not():
    parse = AsyncMock(side_effect=[status_error(429), completed()])
    rt = runtime(parse)
    assert (await call(rt)).parsed.answer == "ok"
    assert parse.await_count == 2
    parse = AsyncMock(side_effect=status_error(400))
    rt = runtime(parse)
    with pytest.raises(APIStatusError):
        await call(rt)
    assert parse.await_count == 1
    assert len(rt.steps) == 1 and rt.steps[0].status == "error"


async def test_incomplete_response_is_rejected_and_the_last_error_is_raised():
    incomplete = SimpleNamespace(
        status="incomplete",
        output_parsed=None,
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
    )
    parse = AsyncMock(return_value=incomplete)
    rt = runtime(parse)
    with pytest.raises(ValueError, match="incomplete"):
        await call(rt)
    assert parse.await_count == 2
    assert [s.status for s in rt.steps] == ["rejected", "rejected"]
    assert rt.usage == Usage(input=14, output=6)


async def test_validator_value_error_marks_the_attempt_rejected_and_retries_once():
    parse = AsyncMock(side_effect=[completed("bad"), completed("good")])
    rt = runtime(parse)

    def validator(parsed):
        if parsed.answer == "bad":
            raise ValueError("Model output asserted unsupported evidence.")
        return {"answer": parsed.answer.upper()}

    result = await call(rt, validator=validator)
    assert result.parsed == {"answer": "GOOD"}
    assert rt.steps[0].status == "rejected"
    assert "unsupported evidence" in rt.steps[0].error
    assert rt.steps[1].status == "ok"


async def test_timeout_is_recorded_and_retried():
    parse = AsyncMock(side_effect=[TimeoutError(), completed()])
    rt = runtime(parse)
    await call(rt)
    assert [s.status for s in rt.steps] == ["timeout", "ok"]


async def test_usage_reads_cached_and_reasoning_details_when_present():
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=40,
        input_tokens_details=SimpleNamespace(cached_tokens=30),
        output_tokens_details=SimpleNamespace(reasoning_tokens=25),
    )
    rt = runtime(AsyncMock(return_value=completed(usage=usage)))
    result = await call(rt)
    assert result.usage == Usage(input=100, output=40, cached=30, reasoning=25)
    assert rt.usage == result.usage
    assert rt.steps[0].usage == result.usage


async def test_cost_is_none_without_prices_and_exact_with_env_prices(monkeypatch):
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=100,
        input_tokens_details=SimpleNamespace(cached_tokens=200),
    )
    for name in ("OPENAI_PRICE_INPUT_PER_M", "OPENAI_PRICE_CACHED_INPUT_PER_M"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPENAI_PRICE_OUTPUT_PER_M", raising=False)
    rt = runtime(AsyncMock(return_value=completed(usage=usage)))
    result = await call(rt)
    assert result.cost_usd is None and rt.cost_usd is None
    monkeypatch.setenv("OPENAI_PRICE_INPUT_PER_M", "2")
    monkeypatch.setenv("OPENAI_PRICE_CACHED_INPUT_PER_M", "0.2")
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_M", "12")
    rt = runtime(AsyncMock(return_value=completed(usage=usage)))
    result = await call(rt)
    # (1000 - 200) * 2 + 200 * 0.2 + 100 * 12 = 1600 + 40 + 1200 = 2840 per million.
    assert result.cost_usd == 0.00284
    await call(rt)
    assert rt.cost_usd == 0.00568


async def test_policy_step_and_finish_snapshot_the_run():
    rt = runtime(AsyncMock(return_value=completed()), model="gpt-test")
    await call(rt)
    step = rt.policy_step(
        input_summary="candidates: Network=3", output_summary="team=Network", detail={"k": 1}
    )
    assert (step.kind, step.seq, step.usage, step.costUsd) == ("policy", 2, Usage(), 0.0)
    run = rt.finish(
        pipeline="deterministic", scoring="v1", status="completed", outcome={"extraction": "ok"}
    )
    assert run.id == rt.id
    assert run.pipeline == "deterministic" and run.status == "completed"
    assert run.model == "gpt-test" and run.reasoningEffort == "high"
    assert run.budget == {
        "maxModelCalls": 2,
        "maxToolCalls": 0,
        "maxTotalTokens": 20000,
        "maxSeconds": 75,
    }
    assert run.usage == Usage(input=10, output=5)
    assert run.costUsd is None
    assert run.pricingVersion == "2026-09-06-openrouter"
    assert [s.kind for s in run.steps] == ["model_call", "policy"]
    assert run.outcome == {"extraction": "ok"}


def test_step_summaries_are_sanitized_and_capped():
    rt = runtime(AsyncMock())
    step = rt.policy_step(
        input_summary="token sk-abcdefghijklmnopqrstuvwxyz " + "x" * 600, output_summary="ok"
    )
    assert "sk-abcdefghijklmnop" not in step.inputSummary
    assert "[REDACTED API KEY]" in step.inputSummary
    assert len(step.inputSummary) == 500


async def test_only_transient_failures_are_retried():
    parse = AsyncMock(side_effect=RuntimeError("boom"))
    rt = runtime(parse)
    with pytest.raises(RuntimeError, match="boom"):
        await call(rt)
    assert parse.await_count == 1
    assert [s.status for s in rt.steps] == ["error"]
    request = httpx.Request("POST", "https://api.test/responses")
    parse = AsyncMock(side_effect=[APIConnectionError(request=request), completed()])
    rt = runtime(parse)
    assert (await call(rt)).parsed.answer == "ok"
    assert parse.await_count == 2


async def test_an_incomplete_response_reports_its_status_and_reason():
    parse = AsyncMock(
        return_value=SimpleNamespace(
            status="incomplete",
            output_parsed=None,
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        )
    )
    rt = runtime(parse)
    with pytest.raises(ValueError, match="max_output_tokens"):
        await call(rt)
    assert "incomplete" in rt.steps[0].error


def test_a_skipped_run_records_no_model():
    rt = runtime(AsyncMock(), model="gpt-test")
    run = rt.finish(pipeline="single", scoring="v1", status="skipped", outcome={})
    assert run.model is None and run.steps == []


async def test_the_deterministic_arm_sanitizes_before_truncating_the_input_summary(monkeypatch):
    from relay.agents.orchestrator import run_deterministic
    from relay.agents.schemas import PipelineContext

    monkeypatch.setenv("APP_MODE", "live")

    # The key straddles the 200-character cut: truncating first would leave a partial secret.
    text = "x" * 189 + " sk-abcdefghijklmnop and more"

    async def extract(*args):
        raise RuntimeError("Provider unavailable")

    ctx = PipelineContext(
        report_id="r1",
        text=text,
        message_ids=["m1"],
        sources=[],
        user={},
        clarifications=0,
        procedure=None,
        settings=SETTINGS,
        pipeline="deterministic",
    )
    rt = runtime(AsyncMock())
    await run_deterministic(ctx, rt, extract)
    assert "sk-abcdef" not in rt.steps[0].inputSummary
    assert "[REDACTED" in rt.steps[0].inputSummary


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str


class FunctionCall(SimpleNamespace):
    """Mirrors the SDK's parsed function-call item: `model_dump` carries `parsed_arguments`."""

    def model_dump(self, *, exclude_none=False, exclude=()):
        return {
            key: value
            for key, value in vars(self).items()
            if key not in exclude and not (exclude_none and value is None)
        }


def function_call(name="lookup", call_id="call_1", **arguments):
    return FunctionCall(
        type="function_call",
        id="fc_1",
        name=name,
        call_id=call_id,
        arguments=json.dumps(arguments),
        parsed_arguments=LookupArgs(**arguments) if name == "lookup" else None,
        status="completed",
        namespace=None,
    )


def calling(*calls):
    return SimpleNamespace(
        status="completed",
        output=list(calls),
        output_parsed=None,
        usage=SimpleNamespace(input_tokens=20, output_tokens=8),
    )


def lookup_tools(impl=None):
    tools = [pydantic_function_tool(LookupArgs, name="lookup", description="Find cases")]
    return tools, {
        "lookup": impl or (lambda args: [{"id": f"case-{args.service}", "team": "Network"}])
    }


def tool_budget(calls):
    return Budget(maxModelCalls=5, maxToolCalls=calls, maxTotalTokens=20000, maxSeconds=75)


async def test_tool_loop_echoes_calls_without_parsed_arguments_and_records_steps():
    parse = AsyncMock(side_effect=[calling(function_call(service="vpn")), completed("routed")])
    rt = runtime(parse, budget=tool_budget(4))
    tools, impls = lookup_tools()
    result = await call(rt, tools=tools, tool_impls=impls)
    assert result.parsed.answer == "routed"
    first = parse.call_args_list[0].kwargs
    assert first["tools"] is tools
    assert first["tool_choice"] == "auto" and first["parallel_tool_calls"] is True
    assert "max_tool_calls" not in first
    second = parse.call_args_list[1].kwargs["input"]
    assert second[:2] == first["input"][:2]
    assert second[2] == {
        "type": "function_call",
        "id": "fc_1",
        "name": "lookup",
        "call_id": "call_1",
        "arguments": json.dumps({"service": "vpn"}),
        "status": "completed",
    }
    assert second[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": json.dumps([{"id": "case-vpn", "team": "Network"}]),
    }
    assert [(s.kind, s.status) for s in rt.steps] == [
        ("model_call", "ok"),
        ("tool_call", "ok"),
        ("model_call", "ok"),
    ]
    tool_step = rt.steps[1]
    assert (tool_step.role, tool_step.toolName, tool_step.toolArgs) == (
        "extractor",
        "lookup",
        {"service": "vpn"},
    )
    assert json.loads(tool_step.toolResultSummary) == [{"id": "case-vpn", "team": "Network"}]
    assert tool_step.costUsd == 0.0 and tool_step.usage == Usage()
    assert "lookup" in rt.steps[0].outputSummary
    assert rt.seen_source_ids == {"case-vpn"}
    assert (rt.model_calls, rt.tool_calls) == (2, 1)
    assert rt.usage == Usage(input=30, output=13)


async def test_tool_loop_echoes_message_items_without_the_sdk_parsed_field():
    """A message item echoed with the SDK's `parsed` content field is rejected by the API."""
    from openai.types.responses.parsed_response import (
        ParsedResponseOutputMessage,
        ParsedResponseOutputText,
    )

    note = ParsedResponseOutputMessage[Output](
        id="msg_1",
        role="assistant",
        status="completed",
        type="message",
        content=[
            ParsedResponseOutputText[Output](
                type="output_text",
                text=json.dumps({"answer": "looking"}),
                annotations=[],
                parsed=Output(answer="looking"),
            )
        ],
    )
    parse = AsyncMock(
        side_effect=[calling(note, function_call(service="vpn")), completed("routed")]
    )
    rt = runtime(parse, budget=tool_budget(4))
    tools, impls = lookup_tools()
    result = await call(rt, tools=tools, tool_impls=impls)
    assert result.parsed.answer == "routed"
    echoed = parse.call_args_list[1].kwargs["input"][2]
    assert echoed == {
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "type": "message",
        "content": [
            {"type": "output_text", "text": json.dumps({"answer": "looking"}), "annotations": []}
        ],
    }
    assert parse.call_args_list[1].kwargs["input"][3]["type"] == "function_call"


async def test_async_tool_results_are_awaited_and_sanitized():
    async def impl(args):
        return {"id": "kb-1", "title": f"{args.service} password: hunter2"}

    parse = AsyncMock(side_effect=[calling(function_call(service="vpn")), completed()])
    rt = runtime(parse, budget=tool_budget(4))
    tools, impls = lookup_tools(impl)
    await call(rt, tools=tools, tool_impls=impls)
    assert "hunter2" not in rt.steps[1].toolResultSummary
    assert "[REDACTED]" in rt.steps[1].toolResultSummary
    assert rt.seen_source_ids == {"kb-1"}


async def test_tool_call_budget_is_enforced_locally():
    parse = AsyncMock(
        side_effect=[
            calling(function_call(service="vpn"), function_call(call_id="call_2", service="sso")),
            completed(),
        ]
    )
    rt = runtime(parse, budget=tool_budget(1))
    tools, impls = lookup_tools()
    with pytest.raises(BudgetExceeded, match="Tool call budget"):
        await call(rt, tools=tools, tool_impls=impls)
    assert parse.await_count == 1
    assert [(s.kind, s.status) for s in rt.steps] == [("model_call", "ok"), ("tool_call", "ok")]
    assert rt.tool_calls == 1


async def test_unknown_tool_name_returns_an_error_output_and_an_error_step():
    parse = AsyncMock(side_effect=[calling(function_call(name="nope", service="vpn")), completed()])
    rt = runtime(parse, budget=tool_budget(4))
    tools, impls = lookup_tools()
    await call(rt, tools=tools, tool_impls=impls)
    output = parse.call_args_list[1].kwargs["input"][3]
    assert output["type"] == "function_call_output" and output["call_id"] == "call_1"
    assert "error" in json.loads(output["output"])
    step = rt.steps[1]
    assert (step.kind, step.status, step.toolName) == ("tool_call", "error", "nope")
    assert "Unknown tool" in step.error
    assert rt.tool_calls == 1 and rt.seen_source_ids == set()


async def test_tool_exception_returns_an_error_output_and_an_error_step():
    def impl(args):
        raise RuntimeError("database unavailable")

    parse = AsyncMock(side_effect=[calling(function_call(service="vpn")), completed()])
    rt = runtime(parse, budget=tool_budget(4))
    tools, impls = lookup_tools(impl)
    await call(rt, tools=tools, tool_impls=impls)
    output = json.loads(parse.call_args_list[1].kwargs["input"][3]["output"])
    assert output == {"error": "database unavailable"}
    assert rt.steps[1].status == "error" and "database unavailable" in rt.steps[1].error
    assert rt.steps[1].toolArgs == {"service": "vpn"}


async def test_neither_calls_nor_parsed_output_is_rejected_and_retried_once():
    parse = AsyncMock(side_effect=[calling(), completed()])
    rt = runtime(parse, budget=tool_budget(4))
    tools, impls = lookup_tools()
    result = await call(rt, tools=tools, tool_impls=impls)
    assert result.parsed.answer == "ok"
    assert [s.status for s in rt.steps] == ["rejected", "ok"]


async def test_the_model_call_budget_bounds_the_tool_loop():
    parse = AsyncMock(return_value=calling(function_call(service="vpn")))
    rt = runtime(
        parse, budget=Budget(maxModelCalls=2, maxToolCalls=4, maxTotalTokens=20000, maxSeconds=75)
    )
    tools, impls = lookup_tools()
    with pytest.raises(BudgetExceeded, match="Model call budget"):
        await call(rt, tools=tools, tool_impls=impls)
    assert parse.await_count == 2
    assert [s.kind for s in rt.steps] == ["model_call", "tool_call", "model_call", "tool_call"]
