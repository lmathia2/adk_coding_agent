"""Thin environment-aware Agents CLI bootstrap over the registered factory."""

from __future__ import annotations

import os

from pydantic import SecretStr

from harness.config import RuntimeBindings, load_harness_composition

from .bootstrap import SETTINGS
from .factory import build_harness


def _optional_int_env(name: str, *, minimum: int) -> int | None:
    """Compatibility parser retained for operators migrating legacy settings."""

    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum} when configured")
    return value


_COMPOSITION = load_harness_composition()
_ASSEMBLY = build_harness(
    _COMPOSITION,
    RuntimeBindings(
        workspace=SETTINGS.workspace,
        state_root=SETTINGS.state_root,
        configuration_root=None,
        source_repository=SETTINGS.source_repository,
        task_id=SETTINGS.task_id_override,
        base_revision=SETTINGS.base_revision_override,
        workspace_id=SETTINGS.workspace_id_override,
        worker_id=SETTINGS.worker_id,
        control_database_url=(
            SecretStr(SETTINGS.control_database_url)
            if SETTINGS.control_database_url is not None
            else None
        ),
    ),
)
app = _ASSEMBLY.app
root_agent = app.root_agent
coding_worker = _ASSEMBLY.agents["coding_worker"]

__all__ = ["app", "coding_worker", "root_agent"]
