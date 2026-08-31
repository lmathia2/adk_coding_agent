"""Scripted model, real ADK workflow/tools: deterministic interaction contracts."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from google.adk import Runner
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from app.agent.factory import default_harness_registry
from harness.ai import ClosedAdkModelProviderRegistry
from harness.config import RuntimeBindings, load_harness_composition
from harness.server.adk_mapper import AdkAgUiNormalizer
from harness.server.protocol import AgUiEventType


class ScriptedModel(BaseLlm):
    _responses: list[list[types.Part]] = PrivateAttr(default_factory=list)
    _calls: int = PrivateAttr(default=0)

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        assert self._responses, "unexpected extra model call"
        self._calls += 1
        yield LlmResponse(content=types.Content(role="model", parts=self._responses.pop(0)))


class Provider:
    provider_id = "scripted"

    def __init__(self, model: ScriptedModel):
        self.model = model

    def build_model(self, config, *, secrets, bindings=None):
        return self.model


def reply(status: str, message: str) -> list[types.Part]:
    return [types.Part(text=json.dumps({"status": status, "message": message}))]


async def run_fixture(tmp_path: Path, model: ScriptedModel, prompt: str, *, monkeypatch=None):
    registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((Provider(model),)))
    composition = load_harness_composition(config_models=registry.config_models())
    config = composition.harness.config
    config = config.model_copy(update={
        "models": {name: value.model_copy(update={"provider": "scripted"})
                   for name, value in config.models.items()},
        "workflow": config.workflow.model_copy(update={"max_iterations": 1}),
    })
    composition = composition.model_copy(update={"harness": composition.harness.model_copy(update={"config": config})})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("A small fixture repository.\n")
    verification_calls: list[dict[str, Any]] = []
    if monkeypatch is not None:
        async def verify(deps, ctx, node_input):
            verification_calls.append(node_input)
            return {"report": {"passed": False, "recommended_next_action": "Tests must pass"}, "changed_paths": []}
        monkeypatch.setattr("app.agent.workflow._verify_task", verify)
    assembly = registry.build(composition, RuntimeBindings(
        workspace=workspace, state_root=tmp_path / "state", task_id="fixture-turn",
    ))
    sessions = InMemorySessionService()
    await sessions.create_session(app_name=assembly.app.name, user_id="user", session_id="conversation")
    runner = Runner(app=assembly.app, session_service=sessions)
    mapper = AdkAgUiNormalizer(run_id="run", thread_id="conversation",
                              explicit_public_messages=assembly.explicit_public_messages)
    events = []
    async for event in runner.run_async(user_id="user", session_id="conversation",
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)])):
        events.extend(mapper.push(event))
    return events, workspace, verification_calls


@pytest.mark.asyncio
async def test_greeting_finishes_in_one_model_call_without_tools_or_verification(tmp_path) -> None:
    model = ScriptedModel(model="fixture")
    model._responses = [reply("answer", "Hello!")]
    events, _, _ = await run_fixture(tmp_path, model, "hello")
    assert model._calls == 1
    assert [e.delta for e in events if e.type == AgUiEventType.TEXT_MESSAGE_CONTENT] == ["Hello!"]
    assert not any(e.type == AgUiEventType.TOOL_CALL_START for e in events)
    results = [e.value for e in events if e.name == "coding.workflow.output"]
    assert results == [{"status": "answered", "verified": False, "changed_paths": []}]


@pytest.mark.asyncio
async def test_read_only_explanation_can_answer_without_coding_verification(tmp_path) -> None:
    model = ScriptedModel(model="fixture")
    model._responses = [[types.Part(function_call=types.FunctionCall(
        id="read1", name="read", args={"path": "README.md"},
    ))], reply("answer", "A small fixture repository.")]
    events, _, _ = await run_fixture(tmp_path, model, "Explain README.md without changing files")
    assert [e.tool_call_name for e in events if e.type == AgUiEventType.TOOL_CALL_START] == ["read"]
    assert next(e.value for e in events if e.name == "coding.workflow.output")["status"] == "answered"


@pytest.mark.asyncio
async def test_answer_after_write_is_withheld_and_forces_verification(tmp_path, monkeypatch) -> None:
    model = ScriptedModel(model="fixture")
    model._responses = [[types.Part(function_call=types.FunctionCall(
        id="write1", name="write", args={"path": "hello.py", "content": "print('hello')\n"},
    ))], reply("answer", "Unverified completion claim")]
    events, workspace, calls = await run_fixture(tmp_path, model, "Create hello.py", monkeypatch=monkeypatch)
    assert (workspace / "hello.py").read_text() == "print('hello')\n"
    assert len(calls) == 1
    assert calls[0]["request"]["mode"] == "coding"
    assert calls[0]["request"]["acceptance_criteria"]
    assert all(e.delta != "Unverified completion claim" for e in events)
    assert next(e.value for e in events if e.name == "coding.workflow.output")["status"] == "blocked"
