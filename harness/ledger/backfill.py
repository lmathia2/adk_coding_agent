"""Deterministic import of legacy local stores into the canonical ledger."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from harness.approvals import ApprovalRequest
from harness.models.checkpoint import Checkpoint
from harness.server.protocol import ServerEnvelope
from harness.server.registry import RunRecord
from harness.state import HarnessEvent, SteeringMessage, ToolReceipt
from harness.telemetry.metrics import ModelUsageSample, TaskOutcomeSample, ToolUsageSample
from harness.tracing import TraceSpan

from .importers import (
    import_approval,
    import_checkpoint,
    import_harness_event,
    import_metric,
    import_public_event,
    import_run,
    import_session_record,
    import_steering,
    import_tool_receipt,
    import_trace_span,
)
from .store import DuckDbLedgerStore


class BackfillAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    imported_records: dict[str, int]
    ledger_sources: dict[str, int]
    expected_events: int
    mismatched_event_ids: tuple[str, ...]
    matched: bool


def audit_backfill(state_root: Path, ledger: DuckDbLedgerStore) -> BackfillAudit:
    imported = backfill_state_root(state_root, ledger)
    sources = ledger.source_counts()
    expected_sources = {
        "harness_event": imported["harness_events"],
        "trace_span": imported["trace_spans"],
        "tool_receipt": imported["tool_receipts"],
        "checkpoint": imported["checkpoints"],
        "approval": imported["approvals"],
        "steering": imported["steering"],
        "metric": imported["metrics"],
        "run_registry": imported["runs"],
        "public_event": imported["public_events"],
        "adk_session": imported["adk_sessions"] + imported["adk_events"],
    }
    actual = {source: sources.get(source, 0) for source in expected_sources}
    with tempfile.TemporaryDirectory() as directory:
        expected_store = DuckDbLedgerStore(Path(directory) / "expected.duckdb")
        backfill_state_root(state_root, expected_store)
        expected_events = {
            event.event_id: event
            for task_id in expected_store.task_ids()
            for event in expected_store.read(task_id)
        }
    actual_events = {
        event.event_id: event
        for task_id in ledger.task_ids()
        for event in ledger.read(task_id)
    }
    mismatched = tuple(
        sorted(
            event_id
            for event_id, event in expected_events.items()
            if actual_events.get(event_id) != event
        )
    )
    return BackfillAudit(
        imported_records=imported,
        ledger_sources=actual,
        expected_events=len(expected_events),
        mismatched_event_ids=mismatched,
        matched=actual == expected_sources and not mismatched,
    )


def _rows(database: Path, table: str) -> list[dict[str, Any]]:
    if not database.exists():
        return []
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            return []
        return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]


def backfill_state_root(state_root: Path, ledger: DuckDbLedgerStore) -> dict[str, int]:
    """Import every recognized local authority; re-running is idempotent."""

    counts: dict[str, int] = {}

    event_count = 0
    for path in sorted((state_root / "events").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                import_harness_event(ledger, HarnessEvent.model_validate_json(line))
                event_count += 1
    counts["harness_events"] = event_count

    traces = _rows(state_root / "traces.db", "trace_spans")
    for row in traces:
        import_trace_span(ledger, TraceSpan.model_validate(row))
    counts["trace_spans"] = len(traces)

    receipts = _rows(state_root / "managed-tools.db", "tool_receipts")
    for row in receipts:
        import_tool_receipt(ledger, ToolReceipt.model_validate(row))
    counts["tool_receipts"] = len(receipts)

    checkpoints = _rows(state_root / "state.db", "checkpoints")
    for row in checkpoints:
        import_checkpoint(ledger, Checkpoint.model_validate_json(row["payload"]))
    counts["checkpoints"] = len(checkpoints)

    approvals = _rows(state_root / "approvals.db", "approval_requests")
    for row in approvals:
        import_approval(ledger, ApprovalRequest.model_validate(row))
    counts["approvals"] = len(approvals)

    steering = _rows(state_root / "state.db", "steering_messages")
    for row in steering:
        row.pop("idempotency_key", None)
        import_steering(ledger, SteeringMessage.model_validate(row))
    counts["steering"] = len(steering)

    metric_database = state_root / "metrics.db"
    model_usage = _rows(metric_database, "model_usage")
    tool_usage = _rows(metric_database, "tool_usage")
    outcomes = _rows(metric_database, "task_outcomes")
    for row in model_usage:
        import_metric(ledger, ModelUsageSample.model_validate(row))
    for row in tool_usage:
        row["replayed"] = bool(row["replayed"])
        import_metric(ledger, ToolUsageSample.model_validate(row))
    for row in outcomes:
        row["passed"] = bool(row["passed"])
        import_metric(ledger, TaskOutcomeSample.model_validate(row))
    counts["metrics"] = len(model_usage) + len(tool_usage) + len(outcomes)

    run_database = state_root / "server" / "runs.db"
    runs = _rows(run_database, "agent_runs")
    for row in runs:
        row["metadata"] = json.loads(row.pop("metadata_json"))
        import_run(ledger, RunRecord.model_validate(row))
    counts["runs"] = len(runs)
    public_events = _rows(run_database, "public_run_events")
    for row in public_events:
        import_public_event(
            ledger,
            ServerEnvelope.model_validate_json(row["envelope_json"]),
            recorded_at=row["created_at"],
        )
    counts["public_events"] = len(public_events)

    session_database = state_root / "adk" / "sessions.db"
    sessions = _rows(session_database, "sessions")
    for row in sessions:
        import_session_record(
            ledger,
            str(row["id"]),
            "session.created",
            {"app_name": row["app_name"], "user_id": row["user_id"]},
            recorded_at=str(row["create_time"]),
        )
    session_events = _rows(session_database, "events")
    for row in session_events:
        import_session_record(
            ledger,
            str(row["session_id"]),
            "session.event",
            {"event": json.loads(row["event_data"])},
            recorded_at=str(row["timestamp"]),
        )
    counts["adk_sessions"] = len(sessions)
    counts["adk_events"] = len(session_events)
    return counts
