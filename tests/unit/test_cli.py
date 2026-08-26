from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness.cli import main, prepare_run


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
