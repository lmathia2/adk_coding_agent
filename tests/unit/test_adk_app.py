from __future__ import annotations

import importlib

import pytest


def test_agents_cli_entrypoint_imports_with_adk_2x(monkeypatch, tmp_path) -> None:
    pytest.importorskip("google.adk")
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))

    module = importlib.import_module("app.agent")

    assert module.app.name == "pi_inspired_adk_coding_agent"
    assert module.root_agent.name == "coding_harness"
    tool_names = {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in module.coding_worker.tools}
    assert {"read", "bash", "edit", "write"}.issubset(tool_names)
