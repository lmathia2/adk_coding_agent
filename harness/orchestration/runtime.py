"""Runtime helpers shared by the ADK workflow and deterministic tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from harness.models.agent_step import AgentStep
from harness.models.task import TaskRequest


def parse_task_request(value: str | dict[str, Any] | TaskRequest) -> TaskRequest:
    if isinstance(value, TaskRequest):
        return value
    if isinstance(value, dict):
        return TaskRequest.model_validate(value)
    text = value.strip()
    if text.startswith("{"):
        try:
            return TaskRequest.model_validate_json(text)
        except ValueError:
            pass
    return TaskRequest(
        goal=text,
        acceptance_criteria=[
            "The requested change is implemented and deterministic verification passes"
        ],
    )


def parse_agent_step(value: str | dict[str, Any] | AgentStep) -> AgentStep:
    if isinstance(value, AgentStep):
        return value
    if isinstance(value, dict):
        return AgentStep.model_validate(value)
    return AgentStep.model_validate_json(value)


def task_id_for(request: TaskRequest, session_id: str | None = None) -> str:
    canonical = json.dumps(
        {
            "session_id": session_id or "",
            "request": request.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


def changed_paths(root: Path, base_revision: str | None) -> list[str]:
    command = ["git", "diff", "--name-only"]
    if base_revision and base_revision != "unknown":
        command.append(base_revision)
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        return []
    return sorted(path for path in completed.stdout.splitlines() if path.strip())
