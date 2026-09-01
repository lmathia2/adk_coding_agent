from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.ledger import DuckDbLedgerStore
from harness.memory import MemoryProgramRuntime, ViewRequest, compile_prompt


def _ledger(path: Path) -> DuckDbLedgerStore:
    store = DuckDbLedgerStore(path)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.append(task_id="task", source="test", source_id="1", kind="task.started", status="started", observed_at=start)
    store.append(task_id="task", source="test", source_id="2", kind="file.write", status="completed", effect="applied", observed_at=start + timedelta(seconds=2), payload={"path": "app.py"})
    store.append(task_id="task", source="test", source_id="3", kind="test.run", status="timeout", effect="unknown", observed_at=start + timedelta(seconds=4))
    return store


def test_seed_views_are_stable_evidenced_and_temporal(tmp_path: Path) -> None:
    runtime = MemoryProgramRuntime(_ledger(tmp_path / "ledger.duckdb"))
    request = ViewRequest(task_id="task", program="task.progress")
    first = runtime.compute(request)
    assert runtime.compute(request) == first
    assert first.watermark == 3
    assert first.data["completed"] == ["file.write"]
    assert first.data["failed_or_blocked"] == ["test.run"]
    earlier = runtime.compute(
        request.model_copy(update={"as_of": datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC)})
    )
    assert earlier.watermark == 2
    assert len(earlier.evidence_event_ids) == 2


def test_memory_query_and_dream_preserve_limitations(tmp_path: Path) -> None:
    runtime = MemoryProgramRuntime(_ledger(tmp_path / "ledger.duckdb"))
    memory = runtime.compute(ViewRequest(task_id="task", program="task.memory", query="app.py"))
    assert memory.data["relevant"][0]["kind"] == "file.write"
    dream = runtime.compute(ViewRequest(task_id="task", program="dream.analysis"))
    assert dream.data["limitations"] == [{"seq": 3, "kind": "test.run", "status": "timeout"}]


def test_unknown_program_and_hard_byte_bound_fail_safely(tmp_path: Path) -> None:
    runtime = MemoryProgramRuntime(_ledger(tmp_path / "ledger.duckdb"))
    with pytest.raises(KeyError):
        runtime.compute(ViewRequest(task_id="task", program="invented"))
    result = runtime.compute(ViewRequest(task_id="task", program="history.model", max_bytes=128))
    assert result.truncated
    assert "summary" in result.data


def test_prompt_manifest_is_byte_stable_and_accounts_for_view_sources(tmp_path: Path) -> None:
    runtime = MemoryProgramRuntime(_ledger(tmp_path / "ledger.duckdb"))
    first = compile_prompt(runtime, task_id="task", static_prefix="stable", query="app.py")
    second = compile_prompt(runtime, task_id="task", static_prefix="stable", query="app.py")
    assert first == second
    assert [component.tier for component in first.components] == ["P0", "P1", "P2", "P3"]
    assert first.components[0].source_view_ids == ()
    assert all(component.source_view_ids for component in first.components[1:])
    assert first.prompt_hash == __import__("hashlib").sha256(first.render().encode()).hexdigest()
