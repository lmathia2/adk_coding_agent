"""Transactional PostgreSQL event storage and distributed task leases."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .events import HarnessEvent


class _Cursor(Protocol):
    def fetchone(self) -> Mapping[str, Any] | None: ...

    def fetchall(self) -> list[Mapping[str, Any]]: ...


class _Connection(Protocol):
    def execute(
        self,
        query: str,
        parameters: tuple[Any, ...] = (),
    ) -> _Cursor: ...


ConnectionFactory = Callable[[], AbstractContextManager[Any]]


class PsycopgConnectionFactory:
    """Lazy psycopg connector so local installs do not require the production driver."""

    def __init__(self, database_url: str) -> None:
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("control database URL must use PostgreSQL")
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def __call__(self) -> AbstractContextManager[_Connection]:
        try:
            psycopg = importlib.import_module("psycopg")
            rows = importlib.import_module("psycopg.rows")
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "PostgreSQL control storage requires the 'production' extra"
            ) from error
        connection = psycopg.connect(self.database_url, row_factory=rows.dict_row)
        return cast(AbstractContextManager[_Connection], connection)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS harness_events (
    task_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT,
    PRIMARY KEY (task_id, sequence),
    UNIQUE (task_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS harness_task_leases (
    task_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    lease_until TIMESTAMPTZ NOT NULL
);
"""


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("event payload must be a JSON object")
        return parsed
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError("event payload must be a mapping")


def _event_from_row(row: Mapping[str, Any]) -> HarnessEvent:
    return HarnessEvent(
        event_id=str(row["event_id"]),
        task_id=str(row["task_id"]),
        sequence=int(row["sequence"]),
        kind=str(row["kind"]),
        payload=_payload(row["payload"]),
        timestamp=row["timestamp"],
        idempotency_key=(
            str(row["idempotency_key"])
            if row.get("idempotency_key") is not None
            else None
        ),
    )


class PostgresEventStore:
    """Append-only event store serialized per task by a transaction advisory lock."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connector: ConnectionFactory | None = None,
        initialize: bool = True,
    ) -> None:
        if connector is None and database_url is None:
            raise ValueError("database_url or connector is required")
        self._connect = connector or PsycopgConnectionFactory(str(database_url))
        if initialize:
            with self._connect() as connection:
                connection.execute(_SCHEMA)

    def read(self, task_id: str, *, after_sequence: int = 0) -> list[HarnessEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT task_id, sequence, event_id, kind, payload, timestamp,
                       idempotency_key
                FROM harness_events
                WHERE task_id=%s AND sequence>%s
                ORDER BY sequence
                """,
                (task_id, after_sequence),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def append(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> HarnessEvent:
        event_payload = payload or {}
        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (task_id,),
            )
            if idempotency_key is not None:
                existing_row = connection.execute(
                    """
                    SELECT task_id, sequence, event_id, kind, payload, timestamp,
                           idempotency_key
                    FROM harness_events
                    WHERE task_id=%s AND idempotency_key=%s
                    """,
                    (task_id, idempotency_key),
                ).fetchone()
                if existing_row is not None:
                    existing = _event_from_row(existing_row)
                    if existing.kind != kind or existing.payload != event_payload:
                        raise ValueError(
                            "idempotency key already used for different event content"
                        )
                    return existing
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence "
                "FROM harness_events WHERE task_id=%s",
                (task_id,),
            ).fetchone()
            sequence = int(row["sequence"] if row else 0) + 1
            event = HarnessEvent(
                task_id=task_id,
                sequence=sequence,
                kind=kind,
                payload=event_payload,
                idempotency_key=idempotency_key,
            )
            connection.execute(
                """
                INSERT INTO harness_events(
                    task_id, sequence, event_id, kind, payload, timestamp,
                    idempotency_key
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    event.task_id,
                    event.sequence,
                    event.event_id,
                    event.kind,
                    json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
                    event.timestamp,
                    event.idempotency_key,
                ),
            )
            return event


class TaskLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    owner: str
    token: str
    lease_until: datetime


class PostgresTaskLeaseStore:
    """Database-clock leases for exclusive multi-worker task execution."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connector: ConnectionFactory | None = None,
        initialize: bool = True,
    ) -> None:
        if connector is None and database_url is None:
            raise ValueError("database_url or connector is required")
        self._connect = connector or PsycopgConnectionFactory(str(database_url))
        if initialize:
            with self._connect() as connection:
                connection.execute(_SCHEMA)

    def acquire(
        self,
        task_id: str,
        owner: str,
        *,
        lease_seconds: int = 120,
    ) -> TaskLease | None:
        token = uuid4().hex
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO harness_task_leases(task_id, owner, token, lease_until)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP + %s * INTERVAL '1 second')
                ON CONFLICT(task_id) DO UPDATE SET
                    owner=EXCLUDED.owner,
                    token=EXCLUDED.token,
                    lease_until=EXCLUDED.lease_until
                WHERE harness_task_leases.lease_until <= CURRENT_TIMESTAMP
                RETURNING task_id, owner, token, lease_until
                """,
                (task_id, owner, token, max(1, lease_seconds)),
            ).fetchone()
        return TaskLease.model_validate(row) if row else None

    def renew(self, lease: TaskLease, *, lease_seconds: int = 120) -> TaskLease | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE harness_task_leases
                SET lease_until=CURRENT_TIMESTAMP + %s * INTERVAL '1 second'
                WHERE task_id=%s AND owner=%s AND token=%s
                  AND lease_until > CURRENT_TIMESTAMP
                RETURNING task_id, owner, token, lease_until
                """,
                (
                    max(1, lease_seconds),
                    lease.task_id,
                    lease.owner,
                    lease.token,
                ),
            ).fetchone()
        return TaskLease.model_validate(row) if row else None

    def release(self, lease: TaskLease) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                DELETE FROM harness_task_leases
                WHERE task_id=%s AND owner=%s AND token=%s
                RETURNING task_id
                """,
                (lease.task_id, lease.owner, lease.token),
            ).fetchone()
        return row is not None
