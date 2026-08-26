"""Changed-file and scope validation."""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath
from typing import Iterable


def _matches(path: str, pattern: str) -> bool:
    normalized = PurePosixPath(path).as_posix().lstrip("./")
    normalized_pattern = pattern.replace("\\", "/").lstrip("./")
    if normalized_pattern.endswith("/"):
        return normalized.startswith(normalized_pattern)
    return fnmatch.fnmatch(normalized, normalized_pattern) or normalized.startswith(
        normalized_pattern.rstrip("/") + "/"
    )


def check_scope(
    changed_paths: Iterable[str],
    *,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> list[str]:
    violations: list[str] = []
    forbidden = forbidden_paths or []
    for path in sorted(set(changed_paths)):
        if any(_matches(path, pattern) for pattern in forbidden):
            violations.append(f"forbidden path changed: {path}")
            continue
        if allowed_paths is not None and not any(
            _matches(path, pattern) for pattern in allowed_paths
        ):
            violations.append(f"path outside allowed scope: {path}")
    return violations
