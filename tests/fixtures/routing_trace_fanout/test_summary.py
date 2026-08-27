import json
from pathlib import Path


def test_summary() -> None:
    summary = json.loads(Path("summary.json").read_text(encoding="utf-8"))
    assert summary == {
        "by_agent": {"coder": 9, "planner": 3, "reviewer": 3},
        "by_status": {"blocked": 1, "error": 1, "ok": 13},
        "by_tool": {"bash": 5, "edit": 2, "read": 7, "write": 1},
        "total_events": 15,
    }
    for value in (summary, summary["by_agent"], summary["by_status"], summary["by_tool"]):
        assert list(value) == sorted(value)
