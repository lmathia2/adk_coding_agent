from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from google.genai import types
from pydantic import ValidationError

from harness.config import load_harness_composition
from harness.config.models import PersistenceConfig, SecretRef
from harness.persistence import adk_services
from harness.persistence.adk_services import (
    ArtifactBackend,
    MemoryBackend,
    PersistenceSettings,
    SessionBackend,
    _construct,
    build_artifact_service,
    build_session_service,
    local_durable_settings,
    settings_from_composition,
    settings_from_env,
)


@dataclass
class _DatabaseService:
    db_url: str


class _KeywordService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _GcsService:
    def __init__(self, bucket_name: str, **kwargs):
        self.bucket_name = bucket_name
        self.kwargs = kwargs


class _DatabaseKeywordService:
    def __init__(self, db_url: str, **kwargs):
        self.db_url = db_url
        self.kwargs = kwargs


def test_construct_matches_supported_signature() -> None:
    service = _construct(
        _DatabaseService,
        db_url="sqlite:///state.db",
        database_url="ignored",
    )
    assert service.db_url == "sqlite:///state.db"

    keyword = _construct(_KeywordService, project="p", location="l", empty=None)
    assert keyword.kwargs == {"project": "p", "location": "l"}


def test_construct_reports_missing_required_setting() -> None:
    with pytest.raises(TypeError, match="db_url"):
        _construct(_DatabaseService, database_url="wrong-name")


def test_settings_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ADK_SESSION_BACKEND", "in_memory")
    monkeypatch.setenv("ADK_DATABASE_URL", "sqlite:///custom.db")
    monkeypatch.setenv("ADK_ARTIFACT_BACKEND", "gcs")
    monkeypatch.setenv("ADK_ARTIFACT_BUCKET", "bucket")
    settings = settings_from_env()
    assert settings.session_backend == SessionBackend.IN_MEMORY
    assert settings.database_url == "sqlite:///custom.db"
    assert settings.artifact_backend == ArtifactBackend.GCS
    assert settings.gcs_bucket == "bucket"


def test_defaults_are_safe_and_credential_free(monkeypatch) -> None:
    monkeypatch.delenv("ADK_SESSION_BACKEND", raising=False)
    monkeypatch.delenv("ADK_ARTIFACT_BACKEND", raising=False)

    direct = PersistenceSettings()
    environment = settings_from_env()

    assert direct.session_backend == SessionBackend.IN_MEMORY
    assert direct.artifact_backend == ArtifactBackend.IN_MEMORY
    assert direct.memory_backend == MemoryBackend.IN_MEMORY
    assert environment == direct


def test_bundled_composition_selects_local_durable_backends() -> None:
    persistence = load_harness_composition().persistence

    assert persistence.session_backend == "sqlite"
    assert persistence.artifact_backend == "file"
    assert persistence.memory_backend == "in_memory"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"session_backend": SessionBackend.SQLITE}, "sqlite_path"),
        ({"session_backend": SessionBackend.DATABASE}, "database_url"),
        ({"artifact_backend": ArtifactBackend.FILE}, "artifact_root"),
        ({"artifact_backend": ArtifactBackend.GCS}, "gcs_bucket"),
    ],
)
def test_selected_backends_require_their_settings(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        PersistenceSettings.model_validate(kwargs)


def test_local_durable_settings_are_scoped_to_resolved_state_root(tmp_path: Path) -> None:
    settings = local_durable_settings(tmp_path / "state" / ".." / "state")

    assert settings.session_backend == SessionBackend.SQLITE
    assert settings.sqlite_path == (tmp_path / "state" / "adk" / "sessions.db").resolve()
    assert settings.artifact_backend == ArtifactBackend.FILE
    assert settings.artifact_root == (tmp_path / "state" / "adk" / "artifacts").resolve()
    assert settings.memory_backend == MemoryBackend.IN_MEMORY


def test_composition_resolves_local_paths_and_database_secret(tmp_path: Path) -> None:
    local = settings_from_composition(
        PersistenceConfig(session_backend="sqlite", artifact_backend="file"),
        state_root=tmp_path / "state",
    )
    database = settings_from_composition(
        PersistenceConfig(
            session_backend="database",
            session_database_url=SecretRef(env="ADK_DATABASE_URL"),
        ),
        state_root=tmp_path / "other",
        secret_resolver=lambda ref: f"resolved:{ref.env}",
    )

    assert local == local_durable_settings(tmp_path / "state")
    assert database.database_url == "resolved:ADK_DATABASE_URL"
    assert database.sqlite_path is None
    assert database.artifact_root is None


def test_environment_selected_local_backends_use_portable_defaults(monkeypatch) -> None:
    monkeypatch.setenv("ADK_SESSION_BACKEND", "sqlite")
    monkeypatch.setenv("ADK_ARTIFACT_BACKEND", "file")
    monkeypatch.delenv("ADK_SQLITE_SESSION_PATH", raising=False)
    monkeypatch.delenv("ADK_ARTIFACT_ROOT", raising=False)

    settings = settings_from_env()

    assert settings.sqlite_path == Path(".adk-coding-agent/sessions.db")
    assert settings.artifact_root == Path(".adk-coding-agent/artifacts")


def test_gcs_builder_forwards_only_canonical_constructor_argument(monkeypatch) -> None:
    monkeypatch.setattr(adk_services, "_import_symbol", lambda candidates: _GcsService)

    service = build_artifact_service(
        PersistenceSettings(
            artifact_backend=ArtifactBackend.GCS,
            gcs_bucket="bucket",
            google_cloud_project="project",
        )
    )

    assert service.bucket_name == "bucket"
    assert service.kwargs == {}


def test_database_builder_forwards_only_canonical_constructor_argument(monkeypatch) -> None:
    monkeypatch.setattr(
        adk_services,
        "_import_symbol",
        lambda candidates: _DatabaseKeywordService,
    )

    service = build_session_service(
        PersistenceSettings(
            session_backend=SessionBackend.DATABASE,
            database_url="postgresql://example.invalid/state",
        )
    )

    assert service.db_url == "postgresql://example.invalid/state"
    assert service.kwargs == {}


@pytest.mark.asyncio
async def test_local_adk_services_survive_reconstruction(tmp_path: Path) -> None:
    settings = local_durable_settings(tmp_path)
    first_sessions = build_session_service(settings)
    first_artifacts = build_artifact_service(settings)

    await first_sessions.create_session(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
        state={"phase": "coding"},
    )
    version = await first_artifacts.save_artifact(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
        filename="result.txt",
        artifact=types.Part.from_text(text="durable"),
    )

    second_sessions = build_session_service(settings)
    second_artifacts = build_artifact_service(settings)
    session = await second_sessions.get_session(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
    )
    artifact = await second_artifacts.load_artifact(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
        filename="result.txt",
        version=version,
    )

    assert session is not None
    assert session.state["phase"] == "coding"
    assert artifact is not None
    assert artifact.text == "durable"
