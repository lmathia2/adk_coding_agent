"""Environment interface used by every model-visible coding tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from harness.models import CommandResult


class WorkspaceViolationError(ValueError):
    """Raised when a requested path leaves the configured workspace."""


class FileConflictError(RuntimeError):
    """Raised when optimistic concurrency or an exact edit precondition fails."""


@dataclass(frozen=True, slots=True)
class FileMutationResult:
    path: str
    changed: bool
    before_sha256: str | None
    after_sha256: str
    diff: str
    already_applied: bool = False


class WorkspaceEnvironment(Protocol):
    """Pluggable workspace contract; tools never access the host directly."""

    root: Path
    artifact_root: Path

    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path: ...

    def read_bytes(self, path: str | Path) -> bytes: ...

    def atomic_write(
        self,
        path: str | Path,
        content: bytes,
        *,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> FileMutationResult: ...

    def replace_text(
        self,
        path: str | Path,
        old_text: str,
        new_text: str,
        *,
        expected_sha256: str | None = None,
    ) -> FileMutationResult: ...

    def run(
        self,
        command: str,
        *,
        timeout_seconds: int,
        extra_env: dict[str, str] | None = None,
    ) -> CommandResult: ...

    def store_artifact(self, category: str, content: bytes, *, suffix: str = ".txt") -> str: ...
