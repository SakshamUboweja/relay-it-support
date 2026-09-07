"""Typed agent-run records and the context handed to every intake pipeline."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..sanitize import sanitize

SUMMARY_LIMIT = 500


def summary(text: str) -> str:
    """Trace text is stored redacted and short: evidence for operators, not a transcript."""
    return sanitize(text)[:SUMMARY_LIMIT]


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: int = 0
    output: int = 0
    cached: int = 0
    reasoning: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input=self.input + other.input,
            output=self.output + other.output,
            cached=self.cached + other.cached,
            reasoning=self.reasoning + other.reasoning,
        )

    @property
    def total(self) -> int:
        return self.input + self.output


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maxModelCalls: int
    maxToolCalls: int
    maxTotalTokens: int
    maxSeconds: float


class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int
    role: str
    kind: Literal["model_call", "tool_call", "policy"]
    model: str | None = None
    promptVersion: str | None = None
    inputSummary: str
    outputSummary: str
    toolName: str | None = None
    toolArgs: dict | None = None
    toolResultSummary: str | None = None
    usage: Usage = Usage()
    costUsd: float | None = None
    latencyMs: int = 0
    status: Literal["ok", "error", "timeout", "rejected", "skipped"] = "ok"
    error: str | None = None
    detail: dict = {}

    @field_validator("inputSummary", "outputSummary", "toolResultSummary", "error")
    @classmethod
    def redact(cls, value):
        return summary(value) if value is not None else None


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    pipeline: Literal["deterministic", "single", "multi"]
    scoring: str
    model: str | None
    reasoningEffort: str | None
    status: Literal["completed", "failed", "budget_exhausted", "skipped"]
    budget: dict
    usage: Usage
    costUsd: float | None
    pricingVersion: str
    latencyMs: int
    outcome: dict
    steps: list[AgentStep]


@dataclass
class PipelineContext:
    report_id: str
    text: str
    message_ids: list[str]
    sources: list[dict]
    user: dict
    clarifications: int
    procedure: dict | None
    settings: dict
    pipeline: str
    image: dict | None = None


@dataclass
class PipelineResult:
    run: AgentRun
    extraction: dict | None = None
    proposal: dict | None = None
    reviewer: dict | None = None
    summary: str | None = None
    requested_support: bool = False
    procedure_tried: bool = False
