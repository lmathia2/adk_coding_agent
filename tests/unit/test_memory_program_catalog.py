from pathlib import Path

import pytest

from harness.ledger import DuckDbLedgerStore
from harness.memory.catalog import ProgramCatalog, validate_relational_program


def test_programs_require_replay_shadow_before_activation(tmp_path: Path) -> None:
    database = tmp_path / "ledger.duckdb"
    ledger = DuckDbLedgerStore(database)
    ledger.append(task_id="task", source="test", source_id="1", kind="failure", status="failed")
    catalog = ProgramCatalog(database)
    catalog.register(
        "failures",
        1,
        "SELECT kind, status FROM ledger_events WHERE task_id=:task_id AND status='failed'",
    )
    with pytest.raises(PermissionError):
        catalog.execute("failures", 1, task_id="task")
    catalog.transition("failures", 1, "shadow")
    catalog.transition("failures", 1, "active")
    assert catalog.execute("failures", 1, task_id="task") == [
        {"kind": "failure", "status": "failed"}
    ]
    catalog.transition("failures", 1, "retired")
    with pytest.raises(PermissionError):
        catalog.execute("failures", 1, task_id="task")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ledger_events",
        "SELECT * FROM ledger_events; DROP TABLE ledger_events",
        "SELECT * FROM secrets",
        "COPY ledger_events TO '/tmp/leak'",
    ],
)
def test_relational_program_validator_fails_closed(sql: str) -> None:
    with pytest.raises(ValueError):
        validate_relational_program(sql)
