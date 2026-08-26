"""Discover a cheap-to-broad validation ladder from repository metadata."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Iterable

from harness.repo.discovery import RepositoryManifest

from .models import ValidationCommand, ValidationPlan


def _existing_candidates(root: Path, candidates: Iterable[Path]) -> list[str]:
    found: list[str] = []
    for candidate in candidates:
        if candidate.is_file():
            found.append(candidate.relative_to(root).as_posix())
    return sorted(set(found))


def find_adjacent_tests(root: Path, changed_paths: Iterable[str]) -> list[str]:
    """Infer likely tests using conservative naming conventions."""

    tests: list[str] = []
    for relative in changed_paths:
        path = root / relative
        suffix = path.suffix.lower()
        stem = path.stem
        if suffix in {".py", ".pyi"} and not stem.startswith("test_"):
            tests.extend(
                _existing_candidates(
                    root,
                    (
                        path.with_name(f"test_{stem}.py"),
                        root / "tests" / path.parent.relative_to(root) / f"test_{stem}.py",
                        root / "tests" / f"test_{stem}.py",
                    ),
                )
            )
        elif suffix in {".ts", ".tsx", ".js", ".jsx"} and not any(
            marker in stem for marker in (".test", ".spec")
        ):
            tests.extend(
                _existing_candidates(
                    root,
                    (
                        path.with_name(f"{stem}.test{suffix}"),
                        path.with_name(f"{stem}.spec{suffix}"),
                        path.parent / "__tests__" / f"{stem}.test{suffix}",
                    ),
                )
            )
    return sorted(set(tests))


def discover_validation_plan(
    manifest: RepositoryManifest,
    changed_paths: Iterable[str],
    *,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> ValidationPlan:
    changed = sorted(set(changed_paths))
    commands: list[ValidationCommand] = []

    python_files = [path for path in changed if Path(path).suffix.lower() in {".py", ".pyi"}]
    if python_files:
        quoted = " ".join(shlex.quote(path) for path in python_files)
        commands.append(
            ValidationCommand(
                category="syntax",
                command=f"python -m py_compile {quoted}",
                source="changed Python files",
                targeted=True,
            )
        )

    adjacent_tests = find_adjacent_tests(manifest.root, changed)
    command_by_kind = {item.kind: item for item in manifest.commands}
    for kind in ("lint", "typecheck"):
        discovered = command_by_kind.get(kind)
        if discovered:
            commands.append(
                ValidationCommand(
                    category=kind,
                    command=discovered.command,
                    source=discovered.source,
                )
            )

    test_command = command_by_kind.get("test")
    if test_command:
        command = test_command.command
        targeted = False
        if adjacent_tests and "pytest" in command:
            command = f"{command} " + " ".join(shlex.quote(path) for path in adjacent_tests)
            targeted = True
        commands.append(
            ValidationCommand(
                category="test",
                command=command,
                source=test_command.source,
                targeted=targeted,
            )
        )

    commands.append(
        ValidationCommand(
            category="diff",
            command="git diff --check",
            source="git",
        )
    )
    return ValidationPlan(
        commands=commands,
        changed_paths=changed,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths or [],
    )
