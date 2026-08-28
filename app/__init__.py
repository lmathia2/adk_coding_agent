"""Agents CLI entrypoint package with lazy compatibility exports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.adk import Workflow
    from google.adk.apps import App

    app: App
    root_agent: Workflow

__all__ = ["app", "root_agent"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from . import agent

    return getattr(agent, name)
