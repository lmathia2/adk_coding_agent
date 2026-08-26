"""Cheap-to-broad deterministic validation planning."""

from __future__ import annotations

import shlex
from pathlib import PurePosixPath
from typing import Any

from harness.repo import RepositoryManifest

from .contracts import ValidationCategory, ValidationCommand, ValidationPlan

_CATEGORY_ALIASES: dict[str, ValidationCategory] = {
    "syntax": "syntax",
    "format": "format",
    "formatter": "format",
    "lint": "lint",
    "type": "typecheck",
    "types": "typecheck",
    "typecheck": "typecheck",
    "type-check": "typecheck",
    "test": "test",
    "tests": "test",
    "unit_test": "test",
    "integration_test": "test",
    "build": "build",
    "compile": "build",
    "diff": "diff",
}
_CATEGORY_ORDER: dict[ValidationCategory, int] = {
    "syntax": 0,
    "format": 1,
    "lint": 2,
    "typecheck": 3,
    "test": 4,
    "build": 5,
    "custom": 6,
    "diff": 7,
}


def _normalize_path(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix().lstrip("./")


def _command_category(value: Any) -> ValidationCategory:
    kind = str(getattr(value, "kind", "custom")).strip().lower()
    return _CATEGORY_ALIASES.get(kind, "custom")


def _syntax_commands(changed_paths: list[str]) -> list[ValidationCommand]:
    python_files = sorted(
        path for path in changed_paths if PathSuffix(path).suffix == ".py"
    )
    commands: list[ValidationCommand] = []
    if python_files:
        quoted = " ".join(shlex.quote(path) for path in python_files)
        commands.append(
            ValidationCommand(
                category="syntax",
                command=f"python -m py_compile {quoted}",
                source="changed Python files",
                timeout_seconds=120,
            )
        )
    json_files = sorted(
        path for path in changed_paths if PathSuffix(path).suffix == ".json"
    )
    for path in json_files:
        commands.append(
            ValidationCommand(
                category="syntax",
                command=f"python -m json.tool {shlex.quote(path)} >/dev/null",
                source=f"changed JSON file {path}",
                timeout_seconds=60,
            )
        )
    return commands


class PathSuffix:
    """Small path helper that tolerates repository paths on every host platform."""

    def __init__(self, path: str) -> None:
        self.path = PurePosixPath(_normalize_path(path))

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()


def discover_validation_plan(
    manifest: RepositoryManifest,
    changed_paths: list[str],
    *,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> ValidationPlan:
    """Build a deterministic validation ladder from repository evidence."""

    normalized_changed = sorted({_normalize_path(path) for path in changed_paths})
    commands = _syntax_commands(normalized_changed)

    for build_command in manifest.commands:
        command = str(getattr(build_command, "command", "")).strip()
        if not command:
            continue
        category = _command_category(build_command)
        source = str(getattr(build_command, "source", "repository manifest"))
        timeout = 900 if category in {"test", "build"} else 300
        commands.append(
            ValidationCommand(
                category=category,
                command=command,
                source=source,
                timeout_seconds=timeout,
            )
        )

    commands.append(
        ValidationCommand(
            category="diff",
            command="git diff --check",
            source="harness completion gate",
            timeout_seconds=60,
        )
    )

    deduplicated: dict[tuple[str, str], ValidationCommand] = {}
    for command in commands:
        deduplicated.setdefault((command.category, command.command), command)
    ordered = sorted(
        deduplicated.values(),
        key=lambda command: (
            _CATEGORY_ORDER[command.category],
            command.command,
            command.source,
        ),
    )
    return ValidationPlan(
        commands=ordered,
        changed_paths=normalized_changed,
        allowed_paths=(
            sorted({_normalize_path(path) for path in allowed_paths})
            if allowed_paths is not None
            else None
        ),
        forbidden_paths=sorted(
            {_normalize_path(path) for path in (forbidden_paths or [])}
        ),
    )


__all__ = ["discover_validation_plan"]
