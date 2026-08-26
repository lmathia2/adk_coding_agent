"""Deterministic grading for coding-harness evaluation runs."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from harness.models.verification import VerificationReport

from .cases import EvaluationCase


class EvaluationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    details: str


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    passed: bool
    checks: list[EvaluationCheck] = Field(default_factory=list)
    metric_summary: dict[str, Any] = Field(default_factory=dict)


def _matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    candidate = pattern.replace("\\", "/").lstrip("./")
    return fnmatch.fnmatch(normalized, candidate) or normalized.startswith(
        candidate.rstrip("/") + "/"
    )


def grade_case(
    case: EvaluationCase,
    *,
    status: str,
    changed_paths: list[str],
    verification: VerificationReport,
    metric_summary: Mapping[str, Any] | None = None,
) -> EvaluationResult:
    metrics = dict(metric_summary or {})
    checks: list[EvaluationCheck] = []

    checks.append(
        EvaluationCheck(
            name="completion_status",
            passed=status == "complete",
            details=f"status={status}",
        )
    )
    checks.append(
        EvaluationCheck(
            name="deterministic_verification",
            passed=verification.passed,
            details=(
                f"tests_passed={verification.tests_passed}, "
                f"tests_failed={verification.tests_failed}"
            ),
        )
    )

    for pattern in case.expected_changed_globs:
        matches = sorted(path for path in changed_paths if _matches(path, pattern))
        checks.append(
            EvaluationCheck(
                name=f"expected_change:{pattern}",
                passed=bool(matches),
                details="matched: " + (", ".join(matches) or "none"),
            )
        )
    for pattern in case.forbidden_changed_globs:
        matches = sorted(path for path in changed_paths if _matches(path, pattern))
        checks.append(
            EvaluationCheck(
                name=f"forbidden_change:{pattern}",
                passed=not matches,
                details="matched: " + (", ".join(matches) or "none"),
            )
        )

    commands = verification.commands_run
    for fragment in case.required_command_fragments:
        matched = [command for command in commands if fragment in command]
        checks.append(
            EvaluationCheck(
                name=f"required_command:{fragment}",
                passed=bool(matched),
                details="matched: " + (", ".join(matched) or "none"),
            )
        )

    budget_fields = {
        "max_iterations": "outcome_iterations",
        "max_cost_usd": "cost_usd",
        "max_uncached_input_tokens": "uncached_input_tokens",
        "max_wall_time_ms": "outcome_wall_time_ms",
    }
    budget_values = case.budgets.model_dump(mode="python")
    for budget_name, metric_name in budget_fields.items():
        maximum = budget_values[budget_name]
        if maximum is None:
            continue
        actual = metrics.get(metric_name)
        passed = actual is not None and float(actual) <= float(maximum)
        checks.append(
            EvaluationCheck(
                name=f"budget:{budget_name}",
                passed=passed,
                details=f"actual={actual}, maximum={maximum}",
            )
        )

    return EvaluationResult(
        case_id=case.case_id,
        passed=all(check.passed for check in checks),
        checks=checks,
        metric_summary=metrics,
    )
