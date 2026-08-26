"""Select local or production control-state backends without import side effects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from .event_store import JsonlEventStore
from .events import HarnessEvent
from .postgres import PostgresEventStore, PostgresTaskLeaseStore, TaskLease


@runtime_checkable
class EventStore(Protocol):
    """Append-only event contract shared by local and production stores."""

    def read(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[HarnessEvent]: ...

    def append(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> HarnessEvent: ...


@runtime_checkable
class TaskLeaseStore(Protocol):
    """Distributed lease contract required by multi-worker deployments."""

    def acquire(
        self,
        task_id: str,
        owner: str,
        *,
        lease_seconds: int = 120,
    ) -> TaskLease | None: ...

    def renew(
        self,
        lease: TaskLease,
        *,
        lease_seconds: int = 120,
    ) -> TaskLease | None: ...

    def release(self, lease: TaskLease) -> bool: ...


@dataclass(frozen=True, slots=True)
class ControlStateBackend:
    """Resolved control-state services for one harness process."""

    kind: Literal["local", "postgres"]
    event_store: EventStore
    task_lease_store: TaskLeaseStore | None


LocalEventStoreConstructor = Callable[[Path], EventStore]
PostgresEventStoreConstructor = Callable[[str], EventStore]
PostgresLeaseStoreConstructor = Callable[[str], TaskLeaseStore]


def create_control_state_backend(
    *,
    state_root: Path,
    database_url: str | None = None,
    local_event_store: LocalEventStoreConstructor = JsonlEventStore,
    postgres_event_store: PostgresEventStoreConstructor = PostgresEventStore,
    postgres_lease_store: PostgresLeaseStoreConstructor = PostgresTaskLeaseStore,
) -> ControlStateBackend:
    """Build the configured stores; PostgreSQL is enabled only by an explicit URL."""

    if database_url is None:
        return ControlStateBackend(
            kind="local",
            event_store=local_event_store(state_root / "events"),
            task_lease_store=None,
        )

    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("control database URL must use PostgreSQL")
    return ControlStateBackend(
        kind="postgres",
        event_store=postgres_event_store(database_url),
        task_lease_store=postgres_lease_store(database_url),
    )


__all__ = [
    "ControlStateBackend",
    "EventStore",
    "TaskLeaseStore",
    "create_control_state_backend",
]
