"""Bounded sandbox output with durable full-log spill."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .base import SandboxResult


def bounded_result(
    *,
    status: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    duration_ms: int,
    artifact_root: Path,
    max_bytes: int = 16_000,
) -> SandboxResult:
    stdout_bytes = stdout.encode(errors="replace")
    stderr_bytes = stderr.encode(errors="replace")
    total_bytes = len(stdout_bytes) + len(stderr_bytes)
    if total_bytes <= max_bytes:
        return SandboxResult(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )

    artifact_root.mkdir(parents=True, exist_ok=True)
    full = f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
    digest = hashlib.sha256(full.encode()).hexdigest()
    path = artifact_root / f"command-{digest}.log"
    if not path.exists():
        path.write_text(full, encoding="utf-8")

    head_budget = max_bytes // 2
    tail_budget = max_bytes - head_budget
    combined = f"{stdout}\n{stderr}"
    head = combined[:head_budget]
    tail = combined[-tail_budget:] if tail_budget else ""
    visible = (
        head
        + f"\n[... {total_bytes - max_bytes} bytes omitted; full log: {path} ...]\n"
        + tail
    )
    return SandboxResult(
        status=status,
        exit_code=exit_code,
        stdout=visible,
        stderr="",
        duration_ms=duration_ms,
        truncated=True,
        omitted_bytes=max(total_bytes - max_bytes, 0),
        artifact_uri=path.as_uri(),
    )


__all__ = ["bounded_result"]
