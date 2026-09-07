"""Cached model results for the arm comparison: a hit replays the run without a model call."""

import hashlib
import json
from pathlib import Path

from .agents import pricing
from .agents.runtime import ModelRuntime
from .agents.schemas import AgentRun, PipelineResult

KEY_LENGTH = 16


def sources_hash(sources: list[dict]) -> str:
    """Digest of what routing can see in the sources: id, service, reviewed team and flag."""
    rows = sorted(
        [
            s["id"],
            s.get("service"),
            (s.get("metadata") or {}).get("team"),
            (s.get("metadata") or {}).get("reviewed"),
        ]
        for s in sources
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def cache_key(
    *,
    arm: str,
    case_id: str,
    text: str,
    model: str,
    effort: str,
    prompt_versions: dict,
    tool_schema_version: str,
    scoring: str,
    sources_hash: str,
    candidate_scoring: str,
) -> str:
    """`scoring` is what compose ran with; `candidate_scoring` is `policy["routingScoring"]`,
    the scoring the arm ranked its candidates with, so a policy flip misses the cache."""
    parts = [
        arm,
        case_id,
        text,
        model,
        effort,
        json.dumps(prompt_versions, sort_keys=True),
        tool_schema_version,
        scoring,
        sources_hash,
        candidate_scoring,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:KEY_LENGTH]


def cache_path(cache_dir: Path, arm: str, case_id: str, key: str) -> Path:
    return cache_dir / arm / f"{case_id}.{key}.json"


def serialize_result(result: PipelineResult) -> dict:
    return {
        "run": result.run.model_dump(),
        "extraction": result.extraction,
        "proposal": result.proposal,
        "reviewer": result.reviewer,
        "summary": result.summary,
        "requested_support": result.requested_support,
        "procedure_tried": result.procedure_tried,
    }


def deserialize_result(data: dict) -> PipelineResult:
    return PipelineResult(
        run=AgentRun(**data["run"]),
        extraction=data["extraction"],
        proposal=data["proposal"],
        reviewer=data["reviewer"],
        summary=data["summary"],
        requested_support=data["requested_support"],
        procedure_tried=data["procedure_tried"],
    )


def read_entry(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_entry(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2, default=str) + "\n")


def replay(entry: dict, rt: ModelRuntime) -> PipelineResult:
    """Rebuild the cached run on a fresh runtime so composition can run again without a call.

    Model and tool steps are re-recorded (costs re-priced from the current table); the policy
    step is left for `compose_decision` to add, exactly as on a live run.
    """
    result = deserialize_result(entry["result"])
    for step in result.run.steps:
        if step.kind == "policy":
            continue
        fields = step.model_dump(exclude={"seq"})
        if step.kind == "model_call":
            fields["costUsd"] = pricing.cost(rt.model, step.usage)
        rt.record(**fields)
    return result
