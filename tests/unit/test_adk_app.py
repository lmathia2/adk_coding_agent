from __future__ import annotations

import importlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from types import SimpleNamespace

import pytest

from harness.memory.adk_plugin import VerifiedProjectMemoryPlugin
from harness.state.postgres import TaskLease
from harness.telemetry.adk_plugin import HarnessMetricsPlugin


def test_agents_cli_entrypoint_imports_with_adk_2x(monkeypatch, tmp_path) -> None:
    pytest.importorskip("google.adk")
    assert version("google-adk").split(".")[:2] == ["2", "7"]
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))

    module = importlib.import_module("app.agent")

    assert module.app.name == "pi_inspired_adk_coding_agent"
    assert module.root_agent.name == "coding_harness"
    assert module.app.root_agent is module.root_agent
    assert any(isinstance(plugin, HarnessMetricsPlugin) for plugin in module.app.plugins)
    assert any(
        isinstance(plugin, VerifiedProjectMemoryPlugin)
        for plugin in module.app.plugins
    )
    tool_names = {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in module.coding_worker.tools}
    assert {"read", "bash", "edit", "write"}.issubset(tool_names)

    reviewer = importlib.import_module("app.agent.reviewer")
    assert reviewer.final_diff_reviewer.tools == []
    assert reviewer.final_diff_reviewer.output_schema is not None

    workflow = importlib.import_module("app.agent.workflow")
    state: dict[str, object] = {}
    workflow._set_model_call_state(
        SimpleNamespace(state=state),
        task_id="task-1",
        dynamic_tokens=321,
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

    settings = config.load_settings()

    assert settings.control_database_url == "postgresql://control.example/harness"
    assert settings.worker_id == "worker-a"
    assert settings.task_lease_seconds == 45


def test_control_state_builder_forwards_settings_without_connecting() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    settings = replace(
        workflow.SETTINGS,
        control_database_url="postgresql://control.example/harness",
    )
    calls: list[tuple[object, object]] = []

    def build_backend(*, state_root, database_url):
        calls.append((state_root, database_url))
        return workflow._CONTROL_STATE

    backend = workflow._build_control_state(settings, factory=build_backend)

    assert backend is workflow._CONTROL_STATE
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
    blocked = json.loads(
        workflow._lease_blocked_result("task-1", "another worker owns it")
    )
    assert blocked == {
        "reason": "another worker owns it",
        "status": "blocked",
        "task_id": "task-1",
    }
