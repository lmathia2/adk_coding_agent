"""Restricted lifecycle for ledger-native relational memory programs."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, ConfigDict

ProgramState = Literal["candidate", "shadow", "active", "retired"]
_FORBIDDEN = re.compile(
    r"\b(attach|call|copy|create|delete|detach|drop|export|import|insert|install|load|pragma|replace|update)\b",
    re.IGNORECASE,
)


class MemoryProgram(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: int
    sql: str
    state: ProgramState
    content_hash: str
    created_at: datetime


def validate_relational_program(sql: str) -> str:
    normalized = " ".join(sql.strip().split())
    if not normalized.casefold().startswith("select "):
        raise ValueError("memory program must be a SELECT")
    if ";" in normalized or _FORBIDDEN.search(normalized):
        raise ValueError("memory program contains a forbidden operation")
    if "ledger_events" not in normalized.casefold():
        raise ValueError("memory program must read ledger_events")
    return normalized


class ProgramCatalog:
    def __init__(self, database: Path) -> None:
        self.database = database
        with duckdb.connect(str(database)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_programs (
                    name VARCHAR NOT NULL,
                    version INTEGER NOT NULL,
                    sql VARCHAR NOT NULL,
                    state VARCHAR NOT NULL,
                    content_hash VARCHAR NOT NULL,
                    created_at VARCHAR NOT NULL,
                    PRIMARY KEY(name, version)
                )
                """
            )

    def register(self, name: str, version: int, sql: str) -> MemoryProgram:
        normalized = validate_relational_program(sql)
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        created_at = datetime.now(UTC)
        with duckdb.connect(str(self.database)) as connection:
            connection.execute(
                "INSERT INTO memory_programs VALUES (?, ?, ?, 'candidate', ?, ?)",
                [name, version, normalized, digest, created_at.isoformat()],
            )
        return MemoryProgram(
            name=name,
            version=version,
            sql=normalized,
            state="candidate",
            content_hash=digest,
            created_at=created_at,
        )

    def transition(self, name: str, version: int, state: ProgramState) -> MemoryProgram:
        allowed: dict[ProgramState, set[ProgramState]] = {
            "candidate": {"shadow", "retired"},
            "shadow": {"active", "retired"},
            "active": {"retired"},
            "retired": set(),
        }
        current = self.get(name, version)
        if current is None:
            raise KeyError(f"unknown memory program: {name}@{version}")
        if state not in allowed[current.state]:
            raise ValueError(f"invalid memory program transition: {current.state} -> {state}")
        with duckdb.connect(str(self.database)) as connection:
            connection.execute(
                "UPDATE memory_programs SET state=? WHERE name=? AND version=?",
                [state, name, version],
            )
        return current.model_copy(update={"state": state})

    def get(self, name: str, version: int) -> MemoryProgram | None:
        with duckdb.connect(str(self.database)) as connection:
            row = connection.execute(
                "SELECT * FROM memory_programs WHERE name=? AND version=?",
                [name, version],
            ).fetchone()
        if row is None:
            return None
        return MemoryProgram(
            name=row[0],
            version=row[1],
            sql=row[2],
            state=row[3],
            content_hash=row[4],
            created_at=row[5],
        )

    def execute(self, name: str, version: int, *, task_id: str) -> list[dict[str, object]]:
        program = self.get(name, version)
        if program is None or program.state != "active":
            raise PermissionError("only active memory programs may affect retrieval")
        sql = program.sql.replace(":task_id", "?")
        with duckdb.connect(str(self.database), read_only=True) as connection:
            cursor = connection.execute(sql, [task_id])
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
