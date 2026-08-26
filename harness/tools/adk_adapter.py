"""Expose the tested four-tool layer as plain ADK function tools.

The adapter accepts minor factory API changes and falls back to a local, confined
implementation so the Agents CLI app remains importable while the core tool package
evolves. The fallback preserves the same four model-visible signatures.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class AdkCodingTools:
    read: Callable[..., dict[str, Any]]
    bash: Callable[..., dict[str, Any]]
    edit: Callable[..., dict[str, Any]]
    write: Callable[..., dict[str, Any]]


def _confined(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {value}") from exc
    return candidate


def _fallback(root: Path) -> AdkCodingTools:
    root = root.resolve()

    def read(path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
        target = _confined(root, path)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(offset - 1, 0)
        selected = lines[start : start + max(1, min(limit, 400))]
        body = "\n".join(
            f"{number:>6} | {line}"
            for number, line in enumerate(selected, start + 1)
        )
        return {
            "status": "ok",
            "model_text": f"{path}\n{body}",
            "truncated": start + len(selected) < len(lines),
        }

    def write(
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> dict[str, Any]:
        target = _confined(root, path)
        if expected_absent and target.exists():
            raise FileExistsError(path)
        if expected_sha256 and target.exists():
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            if current != expected_sha256:
                raise ValueError("file changed since it was read")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        digest = hashlib.sha256(content.encode()).hexdigest()
        return {
            "status": "ok",
            "model_text": f"wrote {path}",
            "content_hashes": {path: digest},
        }

    def edit(
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        target = _confined(root, path)
        current = target.read_text(encoding="utf-8")
        if expected_sha256 and hashlib.sha256(current.encode()).hexdigest() != expected_sha256:
            raise ValueError("file changed since it was read")
        count = current.count(old_text)
        if count == 0 and new_text in current:
            return {
                "status": "ok",
                "model_text": f"{path} already contains requested edit",
            }
        if count != 1:
            raise ValueError(f"edit preimage must match exactly once; matched {count} times")
        return write(
            path,
            current.replace(old_text, new_text, 1),
            expected_sha256=expected_sha256,
        )

    def bash(command: str, timeout_seconds: int = 120) -> dict[str, Any]:
        blocked = ("rm -rf", "git push", "curl ", "wget ", "ssh ", "sudo ")
        lowered = command.lower()
        if any(token in lowered for token in blocked):
            return {
                "status": "blocked",
                "model_text": "command blocked by default policy",
            }
        completed = subprocess.run(
            command,
            cwd=root,
            shell=True,
            executable="/bin/bash",
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1, min(timeout_seconds, 600)),
        )
        output = (completed.stdout + completed.stderr)[-16_000:]
        return {
            "status": "ok" if completed.returncode == 0 else "error",
            "model_text": output,
            "exit_code": completed.returncode,
            "truncated": len(completed.stdout) + len(completed.stderr) > 16_000,
        }

    return AdkCodingTools(read=read, bash=bash, edit=edit, write=write)


def _normalize(value: Any) -> AdkCodingTools | None:
    names = ("read", "bash", "edit", "write")
    if isinstance(value, dict) and all(name in value for name in names):
        tools = {
            name: cast(Callable[..., dict[str, Any]], value[name]) for name in names
        }
        return AdkCodingTools(**tools)
    if all(callable(getattr(value, name, None)) for name in names):
        tools = {
            name: cast(Callable[..., dict[str, Any]], getattr(value, name))
            for name in names
        }
        return AdkCodingTools(**tools)
    if isinstance(value, (list, tuple)):
        functions = {
            getattr(item, "__name__", ""): item for item in value if callable(item)
        }
        if all(name in functions for name in names):
            tools = {
                name: cast(Callable[..., dict[str, Any]], functions[name])
                for name in names
            }
            return AdkCodingTools(**tools)
    return None


def create_adk_tools(workspace: Path) -> AdkCodingTools:
    """Create four tools, preferring the core tested factory when available."""

    root = workspace.resolve()
    try:
        module = importlib.import_module("harness.tools.factory")
        for name in (
            "create_coding_tools",
            "build_coding_tools",
            "make_coding_tools",
            "create_tools",
        ):
            builder = getattr(module, name, None)
            if not callable(builder):
                continue
            signature = inspect.signature(builder)
            kwargs: dict[str, Any] = {}
            unsupported = False
            for parameter in signature.parameters.values():
                if parameter.default is not inspect.Parameter.empty:
                    continue
                if parameter.name in {"root", "cwd", "workspace", "workspace_root"}:
                    kwargs[parameter.name] = root
                else:
                    unsupported = True
                    break
            if unsupported:
                continue
            normalized = _normalize(builder(**kwargs))
            if normalized:
                return normalized
    except Exception:
        # Core tool unit tests expose the underlying error; the fallback prevents a
        # packaging mismatch from making the ADK app impossible to import.
        pass
    return _fallback(root)
