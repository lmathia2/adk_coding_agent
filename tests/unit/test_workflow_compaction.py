from __future__ import annotations

import importlib
from types import SimpleNamespace

from harness.models.agent_step import AgentStep
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


def test_workflow_consumes_monotonic_tool_actions_once() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    context = type(
        "Context",
        (),
        {
            "state": {
                "tool_action_fingerprints": [
                    {"sequence": 2, "fingerprint": "second"},
                    {"sequence": 1, "fingerprint": "first"},
                ]
            }
        },
    )()

    assert workflow._consume_tool_action_fingerprints(context) == ["first", "second"]
    assert workflow._consume_tool_action_fingerprints(context) == []


def test_model_progress_prose_cannot_reset_objective_stagnation() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    step = AgentStep(status="continue", progress=["claimed progress"])
    ledger = workflow._with_workspace_observations(
        _ledger(),
        step,
        [],
        [],
    )
    assert ledger.no_progress_count == 1

    ledger = workflow._with_workspace_observations(
        ledger,
        step,
        [],
        ["read-result"],
    )
    assert ledger.no_progress_count == 0
    ledger = workflow._with_workspace_observations(
        ledger,
        step,
        [],
        ["read-result"],
    )
    assert ledger.no_progress_count == 1


def test_task_input_budget_reservation_fails_closed_without_overcounting() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    state: dict[str, object] = {}

    assert workflow._reserve_task_input_budget(
        state,
        projected_tokens=600,
        limit=1_000,
    ) == (True, 0)
    assert state["estimated_task_input_tokens"] == 600
    assert workflow._reserve_task_input_budget(
        state,
        projected_tokens=401,
        limit=1_000,
    ) == (False, 600)
    assert state["estimated_task_input_tokens"] == 600


def test_recent_context_omits_ledger_and_checkpoint_duplicates(tmp_path) -> None:
    workflow = importlib.import_module("app.agent.workflow")
    store = JsonlEventStore(tmp_path / "events")
    task_id = "task-context-dedupe"
    store.append(task_id, EventKind.TASK_CREATED, {"ledger": {"goal": "duplicate"}})
    store.append(task_id, EventKind.LEDGER_PATCHED, {"set_fields": {"phase": "plan"}})
    store.append(task_id, EventKind.CHECKPOINT_CREATED, {"checkpoint_id": "duplicate"})
    store.append(task_id, EventKind.ACTION_RECORDED, {"kind": "useful"})
    deps = SimpleNamespace(
        event_store=store,
        settings=SimpleNamespace(recent_event_limit=12),
    )

    rendered = workflow._render_recent_events(deps, task_id)

    assert len(rendered) == 1
    assert "action.recorded" in rendered[0]
    assert "useful" in rendered[0]


def test_workflow_compaction_uses_safe_suffix_and_chains_snapshot(
    tmp_path,
) -> None:
    workflow = importlib.import_module("app.agent.workflow")
    store = JsonlEventStore(tmp_path / "events")
    recent_event_limit = 12
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
            {"first": index},
        )
        for index in range(recent_event_limit + 2)
    ]

    first = workflow._prepare_compaction(
        ledger.task_id,
        event_store=store,
        recent_event_limit=recent_event_limit,
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
            {"second": index},
        )
        for index in range(recent_event_limit + 1)
    ]

    second = workflow._prepare_compaction(
        ledger.task_id,
        event_store=store,
        recent_event_limit=recent_event_limit,
        ledger=ledger,
        tokens_before=90_000,
    )

    assert compaction.event_id not in second.summary_markdown
    assert second.previous_summary_hash == first.content_hash()
    assert second.last_summarized_event_id == second_batch[0].event_id
    assert second.first_retained_event_id == second_batch[1].event_id
    second_new_events = second.summary_markdown.rsplit("### Newly Summarized Events\n", 1)[1].split(
        "\n\n<read-files>", 1
    )[0]
    assert '"first":2' in second_new_events

    store.append(
        ledger.task_id,
        EventKind.COMPACTION_CREATED,
        {
            "summary": second.summary_markdown,
            "snapshot": second.model_dump(mode="json"),
        },
    )
    third_batch = [
        store.append(
            ledger.task_id,
            EventKind.ACTION_RECORDED,
            {"third": index},
        )
        for index in range(recent_event_limit + 1)
    ]

    third = workflow._prepare_compaction(
        ledger.task_id,
        event_store=store,
        recent_event_limit=recent_event_limit,
        ledger=ledger,
        tokens_before=95_000,
    )
    third_new_events = third.summary_markdown.rsplit("### Newly Summarized Events\n", 1)[1].split(
        "\n\n<read-files>", 1
    )[0]

    assert '"first":2' not in third_new_events
    assert '"second":1' in third_new_events
    assert third.last_summarized_event_id == third_batch[0].event_id
    assert third.first_retained_event_id == third_batch[1].event_id


def test_workflow_compaction_recovers_normal_coding_tool_artifacts(
    tmp_path,
) -> None:
    import asyncio

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
    snapshot = workflow._prepare_compaction(
        ledger.task_id,
        event_store=store,
        recent_event_limit=12,
        ledger=ledger,
        tokens_before=80_000,
    )

    assert snapshot.artifact_uris == [artifact_uri]
    assert artifact_uri in snapshot.summary_markdown
