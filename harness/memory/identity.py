"""Stable project identity shared by memory and evaluation stores."""

from __future__ import annotations

import hashlib
from pathlib import Path


def project_id_for(repository: Path) -> str:
    root = repository.expanduser().resolve()
    return hashlib.sha256(root.as_posix().encode()).hexdigest()[:24]


__all__ = ["project_id_for"]
