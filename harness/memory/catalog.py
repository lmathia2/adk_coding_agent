"""Restricted lifecycle for ledger-native relational memory programs."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, ConfigDict

ProgramState = Literal["candidate", "shadow", "active", "retired"]
_MAX_RESULT_ROWS = 10_000


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
    if normalized.count(":task_id") != 1 or "?" in normalized:
        raise ValueError("memory program must contain exactly one :task_id parameter")
    try:
        statements = duckdb.extract_statements(normalized.replace(":task_id", "?"))
    except duckdb.Error as exc:
        raise ValueError("memory program is not valid DuckDB SQL") from exc
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("memory program must be a SELECT")
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

    def execute(
        self,
        name: str,
        version: int,
        *,
        task_id: str,
        max_rows: int = 1_000,
    ) -> list[dict[str, object]]:
        program = self.get(name, version)
        if program is None or program.state != "active":
            raise PermissionError("only active memory programs may affect retrieval")
        if not 1 <= max_rows <= _MAX_RESULT_ROWS:
            raise ValueError(f"max_rows must be between 1 and {_MAX_RESULT_ROWS}")
        sql = program.sql.replace(":task_id", "?")
        database = str(self.database).replace("'", "''")
        with duckdb.connect() as connection:
            connection.execute(f"ATTACH '{database}' AS source (READ_ONLY)")
            connection.execute(
                "CREATE TABLE ledger_events AS SELECT * FROM source.ledger_events WHERE task_id=?",
                [task_id],
            )
            connection.execute("DETACH source")
            connection.execute("SET enable_external_access=false")
            # ponytail: active queries have no interruptible deadline; move execution
            # to a bounded worker before live memory programs accept untrusted authors.
            cursor = connection.execute(
                f"SELECT * FROM ({sql}) AS memory_program LIMIT ?",
                [task_id, max_rows],
            )
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
