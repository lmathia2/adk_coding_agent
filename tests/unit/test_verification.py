from __future__ import annotations

from pathlib import Path

from harness.repo.discovery import BuildCommand, RepositoryManifest
from harness.sandbox import SandboxRequest, SandboxResult
from harness.verification import (
    CommandResult,
    check_scope,
    discover_validation_plan,
    run_validation_plan,
)


class _RecordingSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        return SandboxResult(
            status="ok",
            exit_code=0,
            stdout="verification passed",
            stderr="",
            duration_ms=21,
        )


def test_discovery_selects_syntax_targeted_tests_and_diff(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    (tmp_path / "test_auth.py").write_text("def test_login():\n    assert True\n", encoding="utf-8")
    manifest = RepositoryManifest(
        root=tmp_path,
        commands=[
            BuildCommand("lint", "ruff check .", "pyproject.toml"),
            BuildCommand("test", "pytest", "pyproject.toml"),
        ],
    )
    plan = discover_validation_plan(manifest, ["auth.py"])
    assert [item.category for item in plan.commands] == ["syntax", "lint", "test", "diff"]
    test = next(item for item in plan.commands if item.category == "test")
    assert test.targeted
    assert "test_auth.py" in test.command


def test_scope_reports_forbidden_and_outside_paths() -> None:
    violations = check_scope(
        ["src/auth.py", "deployment/prod.tf", "README.md"],
        allowed_paths=["src/", "README.md"],
        forbidden_paths=["deployment/**"],
    )
    assert "forbidden path changed: deployment/prod.tf" in violations
    assert all("README.md" not in item for item in violations)


def test_report_requires_commands_scope_and_explicit_criterion_evidence(tmp_path: Path) -> None:
    manifest = RepositoryManifest(root=tmp_path)
    plan = discover_validation_plan(manifest, ["README.md"])

    def execute(command):
        return CommandResult(
            category=command.category,
            command=command.command,
            exit_code=0,
        )

    report, results = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Documentation explains setup"],
        criterion_evidence={
            "Documentation explains setup": ["README.md contains the setup section"]
        },
        executor=execute,
    )
    assert report.passed
    assert results[-1].category == "diff"


def test_failed_command_stops_ladder_and_recommends_fix(tmp_path: Path) -> None:
    manifest = RepositoryManifest(
        root=tmp_path,
        commands=[BuildCommand("test", "pytest", "pyproject.toml")],
    )
    plan = discover_validation_plan(manifest, [])

    def execute(command):
        return CommandResult(
            category=command.category,
            command=command.command,
            exit_code=1 if command.category == "test" else 0,
            stderr="1 failed",
        )

    report, results = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Tests pass"],
        criterion_evidence={"Tests pass": ["pytest"]},
        executor=execute,
    )
    assert not report.passed
    assert len(results) == 1
    assert report.tests_failed == 1
    assert report.recommended_next_action


def test_default_verifier_uses_configured_managed_sandbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sandbox = _RecordingSandbox(tmp_path)
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        "harness.verification.managed.create_command_sandbox",
        lambda root, state: sandbox,
    )
    plan = discover_validation_plan(
        RepositoryManifest(root=tmp_path),
        ["README.md"],
    )

    report, results = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Verification is managed"],
        criterion_evidence={"Verification is managed": ["git diff --check"]},
    )

    assert report.passed
    assert [result.status for result in results] == ["ok"]
    assert len(sandbox.requests) == 1
    assert sandbox.requests[0].command == "git diff --check"
    assert sandbox.requests[0].environment["UV_OFFLINE"] == "1"
