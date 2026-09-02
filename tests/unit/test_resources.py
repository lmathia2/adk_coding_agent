"""Resource inventory uses execution loaders, without model calls or task writes."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.config import settings_from_composition
from app.agent.factory import SkeinHarnessFactory
from app.agent.skills import build_skill_context
from harness.agent.resources import HarnessResources
from harness.config import RuntimeBindings, load_harness_composition
from harness.server.bootstrap import build_server_assembly
from harness.server.protocol import parse_client_message, parse_server_message


def skill(root: Path, name: str = "python-checks") -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Check Python changes\n---\nPRIVATE_SKILL_BODY\n")


def test_inventory_respects_project_trust_and_matches_runtime_selection(tmp_path: Path) -> None:
    skill(tmp_path / ".agents/skills")
    (tmp_path / "AGENTS.md").write_text("PRIVATE_PROJECT_INSTRUCTION")
    composition = load_harness_composition()
    bindings = RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state")
    factory = SkeinHarnessFactory()
    untrusted = factory.resources(composition, bindings)
    assert not any(item.kind in {"instruction", "skill", "skill_root"} for item in untrusted.items)
    assert "--trust-project" in untrusted.warnings[0]
    bindings = bindings.model_copy(update={"project_trusted": True})
    settings = settings_from_composition(composition, bindings)
    prefix = settings.static_prefix
    trusted = factory.resources(composition, bindings)
    assert factory.resources(composition, bindings) == trusted
    assert [item.name for item in trusted.items if item.kind == "skill"] == ["python-checks"]
    assert [item.path for item in trusted.items if item.kind == "instruction"] == [str(tmp_path / "AGENTS.md")]
    selected = build_skill_context(goal="$python-checks", next_action="", settings=settings)
    assert selected.selected_names == ("python-checks",)
    assert "PRIVATE_SKILL_BODY" in selected.text
    assert "PRIVATE" not in trusted.model_dump_json()
    assert settings.static_prefix == prefix
    assert not bindings.state_root.exists()


def test_configured_roots_work_without_project_trust_and_disabled_budgets_are_truthful(tmp_path: Path) -> None:
    root = tmp_path / "configured"
    skill(root)
    composition = load_harness_composition()
    config = composition.harness.config
    config = config.model_copy(update={"skills": config.skills.model_copy(update={"additional_roots": (Path("configured"),)})})
    composition = composition.model_copy(update={"harness": composition.harness.model_copy(update={"config": config})})
    bindings = RuntimeBindings(workspace=tmp_path, configuration_root=tmp_path, state_root=tmp_path / "state")
    factory = SkeinHarnessFactory()
    inventory = factory.resources(composition, bindings)
    assert any(item.kind == "skill" and item.status == "available" for item in inventory.items)
    disabled = config.model_copy(update={"context": config.context.model_copy(update={"skill_context_bytes": 0})})
    inventory = factory.resources(composition.model_copy(update={"harness": composition.harness.model_copy(update={"config": disabled})}), bindings)
    assert not any(item.kind == "skill" for item in inventory.items)
    assert any(item.kind == "skill_root" and item.status == "disabled" for item in inventory.items)
    (root / "python-checks/SKILL.md").write_text("INVALID PRIVATE_SKILL_BODY")
    inventory = factory.resources(composition, bindings)
    assert any("validation failed" in warning for warning in inventory.warnings)
    assert "PRIVATE_SKILL_BODY" not in inventory.model_dump_json()
    assert not any(item.kind == "skill" for item in inventory.items)


def test_inventory_is_deterministically_bounded(tmp_path: Path) -> None:
    for index in range(130):
        skill(tmp_path / ".agents/skills", f"fixture-{index:03}")
    factory = SkeinHarnessFactory()
    composition = load_harness_composition()
    bindings = RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", project_trusted=True)
    inventory = factory.resources(composition, bindings)
    assert inventory.truncated and len(inventory.items) == 128
    assert inventory == factory.resources(composition, bindings)
    assert len(inventory.model_dump_json().encode()) < 512_000
    assert not bindings.state_root.exists()


@pytest.mark.parametrize("fail", [False, True])
def test_authenticated_resource_discovery_does_not_block_ping_or_expose_failures(tmp_path: Path, monkeypatch, fail: bool) -> None:
    entered, release = threading.Event(), threading.Event()
    def slow(self, composition, bindings):
        entered.set()
        assert release.wait(5), "resource discovery prevented ping"
        if fail:
            raise RuntimeError("PRIVATE_RESOURCE_BODY")
        return HarnessResources()
    monkeypatch.setattr(SkeinHarnessFactory, "resources", slow)
    assembly = build_server_assembly(workspace=tmp_path, state_root=tmp_path / "state")
    token = assembly.auth_token_path.read_text().strip()
    try:
        with TestClient(assembly.app, client=("127.0.0.1", 12345)) as client:
            with client.websocket_connect("/v1/agent") as socket:
                assert socket.receive_json()["code"] == "authentication_failed"
            with client.websocket_connect("/v1/agent", headers={"authorization": f"Bearer {token}"}) as socket:
                socket.send_json({"type": "client.hello", "protocol_versions": [1], "client_name": "resource-fixture"})
                assert "resources" in socket.receive_json()["harness"]["capabilities"]
                message = {"type": "resource.request", "request_id": "inventory", "operation": "list"}
                parse_client_message(message)
                socket.send_json(message)
                assert entered.wait(5)
                socket.send_json({"type": "ping", "nonce": "responsive"})
                assert socket.receive_json()["nonce"] == "responsive"
                socket.send_json({**message, "request_id": "duplicate"})
                assert socket.receive_json()["code"] == "resource_request_busy"
                release.set()
                frame = socket.receive_json()
                parse_server_message(frame)
                assert frame["request_id"] == "inventory"
                assert "PRIVATE" not in json.dumps(frame)
                if fail:
                    assert frame["code"] == "resource_request_failed"
                else:
                    assert frame["data"]["workspace"] == str(tmp_path)
                    assert frame["data"]["state_root"] == str(tmp_path / "state")
                    assert frame["data"]["scope"] == "available_for_next_turn"
        assert assembly.coordinator.conversations.store.threads("local-user") == []
    finally:
        release.set()
