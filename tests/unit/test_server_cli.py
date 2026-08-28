from __future__ import annotations

import json
from pathlib import Path

from harness.cli import main
from harness.config import DEFAULT_COMPOSITION_PATH


def test_serve_print_config_resolves_composition_without_listening(
    tmp_path: Path,
    capsys,
) -> None:
    state_root = tmp_path / "state"

    result = main(
        [
            "serve",
            "--workspace",
            str(tmp_path),
            "--state-root",
            str(state_root),
            "--config",
            str(DEFAULT_COMPOSITION_PATH),
            "--print-config",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["harness"] == "pi_coding_v1"
    assert payload["websocket_url"] == "ws://127.0.0.1:8765/v1/agent"
    assert payload["workspace"] == tmp_path.resolve().as_posix()
    assert payload["state_root"] == state_root.resolve().as_posix()
    assert len(payload["config_sha256"]) == 64
