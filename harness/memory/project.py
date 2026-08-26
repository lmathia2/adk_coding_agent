"""Curated, evidence-backed project memory.

Memory is written only after deterministic verification. The store keeps stable facts
such as canonical commands and confirmed decisions, not raw conversation history.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from harness.models.ledger import TaskLedger
from harness.models.verification import VerificationReport
from harness.repo import RepositoryManifest

MemoryKind = Literal["command", "decision", "convention", "failure", "fact"]


class ProjectMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    kind: MemoryKind
    content: str
    scope: str = "repository"
    confidence: float = Field(ge=0.0, le=1.0)
    content_hash: str
    source_task_id: str
    source_event_ids: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_confirmed_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


class ProjectMemoryStore:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_memories (
                    memory_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    content_hash TEXT NOT NULL,
                    source_task_id TEXT NOT NULL,
                    source_event_ids TEXT NOT NULL,
                    supersedes TEXT,
                    created_at TEXT NOT NULL,
                    last_confirmed_at TEXT NOT NULL,
                    UNIQUE(project_id, content_hash)
                );
                CREATE INDEX IF NOT EXISTS ix_project_memory_lookup
                ON project_memories(project_id, kind, last_confirmed_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ProjectMemory:
        data = dict(row)
        data["source_event_ids"] = json.loads(data["source_event_ids"])
        return ProjectMemory.model_validate(data)

    def upsert(self, memory: ProjectMemory) -> ProjectMemory:
        """Confirm an identical fact or insert a new version."""

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM project_memories
                WHERE project_id=? AND content_hash=?
                """,
                (memory.project_id, memory.content_hash),
            ).fetchone()
            if existing:
                now = datetime.now(UTC).isoformat()
                confidence = max(float(existing["confidence"]), memory.confidence)
                source_ids = sorted(
                    set(json.loads(existing["source_event_ids"]))
                    | set(memory.source_event_ids)
                )
                connection.execute(
                    """
                    UPDATE project_memories
                    SET confidence=?, source_event_ids=?, last_confirmed_at=?
                    WHERE memory_id=?
                    """,
                    (
                        confidence,
                        json.dumps(source_ids, sort_keys=True),
                        now,
                        existing["memory_id"],
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM project_memories WHERE memory_id=?",
                    (existing["memory_id"],),
                ).fetchone()
                assert row is not None
                return self._from_row(row)

            connection.execute(
                """
                INSERT INTO project_memories VALUES (
                    :memory_id, :project_id, :kind, :content, :scope,
                    :confidence, :content_hash, :source_task_id,
                    :source_event_ids, :supersedes, :created_at,
                    :last_confirmed_at
                )
                """,
                {
                    **memory.model_dump(mode="python"),
                    "source_event_ids": json.dumps(
                        memory.source_event_ids, sort_keys=True
                    ),
                },
            )
        return memory

    def search(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 20,
        kinds: Iterable[str] | None = None,
    ) -> list[ProjectMemory]:
        allowed = set(kinds or ())
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM project_memories
                WHERE project_id=?
                ORDER BY last_confirmed_at DESC, confidence DESC
                """,
                (project_id,),
            ).fetchall()
        tokens = {
            token
            for token in re.findall(r"[a-z0-9_./-]+", query.lower())
            if len(token) >= 2
        }
        ranked: list[tuple[float, ProjectMemory]] = []
        for row in rows:
            memory = self._from_row(row)
            if allowed and memory.kind not in allowed:
                continue
            content = f"{memory.kind} {memory.scope} {memory.content}".lower()
            lexical = sum(1 for token in tokens if token in content)
            # Commands and conventions are useful orientation even when the task query
            # does not mention the build system explicitly.
            baseline = 0.5 if memory.kind in {"command", "convention"} else 0.0
            score = lexical + baseline + memory.confidence
            if lexical or baseline:
                ranked.append((score, memory))
        ranked.sort(
            key=lambda item: (
                -item[0],
                -item[1].confidence,
                item[1].kind,
                item[1].content,
            )
        )
        return [memory for _, memory in ranked[:limit]]

    def render_context(
        self,
        project_id: str,
        query: str,
        *,
        max_tokens: int = 1_000,
    ) -> str:
        max_chars = max(max_tokens, 1) * 4
        lines = ["Verified project memory:"]
        for memory in self.search(project_id, query, limit=100):
            line = (
                f"- [{memory.kind}; confidence={memory.confidence:.2f}; "
                f"scope={memory.scope}] {memory.content}"
            )
            if len("\n".join([*lines, line])) > max_chars:
                lines.append("[project memory truncated to budget]")
                break
            lines.append(line)
        return "\n".join(lines) if len(lines) > 1 else ""


def _content_hash(kind: str, scope: str, content: str) -> str:
    canonical = json.dumps(
        {"kind": kind, "scope": scope, "content": content.strip()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decision_text(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        title = (
            value.get("summary")
            or value.get("decision")
            or value.get("title")
            or value.get("name")
        )
        rationale = value.get("rationale") or value.get("reason")
        if title and rationale:
            return f"{title}: {rationale}"
        if title:
            return str(title)
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def extract_verified_memories(
    *,
    project_id: str,
    manifest: RepositoryManifest,
    ledger: TaskLedger,
    verification: VerificationReport,
    source_event_ids: list[str] | None = None,
) -> list[ProjectMemory]:
    """Extract conservative reusable facts from a verified task."""

    if not verification.passed:
        return []
    events = source_event_ids or []
    candidates: list[tuple[MemoryKind, str, str, float]] = []

    for command in manifest.commands:
        candidates.append(
            (
                "command",
                "repository",
                f"Canonical {command.kind} command: {command.command} "
                f"(discovered from {command.source})",
                0.95,
            )
        )

    for decision in ledger.decisions:
        content = _decision_text(decision).strip()
        if content:
            candidates.append(("decision", "repository", content, 0.80))

    for instruction_path in manifest.instruction_files:
        relative = instruction_path.relative_to(manifest.root).as_posix()
        candidates.append(
            (
                "convention",
                relative,
                f"Project instructions are defined in {relative}; load them before "
                "changing code in that scope",
                0.95,
            )
        )

    memories: list[ProjectMemory] = []
    seen: set[str] = set()
    for kind, scope, content, confidence in candidates:
        digest = _content_hash(kind, scope, content)
        if digest in seen:
            continue
        seen.add(digest)
        memories.append(
            ProjectMemory(
                project_id=project_id,
                kind=kind,
                content=content,
                scope=scope,
                confidence=confidence,
                content_hash=digest,
                source_task_id=ledger.task_id,
                source_event_ids=events,
            )
        )
    return memories
