"""Execute validation plans and produce evidence-based completion reports."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Mapping, Protocol

from harness.models.verification import CriterionEvidence, VerificationReport

from .models import CommandResult, ValidationCommand, ValidationPlan
from .scope import check_scope


class CommandExecutor(Protocol):
    def __call__(self, command: ValidationCommand) -> CommandResult: ...


def local_executor(root: Path, timeout_seconds: int = 600) -> CommandExecutor:
    root = root.resolve()

    def execute(command: ValidationCommand) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command.command,
                cwd=root,
                shell=True,
                executable="/bin/bash",
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return CommandResult(
                category=command.category,
                command=command.command,
                exit_code=completed.returncode,
                stdout=completed.stdout[-16_000:],
                stderr=completed.stderr[-16_000:],
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                category=command.category,
                command=command.command,
                exit_code=124,
                stdout=(exc.stdout or "")[-16_000:] if isinstance(exc.stdout, str) else "",
                stderr="validation command timed out",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    return execute


def _test_counts(results: list[CommandResult]) -> tuple[int, int]:
    passed = failed = 0
    for result in results:
        if result.category != "test":
            continue
        text = f"{result.stdout}\n{result.stderr}"
        passed_matches = re.findall(r"(\d+)\s+passed", text)
        failed_matches = re.findall(r"(\d+)\s+failed", text)
        passed += sum(int(value) for value in passed_matches)
        failed += sum(int(value) for value in failed_matches)
        if not passed_matches and not failed_matches:
            failed += int(not result.passed)
    return passed, failed


def _diagnostic(result: CommandResult) -> str:
    body = result.stderr.strip() or result.stdout.strip()
    if len(body) > 1_000:
        body = body[:1_000] + "\n[diagnostic truncated]"
    return f"{result.category} failed: {result.command}\n{body}".strip()


def build_report(
    *,
    criteria: list[str],
    results: list[CommandResult],
    scope_violations: list[str],
    criterion_evidence: Mapping[str, list[str]] | None = None,
) -> VerificationReport:
    evidence_map = criterion_evidence or {}
    required_commands_passed = all(result.passed for result in results)
    criteria_rows = [
        CriterionEvidence(
            criterion=criterion,
            satisfied=bool(evidence_map.get(criterion)) and required_commands_passed,
            evidence=list(evidence_map.get(criterion, [])),
            notes=None if evidence_map.get(criterion) else "No explicit evidence recorded",
        )
        for criterion in criteria
    ]
    diagnostics = [_diagnostic(result) for result in results if not result.passed]
    tests_passed, tests_failed = _test_counts(results)
    passed = (
        required_commands_passed
        and not scope_violations
        and all(row.satisfied for row in criteria_rows)
    )
    next_action: str | None = None
    if scope_violations:
        next_action = "Revert or justify changes outside the permitted scope"
    elif diagnostics:
        next_action = "Fix the first failing validation command and rerun verification"
    elif any(not row.satisfied for row in criteria_rows):
        next_action = "Record concrete evidence for each unsatisfied acceptance criterion"

    return VerificationReport(
        passed=passed,
        criteria=criteria_rows,
        commands_run=[result.command for result in results],
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        scope_violations=scope_violations,
        unresolved_diagnostics=diagnostics,
        recommended_next_action=next_action,
    )


def run_validation_plan(
    root: Path,
    plan: ValidationPlan,
    *,
    acceptance_criteria: list[str],
    criterion_evidence: Mapping[str, list[str]] | None = None,
    executor: CommandExecutor | None = None,
    stop_on_failure: bool = True,
) -> tuple[VerificationReport, list[CommandResult]]:
    execute = executor or local_executor(root)
    results: list[CommandResult] = []
    for command in plan.commands:
        result = execute(command)
        results.append(result)
        if stop_on_failure and command.required and not result.passed:
            break
    violations = check_scope(
        plan.changed_paths,
        allowed_paths=plan.allowed_paths,
        forbidden_paths=plan.forbidden_paths,
    )
    return (
        build_report(
            criteria=acceptance_criteria,
            results=results,
            scope_violations=violations,
            criterion_evidence=criterion_evidence,
        ),
        results,
    )
