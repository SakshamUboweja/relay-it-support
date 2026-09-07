"""Agent traces persist inside the intake transaction and redact operator-only detail."""

from uuid import uuid4

import pytest

from relay.agents.schemas import AgentRun, AgentStep, Usage
from relay.agents.traces import load_traces, persist_run
from relay.db import query, transaction

pytestmark = pytest.mark.usefixtures("isolated_db")


async def saved_report():
    await query(
        "INSERT INTO users(id,name,role,location,device,scope) VALUES('maya','Maya','employee','SF','Mac','sf')"
    )
    report_id = str(uuid4())
    await query(
        "INSERT INTO reports(id,owner_id,submission_key,summary,state,decision,mode) VALUES($1,'maya',$2,'VPN','processing',$3,'demo')",
        [report_id, str(uuid4()), {"team": "Network"}],
    )
    return report_id


def run_with_steps():
    steps = [
        AgentStep(
            seq=1,
            role="extractor",
            kind="model_call",
            model="gpt-test",
            promptVersion="relay-intake-v2",
            inputSummary="VPN fails",
            outputSummary="VPN failure [quotes: symptomQuote]",
            usage=Usage(input=30, output=40, cached=5, reasoning=10),
            costUsd=0.000123,
            latencyMs=1200,
            status="ok",
        ),
        AgentStep(
            seq=2,
            role="policy",
            kind="policy",
            inputSummary="candidates: Network=3",
            outputSummary="team=Network",
            costUsd=0.0,
            latencyMs=1,
            detail={"priority": "normal"},
        ),
    ]
    return AgentRun(
        id=str(uuid4()),
        pipeline="deterministic",
        scoring="v1",
        model="gpt-test",
        reasoningEffort="high",
        status="completed",
        budget={"maxModelCalls": 2},
        usage=Usage(input=30, output=40, cached=5, reasoning=10),
        costUsd=0.000123,
        pricingVersion="2026-09-06-openrouter",
        latencyMs=1201,
        outcome={"extraction": "validated"},
        steps=steps,
    )


async def test_persist_run_inside_transaction_and_load_by_role():
    report_id = await saved_report()
    run = run_with_steps()
    decision_id = str(uuid4())
    async with transaction() as db:
        await query(
            "INSERT INTO decisions VALUES($1,$2,$3,now())",
            [decision_id, report_id, {"team": "Network"}],
            db=db,
        )
        await persist_run(db, report_id, decision_id, run)
    rows = (await query("SELECT * FROM agent_runs WHERE report_id=$1", [report_id])).rows
    assert len(rows) == 1 and rows[0]["decision_id"] == decision_id
    employee = await load_traces(report_id, "employee")
    assert [r["id"] for r in employee["runs"]] == [run.id]
    saved = employee["runs"][0]
    assert saved["pipeline"] == "deterministic" and saved["status"] == "completed"
    assert saved["model"] == "gpt-test" and saved["reasoningEffort"] == "high"
    assert saved["usage"] == {"input": 30, "output": 40, "cached": 5, "reasoning": 10}
    assert saved["costUsd"] == 0.000123 and saved["latencyMs"] == 1201
    assert saved["createdAt"]
    assert not {"outcome", "budget", "pricingVersion"} & set(saved)
    assert [s["seq"] for s in saved["steps"]] == [1, 2]
    first = saved["steps"][0]
    assert first["kind"] == "model_call" and first["promptVersion"] == "relay-intake-v2"
    assert first["usage"]["reasoning"] == 10 and first["costUsd"] == 0.000123
    assert first["toolName"] is None
    assert not {"toolArgs", "toolResultSummary", "error", "detail"} & set(first)
    operator = (await load_traces(report_id, "operator"))["runs"][0]
    assert operator["outcome"] == {"extraction": "validated"}
    assert operator["budget"] == {"maxModelCalls": 2}
    assert operator["pricingVersion"] == "2026-09-06-openrouter"
    assert operator["steps"][1]["detail"] == {"priority": "normal"}
    assert operator["steps"][0]["error"] is None and operator["steps"][0]["toolArgs"] is None


async def test_persist_run_rolls_back_with_the_transaction():
    report_id = await saved_report()
    run = run_with_steps()
    with pytest.raises(RuntimeError):
        async with transaction() as db:
            await persist_run(db, report_id, None, run)
            raise RuntimeError("abort")
    assert (await load_traces(report_id, "operator")) == {"runs": []}
