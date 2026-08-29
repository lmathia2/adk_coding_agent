from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from harness.config import PiCodingConfig, load_harness_composition
from harness.magnitude import (
    MAGNITUDE_API_KEY,
    MagnitudeConnectionError,
    prepare_magnitude_connection,
    select_model_id,
)


def test_prepare_magnitude_connection_uses_selected_model_and_writes_valid_config(
    tmp_path: Path,
) -> None:
    magnitude_state = tmp_path / "models.json"
    magnitude_state.write_text(
        json.dumps(
            {
                "slots": {
                    "primary": {
                        "providerId": "local",
                        "providerModelId": "qwen/local-q8",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    connection = prepare_magnitude_connection(
        state_root=tmp_path / "state",
        magnitude_state_path=magnitude_state,
        reasoning="none",
        start_service=False,
        fetch_json=lambda _url, _timeout: {
            "models": [{"id": "other/local-q4"}, {"id": "qwen/local-q8"}]
        },
    )

    assert connection.model_id == "qwen/local-q8"
    assert connection.config_path.stat().st_mode & 0o777 == 0o600
    composition = load_harness_composition(connection.config_path)
    assert isinstance(composition.harness.config, PiCodingConfig)
    coding = composition.harness.config.models["coding"]
    assert coding.provider == "openai_compatible"
    assert coding.name == "qwen/local-q8"
    assert coding.reasoning == "none"
    assert coding.base_url == "http://127.0.0.1:10100/inference/v1"
    assert MAGNITUDE_API_KEY not in connection.config_path.read_text(encoding="utf-8")


def test_prepare_magnitude_connection_starts_service_when_initial_probe_fails(
    tmp_path: Path,
) -> None:
    calls = 0
    commands: list[tuple[str, ...]] = []

    def fetch(_url: str, _timeout: float) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MagnitudeConnectionError("not running")
        return {"data": [{"id": "local-model"}]}

    def run(command) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        stdout = "0.0.8\n" if tuple(command) == ("magnitude", "--version") else "ready"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    connection = prepare_magnitude_connection(
        state_root=tmp_path / "state",
        fetch_json=fetch,
        command_runner=run,
    )

    assert connection.model_id == "local-model"
    assert commands == [
        ("magnitude", "--version"),
        ("magnitude", "server", "start"),
    ]


def test_prepare_magnitude_connection_accepts_service_that_outlives_start_timeout(
    tmp_path: Path,
) -> None:
    calls = 0

    def fetch(_url: str, _timeout: float) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MagnitudeConnectionError("not running")
        return {"data": [{"id": "local-model"}]}

    def run(command) -> subprocess.CompletedProcess[str]:
        if tuple(command) == ("magnitude", "--version"):
            return subprocess.CompletedProcess(command, 0, stdout="0.0.8\n", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="Unable to connect. Is the computer able to access the url?",
        )

    connection = prepare_magnitude_connection(
        state_root=tmp_path / "state",
        fetch_json=fetch,
        command_runner=run,
    )

    assert connection.model_id == "local-model"


def test_model_catalog_errors_are_not_retried(tmp_path: Path) -> None:
    calls = 0

    def no_models(_url: str, _timeout: float) -> object:
        nonlocal calls
        calls += 1
        return {"data": []}

    with pytest.raises(MagnitudeConnectionError, match="no installed local model"):
        prepare_magnitude_connection(
            state_root=tmp_path / "state",
            start_service=False,
            fetch_json=no_models,
        )

    assert calls == 1


def test_prepare_magnitude_connection_rejects_release_without_external_service(
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    def unavailable(_url: str, _timeout: float) -> object:
        raise MagnitudeConnectionError("not running")

    def run(command) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout="0.0.6\n", stderr="")

    with pytest.raises(MagnitudeConnectionError, match=r"0\.0\.8\+.*found 0\.0\.6"):
        prepare_magnitude_connection(
            state_root=tmp_path / "state",
            fetch_json=unavailable,
            command_runner=run,
        )

    assert commands == [("magnitude", "--version")]


def test_explicit_unavailable_magnitude_model_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(MagnitudeConnectionError, match="available: local-a, local-b"):
        select_model_id(
            ("local-a", "local-b"),
            requested="missing",
            state_path=tmp_path / "missing.json",
        )


def test_generated_magnitude_yaml_preserves_four_tool_surface(tmp_path: Path) -> None:
    connection = prepare_magnitude_connection(
        state_root=tmp_path / "state",
        requested_model="local-model",
        start_service=False,
        fetch_json=lambda _url, _timeout: {"models": ["local-model"]},
    )
    payload = yaml.safe_load(connection.config_path.read_text(encoding="utf-8"))

    assert payload["harness"]["config"]["tools"]["visible"] == [
        "read",
        "bash",
        "edit",
        "write",
    ]
