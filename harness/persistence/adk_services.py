"""Environment-driven persistent Google ADK service factories.

Imports are intentionally lazy so deterministic harness tests do not require cloud
credentials or optional database extras. Constructor arguments are matched by
signature to tolerate small ADK 2.x naming changes while failing loudly when a
requested backend is unavailable.
"""

from __future__ import annotations

import importlib
import inspect
import os
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class SessionBackend(StrEnum):
    IN_MEMORY = "in_memory"
    DATABASE = "database"
    VERTEX = "vertex"


class ArtifactBackend(StrEnum):
    IN_MEMORY = "in_memory"
    GCS = "gcs"


class MemoryBackend(StrEnum):
    IN_MEMORY = "in_memory"
    VERTEX = "vertex"


class PersistenceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_backend: SessionBackend = SessionBackend.DATABASE
    database_url: str = "sqlite+aiosqlite:///./.adk-coding-agent/sessions.db"
    artifact_backend: ArtifactBackend = ArtifactBackend.IN_MEMORY
    memory_backend: MemoryBackend = MemoryBackend.IN_MEMORY

    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    agent_engine_id: str | None = None
    gcs_bucket: str | None = None
    vertex_memory_bank_id: str | None = None


@dataclass(frozen=True, slots=True)
class AdkServiceBundle:
    session_service: Any
    artifact_service: Any
    memory_service: Any


def _import_symbol(candidates: Iterable[tuple[str, str]]) -> type[Any]:
    failures: list[str] = []
    for module_name, symbol_name in candidates:
        try:
            module = importlib.import_module(module_name)
            value = getattr(module, symbol_name)
        except (ImportError, AttributeError) as exc:
            failures.append(f"{module_name}.{symbol_name}: {exc}")
            continue
        if not isinstance(value, type):
            raise TypeError(f"{module_name}.{symbol_name} is not a class")
        return value
    raise ImportError("No compatible ADK service class found:\n- " + "\n- ".join(failures))


def _construct(service_class: type[Any], **candidates: Any) -> Any:
    """Pass only supported non-null keyword arguments to an ADK service class."""

    signature = inspect.signature(service_class)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_kwargs:
        kwargs = {key: value for key, value in candidates.items() if value is not None}
    else:
        kwargs = {
            key: value
            for key, value in candidates.items()
            if value is not None and key in signature.parameters
        }
    missing = [
        name
        for name, parameter in signature.parameters.items()
        if name != "self"
        and parameter.default is inspect.Parameter.empty
        and parameter.kind
        not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        and name not in kwargs
    ]
    if missing:
        raise TypeError(
            f"Cannot construct {service_class.__module__}.{service_class.__name__}; "
            f"missing required settings: {', '.join(missing)}"
        )
    return service_class(**kwargs)


def build_session_service(settings: PersistenceSettings) -> Any:
    if settings.session_backend == SessionBackend.IN_MEMORY:
        cls = _import_symbol(
            (("google.adk.sessions", "InMemorySessionService"),)
        )
        return _construct(cls)
    if settings.session_backend == SessionBackend.DATABASE:
        cls = _import_symbol(
            (("google.adk.sessions", "DatabaseSessionService"),)
        )
        return _construct(
            cls,
            db_url=settings.database_url,
            database_url=settings.database_url,
            url=settings.database_url,
        )
    cls = _import_symbol((("google.adk.sessions", "VertexAiSessionService"),))
    return _construct(
        cls,
        project=settings.google_cloud_project,
        project_id=settings.google_cloud_project,
        location=settings.google_cloud_location,
        agent_engine_id=settings.agent_engine_id,
        reasoning_engine_id=settings.agent_engine_id,
    )


def build_artifact_service(settings: PersistenceSettings) -> Any:
    if settings.artifact_backend == ArtifactBackend.IN_MEMORY:
        cls = _import_symbol(
            (("google.adk.artifacts", "InMemoryArtifactService"),)
        )
        return _construct(cls)
    if not settings.gcs_bucket:
        raise ValueError("gcs_bucket is required for the GCS artifact backend")
    cls = _import_symbol(
        (
            ("google.adk.artifacts", "GcsArtifactService"),
            ("google.adk.artifacts", "GCSArtifactService"),
        )
    )
    return _construct(
        cls,
        bucket_name=settings.gcs_bucket,
        bucket=settings.gcs_bucket,
        project=settings.google_cloud_project,
        project_id=settings.google_cloud_project,
    )


def build_memory_service(settings: PersistenceSettings) -> Any:
    if settings.memory_backend == MemoryBackend.IN_MEMORY:
        cls = _import_symbol(
            (("google.adk.memory", "InMemoryMemoryService"),)
        )
        return _construct(cls)
    cls = _import_symbol(
        (
            ("google.adk.memory", "VertexAiMemoryBankService"),
            ("google.adk.memory", "VertexAIMemoryBankService"),
        )
    )
    return _construct(
        cls,
        project=settings.google_cloud_project,
        project_id=settings.google_cloud_project,
        location=settings.google_cloud_location,
        agent_engine_id=settings.agent_engine_id,
        memory_bank_id=settings.vertex_memory_bank_id,
    )


def build_service_bundle(settings: PersistenceSettings) -> AdkServiceBundle:
    return AdkServiceBundle(
        session_service=build_session_service(settings),
        artifact_service=build_artifact_service(settings),
        memory_service=build_memory_service(settings),
    )


def settings_from_env() -> PersistenceSettings:
    return PersistenceSettings(
        session_backend=SessionBackend(
            os.getenv("ADK_SESSION_BACKEND", SessionBackend.DATABASE.value)
        ),
        database_url=os.getenv(
            "ADK_DATABASE_URL",
            "sqlite+aiosqlite:///./.adk-coding-agent/sessions.db",
        ),
        artifact_backend=ArtifactBackend(
            os.getenv("ADK_ARTIFACT_BACKEND", ArtifactBackend.IN_MEMORY.value)
        ),
        memory_backend=MemoryBackend(
            os.getenv("ADK_MEMORY_BACKEND", MemoryBackend.IN_MEMORY.value)
        ),
        google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT"),
        google_cloud_location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        agent_engine_id=os.getenv("ADK_AGENT_ENGINE_ID"),
        gcs_bucket=os.getenv("ADK_ARTIFACT_BUCKET"),
        vertex_memory_bank_id=os.getenv("ADK_MEMORY_BANK_ID"),
    )
