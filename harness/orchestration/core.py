"""Pure orchestration decisions kept independent of Google ADK runtime objects."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Iterable

from harness.models.agent_step import AgentStep
from harness.models.ledger import TaskLedger
from harness.models.task import TaskRequest
from harness.state.progress import ProgressRoute, route_for_progress


class HarnessRoute(StrEnum):
    CONTINUE = "continue"
    COMPACT = "compact"
    REPLAN = "replan"
    BLOCKED = "blocked"
    VERIFY = "verify"


def create_initial_ledger(
    request: TaskRequest,
    *,
    task_id: str,
    base_revision: str,
    workspace_id: str,
    branch_id: str,
) -> TaskLedger:
    return TaskLedger(
        task_id=task_id,
        goal=request.goal,
        acceptance_criteria=request.acceptance_criteria,
        constraints=request.constraints,
        non_goals=request.non_goals,
        base_revision=base_revision,
        workspace_id=workspace_id,
        branch_id=branch_id,
        next_action="Inspect the repository and identify the smallest coherent change",
    )


def reduce_agent_step(ledger: TaskLedger, step: AgentStep) -> TaskLedger:
    """Project one model work batch into the durable ledger schema."""

    data = ledger.model_dump(mode="python")
    data["iteration"] = int(data.get("iteration", 0)) + 1
    if step.next_action:
        data["next_action"] = step.next_action
    if step.progress:
        data["no_progress_count"] = 0
    if "constraints" in data:
        data["constraints"] = list(
            dict.fromkeys([*data.get("constraints", []), *step.discovered_constraints])
        )
    if "open_questions" in data:
        data["open_questions"] = list(
            dict.fromkeys([*data.get("open_questions", []), *step.questions])
        )

    if step.status == "blocked":
        data["phase"] = "blocked"
        data["status"] = "needs_input"
        blockers = list(data.get("blockers", []))
        blockers.extend(step.questions or [step.next_action or "Coding agent is blocked"])
        data["blockers"] = list(dict.fromkeys(blockers))
    elif step.status in {"verify", "done"}:
        data["phase"] = "verify"
        data["status"] = "verifying"
    else:
        data["phase"] = "implement"
        data["status"] = "active"

    return TaskLedger.model_validate(data)


def decide_route(
    ledger: TaskLedger,
    step: AgentStep,
    *,
    should_compact: bool = False,
) -> HarnessRoute:
    if step.status == "blocked":
        return HarnessRoute.BLOCKED
    if step.status in {"verify", "done"}:
        return HarnessRoute.VERIFY
    progress_route = route_for_progress(ledger)
    if progress_route == ProgressRoute.NEEDS_INPUT:
        return HarnessRoute.BLOCKED
    if progress_route == ProgressRoute.REPLAN:
        return HarnessRoute.REPLAN
    if should_compact:
        return HarnessRoute.COMPACT
    return HarnessRoute.CONTINUE


def build_work_packet(
    ledger: TaskLedger,
    *,
    project_instructions: str = "",
    repository_manifest: str = "",
    repository_map: str = "",
    compaction_summary: str = "",
    recent_events: Iterable[str] = (),
    steering_messages: Iterable[str] = (),
) -> str:
    """Build deterministic dynamic input after the cache-stable system prefix."""

    sections: list[tuple[str, str]] = [
        ("TASK", json.dumps(ledger.compact_projection(), sort_keys=True, indent=2)),
        ("PROJECT INSTRUCTIONS", project_instructions.strip()),
        ("REPOSITORY MANIFEST", repository_manifest.strip()),
        ("REPOSITORY MAP", repository_map.strip()),
        ("COMPACTED HISTORY", compaction_summary.strip()),
        ("RECENT EVENTS", "\n".join(recent_events).strip()),
        ("USER STEERING", "\n".join(steering_messages).strip()),
    ]
    rendered = [f"## {title}\n{body}" for title, body in sections if body]
    return "\n\n".join(rendered)


def replan_ledger(ledger: TaskLedger) -> TaskLedger:
    data = ledger.model_dump(mode="python")
    data["phase"] = "plan"
    data["status"] = "active"
    data["no_progress_count"] = 0
    data["next_action"] = (
        "Reassess the current evidence and choose a materially different approach"
    )
    return TaskLedger.model_validate(data)
