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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from harness.config.models import PersistenceConfig, SecretRef

SecretResolver = Callable[[SecretRef], str]


class SessionBackend(StrEnum):
    IN_MEMORY = "in_memory"
    SQLITE = "sqlite"
    DATABASE = "database"
    VERTEX = "vertex"


class ArtifactBackend(StrEnum):
    IN_MEMORY = "in_memory"
    FILE = "file"
    GCS = "gcs"


class MemoryBackend(StrEnum):
    IN_MEMORY = "in_memory"
    VERTEX = "vertex"


class PersistenceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_backend: SessionBackend = SessionBackend.IN_MEMORY
    sqlite_path: Path | None = None
    database_url: str | None = None
    artifact_backend: ArtifactBackend = ArtifactBackend.IN_MEMORY
    artifact_root: Path | None = None
    memory_backend: MemoryBackend = MemoryBackend.IN_MEMORY

    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    agent_engine_id: str | None = None
    gcs_bucket: str | None = None
    vertex_memory_bank_id: str | None = None

    @model_validator(mode="after")
    def validate_backend_settings(self) -> PersistenceSettings:
        if self.session_backend == SessionBackend.SQLITE and self.sqlite_path is None:
            raise ValueError("sqlite_path is required for the SQLite session backend")
        if self.session_backend == SessionBackend.DATABASE and not self.database_url:
            raise ValueError("database_url is required for the database session backend")
        if self.artifact_backend == ArtifactBackend.FILE and self.artifact_root is None:
            raise ValueError("artifact_root is required for the file artifact backend")
        if self.artifact_backend == ArtifactBackend.GCS and not self.gcs_bucket:
            raise ValueError("gcs_bucket is required for the GCS artifact backend")
        return self


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
    if settings.session_backend == SessionBackend.SQLITE:
        if settings.sqlite_path is None:  # model validation is the public guard.
            raise ValueError("sqlite_path is required for the SQLite session backend")
        sqlite_path = settings.sqlite_path.expanduser().resolve()
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        cls = _import_symbol(
            (
                (
                    "google.adk.sessions.sqlite_session_service",
                    "SqliteSessionService",
                ),
            )
        )
        return _construct(cls, db_path=str(sqlite_path))
    if settings.session_backend == SessionBackend.DATABASE:
        cls = _import_symbol(
            (("google.adk.sessions", "DatabaseSessionService"),)
        )
        return _construct(cls, db_url=settings.database_url)
    cls = _import_symbol((("google.adk.sessions", "VertexAiSessionService"),))
    return _construct(
        cls,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
        agent_engine_id=settings.agent_engine_id,
    )


def build_artifact_service(settings: PersistenceSettings) -> Any:
    if settings.artifact_backend == ArtifactBackend.IN_MEMORY:
        cls = _import_symbol(
            (("google.adk.artifacts", "InMemoryArtifactService"),)
        )
        return _construct(cls)
    if settings.artifact_backend == ArtifactBackend.FILE:
        if settings.artifact_root is None:  # model validation is the public guard.
            raise ValueError("artifact_root is required for the file artifact backend")
        cls = _import_symbol(
            (
                ("google.adk.artifacts", "FileArtifactService"),
                (
                    "google.adk.artifacts.file_artifact_service",
                    "FileArtifactService",
                ),
            )
        )
        return _construct(cls, root_dir=settings.artifact_root.expanduser().resolve())
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
        location=settings.google_cloud_location,
        agent_engine_id=settings.agent_engine_id,
    )


def build_service_bundle(settings: PersistenceSettings) -> AdkServiceBundle:
    return AdkServiceBundle(
        session_service=build_session_service(settings),
        artifact_service=build_artifact_service(settings),
        memory_service=build_memory_service(settings),
    )


def local_durable_settings(state_root: Path) -> PersistenceSettings:
    """Build credential-free, state-root-scoped persistence settings.

    SQLite sessions and filesystem artifacts survive process restarts while the
    in-memory memory service deliberately avoids requiring a cloud identity.
    """

    root = state_root.expanduser().resolve() / "adk"
    return PersistenceSettings(
        session_backend=SessionBackend.SQLITE,
        sqlite_path=root / "sessions.db",
        artifact_backend=ArtifactBackend.FILE,
        artifact_root=root / "artifacts",
        memory_backend=MemoryBackend.IN_MEMORY,
    )


def _environment_secret(ref: SecretRef) -> str:
    value = os.getenv(ref.env)
    if value is None:
        raise ValueError(f"required secret environment variable is not set: {ref.env}")
    return value


def settings_from_composition(
    config: PersistenceConfig,
    *,
    state_root: Path,
    secret_resolver: SecretResolver = _environment_secret,
) -> PersistenceSettings:
    """Resolve portable persistence behavior against a volatile state root."""

    root = state_root.expanduser().resolve() / "adk"
    database_url = (
        secret_resolver(config.session_database_url)
        if config.session_backend == SessionBackend.DATABASE
        and config.session_database_url is not None
        else None
    )
    return PersistenceSettings(
        session_backend=SessionBackend(config.session_backend),
        sqlite_path=(
            root / "sessions.db" if config.session_backend == SessionBackend.SQLITE else None
        ),
        database_url=database_url,
        artifact_backend=ArtifactBackend(config.artifact_backend),
        artifact_root=(
            root / "artifacts" if config.artifact_backend == ArtifactBackend.FILE else None
        ),
        memory_backend=MemoryBackend(config.memory_backend),
        google_cloud_project=config.cloud_project,
        google_cloud_location=config.cloud_location,
        agent_engine_id=config.agent_engine_id,
        gcs_bucket=config.gcs_bucket,
        vertex_memory_bank_id=config.memory_bank_id,
    )


def settings_from_env() -> PersistenceSettings:
    session_backend = SessionBackend(
        os.getenv("ADK_SESSION_BACKEND", SessionBackend.IN_MEMORY.value)
    )
    artifact_backend = ArtifactBackend(
        os.getenv("ADK_ARTIFACT_BACKEND", ArtifactBackend.IN_MEMORY.value)
    )
    return PersistenceSettings(
        session_backend=session_backend,
        sqlite_path=(
            Path(
                os.getenv(
                    "ADK_SQLITE_SESSION_PATH",
                    ".adk-coding-agent/sessions.db",
                )
            )
            if session_backend == SessionBackend.SQLITE
            else None
        ),
        database_url=os.getenv("ADK_DATABASE_URL"),
        artifact_backend=artifact_backend,
        artifact_root=(
            Path(
                os.getenv(
                    "ADK_ARTIFACT_ROOT",
                    ".adk-coding-agent/artifacts",
                )
            )
            if artifact_backend == ArtifactBackend.FILE
            else None
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
