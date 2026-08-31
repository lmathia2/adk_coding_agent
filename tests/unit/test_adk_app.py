from __future__ import annotations

import importlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from types import SimpleNamespace

import pytest

from harness.adk import SteeringPlugin
from harness.state.postgres import TaskLease
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


def test_control_state_settings_are_environment_driven(monkeypatch, tmp_path) -> None:
    config = importlib.import_module("app.agent.config")
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv(
        "ADK_CODING_CONTROL_DATABASE_URL",
        "postgresql://control.example/harness",
    )
    monkeypatch.setenv("ADK_CODING_WORKER_ID", "worker-a")
    monkeypatch.setenv("ADK_CODING_TASK_LEASE_SECONDS", "45")
    monkeypatch.setenv("ADK_CODING_TRACE_MODE", "metadata")
    monkeypatch.setenv("ADK_CODING_TRACE_MAX_CONTENT_BYTES", "2048")
    monkeypatch.setenv(
        "ADK_CODING_SKILL_DIRS",
        f"{tmp_path / 'team-skills'}{os.pathsep}{tmp_path / 'personal-skills'}",
    )
    monkeypatch.setenv("ADK_CODING_SKILL_MAX_SELECTED", "2")
    monkeypatch.setenv("ADK_CODING_SKILL_CONTEXT_BYTES", "12000")
    monkeypatch.setenv("ADK_CODING_TRUST_PROJECT", "1")

    settings = config.load_settings()

    assert settings.control_database_url == "postgresql://control.example/harness"
    assert settings.worker_id == "worker-a"
    assert settings.task_lease_seconds == 45
    assert settings.trace_mode == "metadata"
    assert settings.trace_max_content_bytes == 2048
    assert settings.skill_roots == (
        (tmp_path / ".agents" / "skills").resolve(),
        (tmp_path / "team-skills").resolve(),
        (tmp_path / "personal-skills").resolve(),
    )
    assert settings.skill_max_selected == 2
    assert settings.skill_context_bytes == 12000


def test_invalid_trace_mode_fails_closed(monkeypatch, tmp_path) -> None:
    config = importlib.import_module("app.agent.config")
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADK_CODING_TRUST_PROJECT", "1")
    monkeypatch.setenv("ADK_CODING_TRACE_MODE", "raw")

    with pytest.raises(ValueError, match="off, metadata, redacted"):
        config.load_settings()


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


def test_interval_compaction_requires_explicit_positive_configuration(
    monkeypatch,
) -> None:
    application = importlib.import_module("app.agent.application")
    monkeypatch.delenv("ADK_CODING_COMPACTION_INTERVAL", raising=False)
    monkeypatch.delenv("ADK_CODING_COMPACTION_OVERLAP", raising=False)

    assert (
        application._optional_int_env(
            "ADK_CODING_COMPACTION_INTERVAL",
            minimum=1,
        )
        is None
    )
    assert (
        application._optional_int_env(
            "ADK_CODING_COMPACTION_OVERLAP",
            minimum=0,
        )
        is None
    )

    monkeypatch.setenv("ADK_CODING_COMPACTION_INTERVAL", "12")
    monkeypatch.setenv("ADK_CODING_COMPACTION_OVERLAP", "3")
    assert (
        application._optional_int_env(
            "ADK_CODING_COMPACTION_INTERVAL",
            minimum=1,
        )
        == 12
    )
    assert (
        application._optional_int_env(
            "ADK_CODING_COMPACTION_OVERLAP",
            minimum=0,
        )
        == 3
    )

    monkeypatch.setenv("ADK_CODING_COMPACTION_INTERVAL", "0")
    with pytest.raises(ValueError, match="must be at least 1"):
        application._optional_int_env(
            "ADK_CODING_COMPACTION_INTERVAL",
            minimum=1,
        )


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


def test_control_state_builder_forwards_settings_without_connecting() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    bootstrap = importlib.import_module("app.agent.bootstrap")
    settings = replace(
        bootstrap.SETTINGS,
        control_database_url="postgresql://control.example/harness",
    )
    calls: list[tuple[object, object]] = []
    expected_backend = object()

    def build_backend(*, state_root, database_url):
        calls.append((state_root, database_url))
        return expected_backend

    backend = workflow._build_control_state(settings, factory=build_backend)

    assert backend is expected_backend
    assert calls == [(settings.state_root, settings.control_database_url)]


class _LeaseStore:
    def __init__(self, *, available: bool = True, renews: bool = True) -> None:
        self.available = available
        self.renews = renews
        self.acquired: list[tuple[str, str, int]] = []
        self.renewed = 0
        self.released = 0

    def acquire(
        self,
        task_id: str,
        owner: str,
        *,
        lease_seconds: int = 120,
    ) -> TaskLease | None:
        self.acquired.append((task_id, owner, lease_seconds))
        if not self.available:
            return None
        return TaskLease(
            task_id=task_id,
            owner=owner,
            token="token-1",
            lease_until=datetime.now(UTC),
        )

    def renew(
        self,
        lease: TaskLease,
        *,
        lease_seconds: int = 120,
    ) -> TaskLease | None:
        del lease_seconds
        self.renewed += 1
        return lease if self.renews else None

    def release(self, lease: TaskLease) -> bool:
        del lease
        self.released += 1
        return True


def test_task_lease_guard_acquires_renews_and_releases() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    store = _LeaseStore()

    guard = workflow._TaskLeaseGuard.acquire(
        store,
        task_id="task-1",
        owner="worker-a",
        lease_seconds=90,
    )

    assert guard.acquired
    assert store.acquired == [("task-1", "worker-a", 90)]
    assert guard.renew()
    assert guard.release()
    assert store.renewed == 1
    assert store.released == 1


def test_task_lease_guard_fails_closed_when_unavailable_or_lost() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    unavailable = workflow._TaskLeaseGuard.acquire(
        _LeaseStore(available=False),
        task_id="task-1",
        owner="worker-a",
        lease_seconds=90,
    )
    assert not unavailable.acquired
    assert not unavailable.renew()

    lost = workflow._TaskLeaseGuard.acquire(
        _LeaseStore(renews=False),
        task_id="task-1",
        owner="worker-a",
        lease_seconds=90,
    )
    assert lost.acquired
    assert not lost.renew()
    blocked = json.loads(workflow._lease_blocked_result("task-1", "another worker owns it"))
    assert blocked == {
        "reason": "another worker owns it",
        "status": "blocked",
        "task_id": "task-1",
    }
