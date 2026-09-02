from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

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
    assert payload["harness"] == "skein_v1"
    assert payload["websocket_url"] == "ws://127.0.0.1:8765/v1/agent"
    assert payload["workspace"] == tmp_path.resolve().as_posix()
    assert payload["state_root"] == state_root.resolve().as_posix()
    assert payload["sandbox"] == "local"
    assert payload["production_mode"] is False
    assert payload["project_trusted"] is False
    assert (
        payload["auth_token_source"] == (state_root.resolve() / "server" / "auth-token").as_posix()
    )
    assert len(payload["config_sha256"]) == 64


def test_serve_print_config_announces_explicit_project_trust(
    tmp_path: Path,
    capsys,
) -> None:
    result = main(
        [
            "serve",
            "--workspace",
            str(tmp_path),
            "--state-root",
            str(tmp_path / "state"),
            "--trust-project",
            "--print-config",
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["project_trusted"] is True


def test_production_server_refuses_local_sandbox_before_creating_state(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"

    with pytest.raises(ValueError, match="production mode requires"):
        main(
            [
                "serve",
                "--workspace",
                str(tmp_path),
                "--state-root",
                str(state_root),
                "--config",
                str(DEFAULT_COMPOSITION_PATH),
                "--production",
                "--print-config",
            ]
        )

    assert not state_root.exists()


def test_serve_uses_adk_coding_config_when_flag_is_absent(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    payload = yaml.safe_load(DEFAULT_COMPOSITION_PATH.read_text(encoding="utf-8"))
    payload["server"]["port"] = 9_876
    config_path = tmp_path / "selected.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("SKEIN_CONFIG", str(config_path))

    result = main(
        [
            "serve",
            "--workspace",
            str(tmp_path),
            "--state-root",
            str(tmp_path / "state"),
            "--print-config",
        ]
    )

    assert result == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["websocket_url"] == "ws://127.0.0.1:9876/v1/agent"
