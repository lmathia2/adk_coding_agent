from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.sandbox import (
    HttpRemoteTransport,
    RemoteCommandRequest,
    RemoteCommandResponse,
    RemoteSandbox,
    SandboxRequest,
    create_command_sandbox,
)


class _FakeTransport:
    def __init__(
        self,
        response: RemoteCommandResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or RemoteCommandResponse(
            status="ok",
            exit_code=0,
            stdout="passed",
            stderr="",
            duration_ms=25,
        )
        self.error = error
        self.calls: list[tuple[RemoteCommandRequest, int]] = []

    def execute(
        self,
        request: RemoteCommandRequest,
        *,
        timeout_seconds: int,
    ) -> RemoteCommandResponse:
        self.calls.append((request, timeout_seconds))
        if self.error:
            raise self.error
        return self.response


class _HttpResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> _HttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


def test_remote_request_mapping_and_result_are_deterministic(tmp_path: Path) -> None:
    transport = _FakeTransport()
    sandbox = RemoteSandbox(
        tmp_path,
        tmp_path / "artifacts",
        remote_workspace="workspace-123",
        transport=transport,
        environment={"ZED": "last", "ALPHA": "first"},
    )

    result = sandbox.execute(
        SandboxRequest(
            command="pytest -q",
            timeout_seconds=7,
            environment={"ALPHA": "overridden", "REQUEST": "value"},
        )
    )

    assert result.status == "ok"
    assert result.stdout == "passed"
    request, transport_timeout = transport.calls[0]
    assert request == RemoteCommandRequest(
        workspace="workspace-123",
        command="pytest -q",
        timeout_seconds=7,
        environment=(
            ("ALPHA", "overridden"),
            ("LANG", "C.UTF-8"),
            ("LC_ALL", "C.UTF-8"),
            ("PYTHONUNBUFFERED", "1"),
            ("REQUEST", "value"),
            ("ZED", "last"),
        ),
    )
    assert transport_timeout == 22


def test_remote_output_is_redacted_before_bounded_artifact_spill(
    tmp_path: Path,
) -> None:
    token = "plain-sensitive-value"
    transport = _FakeTransport(
        RemoteCommandResponse(
            status="error",
            exit_code=1,
            stdout=f"token={token}\n" + ("x" * 2_000),
            stderr="",
            duration_ms=30,
        )
    )
    sandbox = RemoteSandbox(
        tmp_path,
        tmp_path / "artifacts",
        remote_workspace="workspace-123",
        transport=transport,
        max_output_bytes=512,
    )

    result = sandbox.execute(
        SandboxRequest(
            command="failing-command",
            environment={"CUSTOM_TOKEN": token},
        )
    )

    assert result.status == "error"
    assert result.truncated
    assert token not in result.stdout
    assert result.artifact_uri
    artifact = Path(result.artifact_uri.removeprefix("file://"))
    assert token not in artifact.read_text(encoding="utf-8")
    assert "<redacted>" in artifact.read_text(encoding="utf-8")


def test_remote_transport_timeout_and_rejection_fail_closed(tmp_path: Path) -> None:
    timeout_sandbox = RemoteSandbox(
        tmp_path,
        tmp_path / "artifacts",
        remote_workspace="workspace-123",
        transport=_FakeTransport(error=TimeoutError()),
    )
    rejected_sandbox = RemoteSandbox(
        tmp_path,
        tmp_path / "artifacts",
        remote_workspace="workspace-123",
        transport=_FakeTransport(error=ValueError("invalid response")),
    )

    timeout = timeout_sandbox.execute(SandboxRequest(command="slow"))
    rejected = rejected_sandbox.execute(SandboxRequest(command="invalid"))

    assert (timeout.status, timeout.exit_code) == ("timeout", 124)
    assert (rejected.status, rejected.exit_code) == ("blocked", None)
    assert "invalid response" in rejected.stderr


def test_remote_rejects_invalid_request_before_calling_transport(
    tmp_path: Path,
) -> None:
    transport = _FakeTransport()
    sandbox = RemoteSandbox(
        tmp_path,
        tmp_path / "artifacts",
        remote_workspace="workspace-123",
        transport=transport,
    )

    result = sandbox.execute(SandboxRequest(command="bad\x00command"))

    assert result.status == "blocked"
    assert result.exit_code is None
    assert transport.calls == []


def test_remote_response_validation_rejects_ambiguous_results() -> None:
    with pytest.raises(ValueError, match="exit_code 0"):
        RemoteCommandResponse.from_payload(
            {
                "status": "ok",
                "exit_code": 1,
                "stdout": "",
                "stderr": "",
                "duration_ms": 1,
            }
        )
    with pytest.raises(ValueError, match="unknown fields"):
        RemoteCommandResponse.from_payload(
            {
                "status": "error",
                "exit_code": 1,
                "stdout": "",
                "stderr": "",
                "duration_ms": 1,
                "unexpected": True,
            }
        )


def test_https_transport_serializes_without_ambient_network() -> None:
    captured: dict[str, Any] = {}
    response = json.dumps(
        {
            "duration_ms": 12,
            "exit_code": 0,
            "status": "ok",
            "stderr": "",
            "stdout": "passed",
        }
    ).encode()

    def fake_urlopen(request, **kwargs):
        captured["request"] = request
        captured["timeout"] = kwargs["timeout"]
        return _HttpResponse(response)

    transport = HttpRemoteTransport(
        "https://sandbox.example.test/v1/execute",
        bearer_token="transport-secret",
        urlopen=fake_urlopen,
    )
    request = RemoteCommandRequest(
        workspace="workspace-123",
        command="pytest -q",
        timeout_seconds=10,
        environment=(("A", "1"),),
    )

    result = transport.execute(request, timeout_seconds=25)

    http_request = captured["request"]
    assert result.stdout == "passed"
    assert captured["timeout"] == 25
    assert http_request.full_url == "https://sandbox.example.test/v1/execute"
    assert json.loads(http_request.data) == request.to_payload()
    assert http_request.get_header("Authorization") == "Bearer transport-secret"


def test_factory_injects_remote_transport_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _FakeTransport()
    monkeypatch.setenv("ADK_CODING_SANDBOX", "remote")
    monkeypatch.setenv("ADK_CODING_REMOTE_WORKSPACE", "workspace-123")
    monkeypatch.delenv("ADK_CODING_REMOTE_ENDPOINT", raising=False)
    monkeypatch.delenv("ADK_CODING_REMOTE_TOKEN", raising=False)

    sandbox = create_command_sandbox(
        tmp_path,
        tmp_path / "state",
        remote_transport=transport,
    )

    assert isinstance(sandbox, RemoteSandbox)
    assert sandbox.transport is transport


def test_http_transport_rejects_insecure_configuration() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HttpRemoteTransport(
            "http://sandbox.example.test/v1/execute",
            bearer_token="secret",
        )
    with pytest.raises(ValueError, match="bearer token"):
        HttpRemoteTransport(
            "https://sandbox.example.test/v1/execute",
            bearer_token="",
        )
