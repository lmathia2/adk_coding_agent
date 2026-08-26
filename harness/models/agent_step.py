"""Structured output emitted by one bounded coding work batch."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CompletionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str
    evidence: list[str] = Field(default_factory=list)


class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["continue", "verify", "blocked", "done"]
    progress: list[str] = Field(default_factory=list)
    next_action: str | None = None
    decisions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    discovered_constraints: list[str] = Field(default_factory=list)
    files_in_focus: list[str] = Field(default_factory=list)
    completion_claims: list[CompletionClaim] = Field(default_factory=list)
