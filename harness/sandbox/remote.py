"""Typed enterprise remote-sandbox transport and adapter."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from .base import SandboxRequest, SandboxResult, SandboxStatus
from .output import bounded_result, environment_secret_values

_MAX_TIMEOUT_SECONDS = 3_600
_DEFAULT_MAX_RESPONSE_BYTES = 2_000_000
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class RemoteCommandRequest:
    workspace: str
    command: str
    timeout_seconds: int
    environment: tuple[tuple[str, str], ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "command": self.command,
            "environment": dict(self.environment),
            "timeout_seconds": self.timeout_seconds,
            "workspace": self.workspace,
        }


@dataclass(frozen=True, slots=True)
class RemoteCommandResponse:
    status: SandboxStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> RemoteCommandResponse:
        allowed = {"status", "exit_code", "stdout", "stderr", "duration_ms"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"remote response contains unknown fields: {sorted(unknown)}")
        status = payload.get("status")
        if status not in {"ok", "error", "blocked", "timeout"}:
            raise ValueError("remote response has invalid status")
        exit_code = payload.get("exit_code")
        if exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)
        ):
            raise ValueError("remote response exit_code must be an integer or null")
        stdout = payload.get("stdout", "")
        stderr = payload.get("stderr", "")
        duration_ms = payload.get("duration_ms")
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise ValueError("remote response output must be text")
        if (
            not isinstance(duration_ms, int)
            or isinstance(duration_ms, bool)
            or duration_ms < 0
        ):
            raise ValueError("remote response duration_ms must be non-negative")
        if status == "ok" and exit_code != 0:
            raise ValueError("successful remote response must have exit_code 0")
        return cls(
            status=cast(SandboxStatus, status),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )


class RemoteTransport(Protocol):
    def execute(
        self,
        request: RemoteCommandRequest,
        *,
        timeout_seconds: int,
    ) -> RemoteCommandResponse: ...


UrlOpen = Callable[..., Any]


class HttpRemoteTransport:
    """Minimal HTTPS JSON transport with strict response validation."""

    def __init__(
        self,
        endpoint: str,
        *,
        bearer_token: str,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        urlopen: UrlOpen = urllib.request.urlopen,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("remote sandbox endpoint must be a plain HTTPS URL")
        if not bearer_token.strip():
            raise ValueError("remote sandbox bearer token is required")
        self.endpoint = endpoint.rstrip("/")
        self.bearer_token = bearer_token
        self.max_response_bytes = max(max_response_bytes, 1_024)
        self.urlopen = urlopen

    def execute(
        self,
        request: RemoteCommandRequest,
        *,
        timeout_seconds: int,
    ) -> RemoteCommandResponse:
        body = json.dumps(
            request.to_payload(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.urlopen(http_request, timeout=timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"remote sandbox returned HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError("remote sandbox request timed out") from exc
            raise RuntimeError("remote sandbox transport failed") from exc
        if len(raw) > self.max_response_bytes:
            raise ValueError("remote sandbox response exceeds configured byte limit")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("remote sandbox returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("remote sandbox response must be a JSON object")
        return RemoteCommandResponse.from_payload(payload)


class RemoteSandbox:
    """Execute through a pluggable enterprise transport without local fallback."""

    def __init__(
        self,
        workspace: Path,
        artifact_root: Path,
        *,
        remote_workspace: str,
        transport: RemoteTransport,
        environment: Mapping[str, str] | None = None,
        max_output_bytes: int = 16_000,
    ) -> None:
        if (
            not remote_workspace.strip()
            or "\x00" in remote_workspace
            or "\n" in remote_workspace
            or "\r" in remote_workspace
        ):
            raise ValueError("remote sandbox workspace identifier is required")
        self.workspace = workspace.resolve()
        self.artifact_root = artifact_root.resolve()
        self.remote_workspace = remote_workspace.strip()
        self.transport = transport
        self.environment = dict(environment or {})
        self.max_output_bytes = max(max_output_bytes, 256)

    @staticmethod
    def _timeout(request: SandboxRequest) -> int:
        return max(1, min(request.timeout_seconds, _MAX_TIMEOUT_SECONDS))

    def map_request(self, request: SandboxRequest) -> RemoteCommandRequest:
        if "\x00" in request.command:
            raise ValueError("remote sandbox command contains NUL")
        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            **self.environment,
            **dict(request.environment),
        }
        for name, value in environment.items():
            if not isinstance(name, str) or not _ENVIRONMENT_NAME.fullmatch(name):
                raise ValueError(f"invalid remote environment variable name: {name!r}")
            if not isinstance(value, str):
                raise ValueError(f"remote environment variable {name!r} must be text")
            if "\x00" in value:
                raise ValueError(f"remote environment variable {name!r} contains NUL")
        return RemoteCommandRequest(
            workspace=self.remote_workspace,
            command=request.command,
            timeout_seconds=self._timeout(request),
            environment=tuple(sorted(environment.items())),
        )

    def execute(self, request: SandboxRequest) -> SandboxResult:
        started = time.monotonic()
        timeout = self._timeout(request)
        known_secrets = environment_secret_values(
            {**self.environment, **dict(request.environment)}
        )
        try:
            response = self.transport.execute(
                self.map_request(request),
                timeout_seconds=timeout + 15,
            )
            return bounded_result(
                status=response.status,
                exit_code=response.exit_code,
                stdout=response.stdout,
                stderr=response.stderr,
                duration_ms=response.duration_ms,
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )
        except TimeoutError:
            return bounded_result(
                status="timeout",
                exit_code=124,
                stdout="",
                stderr="remote sandbox command timed out",
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return bounded_result(
                status="blocked",
                exit_code=None,
                stdout="",
                stderr=f"remote sandbox rejected execution: {exc}",
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )


__all__ = [
    "HttpRemoteTransport",
    "RemoteCommandRequest",
    "RemoteCommandResponse",
    "RemoteSandbox",
    "RemoteTransport",
]
