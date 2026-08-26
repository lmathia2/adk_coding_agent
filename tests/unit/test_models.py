from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.models import (
    AgentStep,
    Decision,
    StepStatus,
    TaskLedger,
    TaskRequest,
)


def test_task_request_adds_default_acceptance_criterion() -> None:
    request = TaskRequest(goal="Fix the parser")
    assert request.acceptance_criteria == ["The requested change is implemented and verified."]


def test_strict_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TaskRequest(goal="Fix it", surprise=True)  # type: ignore[call-arg]


def test_canonical_hash_is_stable_across_input_order() -> None:
    first = TaskRequest(
        goal="Fix it",
        acceptance_criteria=["Tests pass"],
        constraints=["Keep API stable"],
    )
    second = TaskRequest.model_validate(
        {
            "constraints": ["Keep API stable"],
            "acceptance_criteria": ["Tests pass"],
            "goal": "Fix it",
        }
    )
    assert first.canonical_json() == second.canonical_json()
    assert first.content_hash() == second.content_hash()


def test_ledger_projection_is_bounded_and_model_facing() -> None:
    request = TaskRequest(goal="Fix it", acceptance_criteria=["Tests pass"])
    ledger = TaskLedger.from_request(
        request,
        task_id="task-1",
        workspace_id="workspace-1",
        base_revision="abc123",
    )
    ledger.progress = [f"progress-{index}" for index in range(30)]
    ledger.files_read = [f"src/file_{index}.py" for index in range(30)]
    ledger.files_modified = ["src/changed.py"]
    projection = ledger.compact_projection()

    assert projection["goal"] == "Fix it"
    assert projection["recent_progress"] == [f"progress-{index}" for index in range(18, 30)]
    assert "created_at" not in projection
    files_in_focus = projection["files_in_focus"]
    assert isinstance(files_in_focus, list)
    assert "src/changed.py" in files_in_focus


def test_agent_step_round_trip() -> None:
    step = AgentStep(
        status=StepStatus.VERIFY,
        progress=["Implemented parser fix"],
        decisions=[Decision(summary="Preserve public API", rationale="Compatibility")],
        files_modified=["src/parser.py"],
    )
    assert AgentStep.model_validate_json(step.model_dump_json()) == step
