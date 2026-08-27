"""Provider-neutral contracts for optional semantic repository intelligence.

This module deliberately stops at an argv-only command plan.  An operator may wire
that plan to an LSP wrapper, Moderne, or another repository service through the
existing managed ``bash`` boundary.  Nothing here starts a process, opens a network
connection, reads credentials, or adds a model-facing tool.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CONTRACT_VERSION = 1
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BACKEND_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_READ_ONLY_DENIED_ARGUMENTS = {
    "apply",
    "delete",
    "deploy",
    "fix",
    "format",
    "install",
    "publish",
    "remove",
    "rewrite",
    "upload",
}
_READ_ONLY_DENIED_PREFIXES = (
    "--apply",
    "--delete",
    "--fix",
    "--in-place",
    "--install",
    "--output",
    "--publish",
    "--upload",
    "--write",
)
_GENERIC_EXECUTABLES = {
    "bash",
    "cp",
    "curl",
    "dd",
    "git",
    "mv",
    "node",
    "perl",
    "python",
    "python3",
    "rm",
    "ruby",
    "sh",
    "tee",
    "wget",
    "zsh",
}


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IntelligenceOperation(StrEnum):
    """Read-only semantic operations supported by the adapter contract."""

    SEARCH = "search"
    OUTLINE = "outline"
    DEFINITIONS = "definitions"
    REFERENCES = "references"
    IMPLEMENTATIONS = "implementations"


class IntelligenceReadiness(StrEnum):
    """Backend readiness for the exact repository fingerprint in a query."""

    DISABLED = "disabled"
    CONFIGURED = "configured"
    BUILDING = "building"
    READY = "ready"
    STALE = "stale"
    ERROR = "error"


class EvidenceCompleteness(StrEnum):
    """How much of the backend's answer is represented in the bounded result."""

    UNKNOWN = "unknown"
    PARTIAL = "partial"
    COMPLETE = "complete"


def _bounded_text(value: str, *, field: str, max_chars: int) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field} cannot be blank")
    if len(value) > max_chars:
        raise ValueError(f"{field} exceeds {max_chars} characters")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError(f"{field} cannot contain control-line characters")
    return value


def _relative_path(value: str) -> str:
    if "\\" in value or "\x00" in value:
        raise ValueError("repository paths must use forward slashes")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("repository paths must be normalized relative paths")
    return path.as_posix()


def _absolute_path(value: str, *, field: str) -> str:
    if not value or "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError(f"{field} must be a single-line path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{field} must be an operator-supplied absolute path")
    if ".." in path.parts:
        raise ValueError(f"{field} must be normalized")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class RepositoryIntelligenceQuery(_ImmutableModel):
    """A bounded, serializable semantic query with no repository source bodies."""

    operation: IntelligenceOperation
    text: str | None = Field(default=None, max_length=512)
    path: str | None = None
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="query text", max_chars=512)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        return None if value is None else _relative_path(value)

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if self.text is None and self.path is None:
            raise ValueError("a semantic query requires text or a repository path")
        if (self.line is not None or self.column is not None) and self.path is None:
            raise ValueError("line and column require a repository path")
        if self.column is not None and self.line is None:
            raise ValueError("column requires a line")
        return self

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json", exclude_none=True))


