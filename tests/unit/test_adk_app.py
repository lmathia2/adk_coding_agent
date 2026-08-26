from __future__ import annotations

import importlib

import pytest

from harness.memory.adk_plugin import VerifiedProjectMemoryPlugin
from harness.telemetry.adk_plugin import HarnessMetricsPlugin


def test_agents_cli_entrypoint_imports_with_adk_2x(monkeypatch, tmp_path) -> None:
    pytest.importorskip("google.adk")
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
