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

    combined = stdout_bytes + b"\n" + stderr_bytes
    omitted_bytes = total_bytes
    marker = b""
    data_budget = 0
    for _ in range(2):
        marker = (
            f"\n[... {omitted_bytes} bytes omitted; full log: {path} ...]\n"
        ).encode()
        data_budget = max(max_bytes - len(marker), 0)
        omitted_bytes = max(total_bytes - data_budget, 0)
    if len(marker) >= max_bytes:
        visible_bytes = marker[:max_bytes]
    else:
        head_budget = data_budget // 2
        tail_budget = data_budget - head_budget
        head = combined[:head_budget]
        tail = combined[-tail_budget:] if tail_budget else b""
        visible_bytes = head + marker + tail
    visible = visible_bytes.decode("utf-8", errors="ignore")
    return SandboxResult(
        status=status,
        exit_code=exit_code,
        stdout=visible,
        stderr="",
        duration_ms=duration_ms,
        truncated=True,
        omitted_bytes=omitted_bytes,
        artifact_uri=path.as_uri(),
    )


__all__ = ["bounded_result"]
