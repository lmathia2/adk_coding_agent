from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.ledger import DuckDbLedgerStore
from harness.memory import LanceMemorySearch, MemoryProgramRuntime, ViewRequest


def _vectorize(text: str) -> list[float]:
    return [1.0, 0.0] if "authentication" in text.casefold() else [0.0, 1.0]


def test_lance_projection_is_rebuildable_and_preserves_event_provenance(tmp_path: Path) -> None:
    pytest.importorskip("lancedb")
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    for index, text in enumerate(
        ["file write completed", "provider authentication timeout", "tests passed"], start=1
    ):
        ledger.append(
            task_id="task",
            source="test",
            source_id=str(index),
            kind="trace",
            payload={"text": text},
            observed_at=datetime(2026, 1, 1, 0, 0, index, tzinfo=UTC),
        )
    events = ledger.read("task")
    search = LanceMemorySearch(
        tmp_path / "lance", vectorizer=_vectorize, embedding_version="test-v1"
    )

    first = search.search(events, "authentication", limit=1)
    second = search.search(events, "authentication", limit=1)

    assert first == second == (events[1].event_id,)
    task_root = tmp_path / "lance" / hashlib.sha256(b"task").hexdigest()
    assert len([path for path in task_root.iterdir() if not path.name.startswith(".")]) == 1


def test_memory_program_can_use_lance_without_changing_ledger_authority(tmp_path: Path) -> None:
    pytest.importorskip("lancedb")
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    event = ledger.append(
        task_id="task",
        source="test",
        source_id="auth",
        kind="tool.timeout",
        status="timeout",
        payload={"message": "provider authentication failed"},
    )
    runtime = MemoryProgramRuntime(
        ledger,
        semantic_search=LanceMemorySearch(
            tmp_path / "lance", vectorizer=_vectorize, embedding_version="test-v1"
        ),
    )

    result = runtime.compute(ViewRequest(task_id="task", program="task.memory", query="authentication"))

    assert result.evidence_event_ids == (event.event_id,)
    assert result.data["retrieval_version"] == "lancedb:test-v1"
    assert result.data["relevant"][0]["status"] == "timeout"


def test_lance_projection_rejects_invalid_vector_contract(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="embedding_version"):
        LanceMemorySearch(tmp_path, vectorizer=_vectorize, embedding_version="")
