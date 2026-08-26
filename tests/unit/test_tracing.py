from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.sessions.state import State
from google.genai import types

from harness.tracing import (
    HarnessTracePlugin,
    TraceContentMode,
    TraceSpan,
    TraceStore,
)


@dataclass
class _Context:
    invocation_id: str
    state: object
    session: object
    node_path: str = "root/worker@1"
    run_id: str = "1"
    function_call_id: str | None = None


def _context(invocation_id: str = "inv-1", *, tool: bool = False) -> _Context:
    return _Context(
        invocation_id=invocation_id,
        state=State({"task_id": "task-1"}, {}),
        session=SimpleNamespace(id="task-1", state={}),
        function_call_id="call-1" if tool else None,
    )


def _clock() -> datetime:
    return datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def test_store_orders_replays_queries_and_exports(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "trace.db")
    base = TraceSpan(
        span_id="span-1",
        task_id="task",
        sequence=1,
        correlation_id="invocation",
        category="run",
        phase="start",
        name="run",
        timestamp=_clock().isoformat(),
        content_hash="a" * 64,
        payload_json="{}",
        idempotency_key="key-1",
    )
    first = store.append(base)
    replay = store.append(
        base.model_copy(
            update={
                "span_id": "different-generated-id",
                "timestamp": datetime(2026, 8, 27, tzinfo=UTC).isoformat(),
            }
        )
    )
    second = store.append(
        base.model_copy(
            update={
                "span_id": "span-2",
                "phase": "success",
                "parent_span_id": first.span_id,
                "idempotency_key": "key-2",
            }
        )
    )

    assert replay == first
    assert second.sequence == 2
    assert store.task_ids() == ["task"]
    assert store.query("task", phases=["success"]) == [second]
    exported = [json.loads(line) for line in store.export_jsonl("task").splitlines()]
    assert [item["sequence"] for item in exported] == [1, 2]


def test_metadata_only_is_default_and_never_persists_content(tmp_path: Path) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    plugin = HarnessTracePlugin(
        database=tmp_path / "trace.db",
        known_secrets=[secret],
        clock=_clock,
    )
    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=f"prompt {secret}")],
    )

    asyncio.run(
        plugin.on_user_message_callback(
            invocation_context=_context(),
            user_message=message,
        )
    )

    span = plugin.store.query("task-1")[0]
    assert plugin.content_mode == TraceContentMode.METADATA_ONLY
    assert secret not in plugin.store.export_jsonl("task-1")
    assert "prompt" not in span.payload_json
    assert len(span.content_hash) == 64


def test_redacted_content_is_bounded_and_reports_omissions(tmp_path: Path) -> None:
    secret = "super-secret-token-value"
    plugin = HarnessTracePlugin(
        database=tmp_path / "trace.db",
        content_mode=TraceContentMode.REDACTED_CONTENT,
        max_payload_bytes=128,
        known_secrets=[secret],
        clock=_clock,
    )
    message = {"authorization": f"Bearer {secret}", "text": "x" * 2_000}

    asyncio.run(
        plugin.on_user_message_callback(
            invocation_context={
                "invocation_id": "inv-map",
                "state": {"task_id": "task-map"},
            },
            user_message=message,
        )
    )

    span = plugin.store.query("task-map")[0]
    assert secret not in span.payload_json
    assert len(span.payload_json.encode()) <= 128
    assert span.omitted_bytes > 0
    assert json.loads(span.payload_json)["truncated"] is True


def test_plugin_covers_every_adk_callback_and_preserves_provider_objects(
    tmp_path: Path,
) -> None:
    plugin = HarnessTracePlugin(
        database=tmp_path / "trace.db",
        content_mode=TraceContentMode.REDACTED_CONTENT,
        clock=_clock,
    )
    context = _context(tool=True)
    agent = SimpleNamespace(name="worker")
    tool = SimpleNamespace(name="bash")
    request = LlmRequest(
        model="gemini-test",
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text="prompt")])
        ],
    )
    response = LlmResponse(
        model_version="gemini-test-001",
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text="response")],
        ),
    )
    tool_args = {"command": "pytest"}
    tool_result = {"exit_code": 0, "stdout": "ok"}
    request_before = request.model_dump(mode="python")
    response_before = response.model_dump(mode="python")
    args_before = deepcopy(tool_args)
    result_before = deepcopy(tool_result)

    async def invoke() -> None:
        await plugin.on_user_message_callback(
            invocation_context=context,
            user_message=request.contents[0],
        )
        await plugin.before_run_callback(invocation_context=context)
        await plugin.before_agent_callback(agent=agent, callback_context=context)
        await plugin.before_model_callback(
            callback_context=context,
            llm_request=request,
        )
        await plugin.after_model_callback(
            callback_context=context,
            llm_response=response,
        )
        await plugin.before_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=context,
        )
        await plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=context,
            result=tool_result,
        )
        await plugin.after_agent_callback(agent=agent, callback_context=context)
        await plugin.on_event_callback(
            invocation_context=context,
            event={"author": "worker", "content": "event"},
        )
        await plugin.after_run_callback(invocation_context=context)
        # A resumed callback may replay after its in-memory parent stack is gone.
        await plugin.after_run_callback(invocation_context=context)

    asyncio.run(invoke())

    assert request.model_dump(mode="python") == request_before
    assert response.model_dump(mode="python") == response_before
    assert tool_args == args_before
    assert tool_result == result_before
    available_callbacks = {
        name
        for name, value in inspect.getmembers(BasePlugin, inspect.isfunction)
        if name.endswith("_callback")
    }
    assert available_callbacks <= set(HarnessTracePlugin.__dict__)
    spans = plugin.store.query("task-1")
    assert len(spans) == 10
    observed = {(span.category, span.phase) for span in spans}
    assert {
        ("user", "success"),
        ("run", "start"),
        ("run", "success"),
        ("agent", "start"),
        ("agent", "success"),
        ("model", "start"),
        ("model", "success"),
        ("tool", "start"),
        ("tool", "success"),
        ("event", "success"),
    } <= observed
    success_by_category = {
        span.category: span
        for span in spans
        if span.phase == "success" and span.category in {"run", "agent", "model", "tool"}
    }
    assert all(span.parent_span_id for span in success_by_category.values())
    assert {span.correlation_id for span in spans} == {"inv-1"}


