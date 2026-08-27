from __future__ import annotations

import importlib

from harness.models.ledger import TaskLedger
from harness.models.task import TaskRequest
from harness.state import EventKind, JsonlEventStore
from harness.tracing import CodingToolArtifactPlugin


async def _record_tool_artifact(
    plugin: CodingToolArtifactPlugin,
    *,
    task_id: str,
    artifact_uri: str,
) -> None:
    await plugin.after_tool_callback(
        tool=type("Tool", (), {"name": "bash"})(),
        tool_args={"command": "pytest -q"},
        tool_context=type(
            "ToolContext",
            (),
            {"state": {"task_id": task_id}, "parent_ctx": None},
        )(),
        result={"status": "ok", "artifact_uri": artifact_uri},
    )


def _ledger() -> TaskLedger:
    return TaskLedger.from_request(
        TaskRequest(goal="Fix parser", acceptance_criteria=["Parser tests pass"]),
        task_id="task-1",
        workspace_id="workspace",
        base_revision="abc123",
    )


def test_workflow_compaction_uses_safe_suffix_and_chains_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    workflow = importlib.import_module("app.agent.workflow")
    store = JsonlEventStore(tmp_path / "events")
    ledger = _ledger()
    store.append(
        ledger.task_id,
        EventKind.TASK_CREATED,
        {"ledger": ledger.model_dump(mode="json")},
    )
    first_batch = [
        store.append(
            ledger.task_id,
            EventKind.ACTION_RECORDED,
            {"index": index},
        )
        for index in range(workflow.SETTINGS.recent_event_limit + 2)
    ]
    monkeypatch.setattr(workflow, "_EVENT_STORE", store)

    first = workflow._prepare_compaction(
        ledger.task_id,
        ledger=ledger,
        tokens_before=80_000,
    )

    assert first.last_summarized_event_id == first_batch[1].event_id
    assert first.first_retained_event_id == first_batch[2].event_id
    assert first.tokens_before == 80_000
    compaction = store.append(
        ledger.task_id,
        EventKind.COMPACTION_CREATED,
        {
            "summary": first.summary_markdown,
            "snapshot": first.model_dump(mode="json"),
        },
    )
    second_batch = [
        store.append(
            ledger.task_id,
            EventKind.ACTION_RECORDED,
            {"index": index},
        )
        for index in range(workflow.SETTINGS.recent_event_limit + 1)
    ]

    second = workflow._prepare_compaction(
        ledger.task_id,
        ledger=ledger,
        tokens_before=90_000,
    )

    assert compaction.event_id not in second.summary_markdown
    assert second.previous_summary_hash == first.content_hash()
    assert second.last_summarized_event_id == second_batch[0].event_id
    assert second.first_retained_event_id == second_batch[1].event_id


def test_workflow_compaction_recovers_normal_coding_tool_artifacts(
    monkeypatch,
    tmp_path,
) -> None:
    import asyncio

    monkeypatch.setenv("ADK_CODING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    workflow = importlib.import_module("app.agent.workflow")
    store = JsonlEventStore(tmp_path / "events")
    ledger = _ledger()
    store.append(
        ledger.task_id,
        EventKind.TASK_CREATED,
        {"ledger": ledger.model_dump(mode="json")},
    )
    artifact_uri = f"artifact://tool-output/{'a' * 64}.txt"
    asyncio.run(
        _record_tool_artifact(
            CodingToolArtifactPlugin(event_store=store),
            task_id=ledger.task_id,
            artifact_uri=artifact_uri,
        )
    )
    monkeypatch.setattr(workflow, "_EVENT_STORE", store)

    snapshot = workflow._prepare_compaction(
        ledger.task_id,
        ledger=ledger,
        tokens_before=80_000,
    )

    assert snapshot.artifact_uris == [artifact_uri]
    assert artifact_uri in snapshot.summary_markdown
