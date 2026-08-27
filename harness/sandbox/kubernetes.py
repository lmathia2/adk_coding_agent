"""Kubernetes command adapter for pre-provisioned isolated task pods."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath

from .base import SandboxRequest, SandboxResult
from .output import bounded_result, environment_secret_values

Runner = Callable[..., subprocess.CompletedProcess[str]]

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _required_name(value: str, label: str, *, allow_subdomain: bool = False) -> str:
    normalized = value.strip()
    parts = normalized.split(".") if allow_subdomain else [normalized]
    if (
        not normalized
        or len(normalized) > (253 if allow_subdomain else 63)
        or not all(_DNS_LABEL.fullmatch(part) for part in parts)
    ):
        raise ValueError(f"Kubernetes {label} must be a valid DNS name")
    return normalized


def _remote_workspace(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("Kubernetes workspace contains an invalid character")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Kubernetes workspace must be an absolute confined path")
    return path.as_posix()


def _environment(values: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not _ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError(f"invalid environment variable name: {name!r}")
        if not isinstance(value, str):
            raise ValueError(f"environment variable {name!r} must be text")
        if "\x00" in value:
            raise ValueError(f"environment variable {name!r} contains NUL")
        result[name] = value
    return result


class KubernetesSandbox:
    """Execute in an existing pod whose isolation is enforced by cluster policy.

    The adapter never creates a pod and never falls back to local execution. The
    caller must provision a task-specific pod with a read-write task workspace,
    resource limits, a non-root security context, and an enforced deny-by-default
    NetworkPolicy.
    """

    def __init__(
        self,
        workspace: Path,
        artifact_root: Path,
        *,
        namespace: str,
        pod: str,
        container: str | None = None,
        remote_workspace: str,
        network_isolated: bool,
        kubectl_binary: str = "kubectl",
        timeout_binary: str = "/usr/bin/timeout",
        environment: Mapping[str, str] | None = None,
        known_secrets: Sequence[str] = (),
        max_output_bytes: int = 16_000,
        runner: Runner = subprocess.run,
    ) -> None:
        if not network_isolated:
            raise ValueError(
                "Kubernetes sandbox requires an enforced deny-by-default NetworkPolicy"
            )
        if not kubectl_binary.strip():
            raise ValueError("Kubernetes kubectl binary is required")
        if not timeout_binary.strip():
            raise ValueError("Kubernetes timeout binary is required")
        self.workspace = workspace.resolve()
        self.artifact_root = artifact_root.resolve()
        self.namespace = _required_name(namespace, "namespace")
        self.pod = _required_name(pod, "pod", allow_subdomain=True)
        self.container = (
            _required_name(container, "container") if container is not None else None
        )
        self.remote_workspace = _remote_workspace(remote_workspace)
        self.kubectl_binary = kubectl_binary
        self.timeout_binary = timeout_binary
        self.environment = _environment(environment or {})
        self.known_secrets = tuple(known_secrets)
        self.max_output_bytes = max(max_output_bytes, 256)
        self.runner = runner

    @staticmethod
    def _timeout(request: SandboxRequest) -> int:
        return max(1, min(request.timeout_seconds, 3_600))

    def build_command(self, request: SandboxRequest) -> list[str]:
        if "\x00" in request.command:
            raise ValueError("Kubernetes command contains NUL")
        timeout = self._timeout(request)
        environment = _environment(
            {
                "HOME": "/tmp/home",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONUNBUFFERED": "1",
                **self.environment,
                **dict(request.environment),
            }
        )
        command = [
            self.kubectl_binary,
            f"--request-timeout={timeout + 10}s",
            "--namespace",
            self.namespace,
            "exec",
            self.pod,
        ]
        if self.container:
            command.extend(["--container", self.container])
        command.extend(["--", "/usr/bin/env"])
        command.extend(f"{name}={value}" for name, value in sorted(environment.items()))
        command.extend(
            [
                self.timeout_binary,
                "--signal=TERM",
                "--kill-after=5s",
                f"{timeout}s",
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-lc",
                'cd -- "$1" && shift && exec "$@"',
                "sandbox",
                self.remote_workspace,
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-lc",
                request.command,
            ]
        )
        return command

    def execute(self, request: SandboxRequest) -> SandboxResult:
        started = time.monotonic()
        timeout = self._timeout(request)
        known_secrets = tuple(
            sorted(
                {
                    *self.known_secrets,
                    *environment_secret_values(
                        {**self.environment, **dict(request.environment)}
                    ),
                }
            )
        )
        try:
            completed = self.runner(
                self.build_command(request),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout + 15,
            )
            return bounded_result(
                status="ok" if completed.returncode == 0 else "error",
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (
                exc.stdout.decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode(errors="replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            return bounded_result(
                status="timeout",
                exit_code=124,
                stdout=stdout,
                stderr=(stderr + "\nKubernetes command timed out").strip(),
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )
        except (OSError, ValueError) as exc:
            return bounded_result(
                status="blocked",
                exit_code=None,
                stdout="",
                stderr=f"Kubernetes command adapter unavailable: {exc}",
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )


__all__ = ["KubernetesSandbox"]
