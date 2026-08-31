"""Agents CLI bootstrap using the same YAML factory as the server."""
from __future__ import annotations

import os
from pathlib import Path

from harness.config import DEFAULT_COMPOSITION_PATH, load_harness_composition

from .config import runtime_bindings_from_env
from .factory import build_harness

_COMPOSITION_PATH = Path(
    os.getenv("ADK_CODING_CONFIG", str(DEFAULT_COMPOSITION_PATH))
).expanduser().resolve()
_ASSEMBLY = build_harness(
    load_harness_composition(_COMPOSITION_PATH),
    runtime_bindings_from_env(_COMPOSITION_PATH.parent),
)
app = _ASSEMBLY.app
root_agent = app.root_agent
coding_worker = _ASSEMBLY.agents["coding_worker"]

__all__ = ["app", "coding_worker", "root_agent"]
