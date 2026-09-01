"""Explicit physical erasure across local trace-native authorities."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict

from .store import DuckDbLedgerStore


class ErasureResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id_sha256: str
    ledger_rows: int
    sqlite_rows: int
    files: tuple[str, ...]


_SQLITE_TARGETS = {
    "managed-tools.db": (("tool_receipts", "task_id"),),
    "approvals.db": (("approval_requests", "task_id"),),
    "metrics.db": (
        ("model_usage", "task_id"),
        ("tool_usage", "task_id"),
        ("task_outcomes", "task_id"),
    ),
    "traces.db": (("trace_spans", "task_id"),),
    "state.db": (("checkpoints", "task_id"), ("steering_messages", "task_id")),
    "adk/sessions.db": (("events", "session_id"), ("sessions", "id")),
    "server/runs.db": (("public_run_events", "run_id"), ("agent_runs", "run_id")),
}


def _delete_sqlite(database: Path, targets: tuple[tuple[str, str], ...], value: str) -> int:
    if not database.exists():
        return 0
    deleted = 0
    with sqlite3.connect(database) as connection:
        for table, column in targets:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is not None:
                cursor = connection.execute(
                    f'DELETE FROM "{table}" WHERE "{column}"=?', (value,)
                )
                deleted += max(cursor.rowcount, 0)
        connection.commit()
        connection.execute("VACUUM")
    return deleted


def _artifact_uris(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_artifact_uris(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_artifact_uris(item) for item in value), set())
    if isinstance(value, str) and value.startswith(("artifact://", "file://")):
        return {value}
    return set()


def _artifact_path(root: Path, uri: str) -> Path | None:
    parsed = urlsplit(uri)
    if parsed.scheme == "artifact" and parsed.netloc == "sha256":
        candidate = root / "artifacts" / "sha256" / unquote(parsed.path).lstrip("/")
    elif parsed.scheme == "file" and not parsed.netloc:
        candidate = Path(unquote(parsed.path))
    else:
        return None
    resolved = candidate.resolve()
    return resolved if resolved.is_relative_to(root) else None


def erase_task_state(
    state_root: Path,
    *,
    task_id: str,
    ledger: DuckDbLedgerStore | None = None,
) -> ErasureResult:
    """Erase one exact task; callers must separately authorize this destructive action."""

    root = state_root.resolve()
    active_ledger = ledger or DuckDbLedgerStore(root / "ledger.duckdb")
    task_events = active_ledger.read(task_id)
    referenced = set().union(*(_artifact_uris(event.payload) for event in task_events), set())
    retained: set[str] = set()
    for other_task in active_ledger.task_ids():
        if other_task != task_id:
            for event in active_ledger.read(other_task):
                retained.update(_artifact_uris(event.payload))
    ledger_rows = active_ledger.erase_task(task_id)
    sqlite_rows = sum(
        _delete_sqlite(root / relative, targets, task_id)
        for relative, targets in _SQLITE_TARGETS.items()
    )
    digest = hashlib.sha256(task_id.encode()).hexdigest()
    candidates = [
        root / "events" / f"{digest}.jsonl",
        root / "notebooks" / f"{digest[:32]}.ipynb",
    ]
    candidates.extend(
        path
        for uri in sorted(referenced - retained)
        if (path := _artifact_path(root, uri)) is not None
    )
    for manifest in (root / "ledger-segments").glob("*.manifest.json"):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("task_id") == task_id:
            candidates.extend((manifest, manifest.with_suffix("").with_suffix("")))
    removed: list[str] = []
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve().is_relative_to(root):
            candidate.unlink()
            removed.append(candidate.relative_to(root).as_posix())
    return ErasureResult(
        task_id_sha256=digest,
        ledger_rows=ledger_rows,
        sqlite_rows=sqlite_rows,
        files=tuple(sorted(removed)),
    )
