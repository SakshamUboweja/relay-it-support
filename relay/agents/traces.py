"""Persist and read agent traces; requesters see steps without operator-only detail."""

from uuid import uuid4

from ..db import query
from .schemas import AgentRun


async def persist_run(db, report_id: str, decision_id: str | None, run: AgentRun) -> None:
    """Insert the run and its steps inside the caller's transaction."""
    await query(
        "INSERT INTO agent_runs(id,report_id,decision_id,pipeline,scoring,model,reasoning_effort,status,budget,usage,cost_usd,pricing_version,latency_ms,outcome) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
        [
            run.id,
            report_id,
            decision_id,
            run.pipeline,
            run.scoring,
            run.model,
            run.reasoningEffort,
            run.status,
            run.budget,
            run.usage.model_dump(),
            run.costUsd,
            run.pricingVersion,
            run.latencyMs,
            run.outcome,
        ],
        db=db,
    )
    for step in run.steps:
        await query(
            "INSERT INTO agent_steps(id,run_id,seq,role,kind,model,prompt_version,input_summary,output_summary,tool_name,tool_args,tool_result_summary,usage,cost_usd,latency_ms,status,error,detail) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)",
            [
                str(uuid4()),
                run.id,
                step.seq,
                step.role,
                step.kind,
                step.model,
                step.promptVersion,
                step.inputSummary,
                step.outputSummary,
                step.toolName,
                step.toolArgs,
                step.toolResultSummary,
                step.usage.model_dump(),
                step.costUsd,
                step.latencyMs,
                step.status,
                step.error,
                step.detail,
            ],
            db=db,
        )


def _money(value) -> float | None:
    return float(value) if value is not None else None


def _step(row: dict, operator: bool) -> dict:
    step = {
        "seq": row["seq"],
        "role": row["role"],
        "kind": row["kind"],
        "model": row["model"],
        "promptVersion": row["prompt_version"],
        "inputSummary": row["input_summary"],
        "outputSummary": row["output_summary"],
        "toolName": row["tool_name"],
        "usage": row["usage"],
        "costUsd": _money(row["cost_usd"]),
        "latencyMs": row["latency_ms"],
        "status": row["status"],
    }
    if operator:
        step.update(
            toolArgs=row["tool_args"],
            toolResultSummary=row["tool_result_summary"],
            error=row["error"],
            detail=row["detail"],
        )
    return step


def _run(row: dict, steps: list[dict], operator: bool) -> dict:
    run = {
        "id": row["id"],
        "pipeline": row["pipeline"],
        "scoring": row["scoring"],
        "status": row["status"],
        "model": row["model"],
        "reasoningEffort": row["reasoning_effort"],
        "usage": row["usage"],
        "costUsd": _money(row["cost_usd"]),
        "latencyMs": row["latency_ms"],
        "createdAt": row["created_at"],
        "steps": [_step(s, operator) for s in steps],
    }
    if operator:
        run.update(
            outcome=row["outcome"], budget=row["budget"], pricingVersion=row["pricing_version"]
        )
    return run


async def load_traces(report_id: str, role: str) -> dict:
    """All runs for a report, oldest first; operator-only fields appear for operators."""
    runs = (
        await query(
            "SELECT * FROM agent_runs WHERE report_id=$1 ORDER BY created_at,id", [report_id]
        )
    ).rows
    steps = (
        await query(
            "SELECT s.* FROM agent_steps s JOIN agent_runs r ON r.id=s.run_id WHERE r.report_id=$1 ORDER BY s.seq",
            [report_id],
        )
    ).rows
    operator = role == "operator"
    return {"runs": [_run(r, [s for s in steps if s["run_id"] == r["id"]], operator) for r in runs]}
