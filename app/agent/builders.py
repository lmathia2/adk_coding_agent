"""Side-effect-free ADK agent builders shared by factory and legacy bootstrap."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from google.adk import Agent
from google.adk.models import BaseLlm
from google.adk.tools import ToolContext

from harness.config import ToolSurfaceConfig
from harness.tools.adk_adapter import AdkCodingTools, create_adk_tools

from .config import HarnessSettings

LOGGER = logging.getLogger(__name__)

ToolFunction = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class CodingWorkerBundle:
    agent: Agent
    read: ToolFunction
    bash: ToolFunction
    edit: ToolFunction
    write: ToolFunction


def build_coding_worker(
    settings: HarnessSettings,
    model: BaseLlm,
    *,
    tools: AdkCodingTools | None = None,
    tool_config: ToolSurfaceConfig | None = None,
) -> CodingWorkerBundle:
    active_tools = tools or create_adk_tools(
        settings.workspace,
        state_root=settings.state_root,
    )
    active_tool_config = tool_config or ToolSurfaceConfig()
    read_default_lines = active_tool_config.read_default_lines
    bash_default_timeout = active_tool_config.bash_default_timeout_seconds

    def _invoke_tool(operation: str, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Keep expected and unexpected tool failures inside the tool protocol."""

        try:
            return call()
        except Exception as error:
            LOGGER.info(
                "model tool %s returned a recoverable %s",
                operation,
                type(error).__name__,
            )
            raw_message = " ".join(str(error).split())
            message = raw_message[:1_000]
            return {
                "status": "error",
                "model_text": f"{operation} failed: {type(error).__name__}: {message}",
                "truncated": len(raw_message) > 1_000,
                "omitted_bytes": max(
                    0,
                    len(raw_message.encode()) - len(message.encode()),
                ),
                "ui_details": {
                    "error_type": type(error).__name__,
                    "recoverable": True,
                },
            }

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

    async def read(
        path: str,
        offset: int = 1,
        limit: int = read_default_lines,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Read a bounded range from a workspace file or recoverable artifact URI."""

        del tool_context
        return await asyncio.to_thread(
            _invoke_tool,
            "read",
            lambda: active_tools.read(path=path, offset=offset, limit=limit),
        )

    async def bash(
        command: str,
        timeout_seconds: int = bash_default_timeout,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run a bounded command or an in-process indexed search operation."""

        task_scope, _ = _runtime_identity(tool_context)
        return await asyncio.to_thread(
            _invoke_tool,
            "bash",
            lambda: active_tools.bash(
                command=command,
                timeout_seconds=timeout_seconds,
                task_scope=task_scope,
            ),
        )

    async def edit(
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Atomically replace one exact, unique preimage in a workspace file."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        return await asyncio.to_thread(
            _invoke_tool,
            "edit",
            lambda: active_tools.edit(
                path=path,
                old_text=old_text,
                new_text=new_text,
                expected_sha256=expected_sha256,
                task_scope=task_scope,
                invocation_id=invocation_id,
            ),
        )

    async def write(
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Atomically write a complete file with optimistic concurrency."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        return await asyncio.to_thread(
            _invoke_tool,
            "write",
            lambda: active_tools.write(
                path=path,
                content=content,
                expected_sha256=expected_sha256,
                expected_absent=expected_absent,
                task_scope=task_scope,
                invocation_id=invocation_id,
            ),
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


__all__ = ["CodingWorkerBundle", "build_coding_worker"]
