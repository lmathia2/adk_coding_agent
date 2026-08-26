"""Portable coding-harness evaluation case contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from harness.models.task import TaskRequest


class EvaluationBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_iterations: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_uncached_input_tokens: int | None = Field(default=None, ge=0)
    max_wall_time_ms: int | None = Field(default=None, ge=0)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    description: str
    fixture: str
    request: TaskRequest
    expected_changed_globs: list[str] = Field(default_factory=list)
    forbidden_changed_globs: list[str] = Field(default_factory=list)
    required_command_fragments: list[str] = Field(default_factory=list)
    budgets: EvaluationBudgets = Field(default_factory=EvaluationBudgets)
    tags: list[str] = Field(default_factory=list)


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str
    description: str = ""
    cases: list[EvaluationCase]


def load_evaluation_suite(path: Path) -> EvaluationSuite:
    return EvaluationSuite.model_validate_json(path.read_text(encoding="utf-8"))


def write_evaluation_suite(path: Path, suite: EvaluationSuite) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(suite.model_dump(mode="json"), sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
