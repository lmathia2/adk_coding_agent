"""The four model-visible Pi-style coding tools."""

from __future__ import annotations

import hashlib
import json
import time

from harness.environment import FileConflictError, WorkspaceViolationError, active_environment
from harness.models import ToolEnvelope, ToolStatus

from .output import bound_output


def _error(exc: Exception) -> ToolEnvelope:
    status = ToolStatus.BLOCKED if isinstance(exc, WorkspaceViolationError) else ToolStatus.ERROR
    return ToolEnvelope(status=status, model_text=f"{type(exc).__name__}: {exc}")


def execute_read(path: str, offset: int = 1, limit: int = 400) -> ToolEnvelope:
    started = time.monotonic()
    try:
        if offset < 1:
            raise ValueError("offset must be at least 1")
        if limit < 1 or limit > 400:
            raise ValueError("limit must be between 1 and 400 lines")
        environment = active_environment()
        content = environment.read_bytes(path)
        if b"\x00" in content[:8_192]:
            raise ValueError(f"Binary file cannot be read as text: {path}")
        text = content.decode("utf-8")
        lines = text.splitlines()
        start = min(offset - 1, len(lines))
        selected = lines[start : start + limit]
        rendered = "\n".join(
            f"{start + index + 1:>6} | {line}" for index, line in enumerate(selected)
        )
        bounded = bound_output(rendered, max_chars=32_000, max_lines=400)
        digest = hashlib.sha256(content).hexdigest()
        relative = (
            environment.resolve(path, must_exist=True).relative_to(environment.root).as_posix()
        )
        header = (
            f"{relative}\nsha256: {digest}\n"
            f"lines: {start + 1}-{start + len(selected)} of {len(lines)}"
        )
        if start + len(selected) < len(lines):
            header += f"\n[more available: read offset={start + len(selected) + 1}]"
        envelope = ToolEnvelope(
            status=ToolStatus.OK,
            model_text=f"{header}\n\n{bounded.text}",
            duration_ms=int((time.monotonic() - started) * 1_000),
            truncated=bounded.truncated,
            omitted_bytes=bounded.omitted_bytes,
            content_hashes={relative: digest},
            ui_details={"path": relative, "total_lines": len(lines)},
        )
        return envelope
    except Exception as exc:
        return _error(exc)


def execute_edit(
    path: str,
    old_text: str,
    new_text: str,
    expected_sha256: str | None = None,
) -> ToolEnvelope:
    started = time.monotonic()
    try:
        result = active_environment().replace_text(
            path,
            old_text,
            new_text,
            expected_sha256=expected_sha256,
        )
        bounded = bound_output(result.diff, max_chars=12_000, max_lines=240)
        verb = "already applied" if result.already_applied else "updated"
        envelope = ToolEnvelope(
            status=ToolStatus.OK,
            model_text=f"{verb}: {result.path}\nsha256: {result.after_sha256}\n\n{bounded.text}",
            duration_ms=int((time.monotonic() - started) * 1_000),
            truncated=bounded.truncated,
            omitted_bytes=bounded.omitted_bytes,
            changed_paths=[result.path] if result.changed else [],
            content_hashes={result.path: result.after_sha256},
            ui_details={"changed": result.changed, "already_applied": result.already_applied},
        )
        return envelope
    except (FileConflictError, WorkspaceViolationError, FileNotFoundError, ValueError) as exc:
        return _error(exc)


def execute_write(
    path: str,
    content: str,
    expected_sha256: str | None = None,
    expected_absent: bool = False,
) -> ToolEnvelope:
    started = time.monotonic()
    try:
        result = active_environment().atomic_write(
            path,
            content.encode("utf-8"),
            expected_sha256=expected_sha256,
            expected_absent=expected_absent,
        )
        bounded = bound_output(result.diff, max_chars=12_000, max_lines=240)
        verb = "already present" if result.already_applied else "wrote"
        envelope = ToolEnvelope(
            status=ToolStatus.OK,
            model_text=f"{verb}: {result.path}\nsha256: {result.after_sha256}\n\n{bounded.text}",
            duration_ms=int((time.monotonic() - started) * 1_000),
            truncated=bounded.truncated,
            omitted_bytes=bounded.omitted_bytes,
            changed_paths=[result.path] if result.changed else [],
            content_hashes={result.path: result.after_sha256},
            ui_details={"changed": result.changed, "already_applied": result.already_applied},
        )
        return envelope
    except (FileConflictError, WorkspaceViolationError, ValueError) as exc:
        return _error(exc)


def read(path: str, offset: int = 1, limit: int = 400) -> str:
    """Read a UTF-8 file range inside the workspace with line numbers."""

    return execute_read(path, offset, limit).model_text


def edit(path: str, old_text: str, new_text: str, expected_sha256: str | None = None) -> str:
    """Atomically replace one exact text occurrence in a workspace file."""

    return execute_edit(path, old_text, new_text, expected_sha256).model_text


def write(
    path: str,
    content: str,
    expected_sha256: str | None = None,
    expected_absent: bool = False,
) -> str:
    """Atomically create or replace one UTF-8 workspace file."""

    return execute_write(path, content, expected_sha256, expected_absent).model_text


def tool_arguments_fingerprint(name: str, arguments: dict[str, object]) -> str:
    payload = json.dumps(
        {"name": name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
