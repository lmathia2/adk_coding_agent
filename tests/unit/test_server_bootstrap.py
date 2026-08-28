from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.config import DEFAULT_COMPOSITION_PATH
from harness.server.bootstrap import build_server_assembly, require_loopback_host


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_host_is_accepted(host: str) -> None:
    assert require_loopback_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "example.com"])
def test_non_loopback_host_fails_closed(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        require_loopback_host(host)


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
    assert assembly.coordinator.descriptor.implementation == "pi_coding_v1"
    assert (state_root / "server" / "runs.db").is_file()
    assert (state_root / "adk" / "sessions.db").parent.is_dir()
