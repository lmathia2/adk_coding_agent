from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from harness.cli import main, prepare_run
from harness.learning import SkillDraft, SkillRegistry
from harness.tracing import TraceSpan, TraceStore


def _repository(root: Path) -> Path:
    root.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=root, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=root, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=root, check=True)
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=root, check=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=root, check=True)
    return root


def test_prepare_run_sets_workspace_identity_environment(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository")
    preparation = prepare_run(
        repository=repository,
        task_id="task-123",
        prompt="Fix the app",
        state_root=tmp_path / "state",
        harness_root=tmp_path,
    )

    assert preparation.workspace.path.exists()
    assert preparation.environment["ADK_CODING_WORKSPACE"] == preparation.workspace.path.as_posix()
    assert preparation.environment["ADK_CODING_BASE_REVISION"]
    assert preparation.command == ("agents-cli", "run", "Fix the app")
    payload = json.loads(preparation.to_json())
    assert payload["workspace"]["task_id"] == "task-123"


def test_prepare_cli_prints_machine_readable_launch_contract(
    tmp_path: Path, capsys
) -> None:
    repository = _repository(tmp_path / "repository")
    exit_code = main(
        [
            "prepare",
            "--repository",
            str(repository),
            "--task-id",
            "task-123",
            "--state-root",
            str(tmp_path / "state"),
            "Fix the app",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == ["agents-cli", "run", "Fix the app"]


def test_trace_export_and_learned_skill_controls(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    traces = TraceStore(state / "traces.db")
    traces.append(
        TraceSpan(
            span_id="span-1",
            task_id="task-1",
            sequence=1,
            correlation_id="run-1",
            category="tool",
            phase="success",
            name="read",
            timestamp=datetime.now(UTC).isoformat(),
            content_hash="a" * 64,
            payload_json='{"type":"object"}',
            idempotency_key="span-1",
        )
    )
    assert main(
        ["trace-export", "--state-root", str(state), "--task-id", "task-1"]
    ) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["task_id"] == "task-1"

    registry = SkillRegistry(state / "learned-skills")
    registry.emit_candidate(
        SkillDraft(
            name="learned-example",
            description="A learned example.",
            instructions="Verify the result.",
            source_trace_ids=("trace-1",),
        )
    )
    assert main(["learned-skills", "--state-root", str(state)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["status"] == "candidate"
    assert main(
        ["disable-skill", "--state-root", str(state), "learned-example"]
    ) == 0
    disabled = json.loads(capsys.readouterr().out)
    assert disabled["status"] == "disabled"


def test_steer_cli_queues_without_exposing_content_and_reports_status(
    tmp_path: Path,
    capsys,
) -> None:
    state = tmp_path / "state"
    assert main(
        [
            "steer",
            "--state-root",
            str(state),
            "--task-id",
            "task-1",
            "--idempotency-key",
            "user-message-1",
            "Use the public parser API",
        ]
    ) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["delivery"] == "next_model_boundary"
    assert receipt["message"]["status"] == "queued"
    assert "content" not in receipt["message"]

    assert main(
        [
            "steering-status",
            "--state-root",
            str(state),
            "--task-id",
            "task-1",
        ]
    ) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["pending"] is True
    assert status["counts"] == {"acked": 0, "leased": 0, "queued": 1}
    assert "content" not in status["messages"][0]

    assert main(
        [
            "steering-status",
            "--state-root",
            str(state),
            "--task-id",
            "task-1",
            "--include-content",
        ]
    ) == 0
    revealed = json.loads(capsys.readouterr().out)
    assert revealed["messages"][0]["content"] == "Use the public parser API"
