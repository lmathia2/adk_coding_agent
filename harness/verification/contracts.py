"""Deterministic validation plan and command result contracts."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

ValidationCategory: TypeAlias = Literal[
    "syntax",
    "format",
    "lint",
    "typecheck",
    "test",
    "build",
    "diff",
    "custom",
]
ValidationStatus: TypeAlias = Literal["ok", "error", "blocked", "timeout"]


class ValidationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ValidationCategory
    command: str
    source: str
    required: bool = True
    timeout_seconds: int = Field(default=300, ge=1, le=3_600)


class ValidationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[ValidationCommand] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    allowed_paths: list[str] | None = None
    forbidden_paths: list[str] = Field(default_factory=list)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ValidationCategory
    command: str
    source: str
    status: ValidationStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(default=0, ge=0)
    truncated: bool = False
    omitted_bytes: int = Field(default=0, ge=0)
    artifact_uri: str | None = None
    approval_request_id: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "ok" and self.exit_code in {0, None}


__all__ = [
    "CommandResult",
    "ValidationCategory",
    "ValidationCommand",
    "ValidationPlan",
    "ValidationStatus",
]
