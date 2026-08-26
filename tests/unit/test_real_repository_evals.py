from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.evals import RealRepositoryEvaluationSuite, load_real_repository_suite


def _suite_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "eval"
        / "suites"
        / "real_repositories.json"
    )


def test_committed_real_repository_suite_is_reproducible() -> None:
    suite = load_real_repository_suite(_suite_path())

    assert suite.schema_version == "real-repository-v1"
    assert {case.case_id for case in suite.cases} == {
        "django-asgi-script-prefix-boundary",
        "ruff-sim105-preserve-except-semantics",
    }
    assert all(case.source.author_type == "User" for case in suite.cases)
    assert all(len(case.fixture_fingerprint()) == 64 for case in suite.cases)
    assert all(
        case.fixture.materialization_commands()[2][0:3]
        == ("git", "fetch", "--depth=1")
        for case in suite.cases
    )


def test_suite_rejects_revision_drift() -> None:
    payload = json.loads(_suite_path().read_text(encoding="utf-8"))
    payload["cases"][0]["fixture"]["base_revision"] = "0" * 40

    with pytest.raises(ValidationError, match="base revision does not match"):
        RealRepositoryEvaluationSuite.model_validate(payload)


def test_suite_requires_held_out_files_to_be_forbidden() -> None:
    payload = json.loads(_suite_path().read_text(encoding="utf-8"))
    payload["cases"][0]["forbidden_changed_globs"] = ["docs/**"]

    with pytest.raises(ValidationError, match="held-out path must be forbidden"):
        RealRepositoryEvaluationSuite.model_validate(payload)


def test_suite_rejects_non_human_pr_authors() -> None:
    payload = json.loads(_suite_path().read_text(encoding="utf-8"))
    payload["cases"][0]["source"]["author_type"] = "Bot"

    with pytest.raises(ValidationError):
        RealRepositoryEvaluationSuite.model_validate(payload)
