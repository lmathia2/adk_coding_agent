from __future__ import annotations

from pathlib import Path

from harness.evals import (
    EvaluationBudgets,
    EvaluationCase,
    EvaluationSuite,
    grade_case,
    load_evaluation_suite,
    write_evaluation_suite,
)
from harness.models.task import TaskRequest
from harness.models.verification import CriterionEvidence, VerificationReport


def _case() -> EvaluationCase:
    return EvaluationCase(
        case_id="fix-auth",
        description="Fix an authentication regression",
        fixture="tests/fixtures/auth",
        request=TaskRequest(
            goal="Fix login",
            acceptance_criteria=["Login succeeds"],
        ),
        expected_changed_globs=["src/**"],
        forbidden_changed_globs=["deployment/**"],
        required_command_fragments=["pytest"],
        budgets=EvaluationBudgets(
            max_iterations=10,
            max_uncached_input_tokens=10_000,
        ),
    )


def _verification() -> VerificationReport:
    return VerificationReport(
        passed=True,
        criteria=[
            CriterionEvidence(
                criterion="Login succeeds",
                satisfied=True,
                evidence=["pytest tests/test_auth.py"],
            )
        ],
        commands_run=["pytest tests/test_auth.py", "git diff --check"],
        tests_passed=1,
        tests_failed=0,
    )


def test_suite_round_trip(tmp_path: Path) -> None:
    suite = EvaluationSuite(
        suite_id="core",
        description="Core harness behaviors",
        cases=[_case()],
    )
    path = tmp_path / "suite.json"
    write_evaluation_suite(path, suite)
    assert load_evaluation_suite(path) == suite


def test_grade_case_checks_evidence_scope_commands_and_budgets() -> None:
    result = grade_case(
        _case(),
        status="complete",
        changed_paths=["src/auth.py", "tests/test_auth.py"],
        verification=_verification(),
        metric_summary={
            "outcome_iterations": 4,
            "uncached_input_tokens": 7_000,
        },
    )
    assert result.passed


def test_grade_case_fails_for_forbidden_scope_or_budget() -> None:
    result = grade_case(
        _case(),
        status="complete",
        changed_paths=["src/auth.py", "deployment/prod.tf"],
        verification=_verification(),
        metric_summary={
            "outcome_iterations": 4,
            "uncached_input_tokens": 20_000,
        },
    )
    assert not result.passed
    failed_names = {check.name for check in result.checks if not check.passed}
    assert "forbidden_change:deployment/**" in failed_names
    assert "budget:max_uncached_input_tokens" in failed_names
