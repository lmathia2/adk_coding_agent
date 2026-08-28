"""Agents CLI exports, loaded lazily so reusable factory imports stay side-effect free."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.adk import Agent, Workflow
    from google.adk.apps import App

    app: App
    coding_worker: Agent
    root_agent: Workflow

__all__ = ["app", "coding_worker", "root_agent"]


@lru_cache(maxsize=1)
def _legacy_exports() -> dict[str, Any]:
    from .application import app, coding_worker, root_agent

    return {
        "app": app,
        "coding_worker": coding_worker,
        "root_agent": root_agent,
    }


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    return _legacy_exports()[name]
