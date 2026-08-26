from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.state.event_store import JsonlEventStore
from harness.state.events import HarnessEvent
from harness.state.factory import (
    EventStore,
    TaskLeaseStore,
    create_control_state_backend,
)
from harness.state.postgres import TaskLease


class _EventStore:
    def read(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[HarnessEvent]:
        del task_id, after_sequence
        return []

    def append(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, object] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> HarnessEvent:
        del idempotency_key
        return HarnessEvent(
            task_id=task_id,
            sequence=1,
            kind=kind,
            payload=payload or {},
        )


class _TaskLeaseStore:
    def acquire(
        self,
        task_id: str,
        owner: str,
        *,
        lease_seconds: int = 120,
    ) -> TaskLease | None:
        del task_id, owner, lease_seconds
        return None

    def renew(
        self,
        lease: TaskLease,
        *,
        lease_seconds: int = 120,
    ) -> TaskLease | None:
        del lease, lease_seconds
        return None

    def release(self, lease: TaskLease) -> bool:
        del lease
        return True


def test_default_backend_uses_jsonl_event_store(tmp_path: Path) -> None:
    backend = create_control_state_backend(state_root=tmp_path)

    assert backend.kind == "local"
    assert isinstance(backend.event_store, JsonlEventStore)
    assert backend.event_store.root == (tmp_path / "events").resolve()
    assert backend.task_lease_store is None


def test_default_selects_jsonl_without_constructing_postgres(tmp_path: Path) -> None:
    local = _EventStore()
    local_paths: list[Path] = []

    def build_local(path: Path) -> _EventStore:
        local_paths.append(path)
        return local

    def unexpected_postgres(_database_url: str) -> _EventStore:
        raise AssertionError("PostgreSQL constructor must not run in local mode")

    def unexpected_lease(_database_url: str) -> _TaskLeaseStore:
        raise AssertionError("lease constructor must not run in local mode")

    backend = create_control_state_backend(
        state_root=tmp_path,
        local_event_store=build_local,
        postgres_event_store=unexpected_postgres,
        postgres_lease_store=unexpected_lease,
    )

    assert backend.kind == "local"
    assert backend.event_store is local
    assert backend.task_lease_store is None
    assert local_paths == [tmp_path / "events"]
    assert isinstance(backend.event_store, EventStore)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://control.example/harness",
        "postgresql+psycopg://control.example/harness",
    ],
)
def test_explicit_postgres_url_selects_event_and_lease_stores(
    tmp_path: Path,
    database_url: str,
) -> None:
    events = _EventStore()
    leases = _TaskLeaseStore()
    event_urls: list[str] = []
    lease_urls: list[str] = []

    def build_events(url: str) -> _EventStore:
        event_urls.append(url)
        return events

    def build_leases(url: str) -> _TaskLeaseStore:
        lease_urls.append(url)
        return leases

    backend = create_control_state_backend(
        state_root=tmp_path,
        database_url=database_url,
        local_event_store=lambda _path: (_ for _ in ()).throw(
            AssertionError("local constructor must not run in PostgreSQL mode")
        ),
        postgres_event_store=build_events,
        postgres_lease_store=build_leases,
    )

    assert backend.kind == "postgres"
    assert backend.event_store is events
    assert backend.task_lease_store is leases
    assert event_urls == [database_url]
    assert lease_urls == [database_url]
    assert isinstance(backend.event_store, EventStore)
    assert isinstance(backend.task_lease_store, TaskLeaseStore)


@pytest.mark.parametrize(
    "database_url",
    ["", "sqlite:///state.db", "mysql://control.example/harness", "postgres://old"],
)
def test_non_postgres_control_database_url_is_rejected(
    tmp_path: Path,
    database_url: str,
) -> None:
    calls: list[str] = []

    def constructor(value: str) -> _EventStore:
        calls.append(value)
        return _EventStore()

    with pytest.raises(ValueError, match="must use PostgreSQL"):
        create_control_state_backend(
            state_root=tmp_path,
            database_url=database_url,
            postgres_event_store=constructor,
            postgres_lease_store=lambda value: (_ for _ in ()).throw(
                AssertionError(value)
            ),
        )
    assert calls == []


def test_protocol_contracts_are_structural() -> None:
    assert isinstance(_EventStore(), EventStore)
    assert isinstance(_TaskLeaseStore(), TaskLeaseStore)
    lease = TaskLease(
        task_id="task",
        owner="worker",
        token="token",
        lease_until=datetime.now(UTC),
    )
    assert _TaskLeaseStore().release(lease)
