"""Environment-derived configuration for the Agents CLI application."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from harness.context import build_static_prefix
from harness.repo import collect_project_instructions

_BASE_INSTRUCTION = """
You are an expert coding agent operating in an isolated repository workspace.

Work only toward the supplied goal and acceptance criteria. Inspect relevant code
before editing. Make the smallest coherent change that solves the task. Use read for
targeted line ranges and bash for rg, git, builds, and tests. Use edit for exact atomic
replacements and write for complete new or replaced files. Keep tool output and prose
concise. Do not claim completion without concrete evidence and deterministic
verification.

You may use tools for as many turns as needed inside this bounded work batch. When you
stop using tools, your final response MUST be exactly one JSON object with this shape:
{
  "status": "continue" | "verify" | "blocked" | "done",
  "progress": ["concise completed or discovered item"],
  "next_action": "one concrete next action or null",
  "decisions": ["decision and rationale"],
  "questions": ["question requiring user input"],
  "discovered_constraints": ["newly discovered constraint"],
  "files_in_focus": ["repository/relative/path"],
  "completion_claims": [
    {
      "criterion": "exact acceptance criterion",
      "evidence": ["test, command, path, or other concrete evidence"]
    }
  ]
}

Use status "verify" or "done" only when every acceptance criterion has concrete
evidence. The outer workflow—not this response—decides whether the task is complete.
Do not wrap the JSON in Markdown or add explanatory prose before or after it.
""".strip()


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    app_name: str
    model: str
    workspace: Path
    source_repository: Path | None
    state_root: Path
    task_id_override: str | None
    base_revision_override: str | None
    workspace_id_override: str | None
    max_iterations: int
    compact_at_tokens: int
    recent_event_limit: int
    static_instruction: str
    static_prefix: str


def _state_root(workspace: Path) -> Path:
    configured = os.getenv("ADK_CODING_STATE_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        digest = hashlib.sha256(workspace.as_posix().encode()).hexdigest()[:16]
        root = Path.home() / ".cache" / "adk-coding-agent" / digest
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_settings() -> HarnessSettings:
    workspace = Path(os.getenv("ADK_CODING_WORKSPACE", os.getcwd())).resolve()
    source_value = os.getenv("ADK_CODING_SOURCE_REPOSITORY")
    model = os.getenv("ADK_CODING_MODEL", "gemini-3.7-flash")
    project_instructions = collect_project_instructions(workspace)
    if len(project_instructions) > 16_000:
        project_instructions = (
            project_instructions[:16_000]
            + "\n[project instructions truncated; read the source files when needed]"
        )
    instruction = _BASE_INSTRUCTION
    if project_instructions:
        instruction += "\n\nStable project instructions:\n" + project_instructions

    return HarnessSettings(
        app_name="pi_inspired_adk_coding_agent",
        model=model,
        workspace=workspace,
        source_repository=(Path(source_value).resolve() if source_value else None),
        state_root=_state_root(workspace),
        task_id_override=os.getenv("ADK_CODING_TASK_ID"),
        base_revision_override=os.getenv("ADK_CODING_BASE_REVISION"),
        workspace_id_override=os.getenv("ADK_CODING_WORKSPACE_ID"),
        max_iterations=int(os.getenv("ADK_CODING_MAX_ITERATIONS", "40")),
        compact_at_tokens=int(
            os.getenv("ADK_CODING_COMPACT_AT_TOKENS", "80000")
        ),
        recent_event_limit=int(os.getenv("ADK_CODING_RECENT_EVENTS", "12")),
        static_instruction=instruction,
        static_prefix=build_static_prefix(
            model_name=model,
            instruction=instruction,
        ),
    )


SETTINGS = load_settings()

__all__ = ["SETTINGS", "HarnessSettings", "load_settings"]
