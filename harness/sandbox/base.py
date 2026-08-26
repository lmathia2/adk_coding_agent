"""Command execution contracts for local and remote coding sandboxes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    command: str
    timeout_seconds: int = 120
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SandboxResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    omitted_bytes: int = 0
    artifact_uri: str | None = None

    def to_tool_result(self) -> dict[str, object]:
        output = self.stdout
        if self.stderr:
            output = f"{output}\n{self.stderr}" if output else self.stderr
        return {
            "status": self.status,
            "model_text": output,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
            "omitted_bytes": self.omitted_bytes,
            "artifact_uri": self.artifact_uri,
        }


class CommandSandbox(Protocol):
    workspace: Path

    def execute(self, request: SandboxRequest) -> SandboxResult: ...


__all__ = ["CommandSandbox", "SandboxRequest", "SandboxResult"]
