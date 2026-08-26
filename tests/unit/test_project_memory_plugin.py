from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("google.adk")

from harness.memory.adk_plugin import VerifiedProjectMemoryPlugin  # noqa: E402
from harness.models.ledger import TaskLedger  # noqa: E402
from harness.models.verification import (  # noqa: E402
    CriterionEvidence,
    VerificationReport,
)
from harness.state import EventKind, JsonlEventStore  # noqa: E402


@dataclass
class _Context:
    state: dict[str, object]


def _repository(root: Path) -> Path:
    root.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=root, check=True)
    subprocess.run(
        ("git", "config", "user.email", "test@example.com"),
        cwd=root,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Test"),
        cwd=root,
        check=True,
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='memory-fixture'\nversion='0.0.0'\n"
        "[tool.pytest.ini_options]\naddopts='-q'\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("Run tests before completion.\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=root, check=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=root, check=True)
    return root


def test_plugin_writes_memory_only_after_verified_completion(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repository")
    state = tmp_path / "state"
    task_id = "task-1"
    ledger = TaskLedger(
        task_id=task_id,
        goal="Improve authentication",
        acceptance_criteria=["Tests pass"],
        base_revision="abc",
        workspace_id="workspace",
        branch_id="main",
    )
    report = VerificationReport(
        passed=True,
        criteria=[
            CriterionEvidence(
                criterion="Tests pass",
                satisfied=True,
                evidence=["pytest"],
            )
        ],
        commands_run=["pytest"],
        tests_passed=1,
        tests_failed=0,
    )
    events = JsonlEventStore(state / "events")
    events.append(
        task_id,
        EventKind.TASK_CREATED,
        {"ledger": ledger.model_dump(mode="json")},
    )
    events.append(
        task_id,
        EventKind.VERIFICATION_COMPLETED,
        {"report": report.model_dump(mode="json")},
    )
    events.append(task_id, EventKind.TASK_FINISHED, {"verification": {}})

    plugin = VerifiedProjectMemoryPlugin(
        workspace=workspace,
        state_root=state,
        project_root=workspace,
    )
    asyncio.run(
        plugin.after_run_callback(
            invocation_context=_Context(state={"task_id": task_id})
        )
    )

    context = plugin.memories.render_context(
        plugin.project_id,
        "run tests conventions",
    )
    assert "Canonical test command" in context
    assert "AGENTS.md" in context
