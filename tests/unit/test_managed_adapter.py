from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from harness.sandbox import SandboxRequest, SandboxResult
from harness.tools.adk_adapter import create_adk_tools


class _RecordingSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        return SandboxResult(
            status="ok",
            exit_code=0,
            stdout="sandbox output",
            stderr="",
            duration_ms=12,
            artifact_uri="file:///artifact.log",
        )


def test_managed_adapter_blocks_unapproved_network_and_redacts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MY_API_KEY", "super-secret-api-key-value")
    monkeypatch.setenv("ADK_CODING_REDACT_ENV_VARS", "MY_API_KEY")
    tools = create_adk_tools(tmp_path)

    tools.write(
        "secret.txt",
        "token=super-secret-api-key-value\n",
        expected_absent=True,
    )
    read_result = tools.read("secret.txt")
    assert "super-secret-api-key-value" not in read_result["model_text"]
    assert "<redacted>" in read_result["model_text"]

    blocked = tools.bash("curl https://example.com")
    assert blocked["status"] == "blocked"
    assert blocked["approval_required"] is True
    assert blocked["risk"] == "network_access"


def test_exact_write_replay_uses_receipt_without_repeating_side_effect(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    tools = create_adk_tools(tmp_path)

    first = tools.write("result.txt", "stable\n", expected_absent=True)
    second = tools.write("result.txt", "stable\n", expected_absent=True)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["replayed"] is True
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "stable\n"


def test_approved_model_bash_uses_injected_command_sandbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    sandbox = _RecordingSandbox(tmp_path)
    tools = create_adk_tools(tmp_path, sandbox=sandbox)

    result = tools.bash("git status --short", timeout_seconds=17)

    assert result["status"] == "ok"
    assert result["model_text"] == "sandbox output"
    assert result["artifact_uri"] == "file:///artifact.log"
    assert sandbox.requests == [
        SandboxRequest(command="git status --short", timeout_seconds=17)
    ]


def test_managed_read_recovers_bounded_workspace_and_command_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(state_root))
    monkeypatch.setenv("MY_API_KEY", "artifact-sensitive-value")
    monkeypatch.setenv("ADK_CODING_REDACT_ENV_VARS", "MY_API_KEY")
    tools = create_adk_tools(tmp_path)

    workspace_content = b"first\nartifact-sensitive-value\nthird\nfourth\n"
    workspace_digest = hashlib.sha256(workspace_content).hexdigest()
    workspace_artifact = tmp_path / ".artifacts" / "tool-output" / f"{workspace_digest}.txt"
    workspace_artifact.parent.mkdir(parents=True)
    workspace_artifact.write_bytes(workspace_content)
    workspace_uri = f"artifact://tool-output/{workspace_artifact.name}"

    command_content = b"--- stdout ---\nline one\nline two\n--- stderr ---\n"
    command_digest = hashlib.sha256(command_content).hexdigest()
    command_artifact = (
        state_root
        / "artifacts"
        / "commands"
        / f"command-{command_digest}.log"
    )
    command_artifact.parent.mkdir(parents=True)
    command_artifact.write_bytes(command_content)

    workspace_result = tools.read(workspace_uri, offset=2, limit=1)
    command_result = tools.read(command_artifact.as_uri(), offset=2, limit=2)

    assert workspace_result["status"] == "ok"
    assert "artifact-sensitive-value" not in workspace_result["model_text"]
    assert "<redacted>" in workspace_result["model_text"]
    assert workspace_result["truncated"] is True
    assert "[more available: read offset=3]" in workspace_result["model_text"]
    assert "line one" in command_result["model_text"]
    assert "line two" in command_result["model_text"]
    assert len(command_result["model_text"].encode()) < 32_000


def test_managed_read_rejects_foreign_traversal_and_tampered_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(state_root))
    tools = create_adk_tools(tmp_path)

    foreign_content = b"foreign"
    foreign_digest = hashlib.sha256(foreign_content).hexdigest()
    foreign = tmp_path / "foreign" / f"command-{foreign_digest}.log"
    foreign.parent.mkdir()
    foreign.write_bytes(foreign_content)

    tampered_digest = hashlib.sha256(b"original").hexdigest()
    tampered = tmp_path / ".artifacts" / "tool-output" / f"{tampered_digest}.txt"
    tampered.parent.mkdir(parents=True)
    tampered.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="content-addressed filename"):
        tools.read(f"artifact://tool-output/../{foreign_digest}.txt")
    with pytest.raises(ValueError, match="outside managed artifact roots"):
        tools.read(foreign.as_uri())
    with pytest.raises(ValueError, match="content hash"):
        tools.read(f"artifact://tool-output/{tampered.name}")
