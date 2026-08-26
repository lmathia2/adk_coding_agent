from __future__ import annotations

from pathlib import Path

from harness.models.agent_step import AgentStep, CompletionClaim
from harness.models.task import TaskRequest
from harness.orchestration import (
    HarnessRoute,
    build_work_packet,
    create_initial_ledger,
    decide_route,
    parse_agent_step,
    parse_task_request,
    reduce_agent_step,
    replan_ledger,
)
from harness.tools.adk_adapter import create_adk_tools


def _ledger():
    return create_initial_ledger(
        TaskRequest(goal="Fix login", acceptance_criteria=["Login works"]),
        task_id="task",
        base_revision="abc",
        workspace_id="workspace",
        branch_id="main",
    )


def test_task_and_step_parsing() -> None:
    request = parse_task_request("Fix login")
    assert request.goal == "Fix login"
    step = parse_agent_step(
        '{"status":"verify","progress":["implemented"],"completion_claims":[]}'
    )
    assert step.status == "verify"


def test_reducer_and_routes() -> None:
    ledger = _ledger()
    step = AgentStep(status="continue", progress=["found service"], next_action="edit it")
    ledger = reduce_agent_step(ledger, step)
    assert ledger.next_action == "edit it"
    assert decide_route(ledger, step) == HarnessRoute.CONTINUE

    verify = AgentStep(
        status="done",
        completion_claims=[CompletionClaim(criterion="Login works", evidence=["pytest"])],
    )
    ledger = reduce_agent_step(ledger, verify)
    assert decide_route(ledger, verify) == HarnessRoute.VERIFY

    replanned = replan_ledger(ledger)
    assert replanned.phase == "plan"
    assert replanned.no_progress_count == 0


def test_work_packet_is_deterministic_and_steering_is_last() -> None:
    ledger = _ledger()
    packet = build_work_packet(
        ledger,
        project_instructions="Be careful",
        repository_manifest="Python project",
        repository_map="auth.py: login",
        recent_events=["read auth.py"],
        steering_messages=["Do not change the API"],
    )
    assert packet.index("## TASK") < packet.index("## REPOSITORY MAP")
    assert packet.rfind("## USER STEERING") > packet.index("## RECENT EVENTS")


def test_adk_tool_adapter_exposes_four_working_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    tools = create_adk_tools(tmp_path)
    tools.write("hello.py", "print('hello')\n", expected_absent=True)
    assert "hello" in tools.read("hello.py")["model_text"]
    tools.edit("hello.py", "hello", "world")
    assert "world" in tools.read("hello.py")["model_text"]
    assert tools.bash("python hello.py")["exit_code"] == 0
