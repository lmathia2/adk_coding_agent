from __future__ import annotations

import importlib
from importlib.metadata import version
from types import SimpleNamespace

import pytest

from harness.adk import SteeringPlugin
from harness.telemetry.adk_plugin import HarnessMetricsPlugin
from harness.tracing import (
    CodingToolArtifactPlugin,
    HarnessTracePlugin,
    TraceContentMode,
)


def test_agents_cli_entrypoint_imports_with_adk_2x(monkeypatch, tmp_path) -> None:
    pytest.importorskip("google.adk")
    assert version("google-adk").split(".")[:2] == ["2", "7"]
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))

    module = importlib.import_module("app.agent")

    assert module.app.name == "pi_inspired_adk_coding_agent"
    assert module.root_agent.name == "coding_harness"
    assert module.app.root_agent is module.root_agent
    application = importlib.import_module("app.agent.application")
    assert module.coding_worker is application._ASSEMBLY.agents["coding_worker"]
    assert module.app.events_compaction_config.compaction_interval is None
    assert module.app.events_compaction_config.overlap_size is None
    assert module.app.events_compaction_config.token_threshold == 96_000
    assert module.app.events_compaction_config.event_retention_size == 20
    assert any(isinstance(plugin, HarnessMetricsPlugin) for plugin in module.app.plugins)
    assert any(isinstance(plugin, CodingToolArtifactPlugin) for plugin in module.app.plugins)
    assert any(isinstance(plugin, SteeringPlugin) for plugin in module.app.plugins)
    trace_plugin = next(
        plugin for plugin in module.app.plugins if isinstance(plugin, HarnessTracePlugin)
    )
    assert trace_plugin.content_mode == TraceContentMode.METADATA_ONLY
    tool_names = {
        getattr(tool, "name", getattr(tool, "__name__", "")) for tool in module.coding_worker.tools
    }
    assert {"read", "bash", "edit", "write"}.issubset(tool_names)

    workflow = importlib.import_module("app.agent.workflow")
    state: dict[str, object] = {}
    workflow._set_model_call_state(
        SimpleNamespace(state=state),
        task_id="task-1",
        dynamic_tokens=321,
        stable_prefix_hash="coding-prefix",
        static_prefix_tokens=99,
    )
    assert state["task_id"] == "task-1"
    assert state["dynamic_context_tokens_estimate"] == 321
    assert state["stable_instruction_sha256"]

    workflow._set_model_call_state(
        SimpleNamespace(state=state),
        task_id="task-1",
        dynamic_tokens=42,
        stable_prefix_hash="review-prefix",
        static_prefix_tokens=99,
    )
    assert state["stable_instruction_sha256"] == "review-prefix"
    assert state["static_prefix_tokens_estimate"] == 99

    skill_runtime = importlib.import_module("app.agent.skills")
    workflow._set_skill_state(
        SimpleNamespace(state=state),
        skill_runtime.SkillRuntimeContext(
            selected_names=("python-review",),
            selected_hashes=("a" * 64,),
        ),
    )
    assert state["selected_skill_names"] == ["python-review"]
    assert workflow._skill_runtime_from_state(SimpleNamespace(state=state)) == (
        skill_runtime.SkillRuntimeContext(
            selected_names=("python-review",),
            selected_hashes=("a" * 64,),
        )
    )


def test_agents_cli_uses_yaml_behavior_and_runtime_environment(monkeypatch, tmp_path) -> None:
    import yaml

    from harness.config import load_harness_composition

    config = importlib.import_module("app.agent.config")
    payload = load_harness_composition().model_dump(mode="json")
    payload["harness"]["config"]["context"]["skill_context_bytes"] = 12000
    path = tmp_path / "harness.yaml"
    path.write_text(yaml.safe_dump(payload))
    monkeypatch.setenv("ADK_CODING_CONFIG", str(path))
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADK_CODING_TRUST_PROJECT", "1")
    settings = config.load_settings()
    assert settings.skill_context_bytes == 12000
    assert settings.project_trusted
    assert settings.skill_roots == (tmp_path / ".agents" / "skills",)
    assert not settings.state_root.exists()


def test_removed_environment_behavior_fails_with_migration_guidance(monkeypatch, tmp_path) -> None:
    config = importlib.import_module("app.agent.config")
    monkeypatch.setenv("ADK_CODING_MODEL", "ignored-before-cleanup")
    with pytest.raises(ValueError, match="ADK_CODING_CONFIG YAML"):
        config.runtime_bindings_from_env(tmp_path)


def test_trace_initialization_failure_disables_optional_plugin(
    monkeypatch,
    caplog,
    tmp_path,
) -> None:
    factory = importlib.import_module("app.agent.factory")
    harness_config = importlib.import_module("harness.config")

    def fail_trace_plugin(**_kwargs):
        raise OSError("trace volume unavailable")

    monkeypatch.setattr(factory, "HarnessTracePlugin", fail_trace_plugin)
    composition = harness_config.load_harness_composition(
        config_models=factory.default_harness_registry().config_models()
    )
    assembly = factory.build_harness(
        composition,
        harness_config.RuntimeBindings(
            workspace=tmp_path,
            state_root=tmp_path / "state",
        ),
    )

    assert not any(
        isinstance(plugin, HarnessTracePlugin) for plugin in assembly.app.plugins
    )
    assert "tracing is disabled" in caplog.text


def test_skill_root_symlink_is_preserved_for_registry_validation(
    monkeypatch,
    tmp_path,
) -> None:
    config = importlib.import_module("app.agent.config")
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    (workspace / ".agents").mkdir(parents=True)
    external.mkdir()
    (workspace / ".agents" / "skills").symlink_to(external, target_is_directory=True)
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(workspace))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADK_CODING_TRUST_PROJECT", "1")

    settings = config.load_settings()

    assert settings.skill_roots[0].is_symlink()


def test_legacy_settings_do_not_load_project_instructions_without_trust(
    monkeypatch,
    tmp_path,
) -> None:
    config = importlib.import_module("app.agent.config")
    (tmp_path / "AGENTS.md").write_text(
        "UNTRUSTED PROJECT INSTRUCTION",
        encoding="utf-8",
    )
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ADK_CODING_TRUST_PROJECT", raising=False)

    settings = config.load_settings()

    assert not settings.project_trusted
    assert "UNTRUSTED PROJECT INSTRUCTION" not in settings.static_instruction
    assert tmp_path / ".agents" / "skills" not in settings.skill_roots
