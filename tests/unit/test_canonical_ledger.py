from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.ledger import DuckDbLedgerStore
from harness.ledger.importers import import_harness_event
from harness.ledger.shadow import LedgerBackedEventStore
from harness.state import JsonlEventStore
from harness.state.events import HarnessEvent


def test_append_is_idempotent_gap_free_and_rejects_conflicts(tmp_path: Path) -> None:
    store = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    first = store.append(
        task_id="task",
        source="test",
        source_id="one",
        kind="operation.started",
        status="started",
        effect="intended",
        payload={"b": 2, "a": 1},
        idempotency_key="one",
    )
    assert store.append(
        task_id="task",
        source="test",
        source_id="one",
        kind="operation.started",
        status="started",
        effect="intended",
        payload={"a": 1, "b": 2},
        idempotency_key="one",
    ) == first
    with pytest.raises(ValueError, match="different content"):
        store.append(
            task_id="task",
            source="test",
            source_id="one",
            kind="operation.completed",
            idempotency_key="one",
        )
    second = store.append(
        task_id="task", source="test", source_id="two", kind="operation.open"
    )
    assert [event.sequence for event in store.read("task")] == [1, 2]
    assert second.status == "observed"


def test_import_is_deterministic_and_as_of_uses_observed_time(tmp_path: Path) -> None:
    timestamp = datetime(2026, 1, 2, tzinfo=UTC)
    event = HarnessEvent(
        event_id="event-1",
        task_id="task",
        sequence=1,
        kind="task.created",
        payload={"value": 1},
        timestamp=timestamp,
    )
    left = DuckDbLedgerStore(tmp_path / "left.duckdb")
    right = DuckDbLedgerStore(tmp_path / "right.duckdb")
    assert import_harness_event(left, event).event_id == import_harness_event(right, event).event_id
    assert left.content_hash("task") == right.content_hash("task")
    assert left.read("task", as_of=timestamp - timedelta(microseconds=1)) == []
    assert len(left.read("task", as_of=timestamp)) == 1


def test_open_attempt_never_projects_as_success(tmp_path: Path) -> None:
    store = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    store.append(
        task_id="task",
        source="broker",
        source_id="call-1",
        kind="capability.requested",
        status="requested",
        effect="intended",
    )
    events = store.read("task")
    assert events[-1].status == "requested"
    assert events[-1].effect == "intended"


def test_compatibility_store_shadow_appends_without_changing_reducer_sequence(
    tmp_path: Path,
) -> None:
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    store = LedgerBackedEventStore(JsonlEventStore(tmp_path / "events"), ledger)
    first = store.append("task", "one", {"value": 1}, idempotency_key="one")
    second = store.append("task", "two", idempotency_key="two")
    assert [event.sequence for event in store.read("task")] == [1, 2]
    assert [event.source_id for event in ledger.read("task")] == [
        first.event_id,
        second.event_id,
    ]