def test_error_callbacks_are_redacted_and_parented(tmp_path: Path) -> None:
    secret = "password-that-must-not-leak"
    plugin = HarnessTracePlugin(
        database=tmp_path / "trace.db",
        content_mode=TraceContentMode.REDACTED_CONTENT,
        known_secrets=[secret],
        clock=_clock,
    )
    agent = SimpleNamespace(name="worker")
    tool = SimpleNamespace(name="write")

    async def invoke_errors() -> None:
        run_context = _context("run-error")
        await plugin.before_run_callback(invocation_context=run_context)
        await plugin.on_run_error_callback(
            invocation_context=run_context,
            error=RuntimeError(secret),
        )

        agent_context = _context("agent-error")
        await plugin.before_agent_callback(agent=agent, callback_context=agent_context)
        await plugin.on_agent_error_callback(
            agent=agent,
            callback_context=agent_context,
            error=RuntimeError(secret),
        )

        model_context = _context("model-error")
        request = {"modelName": "gemini-test", "contents": [secret]}
        await plugin.before_model_callback(
            callback_context=model_context,
            llm_request=request,
        )
        await plugin.on_model_error_callback(
            callback_context=model_context,
            llm_request=request,
            error=RuntimeError(secret),
        )

        tool_context = _context("tool-error", tool=True)
        arguments = {"password": secret}
        await plugin.before_tool_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=tool_context,
        )
        await plugin.on_tool_error_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=tool_context,
            error=RuntimeError(secret),
        )

    asyncio.run(invoke_errors())

    errors = plugin.store.query("task-1", phases=["error"])
    assert {span.category for span in errors} == {"run", "agent", "model", "tool"}
    assert all(span.parent_span_id for span in errors)
    assert secret not in plugin.store.export_jsonl("task-1")
    assert all("<redacted>" in span.payload_json for span in errors)


def test_session_identity_keeps_early_and_late_callbacks_together(
    tmp_path: Path,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context = _context()
    context.session.id = "session-direct"

    async def invoke() -> None:
        await plugin.before_run_callback(invocation_context=context)
        context.state["task_id"] = "derived-task"
        await plugin.before_model_callback(
            callback_context=context,
            llm_request={"model": "gemini-test"},
        )
        await plugin.after_run_callback(invocation_context=context)

    asyncio.run(invoke())

    assert len(plugin.store.query("session-direct")) == 3
    assert plugin.store.query("derived-task") == []


def test_partial_models_blocked_tools_and_storage_failures_are_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context = _context(tool=True)

    async def invoke() -> None:
        await plugin.before_model_callback(
            callback_context=context,
            llm_request={"model": "gemini-test"},
        )
        await plugin.after_model_callback(
            callback_context=context,
            llm_response={"partial": True},
        )
        await plugin.after_model_callback(
            callback_context=context,
            llm_response={"partial": False},
        )
        await plugin.before_tool_callback(
            tool=SimpleNamespace(name="bash"),
            tool_args={"command": "curl example.test"},
            tool_context=context,
        )
        await plugin.after_tool_callback(
            tool=SimpleNamespace(name="bash"),
            tool_args={"command": "curl example.test"},
            tool_context=context,
            result={"status": "blocked", "risk": "network"},
        )

    asyncio.run(invoke())
    spans = plugin.store.query("task-1")
    assert len([span for span in spans if span.category == "model"]) == 2
    assert any(span.category == "tool" and span.phase == "blocked" for span in spans)

    def fail_append(_span):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(plugin.store, "append", fail_append)
    asyncio.run(plugin.before_run_callback(invocation_context=context))
