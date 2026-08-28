from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from harness.config import (
    FOUR_CODING_TOOLS,
    RuntimeBindings,
    load_harness_composition,
    parse_harness_composition,
)


def _composition_payload() -> dict[str, Any]:
    harness_config = {
        "models": {
            "coding": {"provider": "google_adk", "name": "coding-model"},
            "review": {"provider": "google_adk", "name": "review-model"},
        },
        "agents": {
            "coding_worker": {
                "kind": "llm",
                "model": "coding",
                "prompt": {"source": "builtin", "name": "coding_worker_v1"},
                "tools": list(FOUR_CODING_TOOLS),
                "output_schema": "agent_step",
                "mode": "multi_turn",
            },
            "final_diff_reviewer": {
                "kind": "reviewer",
                "model": "review",
                "prompt": {"source": "builtin", "name": "final_diff_review_v1"},
                "tools": [],
                "output_schema": "final_diff_review",
                "mode": "single_turn",
            },
        },
        "workflow": {
            "entry": "initialize",
            "nodes": {
                "initialize": {"kind": "initialize", "next": "verify"},
                "verify": {
                    "kind": "verify",
                    "routes": {"passed": "review", "failed": "blocked"},
                },
                "review": {
                    "kind": "review",
                    "agent": "final_diff_reviewer",
                    "next": "finish",
                },
                "finish": {"kind": "finish"},
                "blocked": {"kind": "blocked"},
            },
        },
    }
    return {
        "schema_version": 1,
        "app": {"name": "coding_harness"},
        "harness": {
            "implementation": "pi_coding_v1",
            "api_version": 1,
            "config": harness_config,
        },
        "persistence": {
            "session_backend": "database",
            "session_database_url": {"env": "ADK_DATABASE_URL"},
        },
    }


def test_default_composition_is_strict_and_uses_the_four_tool_surface() -> None:
    composition = load_harness_composition()

    assert composition.schema_version == 1
    assert composition.harness.config.tools.visible == FOUR_CODING_TOOLS
    assert composition.server.protocol == "ag_ui_websocket_v1"
    assert "tui" not in type(composition.server).model_fields
    assert composition.harness.config.models["coding"].provider == "google_adk"


@pytest.mark.parametrize("removed_field", ["inbound_queue_size", "heartbeat_seconds"])
def test_server_rejects_settings_without_runtime_semantics(removed_field: str) -> None:
    payload = _composition_payload()
    payload["server"] = {removed_field: 20}

    with pytest.raises(ValidationError, match=removed_field):
        parse_harness_composition(payload)


def test_canonical_composition_hash_is_stable_across_mapping_order() -> None:
    payload = _composition_payload()
    reordered = dict(reversed(payload.items()))

    first = parse_harness_composition(payload)
    second = parse_harness_composition(reordered)

    assert first.canonical_json() == second.canonical_json()
    assert first.composition_sha256 == second.composition_sha256


def test_loader_preserves_portable_relative_resource_paths(tmp_path: Path) -> None:
    payload = _composition_payload()
    config = payload["harness"]["config"]
    config["agents"]["coding_worker"]["prompt"] = {
        "source": "file",
        "path": "prompts/coding.md",
    }
    config["skills"] = {"additional_roots": ["skills"]}
    config_path = tmp_path / "config" / "harness.yaml"
    config_path.parent.mkdir()
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    composition = load_harness_composition(config_path)

    prompt_path = composition.harness.config.agents["coding_worker"].prompt.path
    assert prompt_path == Path("prompts/coding.md")
    assert composition.harness.config.skills.additional_roots == (Path("skills"),)
    assert str(tmp_path) not in composition.canonical_json()


def test_resolved_behavior_hash_tracks_file_prompt_content(tmp_path: Path) -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["agents"]["coding_worker"]["prompt"] = {
        "source": "file",
        "path": "prompts/coding.md",
    }
    prompt = tmp_path / "prompts" / "coding.md"
    prompt.parent.mkdir()
    prompt.write_text("first instruction", encoding="utf-8")
    composition = parse_harness_composition(payload)

    first = composition.resolved_behavior_sha256(tmp_path)
    prompt.write_text("second instruction", encoding="utf-8")

    assert composition.resolved_behavior_sha256(tmp_path) != first


def test_behavior_hash_excludes_deployment_configuration() -> None:
    composition = parse_harness_composition(_composition_payload())
    moved = composition.model_copy(
        update={"server": composition.server.model_copy(update={"host": "0.0.0.0", "port": 9999})}
    )

    assert moved.composition_sha256 != composition.composition_sha256
    assert moved.behavior_sha256 == composition.behavior_sha256


