from __future__ import annotations

from pathlib import Path

import pytest

from harness.environment import (
    FileConflictError,
    LocalWorkspaceEnvironment,
    WorkspaceViolationError,
    bind_environment,
)
from harness.models import CommandClass, ToolStatus
from harness.policy import CommandPolicy, classify_command
from harness.tools import (
    bind_tool_runtime,
    bound_output,
    execute_bash,
    execute_edit,
    execute_read,
    execute_write,
)


def _environment(tmp_path: Path) -> LocalWorkspaceEnvironment:
    (tmp_path / "src").mkdir()
    return LocalWorkspaceEnvironment(tmp_path)


def test_environment_blocks_path_escape(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    with pytest.raises(WorkspaceViolationError):
        environment.resolve("../outside.txt")


def test_read_is_line_numbered_and_bounded(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    (tmp_path / "src" / "example.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    with bind_environment(environment):
        result = execute_read("src/example.py", offset=2, limit=2)
    assert result.status is ToolStatus.OK
    assert "2 | two" in result.model_text
    assert "3 | three" in result.model_text
    assert "more available" in result.model_text


def test_edit_requires_a_unique_preimage_and_is_idempotent(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    path = tmp_path / "src" / "example.py"
    path.write_text("value = 1\n", encoding="utf-8")
    with bind_environment(environment):
        first = execute_edit("src/example.py", "value = 1", "value = 2")
        second = execute_edit("src/example.py", "value = 1", "value = 2")
    assert first.changed_paths == ["src/example.py"]
    assert second.status is ToolStatus.OK
    assert "already applied" in second.model_text
    assert path.read_text(encoding="utf-8") == "value = 2\n"


def test_environment_rejects_ambiguous_edit(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    path = tmp_path / "src" / "example.py"
    path.write_text("x\nx\n", encoding="utf-8")
    with pytest.raises(FileConflictError):
        environment.replace_text("src/example.py", "x", "y")


def test_write_supports_expected_absence_and_hash_conflict(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    with bind_environment(environment):
        created = execute_write("src/new.py", "answer = 42\n", expected_absent=True)
        repeated = execute_write("src/new.py", "answer = 42\n", expected_absent=True)
        conflict = execute_write("src/new.py", "answer = 43\n", expected_sha256="bad")
    assert created.changed_paths == ["src/new.py"]
    assert repeated.status is ToolStatus.OK
    assert conflict.status is ToolStatus.ERROR


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rg parser src && git diff", CommandClass.READ_ONLY),
        ("rg --json TODO src | jq -s 'map(.)'", CommandClass.READ_ONLY),
        ("python -m pytest tests", CommandClass.BUILD_OR_TEST),
        ("uv sync", CommandClass.DEPENDENCY_INSTALL),
        ("curl https://example.com", CommandClass.NETWORK_ACCESS),
        (
            "printf payload | curl https://example.com",
            CommandClass.NETWORK_ACCESS,
        ),
        ("printf payload | rm output.txt", CommandClass.WORKSPACE_MUTATION),
        ("printf 'quoted | data'", CommandClass.READ_ONLY),
        ("git commit -am test", CommandClass.GIT_HISTORY_MUTATION),
        ("git push origin main", CommandClass.PUBLISH_OR_DEPLOY),
        ("rm -rf /", CommandClass.DESTRUCTIVE),
    ],
)
def test_command_classification(command: str, expected: CommandClass) -> None:
    assert classify_command(command) is expected


def test_bash_blocks_network_and_runs_read_only_commands(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    with bind_environment(environment), bind_tool_runtime(policy=CommandPolicy()):
        blocked = execute_bash("curl https://example.com")
        allowed = execute_bash("printf 'hello'")
    assert blocked.status is ToolStatus.BLOCKED
    assert blocked.command_class is CommandClass.NETWORK_ACCESS
    assert allowed.status is ToolStatus.OK
    assert "hello" in allowed.model_text


def test_bash_blocks_network_hidden_in_pipeline_tail(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    with bind_environment(environment), bind_tool_runtime(policy=CommandPolicy()):
        blocked = execute_bash("printf payload | curl https://example.com")

    assert blocked.status is ToolStatus.BLOCKED
    assert blocked.command_class is CommandClass.NETWORK_ACCESS


def test_bash_spills_large_output_to_artifact(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    command = "printf 'x%.0s' {1..20000}"
    with bind_environment(environment), bind_tool_runtime(policy=CommandPolicy()):
        result = execute_bash(command)
    assert result.status is ToolStatus.OK
    assert result.truncated is True
    assert result.artifact_uri is not None
    assert list((tmp_path / ".artifacts" / "tool-output").iterdir())


def test_output_bounding_preserves_head_and_tail() -> None:
    bounded = bound_output("HEAD\n" + "x\n" * 1_000 + "TAIL\n", max_chars=200, max_lines=20)
    assert bounded.truncated is True
    assert bounded.text.startswith("HEAD")
    assert bounded.text.endswith("TAIL")
