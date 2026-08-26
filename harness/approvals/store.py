"""Durable human approval requests for exact command fingerprints."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    fingerprint: str
    operation: str
    risk: str
    reason: str
    status: Literal["pending", "approved", "denied", "expired"] = "pending"
    requested_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    decided_at: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None


class ApprovalStore:
    """SQLite approval transport suitable for CLI, API, or managed workers."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    request_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    decision_note TEXT,
                    UNIQUE(task_id, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS ix_approval_pending
                ON approval_requests(task_id, status, requested_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> ApprovalRequest | None:
        return ApprovalRequest.model_validate(dict(row)) if row else None

    def request(
        self,
        *,
        task_id: str,
        fingerprint: str,
        operation: str,
        risk: str,
        reason: str,
    ) -> ApprovalRequest:
        candidate = ApprovalRequest(
            task_id=task_id,
            fingerprint=fingerprint,
            operation=operation,
            risk=risk,
            reason=reason,
        )
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE task_id=? AND fingerprint=?
                """,
                (task_id, fingerprint),
            ).fetchone()
            if existing:
                request = self._from_row(existing)
                assert request is not None
                return request
            connection.execute(
                """
                INSERT INTO approval_requests VALUES (
                    :request_id, :task_id, :fingerprint, :operation, :risk,
                    :reason, :status, :requested_at, :decided_at, :decided_by,
                    :decision_note
                )
                """,
                candidate.model_dump(mode="python"),
            )
        return candidate

    def get(self, request_id: str) -> ApprovalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        return self._from_row(row)

    def for_fingerprint(
        self,
        task_id: str,
        fingerprint: str,
    ) -> ApprovalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE task_id=? AND fingerprint=?
                """,
                (task_id, fingerprint),
            ).fetchone()
        return self._from_row(row)

    def list(
        self,
        *,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        conditions: list[str] = []
        values: list[object] = []
        if task_id is not None:
            conditions.append("task_id=?")
            values.append(task_id)
        if status is not None:
            conditions.append("status=?")
            values.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = (
            "SELECT * FROM approval_requests"
            + where
            + " ORDER BY requested_at DESC LIMIT ?"
        )
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [request for row in rows if (request := self._from_row(row))]

    def decide(
        self,
        request_id: str,
        *,
        decision: Literal["approved", "denied"],
        actor: str,
        note: str | None = None,
    ) -> ApprovalRequest:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            request = self._from_row(row)
            if request is None:
                raise KeyError(request_id)
            if request.status in {"approved", "denied"}:
                if request.status != decision:
                    raise ValueError(
                        f"approval request already decided as {request.status}"
                    )
                return request
            if request.status != "pending":
                raise ValueError(
                    f"cannot decide approval request in status {request.status}"
                )
            connection.execute(
                """
                UPDATE approval_requests
                SET status=?, decided_at=?, decided_by=?, decision_note=?
                WHERE request_id=?
                """,
                (decision, now, actor, note, request_id),
            )
            updated = connection.execute(
                "SELECT * FROM approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        result = self._from_row(updated)
        assert result is not None
        return result

    def is_approved(self, task_id: str, fingerprint: str) -> bool:
        request = self.for_fingerprint(task_id, fingerprint)
        return request is not None and request.status == "approved"
