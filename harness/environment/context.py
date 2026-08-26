"""Per-invocation environment binding, adapted from Horizon's ContextVar pattern."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from .base import WorkspaceEnvironment

_ACTIVE_ENVIRONMENT: ContextVar[WorkspaceEnvironment | None] = ContextVar(
    "adk_coding_active_environment", default=None
)


def active_environment() -> WorkspaceEnvironment:
    environment = _ACTIVE_ENVIRONMENT.get()
    if environment is None:
        raise RuntimeError("No coding workspace environment is bound to this invocation")
    return environment


@contextmanager
def bind_environment(environment: WorkspaceEnvironment) -> Iterator[WorkspaceEnvironment]:
    token: Token[WorkspaceEnvironment | None] = _ACTIVE_ENVIRONMENT.set(environment)
    try:
        yield environment
    finally:
        _ACTIVE_ENVIRONMENT.reset(token)
