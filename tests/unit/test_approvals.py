from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.approvals import ApprovalStore
from harness.approvals.__main__ import main
from harness.tools.adk_adapter import create_adk_tools


def test_approval_store_is_idempotent_and_decisions_are_final(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "approvals.db")
    first = store.request(
        task_id="task",
        fingerprint="fingerprint",
        operation="printf approved",
        risk="unknown",
        reason="review required",
    )
    duplicate = store.request(
        task_id="task",
        fingerprint="fingerprint",
        operation="printf approved",
        risk="unknown",
        reason="review required",
    )
    assert duplicate.request_id == first.request_id

    approved = store.decide(
        first.request_id,
        decision="approved",
        actor="reviewer",
        note="safe local command",
    )
    assert approved.status == "approved"
    assert store.is_approved("task", "fingerprint")
    assert (
        store.decide(
            first.request_id,
            decision="approved",
            actor="reviewer",
        ).status
        == "approved"
    )
    with pytest.raises(ValueError, match="already decided"):
        store.decide(
            first.request_id,
            decision="denied",
            actor="other",
        )


def test_managed_command_executes_after_exact_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(state))
    monkeypatch.setenv("ADK_CODING_TASK_ID", "task")
    tools = create_adk_tools(tmp_path)

    blocked = tools.bash("printf approved")
    assert blocked["status"] == "blocked"
    request_id = blocked["approval_request_id"]
    assert request_id

    ApprovalStore(state / "approvals.db").decide(
        request_id,
        decision="approved",
        actor="test",
    )
    result = tools.bash("printf approved")
    assert result["status"] == "ok"
    assert "approved" in result["model_text"]


def test_approval_cli_lists_and_decides_requests(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "approvals.db"
    request = ApprovalStore(database).request(
        task_id="task",
        fingerprint="fp",
        operation="command",
        risk="unknown",
        reason="review",
    )
    assert main(["--database", str(database), "list", "--status", "pending"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["request_id"] == request.request_id

    assert (
        main(
            [
                "--database",
                str(database),
                "approve",
                request.request_id,
                "--actor",
                "tester",
            ]
        )
        == 0
    )
    decided = json.loads(capsys.readouterr().out)
    assert decided["status"] == "approved"