def test_capability_order_does_not_change_behavior_hash() -> None:
    payload = _composition_payload()
    payload["harness"]["required_capabilities"] = ["streaming", "steering", "cancel"]
    reordered = _composition_payload()
    reordered["harness"]["required_capabilities"] = ["cancel", "streaming", "steering"]

    first = parse_harness_composition(payload)
    second = parse_harness_composition(reordered)

    assert first.behavior_sha256 == second.behavior_sha256


def test_unknown_configuration_fields_are_rejected() -> None:
    payload = _composition_payload()
    payload["surprise"] = True

    with pytest.raises(ValidationError, match="surprise"):
        parse_harness_composition(payload)


def test_workflow_cannot_reach_finish_without_verification() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["workflow"]["nodes"]["initialize"]["next"] = "finish"

    with pytest.raises(ValidationError, match="must pass through verify"):
        parse_harness_composition(payload)


def test_workflow_references_must_resolve() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["workflow"]["nodes"]["review"]["next"] = "missing"

    with pytest.raises(ValidationError, match="references missing nodes"):
        parse_harness_composition(payload)


def test_workflow_requires_a_reachable_finish_node() -> None:
    payload = _composition_payload()
    nodes = payload["harness"]["config"]["workflow"]["nodes"]
    del nodes["finish"]
    nodes["review"]["next"] = "blocked"

    with pytest.raises(ValidationError, match="finish node"):
        parse_harness_composition(payload)


@pytest.mark.parametrize(
    ("node_name", "node", "message"),
    [
        ("invoke", {"kind": "invoke_agent", "next": "verify"}, "agent"),
        ("parallel", {"kind": "parallel", "next": "verify"}, "agents"),
    ],
)
def test_workflow_node_kind_requires_its_operands(
    node_name: str,
    node: dict[str, object],
    message: str,
) -> None:
    payload = _composition_payload()
    nodes = payload["harness"]["config"]["workflow"]["nodes"]
    nodes["initialize"]["next"] = node_name
    nodes[node_name] = node

    with pytest.raises(ValidationError, match=message):
        parse_harness_composition(payload)


@pytest.mark.parametrize(
    "visible",
    [
        ["read", "bash", "edit"],
        ["bash", "read", "edit", "write"],
        ["read", "bash", "edit", "write", "read"],
    ],
)
def test_tool_surface_cannot_be_broadened_or_reordered(visible: list[str]) -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["tools"] = {"visible": visible}

    with pytest.raises(ValidationError, match="exactly read, bash, edit, write"):
        parse_harness_composition(payload)


def test_reviewer_cannot_receive_model_visible_tools() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["agents"]["final_diff_reviewer"]["tools"] = ["read"]

    with pytest.raises(ValidationError, match="cannot expose tools"):
        parse_harness_composition(payload)


def test_runtime_bindings_are_not_part_of_declarative_behavior(tmp_path: Path) -> None:
    composition = parse_harness_composition(_composition_payload())
    bindings = RuntimeBindings(
        workspace=tmp_path / "workspace",
        state_root=tmp_path / "state",
        source_repository=tmp_path / "source",
        task_id="task-123",
    )

    assert bindings.task_id == "task-123"
    serialized = composition.canonical_json()
    assert "task-123" not in serialized
    assert str(tmp_path) not in serialized


def test_secret_values_cannot_be_embedded_in_composition() -> None:
    payload = _composition_payload()
    payload["persistence"]["session_database_url"] = {
        "env": "ADK_DATABASE_URL",
        "value": "postgresql://user:secret@example.invalid/db",
    }

    with pytest.raises(ValidationError, match="value"):
        parse_harness_composition(payload)


def test_search_default_page_size_cannot_exceed_maximum() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["tools"] = {
        "search": {"default_page_size": 40, "max_page_size": 20},
    }

    with pytest.raises(ValidationError, match="default search page size"):
        parse_harness_composition(payload)


def test_missing_file_prompt_path_is_a_validation_error(tmp_path: Path) -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["agents"]["coding_worker"]["prompt"] = {
        "source": "file"
    }
    config_path = tmp_path / "harness.yaml"
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="file prompts require a path"):
        load_harness_composition(config_path)


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "harness.yaml"
    config_path.write_text(
        "schema_version: 1\nschema_version: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_harness_composition(config_path)
