from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from harness.state import PostgresEventStore, PostgresTaskLeaseStore, TaskLease


class _Cursor:
    def __init__(self, one: dict[str, Any] | None = None, many: list[dict[str, Any]] | None = None) -> None:
        self.one = one
        self.many = many or []

    def fetchone(self) -> dict[str, Any] | None:
        return self.one

    def fetchall(self) -> list[dict[str, Any]]:
        return self.many


class _Connection(AbstractContextManager["_Connection"]):
    def __init__(self, *, existing: dict[str, Any] | None = None) -> None:
        self.existing = existing
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.events: list[dict[str, Any]] = []

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> _Cursor:
        self.queries.append((query, parameters))
        normalized = " ".join(query.split())
        if "idempotency_key=%s" in normalized:
            return _Cursor(one=self.existing)
        if "MAX(sequence)" in normalized:
            return _Cursor(one={"sequence": len(self.events)})
        if normalized.startswith("INSERT INTO harness_events"):
            self.events.append({"parameters": parameters})
        if "RETURNING task_id, owner, token, lease_until" in normalized:
            if normalized.startswith("INSERT"):
                task_id, owner, token, _seconds = parameters
            else:
                _seconds, task_id, owner, token = parameters
            return _Cursor(
                one={
                    "task_id": task_id,
                    "owner": owner,
                    "token": token,
                    "lease_until": datetime.now(UTC) + timedelta(minutes=2),
                }
            )
        if normalized.startswith("DELETE FROM harness_task_leases"):
            return _Cursor(one={"task_id": parameters[0]})
        return _Cursor()


def test_postgres_events_use_transaction_lock_and_idempotent_append() -> None:
    connection = _Connection()
    store = PostgresEventStore(connector=lambda: connection, initialize=False)

    event = store.append("task", "task.created", {"value": 1}, idempotency_key="key")

    assert event.sequence == 1
    assert connection.events
    assert any("pg_advisory_xact_lock" in query for query, _ in connection.queries)
    assert '"value":1' in str(connection.events[0]["parameters"])

    existing = {
        "task_id": "task",
        "sequence": 1,
        "event_id": event.event_id,
        "kind": event.kind,
        "payload": event.payload,
        "timestamp": event.timestamp,
        "idempotency_key": "key",
    }
    duplicate_store = PostgresEventStore(
        connector=lambda: _Connection(existing=existing),
        initialize=False,
    )
    assert duplicate_store.append(
        "task", "task.created", {"value": 1}, idempotency_key="key"
    ) == event

    with pytest.raises(ValueError, match="different event content"):
        duplicate_store.append(
            "task", "task.created", {"value": 2}, idempotency_key="key"
        )


def test_postgres_task_lease_uses_tokens_and_database_expiry() -> None:
    connection = _Connection()
    store = PostgresTaskLeaseStore(connector=lambda: connection, initialize=False)

    lease = store.acquire("task", "worker-1", lease_seconds=30)

    assert lease is not None
    assert lease.task_id == "task"
    assert lease.owner == "worker-1"
    renewed = store.renew(lease, lease_seconds=45)
    assert renewed is not None
    assert renewed.token == lease.token
    assert store.release(renewed)
    rendered = "\n".join(query for query, _ in connection.queries)
    assert "CURRENT_TIMESTAMP" in rendered
    assert "lease_until <= CURRENT_TIMESTAMP" in rendered
    assert "OR harness_task_leases.owner" not in rendered


def test_task_lease_is_immutable() -> None:
    lease = TaskLease(
        task_id="task",
        owner="worker",
        token="token",
        lease_until=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        lease.owner = "other"  # type: ignore[misc]
