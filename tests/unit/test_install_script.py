from __future__ import annotations

import os
import subprocess
from pathlib import Path


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

    assert "--no-local-models" in completed.stdout
    assert "--magnitude" in completed.stdout
    assert "--tui" in completed.stdout


def test_install_script_rejects_magnitude_without_local_model_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        (str(root / "install.sh"), "--magnitude", "--no-local-models"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "cannot be combined" in completed.stderr
