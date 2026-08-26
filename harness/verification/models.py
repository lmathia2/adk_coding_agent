"""Internal validation-plan and command-result contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ValidationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal["syntax", "format", "lint", "typecheck", "test", "diff", "custom"]
    command: str
    source: str
    required: bool = True
    targeted: bool = False


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    artifact_uri: str | None = None

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class ValidationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[ValidationCommand] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    allowed_paths: list[str] | None = None
    forbidden_paths: list[str] = Field(default_factory=list)
