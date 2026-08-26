"""Environment-driven sandbox backend selection."""

from __future__ import annotations

import os
from pathlib import Path

from .base import CommandSandbox
from .docker import DockerSandbox
from .local import LocalSandbox


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_command_sandbox(
    workspace: Path,
    state_root: Path,
) -> CommandSandbox:
    backend = os.getenv("ADK_CODING_SANDBOX", "local").strip().lower()
    artifact_root = state_root / "artifacts" / "commands"
    if backend == "local":
        return LocalSandbox(
            workspace,
            artifact_root,
            max_memory_bytes=int(
                os.getenv("ADK_CODING_LOCAL_MEMORY_BYTES", str(4 * 1024**3))
            ),
            max_processes=int(os.getenv("ADK_CODING_LOCAL_PIDS", "256")),
            max_file_bytes=int(
                os.getenv("ADK_CODING_LOCAL_FILE_BYTES", str(1024**3))
            ),
            max_output_bytes=int(os.getenv("ADK_CODING_TOOL_OUTPUT_BYTES", "16000")),
        )
    if backend == "docker":
        image = os.getenv("ADK_CODING_SANDBOX_IMAGE", "").strip()
        if not image:
            raise ValueError(
                "ADK_CODING_SANDBOX_IMAGE is required for the Docker sandbox"
            )
        return DockerSandbox(
            workspace,
            artifact_root,
            image=image,
            docker_binary=os.getenv("ADK_CODING_DOCKER_BINARY", "docker"),
            allow_network=_truthy("ADK_CODING_ALLOW_NETWORK"),
            cpus=float(os.getenv("ADK_CODING_SANDBOX_CPUS", "2.0")),
            memory=os.getenv("ADK_CODING_SANDBOX_MEMORY", "4g"),
            pids_limit=int(os.getenv("ADK_CODING_SANDBOX_PIDS", "256")),
            tmpfs_size=os.getenv("ADK_CODING_SANDBOX_TMPFS", "512m"),
            max_output_bytes=int(os.getenv("ADK_CODING_TOOL_OUTPUT_BYTES", "16000")),
        )
    raise ValueError(
        f"unsupported ADK_CODING_SANDBOX={backend!r}; use 'local' or 'docker'"
    )


__all__ = ["create_command_sandbox"]
