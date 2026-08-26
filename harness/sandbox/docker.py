"""Hardened Docker command sandbox for managed coding tasks."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .base import SandboxRequest, SandboxResult
from .output import bounded_result

Runner = Callable[..., subprocess.CompletedProcess[str]]


class DockerSandbox:
    """Execute one command in a disposable, capability-dropped container."""

    def __init__(
        self,
        workspace: Path,
        artifact_root: Path,
        *,
        image: str,
        docker_binary: str = "docker",
        allow_network: bool = False,
        environment: Mapping[str, str] | None = None,
        cpus: float = 2.0,
        memory: str = "4g",
        pids_limit: int = 256,
        tmpfs_size: str = "512m",
        max_output_bytes: int = 16_000,
        runner: Runner = subprocess.run,
    ) -> None:
        if not image or ":" not in image:
            raise ValueError(
                "Docker sandbox image must be explicitly tagged or digest-pinned"
            )
        self.workspace = workspace.resolve()
        self.artifact_root = artifact_root.resolve()
        self.image = image
        self.docker_binary = docker_binary
        self.allow_network = allow_network
        self.environment = dict(environment or {})
        self.cpus = max(cpus, 0.1)
        self.memory = memory
        self.pids_limit = max(pids_limit, 16)
        self.tmpfs_size = tmpfs_size
        self.max_output_bytes = max_output_bytes
        self.runner = runner

    def build_command(self, request: SandboxRequest) -> list[str]:
        command = [
            self.docker_binary,
            "run",
            "--rm",
            "--init",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={self.workspace},dst=/workspace,rw",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            str(self.cpus),
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,noexec,size={self.tmpfs_size}",
        ]
        if not self.allow_network:
            command.extend(["--network", "none"])
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        environment = {
            "HOME": "/tmp/home",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            **self.environment,
            **dict(request.environment),
        }
        for name, value in sorted(environment.items()):
            command.extend(["--env", f"{name}={value}"])
        command.extend(
            [
                self.image,
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
        timeout = max(1, min(request.timeout_seconds, 3_600))
        command = self.build_command(request)
        try:
            completed = self.runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
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
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return bounded_result(
                status="timeout",
                exit_code=124,
                stdout=stdout,
                stderr=(stderr + "\ncontainer command timed out").strip(),
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
            )


__all__ = ["DockerSandbox"]
