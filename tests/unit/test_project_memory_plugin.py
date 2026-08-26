from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("google.adk")

from google.adk.sessions.state import State

from harness.memory.adk_plugin import VerifiedProjectMemoryPlugin
from harness.models.ledger import TaskLedger
from harness.models.verification import (
    CriterionEvidence,
    VerificationReport,
)
from harness.state import EventKind, JsonlEventStore


@dataclass
class _Context:
    state: Any


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


def _record_verified_task(
    events: JsonlEventStore,
    task_id: str,
    *,
    finished: bool,
) -> None:
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
    if finished:
        events.append(task_id, EventKind.TASK_FINISHED, {"verification": {}})


def test_plugin_does_not_write_memory_before_task_finished(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repository")
    state = tmp_path / "state"
    task_id = "task-1"
    _record_verified_task(JsonlEventStore(state / "events"), task_id, finished=False)
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

    assert plugin.memories.search(plugin.project_id, "run tests") == []


def test_plugin_writes_memory_only_after_verified_completion(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repository")
    state = tmp_path / "state"
    task_id = "task-1"
    events = JsonlEventStore(state / "events")
    _record_verified_task(events, task_id, finished=True)

    plugin = VerifiedProjectMemoryPlugin(
        workspace=workspace,
        state_root=state,
        project_root=workspace,
    )
    asyncio.run(
        plugin.after_run_callback(
            invocation_context=_Context(
                state=State(value={"task_id": task_id}, delta={})
            )
        )
    )

    context = plugin.memories.render_context(
        plugin.project_id,
        "run tests conventions",
    )
    assert "Canonical test command" in context
    assert "AGENTS.md" in context
