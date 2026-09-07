"""Typed agent-run records, routing proposals, and the context handed to every pipeline."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain import TEAMS
from ..model import Extraction
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


class RoutingProposal(BaseModel):
    """A model's routing opinion. Deterministic lanes decide how much of it may apply."""

    model_config = ConfigDict(extra="forbid", strict=True)
    service: Literal["vpn", "sso", "wifi", "laptop", "atlas"] | None
    team: str = Field(max_length=40)
    abstain: bool
    blockedQuote: str | None
    broadImpactQuote: str | None
    securityQuote: str | None
    rationale: str = Field(max_length=300)
    probability: float = Field(ge=0, le=1)
    citedSourceIds: list[str] = Field(max_length=10)


class SingleAgentOutput(Extraction, RoutingProposal):
    """One structured call: the extraction contract plus a routing proposal over it."""


def validate_proposal(
    proposal: RoutingProposal | dict, text: str, allowed_source_ids: set[str]
) -> dict:
    """Quotes must be literal spans of the requester text; teams and sources must exist."""
    parsed = (
        proposal
        if isinstance(proposal, RoutingProposal)
        else RoutingProposal.model_validate(proposal)
    )
    data = parsed.model_dump(include=set(RoutingProposal.model_fields))
    for quote in (data["blockedQuote"], data["broadImpactQuote"], data["securityQuote"]):
        if quote and quote not in text:
            raise ValueError("Proposal asserted unsupported evidence.")
    if data["team"] not in TEAMS:
        raise ValueError("Proposal named an unknown team.")
    if any(identifier not in allowed_source_ids for identifier in data["citedSourceIds"]):
        raise ValueError("Proposal cited a source that was not provided.")
    return data


class ReviewerIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str = Field(max_length=100)
    message: str = Field(max_length=300)
    evidenceQuote: str | None


class ReviewerOutput(BaseModel):
    """An independent critic's verdict on a proposal; it can only ask, never route."""

    model_config = ConfigDict(extra="forbid", strict=True)
    verdict: Literal["accept", "revise", "human_review"]
    agreementProbability: float = Field(ge=0, le=1)
    issues: list[ReviewerIssue] = Field(max_length=10)
    securityQuote: str | None


def validate_reviewer(output: ReviewerOutput | dict, text: str) -> dict:
    """Every quote the reviewer gives must be a literal span of the requester text."""
    parsed = output if isinstance(output, ReviewerOutput) else ReviewerOutput.model_validate(output)
    data = parsed.model_dump()
    quotes = [data["securityQuote"], *(issue["evidenceQuote"] for issue in data["issues"])]
    if any(quote and quote not in text for quote in quotes):
        raise ValueError("Reviewer asserted unsupported evidence.")
    return data


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
