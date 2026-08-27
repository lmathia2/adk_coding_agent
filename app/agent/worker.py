"""Cache-stable coding worker with exactly four model-visible tools."""

from __future__ import annotations

from typing import Any

from google.adk import Agent
from google.adk.models import Gemini
from google.genai import types

from harness.tools.adk_adapter import create_adk_tools

from .config import SETTINGS

_TOOLS = create_adk_tools(SETTINGS.workspace)


def read(path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
    """Read a bounded range from a workspace file or recoverable artifact URI."""

    return _TOOLS.read(path=path, offset=offset, limit=limit)


def bash(command: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Run a bounded command or an in-process `search grep|find|health` operation."""

    return _TOOLS.bash(command=command, timeout_seconds=timeout_seconds)


def edit(
    path: str,
    old_text: str,
    new_text: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Atomically replace one exact, unique preimage in a workspace file."""

    return _TOOLS.edit(
        path=path,
        old_text=old_text,
        new_text=new_text,
        expected_sha256=expected_sha256,
    )


def write(
    path: str,
    content: str,
    expected_sha256: str | None = None,
    expected_absent: bool = False,
) -> dict[str, Any]:
    """Atomically write a complete workspace file with optimistic concurrency."""

    return _TOOLS.write(
        path=path,
        content=content,
        expected_sha256=expected_sha256,
        expected_absent=expected_absent,
    )


coding_worker = Agent(
    name="coding_worker",
    model=Gemini(
        model=SETTINGS.model,
        retry_options=types.HttpRetryOptions(
            attempts=3,
            exp_base=2,
            initial_delay=1,
            http_status_codes=[429, 500, 502, 503, 504],
        ),
    ),
    description="Executes one bounded coding work batch with four composable tools.",
    static_instruction=SETTINGS.static_instruction,
    instruction="",
    tools=[read, bash, edit, write],
)

__all__ = ["bash", "coding_worker", "edit", "read", "write"]