class IntelligenceBackendStatus(_ImmutableModel):
    """Readiness for a requested repository state, never just a backend globally."""

    backend: str
    enabled: bool
    readiness: IntelligenceReadiness
    requested_repository_fingerprint: str = Field(min_length=1, max_length=256)
    indexed_repository_fingerprint: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=512)

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if not _BACKEND_NAME.fullmatch(value):
            raise ValueError("backend must be a normalized identifier")
        return value

    @field_validator("requested_repository_fingerprint", "indexed_repository_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="repository fingerprint", max_chars=256)

    @model_validator(mode="after")
    def validate_readiness(self) -> Self:
        if not self.enabled and self.readiness is not IntelligenceReadiness.DISABLED:
            raise ValueError("a disabled backend must report disabled readiness")
        if self.readiness is IntelligenceReadiness.DISABLED and self.enabled:
            raise ValueError("an enabled backend cannot report disabled readiness")
        if (
            self.readiness is IntelligenceReadiness.READY
            and self.indexed_repository_fingerprint != self.requested_repository_fingerprint
        ):
            raise ValueError("ready status must match the requested repository fingerprint")
        return self

    @property
    def current(self) -> bool:
        return self.readiness is IntelligenceReadiness.READY


class IntelligenceProvenance(_ImmutableModel):
    """Stable evidence provenance for auditing and cache invalidation."""

    backend: str
    adapter: str = "operator-cli-json-v1"
    backend_version: str | None = Field(default=None, max_length=128)
    repository_fingerprint: str = Field(min_length=1, max_length=256)
    query_sha256: str
    response_sha256: str

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if not _BACKEND_NAME.fullmatch(value):
            raise ValueError("backend must be a normalized identifier")
        return value

    @field_validator("query_sha256", "response_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not _HEX_SHA256.fullmatch(value):
            raise ValueError("provenance hashes must be lowercase SHA-256 values")
        return value

    @field_validator("repository_fingerprint")
    @classmethod
    def validate_repository_fingerprint(cls, value: str) -> str:
        return _bounded_text(value, field="repository fingerprint", max_chars=256)

    @field_validator("backend_version")
    @classmethod
    def validate_backend_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="backend version", max_chars=128)


class RepositoryEvidence(_ImmutableModel):
    """One source-linked, byte-bounded semantic result."""

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    symbol: str | None = Field(default=None, max_length=256)
    kind: str = Field(min_length=1, max_length=64)
    snippet: str = Field(default="", max_length=2_048)
    score: float = Field(ge=0.0, le=1.0)
    content_sha256: str
    provenance: IntelligenceProvenance

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("evidence score must be finite")
        return value

    @field_validator("content_sha256")
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        if not _HEX_SHA256.fullmatch(value):
            raise ValueError("evidence content_sha256 must be a lowercase SHA-256 value")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("evidence end_line cannot precede start_line")
        return self


class RepositoryIntelligenceResult(_ImmutableModel):
    """Immutable, deterministically ordered answer returned to the control plane."""

    query: RepositoryIntelligenceQuery
    status: IntelligenceBackendStatus
    completeness: EvidenceCompleteness
    evidence: tuple[RepositoryEvidence, ...] = ()
    truncated: bool = False
    omitted_count: int = Field(default=0, ge=0)
    provenance: IntelligenceProvenance

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.evidence and not self.status.current:
            raise ValueError("non-current backend evidence cannot be published")
        if self.truncated and self.completeness is EvidenceCompleteness.COMPLETE:
            raise ValueError("a truncated result cannot be complete")
        if self.omitted_count and not self.truncated:
            raise ValueError("omitted evidence must be reported as truncated")
        if self.provenance.query_sha256 != self.query.sha256:
            raise ValueError("result provenance does not match its query")
        if self.status.backend != self.provenance.backend:
            raise ValueError("result status and provenance must name the same backend")
        if (
            self.status.indexed_repository_fingerprint is not None
            and self.status.indexed_repository_fingerprint
            != self.provenance.repository_fingerprint
        ):
            raise ValueError("result status and provenance must identify the same index state")
        if (
            self.status.current
            and self.status.requested_repository_fingerprint
            != self.provenance.repository_fingerprint
        ):
            raise ValueError("current result provenance must match the requested repository state")
        if any(item.provenance != self.provenance for item in self.evidence):
            raise ValueError("all evidence must share the result provenance")
        return self

    @property
    def sha256(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


class CliOperationCommand(_ImmutableModel):
    """Fixed read-only argv suffix for one semantic operation."""

    operation: IntelligenceOperation
    arguments: tuple[str, ...]

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value or "\x00" in value or "\r" in value or "\n" in value:
                raise ValueError("CLI arguments must be non-empty single-line strings")
            lowered = value.casefold()
            if lowered in _READ_ONLY_DENIED_ARGUMENTS or lowered.startswith(
                _READ_ONLY_DENIED_PREFIXES
            ):
                raise ValueError(f"mutating CLI argument is not allowed: {value}")
        return values


class AllowlistedCliExecutable(_ImmutableModel):
    """Operator-approved dedicated wrapper identity and expected file digest."""

    identity: str
    path: str
    sha256: str

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not _BACKEND_NAME.fullmatch(value):
            raise ValueError("executable identity must be a normalized identifier")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        validated = _absolute_path(value, field="CLI executable")
        if Path(validated).name.casefold() in _GENERIC_EXECUTABLES:
            raise ValueError("semantic backends require a dedicated operator wrapper executable")
        return validated

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _HEX_SHA256.fullmatch(value):
            raise ValueError("executable sha256 must be a lowercase SHA-256 value")
        return value

    @model_validator(mode="after")
    def validate_identity_matches_path(self) -> Self:
        if Path(self.path).name != self.identity:
            raise ValueError("executable identity must match the executable basename")
        return self


class ReadOnlyCliBackendConfig(_ImmutableModel):
    """Disabled-by-default configuration for an operator-managed CLI wrapper."""

    backend: str
    executable: AllowlistedCliExecutable
    commands: tuple[CliOperationCommand, ...]
    enabled: bool = False
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_response_bytes: int = Field(default=262_144, ge=1_024, le=1_048_576)
    max_evidence: int = Field(default=50, ge=1, le=100)
    max_total_snippet_chars: int = Field(default=16_384, ge=0, le=65_536)

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if not _BACKEND_NAME.fullmatch(value):
            raise ValueError("backend must be a normalized identifier")
        return value

    @model_validator(mode="after")
    def validate_commands(self) -> Self:
        operations = [command.operation for command in self.commands]
        if not operations:
            raise ValueError("at least one read-only operation command is required")
        if len(operations) != len(set(operations)):
            raise ValueError("each semantic operation may be configured only once")
        return self


class SemanticCliCommandPlan(_ImmutableModel):
    """An inert execution recipe suitable for the existing managed command boundary."""

    backend: str
    executable: AllowlistedCliExecutable
    argv: tuple[str, ...]
    working_directory: str
    stdin_json: str
    timeout_seconds: int = Field(ge=1, le=120)
    max_stdout_bytes: int = Field(ge=1_024, le=1_048_576)
    query: RepositoryIntelligenceQuery
    repository_fingerprint: str
    environment: tuple[tuple[str, str], ...] = (("LANG", "C"), ("LC_ALL", "C"))
    shell: Literal[False] = False
    network_allowed: Literal[False] = False
    inherit_environment: Literal[False] = False
    require_read_only_filesystem: Literal[True] = True
    require_executable_hash_verification: Literal[True] = True
    require_repository_fingerprint_before_and_after: Literal[True] = True
    require_evidence_content_hash_verification: Literal[True] = True

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        return _absolute_path(value, field="semantic CLI working directory")

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if not _BACKEND_NAME.fullmatch(value):
            raise ValueError("backend must be a normalized identifier")
        return value

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or not Path(values[0]).is_absolute():
            raise ValueError("semantic CLI argv must begin with an absolute executable")
        if any(not item or "\x00" in item or "\r" in item or "\n" in item for item in values):
            raise ValueError("semantic CLI argv must contain only single-line tokens")
        for item in values[1:]:
            lowered = item.casefold()
            if lowered in _READ_ONLY_DENIED_ARGUMENTS or lowered.startswith(
                _READ_ONLY_DENIED_PREFIXES
            ):
                raise ValueError(f"mutating CLI argument is not allowed: {item}")
        return values

    @model_validator(mode="after")
    def validate_fixed_executable(self) -> Self:
        if self.argv[0] != self.executable.path:
            raise ValueError("semantic CLI argv must use the allowlisted executable")
        expected_stdin = _canonical_json(
            {
                "contract_version": _CONTRACT_VERSION,
                "query": self.query.model_dump(mode="json", exclude_none=True),
                "repository_fingerprint": self.repository_fingerprint,
            }
        )
        if self.stdin_json != expected_stdin:
            raise ValueError("semantic CLI stdin must match the command-plan query and repository")
        return self

    @field_validator("repository_fingerprint")
    @classmethod
    def validate_repository_fingerprint(cls, value: str) -> str:
        return _bounded_text(value, field="repository fingerprint", max_chars=256)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        expected = (("LANG", "C"), ("LC_ALL", "C"))
        if value != expected:
            raise ValueError("semantic CLI plans use only the deterministic locale environment")
        return value

    @property
    def sha256(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


class SemanticFileDigest(_ImmutableModel):
    """Digest independently observed by the managed executor after CLI completion."""

    path: str
    content_sha256: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("content_sha256")
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        if not _HEX_SHA256.fullmatch(value):
            raise ValueError("file content_sha256 must be a lowercase SHA-256 value")
        return value


class SemanticExecutionReceipt(_ImmutableModel):
    """Execution-time checks required before semantic evidence can be published.

    The adapter never creates this receipt. The managed executor must hash the exact
    executable, fingerprint the workspace before and after execution, enforce a
    read-only/network-isolated process, and hash every evidence file after execution.
    """

    plan_sha256: str
    executable_sha256: str
    repository_fingerprint_before: str = Field(min_length=1, max_length=256)
    repository_fingerprint_after: str = Field(min_length=1, max_length=256)
    file_digests: tuple[SemanticFileDigest, ...] = ()
    filesystem_read_only: Literal[True]
    network_isolated: Literal[True]
    environment_isolated: Literal[True]

    @field_validator("plan_sha256", "executable_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _HEX_SHA256.fullmatch(value):
            raise ValueError("execution receipt hashes must be lowercase SHA-256 values")
        return value

    @field_validator("repository_fingerprint_before", "repository_fingerprint_after")
    @classmethod
    def validate_repository_fingerprint(cls, value: str) -> str:
        return _bounded_text(value, field="repository fingerprint", max_chars=256)

    @model_validator(mode="after")
    def validate_unique_files(self) -> Self:
        paths = [item.path for item in self.file_digests]
        if len(paths) != len(set(paths)):
            raise ValueError("execution receipt file digests must have unique paths")
        return self


class SemanticBackendDisabledError(RuntimeError):
    """Raised when a disabled semantic backend is asked to create a plan."""


class UnsupportedSemanticOperationError(ValueError):
    """Raised when no operator-approved argv exists for the requested operation."""


class RepositoryIntelligence(Protocol):
    """Non-executing provider contract for optional semantic backends."""

    def status(self, repository_fingerprint: str) -> IntelligenceBackendStatus:
        """Return local status without probing external state."""

        ...

    def plan(
        self,
        query: RepositoryIntelligenceQuery,
        *,
        repository_root: Path,
        repository_fingerprint: str,
    ) -> SemanticCliCommandPlan:
        """Return an inert read-only command plan."""

        ...

    def parse_result(
        self,
        plan: SemanticCliCommandPlan,
        stdout: bytes | str,
        *,
        receipt: SemanticExecutionReceipt,
    ) -> RepositoryIntelligenceResult:
        """Parse bounded provider output without performing I/O."""

        ...


class ReadOnlySemanticCliAdapter:
    """Plan and parse calls to a JSON-speaking LSP/Moderne-like CLI wrapper.

    Query data is sent only as canonical JSON on stdin.  It is never interpolated into
    an argv token or a shell command.  Execution remains the responsibility of the
    managed command boundary, which must independently authorize the executable.
    """

    def __init__(self, config: ReadOnlyCliBackendConfig) -> None:
        self.config = config
        self._commands = {command.operation: command.arguments for command in config.commands}

    def status(self, repository_fingerprint: str) -> IntelligenceBackendStatus:
        """Return local configuration status without probing or executing the backend."""

        readiness = (
            IntelligenceReadiness.CONFIGURED
            if self.config.enabled
            else IntelligenceReadiness.DISABLED
        )
        return IntelligenceBackendStatus(
            backend=self.config.backend,
            enabled=self.config.enabled,
            readiness=readiness,
            requested_repository_fingerprint=repository_fingerprint,
            detail=(
                "configured; readiness requires an operator-authorized probe"
                if self.config.enabled
                else "semantic repository intelligence is disabled by default"
            ),
        )

    def plan(
        self,
        query: RepositoryIntelligenceQuery,
        *,
        repository_root: Path,
        repository_fingerprint: str,
    ) -> SemanticCliCommandPlan:
        """Build a deterministic, non-executing argv/stdin plan."""

        if not self.config.enabled:
            raise SemanticBackendDisabledError(
                f"semantic backend {self.config.backend!r} is disabled"
            )
        arguments = self._commands.get(query.operation)
        if arguments is None:
            raise UnsupportedSemanticOperationError(
                f"operation {query.operation.value!r} is not configured for {self.config.backend!r}"
            )
        if not repository_root.is_absolute():
            raise ValueError("repository_root must be absolute")
        stdin = _canonical_json(
            {
                "contract_version": _CONTRACT_VERSION,
                "query": query.model_dump(mode="json", exclude_none=True),
                "repository_fingerprint": repository_fingerprint,
            }
        )
        return SemanticCliCommandPlan(
            backend=self.config.backend,
            executable=self.config.executable,
            argv=(self.config.executable.path, *arguments),
            working_directory=str(repository_root),
            stdin_json=stdin,
            timeout_seconds=self.config.timeout_seconds,
            max_stdout_bytes=self.config.max_response_bytes,
            query=query,
            repository_fingerprint=repository_fingerprint,
        )

    def parse_result(
        self,
        plan: SemanticCliCommandPlan,
        stdout: bytes | str,
        *,
        receipt: SemanticExecutionReceipt,
    ) -> RepositoryIntelligenceResult:
        """Parse output only after required executor checks have been attested."""

        if not self.config.enabled:
            raise SemanticBackendDisabledError(
                f"semantic backend {self.config.backend!r} is disabled"
            )
        if plan.backend != self.config.backend:
            raise ValueError("command plan belongs to a different semantic backend")
        expected_arguments = self._commands.get(plan.query.operation)
        if (
            plan.executable != self.config.executable
            or expected_arguments is None
            or plan.argv != (self.config.executable.path, *expected_arguments)
        ):
            raise ValueError("command plan does not match the operator-approved fixed argv")
        self._validate_execution_receipt(plan, receipt)
        raw = stdout if isinstance(stdout, bytes) else stdout.encode("utf-8")
        response_limit = min(plan.max_stdout_bytes, self.config.max_response_bytes)
        if len(raw) > response_limit:
            raise ValueError("semantic backend response exceeds the configured byte limit")
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("semantic backend response must be UTF-8 JSON") from error
        if not isinstance(document, Mapping):
            raise ValueError("semantic backend response must be a JSON object")
        if document.get("contract_version") != _CONTRACT_VERSION:
            raise ValueError("semantic backend response has an unsupported contract_version")
        if document.get("query_sha256") != plan.query.sha256:
            raise ValueError("semantic backend response does not match the command-plan query")
        return self._parse_document(
            plan,
            document,
            receipt=receipt,
            response_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _validate_execution_receipt(
        self,
        plan: SemanticCliCommandPlan,
        receipt: SemanticExecutionReceipt,
    ) -> None:
        if receipt.plan_sha256 != plan.sha256:
            raise ValueError("execution receipt does not match the semantic command plan")
        if receipt.executable_sha256 != plan.executable.sha256:
            raise ValueError("executed semantic wrapper does not match its allowlisted digest")
        if (
            receipt.repository_fingerprint_before != plan.repository_fingerprint
            or receipt.repository_fingerprint_after != plan.repository_fingerprint
        ):
            raise ValueError("repository changed before or during semantic CLI execution")

    def _parse_document(
        self,
        plan: SemanticCliCommandPlan,
        document: Mapping[str, Any],
        *,
        receipt: SemanticExecutionReceipt,
        response_sha256: str,
    ) -> RepositoryIntelligenceResult:
        indexed_fingerprint = document.get("repository_fingerprint")
        if not isinstance(indexed_fingerprint, str):
            raise ValueError("semantic response is missing repository_fingerprint")
        readiness = IntelligenceReadiness(document.get("readiness", "error"))
        if readiness is IntelligenceReadiness.READY and indexed_fingerprint != plan.repository_fingerprint:
            readiness = IntelligenceReadiness.STALE
        backend_version = document.get("backend_version")
        if backend_version is not None and not isinstance(backend_version, str):
            raise ValueError("backend_version must be a string")
        provenance = IntelligenceProvenance(
            backend=self.config.backend,
            backend_version=backend_version,
            repository_fingerprint=indexed_fingerprint,
            query_sha256=plan.query.sha256,
            response_sha256=response_sha256,
        )
        status = IntelligenceBackendStatus(
            backend=self.config.backend,
            enabled=True,
            readiness=readiness,
            requested_repository_fingerprint=plan.repository_fingerprint,
            indexed_repository_fingerprint=indexed_fingerprint,
            detail=document.get("detail"),
        )
        raw_items = document.get("evidence", [])
        if not isinstance(raw_items, list):
            raise ValueError("semantic response evidence must be a JSON array")
        provider_omitted = document.get("omitted_count", 0)
        if not isinstance(provider_omitted, int) or isinstance(provider_omitted, bool) or provider_omitted < 0:
            raise ValueError("semantic response omitted_count must be a non-negative integer")
        provider_completeness = EvidenceCompleteness(document.get("completeness", "unknown"))

        if not status.current:
            omitted = provider_omitted + len(raw_items)
            return RepositoryIntelligenceResult(
                query=plan.query,
                status=status,
                completeness=EvidenceCompleteness.UNKNOWN,
                evidence=(),
                truncated=omitted > 0,
                omitted_count=omitted,
                provenance=provenance,
            )

        candidates: dict[tuple[str, int, int, str | None, str], RepositoryEvidence] = {}
        observed_digests = {
            item.path: item.content_sha256 for item in receipt.file_digests
        }
        locally_truncated = False
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                raise ValueError("each semantic evidence item must be a JSON object")
            item = dict(raw_item)
            snippet = item.get("snippet", "")
            if not isinstance(snippet, str):
                raise ValueError("semantic evidence snippet must be a string")
            if len(snippet) > 2_048:
                snippet = snippet[:2_048]
                locally_truncated = True
            item["snippet"] = snippet
            item["provenance"] = provenance
            evidence = RepositoryEvidence.model_validate(item)
            observed_digest = observed_digests.get(evidence.path)
            if observed_digest is None:
                raise ValueError(
                    f"semantic evidence file was not independently hashed: {evidence.path}"
                )
            if observed_digest != evidence.content_sha256:
                raise ValueError(
                    f"semantic evidence content hash does not match the workspace: {evidence.path}"
                )
            key = (
                evidence.path,
                evidence.start_line,
                evidence.end_line,
                evidence.symbol,
                evidence.kind,
            )
            current = candidates.get(key)
            if current is None or evidence.score > current.score:
                candidates[key] = evidence

        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                -item.score,
                item.path,
                item.start_line,
                item.end_line,
                item.symbol or "",
                item.kind,
            ),
        )
        count_limit = min(plan.query.limit, self.config.max_evidence)
        selected: list[RepositoryEvidence] = []
        snippet_budget = self.config.max_total_snippet_chars
        for item in ordered[:count_limit]:
            snippet = item.snippet[:snippet_budget]
            if snippet != item.snippet:
                locally_truncated = True
            snippet_budget -= len(snippet)
            selected.append(item.model_copy(update={"snippet": snippet}))
        omitted = provider_omitted + max(0, len(raw_items) - len(selected))
        truncated = locally_truncated or omitted > 0
        completeness = (
            EvidenceCompleteness.PARTIAL if truncated else provider_completeness
        )
        return RepositoryIntelligenceResult(
            query=plan.query,
            status=status,
            completeness=completeness,
            evidence=tuple(selected),
            truncated=truncated,
            omitted_count=omitted,
            provenance=provenance,
        )


__all__ = [
    "AllowlistedCliExecutable",
    "CliOperationCommand",
    "EvidenceCompleteness",
    "IntelligenceBackendStatus",
    "IntelligenceOperation",
    "IntelligenceProvenance",
    "IntelligenceReadiness",
    "ReadOnlyCliBackendConfig",
    "ReadOnlySemanticCliAdapter",
    "RepositoryEvidence",
    "RepositoryIntelligence",
    "RepositoryIntelligenceQuery",
    "RepositoryIntelligenceResult",
    "SemanticBackendDisabledError",
    "SemanticCliCommandPlan",
    "SemanticExecutionReceipt",
    "SemanticFileDigest",
    "UnsupportedSemanticOperationError",
]
