"""SQLite tool-call receipts for at-least-once ADK resumability."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ToolReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    invocation_id: str
    tool_call_id: str
    tool_name: str
    arguments_hash: str
    status: Literal["started", "completed", "failed"]
    result_hash: str | None = None
    artifact_uri: str | None = None
    side_effect_key: str | None = None
    error: str | None = None
    started_at: str
    completed_at: str | None = None


class ToolReceiptStore:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_receipts (
                    task_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_hash TEXT,
                    artifact_uri TEXT,
                    side_effect_key TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (task_id, tool_call_id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_side_effect
                ON tool_receipts(task_id, side_effect_key)
                WHERE side_effect_key IS NOT NULL
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> ToolReceipt | None:
        return ToolReceipt.model_validate(dict(row)) if row else None

    def get(self, task_id: str, tool_call_id: str) -> ToolReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_receipts WHERE task_id=? AND tool_call_id=?",
                (task_id, tool_call_id),
            ).fetchone()
        return self._from_row(row)

    def begin(
        self,
        *,
        task_id: str,
        invocation_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments_hash: str,
        side_effect_key: str | None = None,
    ) -> ToolReceipt:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO tool_receipts(
                        task_id, invocation_id, tool_call_id, tool_name,
                        arguments_hash, status, side_effect_key, started_at
                    ) VALUES (?, ?, ?, ?, ?, 'started', ?, ?)
                    """,
                    (
                        task_id,
                        invocation_id,
                        tool_call_id,
                        tool_name,
                        arguments_hash,
                        side_effect_key,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get(task_id, tool_call_id)
                if existing is None and side_effect_key is not None:
                    row = connection.execute(
                        "SELECT * FROM tool_receipts WHERE task_id=? AND side_effect_key=?",
                        (task_id, side_effect_key),
                    ).fetchone()
                    existing = self._from_row(row)
                if existing is None:
                    raise
                if existing.tool_name != tool_name or existing.arguments_hash != arguments_hash:
                    raise ValueError("tool receipt key reused with different arguments")
                return existing
        receipt = self.get(task_id, tool_call_id)
        assert receipt is not None
        return receipt

    def finish(
        self,
        *,
        task_id: str,
        tool_call_id: str,
        status: Literal["completed", "failed"],
        result_hash: str | None = None,
        artifact_uri: str | None = None,
        error: str | None = None,
    ) -> ToolReceipt:
        completed_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tool_receipts
                SET status=?, result_hash=?, artifact_uri=?, error=?, completed_at=?
                WHERE task_id=? AND tool_call_id=?
                """,
                (
                    status,
                    result_hash,
                    artifact_uri,
                    error,
                    completed_at,
                    task_id,
                    tool_call_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown tool receipt: {task_id}/{tool_call_id}")
        receipt = self.get(task_id, tool_call_id)
        assert receipt is not None
        return receipt
