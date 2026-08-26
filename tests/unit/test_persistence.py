from __future__ import annotations

from dataclasses import dataclass

import pytest

from harness.persistence.adk_services import (
    ArtifactBackend,
    PersistenceSettings,
    SessionBackend,
    _construct,
    build_artifact_service,
    settings_from_env,
)


@dataclass
class _DatabaseService:
    db_url: str


class _KeywordService:
    def __init__(self, **kwargs):
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


def test_gcs_backend_requires_bucket_before_import() -> None:
    settings = PersistenceSettings(artifact_backend=ArtifactBackend.GCS)
    with pytest.raises(ValueError, match="gcs_bucket"):
        build_artifact_service(settings)
