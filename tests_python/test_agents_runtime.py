"""ModelRuntime budgets, retries, usage accounting and request shape at the SDK boundary."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError
from pydantic import BaseModel

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


async def test_tools_are_not_available_yet():
    rt = runtime(AsyncMock(return_value=completed()))
    with pytest.raises(NotImplementedError, match="later task"):
        await call(rt, tools=[{"type": "function", "name": "lookup"}])


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
