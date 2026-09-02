from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml

from harness.config import DEFAULT_COMPOSITION_PATH
from harness.server.bootstrap import (
    LOCAL_TOKEN_ENV,
    build_server_assembly,
    load_or_create_local_auth_token,
    require_loopback_host,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_host_is_accepted(host: str) -> None:
    assert require_loopback_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "example.com"])
def test_non_loopback_host_fails_closed(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        require_loopback_host(host)


def test_local_auth_token_is_generated_once_with_private_permissions(tmp_path: Path) -> None:
    first, first_path = load_or_create_local_auth_token(tmp_path, environment={})
    second, second_path = load_or_create_local_auth_token(tmp_path, environment={})

    assert len(first.encode("utf-8")) >= 32
    assert second == first
    assert second_path == first_path == tmp_path / "server" / "auth-token"
    assert first_path is not None
    assert stat.S_IMODE(first_path.stat().st_mode) == 0o600


def test_explicit_local_auth_token_stays_environment_only(tmp_path: Path) -> None:
    explicit = "x" * 32
    token, token_path = load_or_create_local_auth_token(
        tmp_path,
        environment={LOCAL_TOKEN_ENV: explicit},
    )

    assert token == explicit
    assert token_path is None
    assert not (tmp_path / "server" / "auth-token").exists()


def test_short_explicit_local_auth_token_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 32"):
        load_or_create_local_auth_token(
            tmp_path,
            environment={LOCAL_TOKEN_ENV: "short"},
        )


def test_server_assembly_maps_yaml_and_creates_durable_local_services(
    tmp_path: Path,
) -> None:
    payload = yaml.safe_load(DEFAULT_COMPOSITION_PATH.read_text(encoding="utf-8"))
    payload["server"].update(
        {
            "websocket_path": "/custom/agent",
            "max_connections": 7,
            "outbound_queue_size": 19,
        }
    )
    config_path = tmp_path / "harness.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    state_root = tmp_path / "state"

    assembly = build_server_assembly(
        workspace=tmp_path,
        state_root=state_root,
        config_path=config_path,
    )

    server = assembly.app.state.agent_websocket_server
    assert server.settings.path == "/custom/agent"
    assert server.settings.max_connections == 7
    assert server.settings.outbound_queue_capacity == 19
    assert assembly.coordinator.broker.queue_capacity == 19
    assert assembly.coordinator.descriptor.implementation == "skein_v1"
    assert (state_root / "server" / "runs.db").is_file()
    assert assembly.auth_token_path == state_root / "server" / "auth-token"
    assert assembly.auth_token_path.is_file()
    assert (state_root / "adk" / "sessions.db").parent.is_dir()
