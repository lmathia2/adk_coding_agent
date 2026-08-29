"""Side-effect-free ADK agent builders shared by factory and legacy bootstrap."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.adk import Agent
from google.adk.models import BaseLlm
from google.adk.tools import ToolContext

from harness.config import ToolSurfaceConfig
from harness.context import build_static_prefix
from harness.review import DiffReviewPacket, FinalDiffReview
from harness.tools.adk_adapter import AdkCodingTools, create_adk_tools

from .config import HarnessSettings

FINAL_REVIEW_INSTRUCTION = """
Review only the supplied final diff and deterministic verification summary. Identify
concrete correctness, security, reliability, maintainability, or scope defects that
were introduced by the diff. Do not speculate about code that is not shown. Prefer a
small number of actionable findings with exact paths and lines. Return `clear` when
there are no material findings. This review is advisory and has no tools.
""".strip()

ToolFunction = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class CodingWorkerBundle:
    agent: Agent
    read: ToolFunction
    bash: ToolFunction
    edit: ToolFunction
    write: ToolFunction


@dataclass(frozen=True, slots=True)
class ReviewerBundle:
    agent: Agent
    static_prefix: str


def build_coding_worker(
    settings: HarnessSettings,
    model: BaseLlm,
    *,
    tools: AdkCodingTools | None = None,
    tool_config: ToolSurfaceConfig | None = None,
) -> CodingWorkerBundle:
    active_tools = tools or create_adk_tools(settings.workspace)
    active_tool_config = tool_config or ToolSurfaceConfig()
    read_default_lines = active_tool_config.read_default_lines
    bash_default_timeout = active_tool_config.bash_default_timeout_seconds

    def _runtime_identity(
        tool_context: ToolContext | None,
    ) -> tuple[str | None, str | None]:
        if tool_context is None:
            return settings.task_id_override, None
        task_id = tool_context.state.get("task_id") or settings.task_id_override
        invocation_id = getattr(tool_context, "invocation_id", None)
        return (
            str(task_id) if task_id else None,
            str(invocation_id) if invocation_id else None,
        )

    def read(
        path: str,
        offset: int = 1,
        limit: int = read_default_lines,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Read a bounded range from a workspace file or recoverable artifact URI."""

        del tool_context
        return active_tools.read(path=path, offset=offset, limit=limit)

    def bash(
        command: str,
        timeout_seconds: int = bash_default_timeout,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run a bounded command or an in-process indexed search operation."""

        task_scope, _ = _runtime_identity(tool_context)
        return active_tools.bash(
            command=command,
            timeout_seconds=timeout_seconds,
            task_scope=task_scope,
        )

    def edit(
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Atomically replace one exact, unique preimage in a workspace file."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        return active_tools.edit(
            path=path,
            old_text=old_text,
            new_text=new_text,
            expected_sha256=expected_sha256,
            task_scope=task_scope,
            invocation_id=invocation_id,
        )

    def write(
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Atomically write a complete file with optimistic concurrency."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        return active_tools.write(
            path=path,
            content=content,
            expected_sha256=expected_sha256,
            expected_absent=expected_absent,
            task_scope=task_scope,
            invocation_id=invocation_id,
        )

    agent = Agent(
        name="coding_worker",
        model=model,
        description="Executes one bounded coding work batch with four composable tools.",
        static_instruction=settings.static_instruction,
        instruction="",
        tools=[read, bash, edit, write],
    )
    return CodingWorkerBundle(agent=agent, read=read, bash=bash, edit=edit, write=write)


def build_final_diff_reviewer(
    model: BaseLlm,
    *,
    model_name: str | None = None,
    instruction: str = FINAL_REVIEW_INSTRUCTION,
) -> ReviewerBundle:
    agent = Agent(
        name="final_diff_reviewer",
        model=model,
        description="Advisory, bounded review of a deterministically verified final diff.",
        static_instruction=instruction,
        instruction="",
        tools=[],
        include_contents="none",
        mode="single_turn",
        output_schema=FinalDiffReview,
    )
    return ReviewerBundle(
        agent=agent,
        static_prefix=build_static_prefix(
            model_name=model_name or model.model,
            tool_names=(),
            instruction=instruction,
        ),
    )


def build_review_input(
    packet: DiffReviewPacket,
    verification: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "diff_packet": packet.model_dump(mode="json"),
            "verification": verification,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_final_diff_review(value: Any) -> FinalDiffReview:
    if isinstance(value, FinalDiffReview):
        return value
    if isinstance(value, str):
        return FinalDiffReview.model_validate_json(value)
    return FinalDiffReview.model_validate(value)


__all__ = [
    "FINAL_REVIEW_INSTRUCTION",
    "CodingWorkerBundle",
    "ReviewerBundle",
    "build_coding_worker",
    "build_final_diff_reviewer",
    "build_review_input",
    "parse_final_diff_review",
]
