from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TEAMS = [
    "Service Desk",
    "Identity & Access",
    "Network",
    "Endpoint",
    "Business Applications",
    "Security Review",
]
Team = Literal[
    "Service Desk",
    "Identity & Access",
    "Network",
    "Endpoint",
    "Business Applications",
    "Security Review",
]
Priority = Literal["normal", "elevated", "urgent"]


def fact(value, origin="unknown", evidence_ids=None):
    return {"value": value, "origin": origin, "evidenceIds": list(evidence_ids or [])}


class IntakeInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(default="", max_length=6000)
    reportId: UUID | None = None
    submissionKey: UUID
    action: Literal["message", "support", "fixed", "broken", "follow"] = "message"

    @field_validator("text", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def has_content(self):
        if not self.reportId and not self.text:
            raise ValueError("Describe the issue first.")
        return self


class CorrectionInput(BaseModel):
    reportId: UUID
    action: Literal["acknowledge", "correct", "retry", "refresh"]
    team: Team | None = None
    priority: Priority | None = None
    reason: str | None = Field(default=None, max_length=1000)


class SessionInput(BaseModel):
    userId: str | None = None
    token: str | None = Field(default=None, max_length=200)
