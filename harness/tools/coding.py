"""The four model-visible Pi-style coding tools."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from harness.environment import FileConflictError, WorkspaceViolationError, active_environment
from harness.models import ToolEnvelope, ToolStatus
from harness.policy import CommandPolicy

from .output import bound_output

_TOOL_OBSERVER: ContextVar[Callable[[str, dict[str, object], ToolEnvelope], None] | None] = ContextVar(
    "adk_coding_tool_observer", default=None
)
_COMMAND_POLICY: ContextVar[CommandPolicy | None] = ContextVar(
    "adk_coding_command_policy", default=None
)


def _emit(name: str, arguments: dict[str, object], envelope: ToolEnvelope) -> ToolEnvelope:
    observer = _TOOL_OBSERVER.get()
    if observer is not None:
        observer(name, arguments, envelope)
    return envelope


@contextmanager
def bind_tool_runtime(
    *,
    policy: CommandPolicy | None = None,
    observer: Callable[[str, dict[str, object], ToolEnvelope], None] | None = None,
) -> Iterator[None]:
    policy_token: Token[CommandPolicy | None] | None = None
    observer_token: Token[Callable[[str, dict[str, object], ToolEnvelope], None] | None] | None = None
    if policy is not None:
        policy_token = _COMMAND_POLICY.set(policy)
    if observer is not None:
        observer_token = _TOOL_OBSERVER.set(observer)
    try:
        yield
    finally:
        if observer_token is not None:
            _TOOL_OBSERVER.reset(observer_token)
        if policy_token is not None:
            _COMMAND_POLICY.reset(policy_token)


def _error(name: str, arguments: dict[str, object], exc: Exception) -> ToolEnvelope:
    status = ToolStatus.BLOCKED if isinstance(exc, WorkspaceViolationError) else ToolStatus.ERROR
    return _emit(
        name,
        arguments,
        ToolEnvelope(status=status, model_text=f"{type(exc).__name__}: {exc}"),
    )


def execute_read(path: str, offset: int = 1, limit: int = 400) -> ToolEnvelope:
    arguments: dict[str, object] = {"path": path, "offset": offset, "limit": limit}
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
        relative = environment.resolve(path, must_exist=True).relative_to(environment.root).as_posix()
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
        return _emit("read", arguments, envelope)
    except Exception as exc:
        return _error("read", arguments, exc)


def execute_edit(
    path: str,
    old_text: str,
    new_text: str,
    expected_sha256: str | None = None,
) -> ToolEnvelope:
    arguments: dict[str, object] = {
        "path": path,
        "old_text_sha256": hashlib.sha256(old_text.encode()).hexdigest(),
        "new_text_sha256": hashlib.sha256(new_text.encode()).hexdigest(),
        "expected_sha256": expected_sha256,
    }
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
        return _emit("edit", arguments, envelope)
    except (FileConflictError, WorkspaceViolationError, FileNotFoundError, ValueError) as exc:
        return _error("edit", arguments, exc)


def execute_write(
    path: str,
    content: str,
    expected_sha256: str | None = None,
    expected_absent: bool = False,
) -> ToolEnvelope:
    arguments: dict[str, object] = {
        "path": path,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "expected_sha256": expected_sha256,
        "expected_absent": expected_absent,
    }
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
        return _emit("write", arguments, envelope)
    except (FileConflictError, WorkspaceViolationError, ValueError) as exc:
        return _error("write", arguments, exc)


def execute_bash(command: str, timeout_seconds: int = 120) -> ToolEnvelope:
    arguments: dict[str, object] = {"command": command, "timeout_seconds": timeout_seconds}
    started = time.monotonic()
    policy = _COMMAND_POLICY.get() or CommandPolicy()
    decision = policy.evaluate(command)
    if not decision.allowed:
        return _emit(
            "bash",
            arguments,
            ToolEnvelope(
                status=ToolStatus.BLOCKED,
                model_text=(
                    f"Command blocked ({decision.command_class.value}): {decision.reason}. "
                    "Choose a safer local command or request explicit approval."
                ),
                command_class=decision.command_class,
            ),
        )
    try:
        environment = active_environment()
        result = environment.run(command, timeout_seconds=max(1, min(timeout_seconds, 1_800)))
        full_text = ""
        if result.stdout:
            full_text += f"[stdout]\n{result.stdout}"
        if result.stderr:
            full_text += ("\n" if full_text else "") + f"[stderr]\n{result.stderr}"
        if not full_text:
            full_text = "(no output)"
        bounded = bound_output(full_text)
        artifact_uri = None
        if bounded.truncated:
            artifact_uri = environment.store_artifact(
                "tool-output",
                full_text.encode("utf-8", errors="replace"),
            )
        status = (
            ToolStatus.TIMEOUT
            if result.timed_out
            else (ToolStatus.OK if result.exit_code == 0 else ToolStatus.ERROR)
        )
        footer = (
            f"exit_code: {result.exit_code}\nduration_ms: {result.duration_ms}\n"
            f"classification: {decision.command_class.value}"
        )
        if artifact_uri:
            footer += f"\nfull_output: {artifact_uri}"
        envelope = ToolEnvelope(
            status=status,
            model_text=f"{bounded.text}\n\n{footer}",
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            truncated=bounded.truncated,
            omitted_bytes=bounded.omitted_bytes,
            artifact_uri=artifact_uri,
            command_class=decision.command_class,
            ui_details={"command": command, "timed_out": result.timed_out},
        )
        return _emit("bash", arguments, envelope)
    except Exception as exc:
        envelope = ToolEnvelope(
            status=ToolStatus.ERROR,
            model_text=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.monotonic() - started) * 1_000),
            command_class=decision.command_class,
        )
        return _emit("bash", arguments, envelope)


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


def bash(command: str, timeout_seconds: int = 120) -> str:
    """Run an approved local shell command for search, git inspection, build, or tests."""

    return execute_bash(command, timeout_seconds).model_text


def tool_arguments_fingerprint(name: str, arguments: dict[str, object]) -> str:
    payload = json.dumps(
        {"name": name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
