"""Reproducible evaluation cases derived from human pull requests.

These contracts describe how to materialize a large public repository at an
immutable base commit and how to overlay held-out validation files from the human
solution. Network access and checkout execution remain runner concerns.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness.models.task import TaskRequest

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _relative_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("repository paths must use forward slashes")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("repository paths must be normalized relative paths")
    return path.as_posix()


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("/") + "/")


class HumanPullRequestSource(BaseModel):
    """Immutable GitHub provenance for a merged, human-authored pull request."""

    model_config = ConfigDict(extra="forbid")

    repository: str
    repository_size_kb: int = Field(ge=100_000)
    metadata_observed_at: datetime
    pull_request_number: int = Field(ge=1)
    pull_request_url: str
    api_url: str
    author_login: str = Field(min_length=1)
    author_type: Literal["User"]
    title: str = Field(min_length=1)
    upstream_task_text: str = Field(min_length=1)
    upstream_task_url: str
    merged_at: datetime
    base_revision: str
    solution_revision: str
    merge_revision: str

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if not _REPOSITORY.fullmatch(value):
            raise ValueError("repository must be an owner/name GitHub slug")
        return value

    @field_validator("base_revision", "solution_revision", "merge_revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _GIT_SHA.fullmatch(value):
            raise ValueError("Git revisions must be full lowercase 40-character SHAs")
        return value

    @model_validator(mode="after")
    def validate_provenance(self) -> HumanPullRequestSource:
        expected_url = (
            f"https://github.com/{self.repository}/pull/{self.pull_request_number}"
        )
        expected_api = (
            f"https://api.github.com/repos/{self.repository}/pulls/"
            f"{self.pull_request_number}"
        )
        if self.pull_request_url != expected_url:
            raise ValueError("pull_request_url does not match repository and PR number")
        if self.api_url != expected_api:
            raise ValueError("api_url does not match repository and PR number")
        if len({self.base_revision, self.solution_revision, self.merge_revision}) < 2:
            raise ValueError("base and solution revisions must differ")
        if self.metadata_observed_at.tzinfo is None or self.merged_at.tzinfo is None:
            raise ValueError("provenance timestamps must include a timezone")
        return self


class GitRepositoryFixture(BaseModel):
    """Deterministic recipe for checking out the task and reference solution."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["git"] = "git"
    repository_url: str
    base_revision: str
    solution_revision: str
    fetch_strategy: Literal["commit"] = "commit"
    initialize_submodules: bool = False

    @field_validator("base_revision", "solution_revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _GIT_SHA.fullmatch(value):
            raise ValueError("Git revisions must be full lowercase 40-character SHAs")
        return value

    @model_validator(mode="after")
    def validate_revisions(self) -> GitRepositoryFixture:
        if self.base_revision == self.solution_revision:
            raise ValueError("base and solution revisions must differ")
        if not self.repository_url.startswith("https://github.com/") or not self.repository_url.endswith(
            ".git"
        ):
            raise ValueError("repository_url must be an HTTPS GitHub clone URL")
        return self

    def materialization_commands(self) -> tuple[tuple[str, ...], ...]:
        """Return an argv-only checkout recipe without executing it."""

        commands: list[tuple[str, ...]] = [
            ("git", "init", "--quiet"),
            ("git", "remote", "add", "origin", self.repository_url),
            ("git", "fetch", "--depth=1", "origin", self.base_revision),
            ("git", "checkout", "--detach", self.base_revision),
        ]
        if self.initialize_submodules:
            commands.append(("git", "submodule", "update", "--init", "--recursive"))
        return tuple(commands)


class HeldOutFile(BaseModel):
    """One validation file copied from the human solution after agent execution."""

    model_config = ConfigDict(extra="forbid")

    path: str
    blob_sha: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("blob_sha")
    @classmethod
    def validate_blob_sha(cls, value: str) -> str:
        if not _GIT_SHA.fullmatch(value):
            raise ValueError("held-out blob_sha must be a full lowercase Git SHA")
        return value


class HeldOutValidation(BaseModel):
    """Files and commands hidden from the coding agent and applied by the runner."""

    model_config = ConfigDict(extra="forbid")

    source_revision: str
    files: list[HeldOutFile] = Field(min_length=1)
    commands: list[str] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _GIT_SHA.fullmatch(value):
            raise ValueError("source_revision must be a full lowercase Git SHA")
        return value

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("held-out validation commands cannot be blank")
        return values

    @model_validator(mode="after")
    def validate_unique_files(self) -> HeldOutValidation:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("held-out validation paths must be unique")
        return self


class RealRepositoryEvaluationCase(BaseModel):
    """One fail-to-pass task grounded in an immutable upstream pull request."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: HumanPullRequestSource
    fixture: GitRepositoryFixture
    request: TaskRequest
    expected_changed_globs: list[str] = Field(min_length=1)
    forbidden_changed_globs: list[str] = Field(min_length=1)
    held_out_validation: HeldOutValidation
    tags: list[str] = Field(default_factory=list)

    @field_validator("expected_changed_globs", "forbidden_changed_globs")
    @classmethod
    def validate_globs(cls, values: list[str]) -> list[str]:
        normalized = [_relative_path(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("changed-path globs must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_case_alignment(self) -> RealRepositoryEvaluationCase:
        expected_clone_url = f"https://github.com/{self.source.repository}.git"
        if self.fixture.repository_url != expected_clone_url:
            raise ValueError("fixture repository does not match PR provenance")
        if self.fixture.base_revision != self.source.base_revision:
            raise ValueError("fixture base revision does not match PR provenance")
        if self.fixture.solution_revision != self.source.solution_revision:
            raise ValueError("fixture solution revision does not match PR provenance")
        if self.held_out_validation.source_revision != self.source.solution_revision:
            raise ValueError("held-out files must come from the human solution revision")
        if set(self.expected_changed_globs) & set(self.forbidden_changed_globs):
            raise ValueError("expected and forbidden changed-path globs cannot be equal")
        for held_out in self.held_out_validation.files:
            if not any(
                _matches(held_out.path, pattern)
                for pattern in self.forbidden_changed_globs
            ):
                raise ValueError(
                    f"held-out path must be forbidden to the agent: {held_out.path}"
                )
        return self

    def fixture_fingerprint(self) -> str:
        """Hash the immutable inputs needed to reproduce this fixture."""

        payload = {
            "repository_url": self.fixture.repository_url,
            "base_revision": self.fixture.base_revision,
            "solution_revision": self.fixture.solution_revision,
            "held_out": [
                item.model_dump(mode="json")
                for item in sorted(
                    self.held_out_validation.files,
                    key=lambda value: value.path,
                )
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class RealRepositoryEvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["real-repository-v1"]
    suite_id: str = Field(min_length=1)
    description: str = ""
    cases: list[RealRepositoryEvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_cases(self) -> RealRepositoryEvaluationSuite:
        case_ids = [case.case_id for case in self.cases]
        sources = [
            (case.source.repository, case.source.pull_request_number)
            for case in self.cases
        ]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("real-repository case IDs must be unique")
        if len(sources) != len(set(sources)):
            raise ValueError("a pull request may appear only once in a suite")
        return self


def load_real_repository_suite(path: Path) -> RealRepositoryEvaluationSuite:
    """Load and fully validate a reproducible real-repository suite."""

    return RealRepositoryEvaluationSuite.model_validate_json(
        path.read_text(encoding="utf-8")
    )


__all__ = [
    "GitRepositoryFixture",
    "HeldOutFile",
    "HeldOutValidation",
    "HumanPullRequestSource",
    "RealRepositoryEvaluationCase",
    "RealRepositoryEvaluationSuite",
    "load_real_repository_suite",
]
