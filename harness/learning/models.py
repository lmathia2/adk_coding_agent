"""Normalized, tracing-independent contracts for deterministic skill learning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NormalizedAction(BaseModel):
    """Privacy-safe action label with no arguments, source, or output body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    category: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]{0,63}$",
    )
    outcome: Literal["ok", "error", "blocked"] = "ok"

    @property
    def token(self) -> str:
        category = self.category or "general"
        return f"{self.action}:{category}:{self.outcome}"


class EpisodeQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    cost_usd: float = Field(default=0.0, ge=0)
    uncached_input_tokens: int = Field(default=0, ge=0)
    cache_read_ratio: float = Field(default=0.0, ge=0, le=1)
    tool_calls: int = Field(default=0, ge=0)
    wall_time_ms: int = Field(default=0, ge=0)


class WorkflowEpisode(BaseModel):
    """One completed workflow reduced independently of its tracing provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = Field(min_length=1, max_length=128)
    workflow_kind: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    actions: tuple[NormalizedAction, ...] = Field(min_length=1, max_length=256)
    verified_completed: bool
    blocked: bool = False
    security_risks: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    quality: EpisodeQuality

    def require_eligible(self) -> None:
        if not self.verified_completed or not self.quality.passed:
            raise ValueError("learning requires deterministically verified completion")
        if self.blocked or any(action.outcome == "blocked" for action in self.actions):
            raise ValueError("blocked workflow traces are not eligible for learning")
        if self.security_risks:
            raise ValueError("security-risk workflow traces are not eligible for learning")


class WorkflowObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str
    workflow_kind: str
    workflow_fingerprint: str
    action_tokens: tuple[str, ...]
    quality: EpisodeQuality


class RepeatedActionSequence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tokens: tuple[str, ...]
    support: int = Field(ge=1)
    source_trace_ids: tuple[str, ...]


class TrialAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str = Field(min_length=1, max_length=128)
    unit_id: str = Field(min_length=1, max_length=256)
    variant: Literal["baseline", "candidate"]


class TrialOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str = Field(min_length=1, max_length=128)
    unit_id: str = Field(min_length=1, max_length=256)
    skill_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    variant: Literal["baseline", "candidate"]
    quality: EpisodeQuality


class QualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    support: int = Field(ge=0)
    passes: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    cost_per_passed_task: float | None = Field(default=None, ge=0)
    uncached_input_tokens: float = Field(ge=0)
    cache_read_ratio: float = Field(ge=0, le=1)
    tool_calls: float = Field(ge=0)
    wall_time_ms: float = Field(ge=0)


class SkillLifecycle(BaseModel):
    """Interoperable metadata stored beside every generated SKILL.md."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=512)
    status: Literal["enabled", "candidate", "disabled"]
    version: int = Field(ge=1)
    source_trace_ids: tuple[str, ...]


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=512)
    instructions: str = Field(min_length=1, max_length=16_000)
    source_trace_ids: tuple[str, ...]


__all__ = [
    "EpisodeQuality",
    "NormalizedAction",
    "QualitySummary",
    "RepeatedActionSequence",
    "SkillDraft",
    "SkillLifecycle",
    "TrialAssignment",
    "TrialOutcome",
    "WorkflowEpisode",
    "WorkflowObservation",
]
