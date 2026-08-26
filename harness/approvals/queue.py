"""Durable leased delivery for managed approval consumers."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from uuid import uuid4

from .contracts import ApprovalLease
from .store import ApprovalStore


class ManagedApprovalQueue:
    def __init__(self, store: ApprovalStore) -> None:
        self.store = store
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_deliveries (
                    request_id TEXT PRIMARY KEY,
                    lease_id TEXT UNIQUE,
                    consumer_id TEXT,
                    leased_at TEXT,
                    lease_expires_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    acknowledged_at TEXT,
                    FOREIGN KEY(request_id) REFERENCES approval_requests(request_id)
                );
                CREATE INDEX IF NOT EXISTS ix_approval_delivery_available
                ON approval_deliveries(acknowledged_at, lease_expires_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def lease(
        self,
        consumer_id: str,
        *,
        limit: int = 1,
        lease_seconds: int = 60,
    ) -> list[ApprovalLease]:
        if not consumer_id:
            raise ValueError("consumer_id must not be empty")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")

        self.store.expire_due()
        now = self.store._now()
        leased_at = now.isoformat()
        lease_expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
        claimed: list[tuple[str, str, int]] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT approvals.request_id,
                       COALESCE(deliveries.attempt_count, 0) AS attempt_count
                FROM approval_requests AS approvals
                LEFT JOIN approval_deliveries AS deliveries
                    ON deliveries.request_id=approvals.request_id
                WHERE approvals.status='pending'
                    AND (approvals.expires_at IS NULL OR approvals.expires_at>?)
                    AND (
                        deliveries.request_id IS NULL
                        OR (
                            deliveries.acknowledged_at IS NULL
                            AND deliveries.lease_expires_at<=?
                        )
                    )
                ORDER BY approvals.requested_at, approvals.request_id
                LIMIT ?
                """,
                (leased_at, leased_at, limit),
            ).fetchall()
            for row in rows:
                lease_id = uuid4().hex
                attempt = int(row["attempt_count"]) + 1
                connection.execute(
                    """
                    INSERT INTO approval_deliveries (
                        request_id, lease_id, consumer_id, leased_at,
                        lease_expires_at, attempt_count, acknowledged_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(request_id) DO UPDATE SET
                        lease_id=excluded.lease_id,
                        consumer_id=excluded.consumer_id,
                        leased_at=excluded.leased_at,
                        lease_expires_at=excluded.lease_expires_at,
                        attempt_count=excluded.attempt_count,
                        acknowledged_at=NULL
                    """,
                    (
                        row["request_id"],
                        lease_id,
                        consumer_id,
                        leased_at,
                        lease_expires_at,
                        attempt,
                    ),
                )
                claimed.append((str(row["request_id"]), lease_id, attempt))

        leases: list[ApprovalLease] = []
        for request_id, lease_id, attempt in claimed:
            request = self.store.get(request_id)
            if request is None or request.status != "pending":
                continue
            leases.append(
                ApprovalLease(
                    lease_id=lease_id,
                    consumer_id=consumer_id,
                    request=request,
                    leased_at=leased_at,
                    lease_expires_at=lease_expires_at,
                    attempt=attempt,
                )
            )
        return leases

    def ack(self, lease_id: str, *, consumer_id: str) -> ApprovalLease:
        self.store.expire_due()
        now = self.store._now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT deliveries.*, approvals.status
                FROM approval_deliveries AS deliveries
                JOIN approval_requests AS approvals
                    ON approvals.request_id=deliveries.request_id
                WHERE deliveries.lease_id=?
                """,
                (lease_id,),
            ).fetchone()
            if row is None:
                raise KeyError(lease_id)
            if row["consumer_id"] != consumer_id:
                raise ValueError("approval lease belongs to another consumer")
            if row["status"] == "pending":
                raise ValueError("cannot acknowledge an undecided approval request")
            acknowledged_at = row["acknowledged_at"] or now
            if row["acknowledged_at"] is None:
                connection.execute(
                    """
                    UPDATE approval_deliveries SET acknowledged_at=?
                    WHERE lease_id=? AND consumer_id=?
                    """,
                    (acknowledged_at, lease_id, consumer_id),
                )

        request = self.store.get(str(row["request_id"]))
        assert request is not None
        return ApprovalLease(
            lease_id=lease_id,
            consumer_id=consumer_id,
            request=request,
            leased_at=str(row["leased_at"]),
            lease_expires_at=str(row["lease_expires_at"]),
            attempt=int(row["attempt_count"]),
            acknowledged_at=str(acknowledged_at),
        )


__all__ = ["ManagedApprovalQueue"]
