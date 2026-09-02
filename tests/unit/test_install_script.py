from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("protected", ["venv-symlink", "command-file"])
def test_install_refuses_to_overwrite_unowned_paths(tmp_path: Path, protected: str) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    script = checkout / "install.sh"
    shutil.copy2(Path(__file__).resolve().parents[2] / "install.sh", script)
    commands = tmp_path / "commands"
    commands.mkdir()
    prerequisites = tmp_path / "prerequisites"
    prerequisites.mkdir()
    for name in ("uv", "git"):
        command = prerequisites / name
        command.write_text("#!/bin/sh\nexit 99\n")
        command.chmod(0o755)
    untouched = tmp_path / "unowned"
    untouched.mkdir()
    sentinel = untouched / "keep"
    sentinel.write_text("preserve me")
    if protected == "venv-symlink":
        (checkout / ".venv").symlink_to(untouched, target_is_directory=True)
    else:
        (commands / "skein").write_text("user-owned command")
    result = subprocess.run(
        [str(script), "--minimal", "--bin-dir", str(commands)],
        env={**os.environ, "PATH": str(prerequisites) + os.pathsep + os.environ["PATH"]},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "refusing" in result.stderr
    assert sentinel.read_text() == "preserve me"
    if protected == "command-file":
        assert (commands / "skein").read_text() == "user-owned command"


def test_install_script_is_executable_and_has_valid_help() -> None:
    root = Path(__file__).resolve().parents[2]
    script = root / "install.sh"

    assert os.access(script, os.X_OK)
    subprocess.run(("sh", "-n", str(script)), check=True)
    completed = subprocess.run(
        (str(script), "--help"),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--magnitude" not in completed.stdout
    assert "--minimal" in completed.stdout
    assert "--plan" in completed.stdout
    assert "--tui" in completed.stdout


def test_install_script_reports_platform_aware_plan_without_installing() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        (str(root / "install.sh"), "--plan"),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Detected platform:" in completed.stdout
    assert "Installation plan:" in completed.stdout
    assert f"Python environment: {root}/.venv" in completed.stdout
    assert "remove and recreate on every installation" in completed.stdout
    assert "Runtime launcher:" in completed.stdout
    assert "Launch workspace: selected at runtime" in completed.stdout


def test_install_script_macos_plan_includes_full_local_tui_stack(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uname = fake_bin / "uname"
    fake_uname.write_text("#!/bin/sh\nprintf '%s\\n' Darwin\n", encoding="utf-8")
    fake_uname.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

    completed = subprocess.run(
        (str(root / "install.sh"), "--plan"),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert "Detected platform: macOS" in completed.stdout
    assert "Magnitude:" not in completed.stdout
    assert "Pi-style terminal: 1" in completed.stdout
    assert "Node.js" in (root / "install.sh").read_text(encoding="utf-8")
    assert "Go 1.24" not in (root / "install.sh").read_text(encoding="utf-8")


def test_install_script_rejects_removed_options() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        (str(root / "install.sh"), "--magnitude", "--no-local-models"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "unknown option" in completed.stderr
