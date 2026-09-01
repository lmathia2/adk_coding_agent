from __future__ import annotations

from pathlib import Path

import pytest

from harness.approvals import ApprovalStore
from harness.ledger import DuckDbLedgerStore
from harness.ledger.backfill import audit_backfill, backfill_state_root
from harness.persistence import local_durable_settings
from harness.persistence.adk_services import build_service_bundle
from harness.server import AgUiEvent, AgUiEventType
from harness.server.registry import SqliteRunEventStore
from harness.state import JsonlEventStore, SteeringQueue
from harness.telemetry.metrics import MetricsStore, ModelUsageSample


@pytest.mark.asyncio
async def test_backfill_is_repeatable_and_reconstructs_recognized_stores(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    JsonlEventStore(state / "events").append("task", "task.created", {"goal": "test"})
    approval = ApprovalStore(state / "approvals.db").request(
        task_id="task",
        fingerprint="fingerprint",
        operation="shell",
        risk="write",
        reason="test",
    )
    ApprovalStore(state / "approvals.db").decide(
        approval.request_id, decision="approved", actor="operator"
    )
    SteeringQueue(state / "state.db").enqueue("task", "continue")
    MetricsStore(state / "metrics.db").record_model_usage(
        ModelUsageSample(
            task_id="task",
            invocation_id="invocation",
            model="fixture",
            static_prefix_hash="hash",
            static_prefix_tokens=1,
            dynamic_suffix_tokens=2,
            input_tokens=3,
            output_tokens=1,
        )
    )
    runs = SqliteRunEventStore(state / "server" / "runs.db")
    run, _ = runs.create_run(
        request_id="request",
        idempotency_key="start",
        thread_id="thread",
        user_id="user",
        input="hello",
    )
    runs.append_event(
        run.run_id,
        AgUiEvent(
            type=AgUiEventType.CUSTOM,
            run_id=run.run_id,
            name="coding.test",
            value={"ok": True},
        ),
    )
    services = build_service_bundle(local_durable_settings(state))
    await services.session_service.create_session(
        app_name="coding_harness", user_id="user", session_id="session"
    )

    left = DuckDbLedgerStore(tmp_path / "left.duckdb")
    right = DuckDbLedgerStore(tmp_path / "right.duckdb")
    first_counts = backfill_state_root(state, left)
    second_counts = backfill_state_root(state, right)
    assert first_counts == second_counts
    assert first_counts == {
        "harness_events": 1,
        "trace_spans": 0,
        "tool_receipts": 0,
        "checkpoints": 0,
        "approvals": 1,
        "steering": 1,
        "metrics": 1,
        "runs": 1,
        "public_events": 1,
        "adk_sessions": 1,
        "adk_events": 0,
    }
    assert left.content_hash("task") == right.content_hash("task")
    assert left.content_hash(run.run_id) == right.content_hash(run.run_id)
    assert left.content_hash("session") == right.content_hash("session")
    assert backfill_state_root(state, left) == first_counts
    assert audit_backfill(state, left).matched
