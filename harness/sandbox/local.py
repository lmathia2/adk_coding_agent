"""Resource-bounded local command runner for development and CI.

This runner is not an OS security boundary. Production deployments should use the
Docker or remote sandbox adapter; approval policy remains active for every backend.
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from .base import SandboxRequest, SandboxResult
from .output import bounded_result


def _limit_resources(
    *,
    max_memory_bytes: int,
    max_processes: int,
    max_file_bytes: int,
) -> None:
    def set_limit(kind: int, requested: int) -> None:
        try:
            _, hard = resource.getrlimit(kind)
            limit = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
            resource.setrlimit(kind, (limit, limit))
        except (OSError, ValueError):
            # Some development hosts disallow selected limits in a pre-exec child.
            # The local adapter is not a security boundary; managed deployments use
            # Docker or a remote sandbox for enforceable resource isolation.
            return

    set_limit(resource.RLIMIT_CORE, 0)
    set_limit(resource.RLIMIT_FSIZE, max_file_bytes)
    if hasattr(resource, "RLIMIT_AS"):
        set_limit(resource.RLIMIT_AS, max_memory_bytes)
    if hasattr(resource, "RLIMIT_NPROC") and sys.platform != "darwin":
        # RLIMIT_NPROC is per-user on macOS, so lowering it in a child can block
        # ordinary shell pipelines when the desktop user already has many processes.
        set_limit(resource.RLIMIT_NPROC, max_processes)


class LocalSandbox:
    """Execute with a minimal environment and Unix resource limits."""

    def __init__(
        self,
        workspace: Path,
        artifact_root: Path,
        *,
        environment: Mapping[str, str] | None = None,
        max_memory_bytes: int = 4 * 1024 * 1024 * 1024,
        max_processes: int = 256,
        max_file_bytes: int = 1024 * 1024 * 1024,
        max_output_bytes: int = 16_000,
    ) -> None:
        self.workspace = workspace.resolve()
        self.artifact_root = artifact_root.resolve()
        self.environment = dict(environment or {})
        self.max_memory_bytes = max_memory_bytes
        self.max_processes = max_processes
        self.max_file_bytes = max_file_bytes
        self.max_output_bytes = max_output_bytes

    def _environment(self, request: SandboxRequest) -> dict[str, str]:
        safe_defaults = {
            "HOME": str(self.workspace / ".sandbox-home"),
            "LANG": os.getenv("LANG", "C.UTF-8"),
            "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
            "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONUNBUFFERED": "1",
        }
        home = Path(safe_defaults["HOME"])
        home.mkdir(parents=True, exist_ok=True)
        return {**safe_defaults, **self.environment, **dict(request.environment)}

    def execute(self, request: SandboxRequest) -> SandboxResult:
        started = time.monotonic()
        timeout = max(1, min(request.timeout_seconds, 3_600))
        try:
            completed = subprocess.run(
                request.command,
                cwd=self.workspace,
                shell=True,
                executable="/bin/bash",
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._environment(request),
                preexec_fn=lambda: _limit_resources(
                    max_memory_bytes=self.max_memory_bytes,
                    max_processes=self.max_processes,
                    max_file_bytes=self.max_file_bytes,
                ),
            )
            return bounded_result(
                status="ok" if completed.returncode == 0 else "error",
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
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
                stderr=(stderr + "\ncommand timed out").strip(),
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
            )


__all__ = ["LocalSandbox"]
