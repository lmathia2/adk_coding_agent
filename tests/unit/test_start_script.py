from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _fake_command(directory: Path, name: str) -> Path:
    command = directory / name
    command.write_text(
        "#!/bin/sh\n"
        "printf 'token-present=%s\\n' \"${ADK_CODING_AGENT_TOKEN:+yes}\"\n"
        "printf 'arg=%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    return command


def _environment(fake_bin: Path, home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment.pop("ADK_CODING_AGENT_STATE_ROOT", None)
    environment.pop("ADK_CODING_AGENT_SERVER_URL", None)
    environment.pop("ADK_CODING_AGENT_TOKEN", None)
    return environment


def _fake_managed_server(directory: Path) -> Path:
    command = directory / "adk-coding-agent"
    command.write_text(
        "#!/bin/sh\n"
        "state_root=\n"
        "model=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --state-root) state_root=$2; shift 2 ;;\n"
        "    --model) model=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "mkdir -p \"$state_root/server\"\n"
        "printf 'managed-test-token\\n' >\"$state_root/server/auth-token\"\n"
        "printf 'model=%s\\n' \"$model\"\n"
        "trap 'exit 0' TERM INT HUP\n"
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    return command


def test_start_script_is_executable_and_has_valid_help() -> None:
    root = Path(__file__).resolve().parents[2]
    script = root / "start.sh"

    assert os.access(script, os.X_OK)
    subprocess.run(("sh", "-n", str(script)), check=True)
    completed = subprocess.run(
        (str(script), "--help"),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "adk-agent-start server" in completed.stdout
    assert "adk-agent-start tui" in completed.stdout
    assert "adk-agent-start run" in completed.stdout
    assert "ADK_CODING_AGENT_STATE_ROOT" in completed.stdout
    assert "ADK_CODING_AGENT_SERVER_URL" in completed.stdout


def test_server_uses_current_directory_and_shared_default_state(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_command(fake_bin, "adk-coding-agent")
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    completed = subprocess.run(
        (str(root / "start.sh"), "server"),
        cwd=workspace,
        env=_environment(fake_bin, home),
        check=True,
        capture_output=True,
        text=True,
    )

    state_root = home / ".local/state/adk-coding-agent"
    assert state_root.is_dir()
    assert f"State root: {state_root}" in completed.stdout
    assert f"Auth token file: {state_root}/server/auth-token" in completed.stdout
    assert f"Workspace: {workspace}" in completed.stdout
    assert "Environment set: none" in completed.stdout
    assert "arg=serve-magnitude" in completed.stdout
    assert f"arg={workspace}" in completed.stdout
    assert f"arg={state_root}" in completed.stdout


def test_server_accepts_flag_alias_and_state_environment_override(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_command(fake_bin, "adk-coding-agent")
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "shared-state"
    environment = _environment(fake_bin, home)
    environment["ADK_CODING_AGENT_STATE_ROOT"] = str(state_root)

    completed = subprocess.run(
        (str(root / "start.sh"), "--server", "--workspace", str(workspace)),
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"State root: {state_root}" in completed.stdout
    assert "TUI WebSocket URL: ws://127.0.0.1:8765/v1/agent" in completed.stdout
    assert f"arg={workspace}" in completed.stdout


def test_server_forwards_and_announces_explicit_project_trust(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_command(fake_bin, "adk-coding-agent")
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    completed = subprocess.run(
        (
            str(root / "start.sh"),
            "server",
            "--workspace",
            str(workspace),
            "--trust-project",
        ),
        env=_environment(fake_bin, home),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Project instructions/skills trusted: 1" in completed.stdout
    assert "arg=--trust-project" in completed.stdout


def test_tui_reads_token_and_does_not_expose_it(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_command(fake_bin, "adk-agent-tui")
    home = tmp_path / "home"
    home.mkdir()
    state_root = home / ".local/state/adk-coding-agent"
    token_file = state_root / "server/auth-token"
    token_file.parent.mkdir(parents=True)
    secret = "a-secret-token-that-must-not-be-printed"
    token_file.write_text(f"{secret}\n", encoding="utf-8")
    environment = _environment(fake_bin, home)
    environment["ADK_CODING_AGENT_SERVER_URL"] = "ws://localhost:9999/custom"

    completed = subprocess.run(
        (str(root / "start.sh"), "tui", "--", "--input", "hello"),
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert secret not in completed.stdout
    assert "Environment set for TUI: ADK_CODING_AGENT_TOKEN" in completed.stdout
    assert "token-present=yes" in completed.stdout
    assert "arg=ws://localhost:9999/custom" in completed.stdout
    assert "arg=--input" in completed.stdout
    assert "arg=hello" in completed.stdout


def test_tui_explains_missing_token(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_command(fake_bin, "adk-agent-tui")
    home = tmp_path / "home"
    home.mkdir()

    completed = subprocess.run(
        (str(root / "start.sh"), "tui"),
        env=_environment(fake_bin, home),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "auth token not found" in completed.stderr
    assert "start the server first" in completed.stderr


def test_run_owns_server_and_forwards_exact_magnitude_model(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_managed_server(fake_bin)
    _fake_command(fake_bin, "adk-agent-tui")
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "managed-state"
    model = "qwen/local-coder:q8"

    completed = subprocess.run(
        (
            str(root / "start.sh"),
            "run",
            "--workspace",
            str(workspace),
            "--state-root",
            str(state_root),
            "--model",
            model,
            "--",
            "--input",
            "inspect this repository",
        ),
        env=_environment(fake_bin, home),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert f"Workspace: {workspace}" in completed.stdout
    assert "Model provider: magnitude" in completed.stdout
    assert f"Model: {model}" in completed.stdout
    assert f"Server log: {state_root}/server/foreground.log" in completed.stdout
    assert "Lifecycle: this command owns and stops its harness server child" in completed.stdout
    assert "token-present=yes" in completed.stdout
    assert "arg=--input" in completed.stdout
    assert "arg=inspect this repository" in completed.stdout
    assert model in (state_root / "server/foreground.log").read_text(encoding="utf-8")
    assert "managed-test-token" not in completed.stdout


def test_server_can_select_codex_provider_without_login(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_command(fake_bin, "adk-coding-agent")
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    completed = subprocess.run(
        (
            str(root / "start.sh"),
            "server",
            "--provider",
            "codex",
            "--workspace",
            str(workspace),
        ),
        env=_environment(fake_bin, home),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Model provider: codex" in completed.stdout
    assert "arg=serve-codex" in completed.stdout
