"""Durable user-steering queue with lease/ack semantics."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class SteeringMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    task_id: str
    content: str
    priority: int
    created_at: str
    status: str
    lease_owner: str | None = None
    lease_until: str | None = None


class SteeringQueue:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS steering_messages (
                    message_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_until TEXT,
                    idempotency_key TEXT,
                    UNIQUE(task_id, idempotency_key)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SteeringMessage:
        data = dict(row)
        data.pop("idempotency_key", None)
        return SteeringMessage.model_validate(data)

    def enqueue(
        self,
        task_id: str,
        content: str,
        *,
        priority: int = 0,
        idempotency_key: str | None = None,
    ) -> SteeringMessage:
        now = datetime.now(UTC).isoformat()
        message_id = uuid4().hex
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO steering_messages(
                        message_id, task_id, content, priority, created_at,
                        status, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (message_id, task_id, content, priority, now, idempotency_key),
                )
            except sqlite3.IntegrityError as error:
                row = connection.execute(
                    "SELECT * FROM steering_messages WHERE task_id=? AND idempotency_key=?",
                    (task_id, idempotency_key),
                ).fetchone()
                assert row is not None
                existing = self._from_row(row)
                if existing.content != content or existing.priority != priority:
                    raise ValueError(
                        "steering idempotency key reused with different content"
                    ) from error
                return existing
            row = connection.execute(
                "SELECT * FROM steering_messages WHERE message_id=?", (message_id,)
            ).fetchone()
        assert row is not None
        return self._from_row(row)

    def lease(
        self,
        task_id: str,
        owner: str,
        *,
        limit: int = 10,
        lease_seconds: int = 60,
    ) -> list[SteeringMessage]:
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE steering_messages SET status='queued', lease_owner=NULL, lease_until=NULL
                WHERE task_id=? AND status='leased' AND lease_until < ?
                """,
                (task_id, now.isoformat()),
            )
            rows = connection.execute(
                """
                SELECT * FROM steering_messages
                WHERE task_id=? AND status='queued'
                ORDER BY priority DESC, created_at ASC LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
            ids = [row["message_id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE steering_messages
                    SET status='leased', lease_owner=?, lease_until=?
                    WHERE message_id IN ({placeholders})
                    """,
                    (owner, lease_until.isoformat(), *ids),
                )
            connection.execute("COMMIT")
        return [
            message.model_copy(
                update={
                    "status": "leased",
                    "lease_owner": owner,
                    "lease_until": lease_until.isoformat(),
                }
            )
            for message in map(self._from_row, rows)
        ]

    def ack(self, message_ids: list[str], owner: str) -> int:
        if not message_ids:
            return 0
        placeholders = ",".join("?" for _ in message_ids)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE steering_messages
                SET status='acked', lease_owner=NULL, lease_until=NULL
                WHERE message_id IN ({placeholders}) AND status='leased' AND lease_owner=?
                """,
                (*message_ids, owner),
            )
        return cursor.rowcount

    def release(self, message_ids: list[str], owner: str) -> int:
        if not message_ids:
            return 0
        placeholders = ",".join("?" for _ in message_ids)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE steering_messages
                SET status='queued', lease_owner=NULL, lease_until=NULL
                WHERE message_id IN ({placeholders}) AND status='leased' AND lease_owner=?
                """,
                (*message_ids, owner),
            )
        return cursor.rowcount
