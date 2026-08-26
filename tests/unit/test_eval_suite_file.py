from __future__ import annotations

from pathlib import Path

from harness.evals import load_evaluation_suite


def test_committed_core_evaluation_suite_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    suite = load_evaluation_suite(root / "tests" / "eval" / "suites" / "core.json")
    assert suite.suite_id == "core-v1"
    assert {case.case_id for case in suite.cases} == {"python-localized-bugfix"}
