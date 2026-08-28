"""Environment-driven sandbox backend selection."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path

from harness.config import SandboxConfig, SecretRef

from .base import CommandSandbox
from .docker import DockerSandbox
from .kubernetes import KubernetesSandbox
from .local import LocalSandbox
from .remote import HttpRemoteTransport, RemoteSandbox, RemoteTransport

SecretResolver = Callable[[SecretRef], str]


def _environment_secret(ref: SecretRef) -> str:
    value = os.getenv(ref.env)
    if not value:
        raise ValueError(f"required secret environment variable is not set: {ref.env}")
    return value


def create_configured_command_sandbox(
    workspace: Path,
    state_root: Path,
    config: SandboxConfig,
    *,
    max_output_bytes: int,
    known_secrets: Sequence[str] = (),
    remote_transport: RemoteTransport | None = None,
    secret_resolver: SecretResolver = _environment_secret,
) -> CommandSandbox:
    """Build a sandbox entirely from validated composition and explicit bindings."""

    artifact_root = state_root / "artifacts" / "commands"
    if config.kind == "local":
        return LocalSandbox(
            workspace,
            artifact_root,
            known_secrets=known_secrets,
            max_memory_bytes=config.memory_bytes,
            max_processes=config.max_processes,
            max_file_bytes=config.max_file_bytes,
            max_output_bytes=max_output_bytes,
        )
    if config.kind == "docker":
        return DockerSandbox(
            workspace,
            artifact_root,
            image=config.image,
            known_secrets=known_secrets,
            allow_network=False,
            cpus=config.cpus,
            memory=config.memory,
            pids_limit=config.pids_limit,
            max_output_bytes=max_output_bytes,
        )
    if config.kind == "kubernetes":
        return KubernetesSandbox(
            workspace,
            artifact_root,
            namespace=config.namespace,
            pod=config.pod,
            container=config.container,
            remote_workspace=config.remote_workspace,
            network_isolated=config.network_isolated,
            known_secrets=known_secrets,
            max_output_bytes=max_output_bytes,
        )
    transport = remote_transport
    if transport is None:
        token = secret_resolver(config.token)
        transport = HttpRemoteTransport(
            config.endpoint,
            bearer_token=token,
            max_response_bytes=config.max_response_bytes,
        )
    return RemoteSandbox(
        workspace,
        artifact_root,
        remote_workspace=config.remote_workspace,
        transport=transport,
        known_secrets=known_secrets,
        max_output_bytes=max_output_bytes,
    )


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_command_sandbox(
    workspace: Path,
    state_root: Path,
    *,
    remote_transport: RemoteTransport | None = None,
    known_secrets: Sequence[str] = (),
) -> CommandSandbox:
    backend = os.getenv("ADK_CODING_SANDBOX", "local").strip().lower()
    artifact_root = state_root / "artifacts" / "commands"
    if backend == "local":
        return LocalSandbox(
            workspace,
            artifact_root,
            known_secrets=known_secrets,
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
            known_secrets=known_secrets,
            docker_binary=os.getenv("ADK_CODING_DOCKER_BINARY", "docker"),
            allow_network=_truthy("ADK_CODING_ALLOW_NETWORK"),
            cpus=float(os.getenv("ADK_CODING_SANDBOX_CPUS", "2.0")),
            memory=os.getenv("ADK_CODING_SANDBOX_MEMORY", "4g"),
            pids_limit=int(os.getenv("ADK_CODING_SANDBOX_PIDS", "256")),
            tmpfs_size=os.getenv("ADK_CODING_SANDBOX_TMPFS", "512m"),
            max_output_bytes=int(os.getenv("ADK_CODING_TOOL_OUTPUT_BYTES", "16000")),
        )
    if backend == "kubernetes":
        namespace = os.getenv("ADK_CODING_K8S_NAMESPACE", "").strip()
        pod = os.getenv("ADK_CODING_K8S_POD", "").strip()
        remote_workspace = os.getenv("ADK_CODING_K8S_WORKSPACE", "").strip()
        if not namespace or not pod or not remote_workspace:
            raise ValueError(
                "ADK_CODING_K8S_NAMESPACE, ADK_CODING_K8S_POD, and "
                "ADK_CODING_K8S_WORKSPACE are required for the Kubernetes sandbox"
            )
        return KubernetesSandbox(
            workspace,
            artifact_root,
            namespace=namespace,
            pod=pod,
            container=os.getenv("ADK_CODING_K8S_CONTAINER") or None,
            remote_workspace=remote_workspace,
            network_isolated=_truthy("ADK_CODING_K8S_NETWORK_ISOLATED"),
            known_secrets=known_secrets,
            kubectl_binary=os.getenv("ADK_CODING_KUBECTL_BINARY", "kubectl"),
            timeout_binary=os.getenv(
                "ADK_CODING_K8S_TIMEOUT_BINARY", "/usr/bin/timeout"
            ),
            max_output_bytes=int(os.getenv("ADK_CODING_TOOL_OUTPUT_BYTES", "16000")),
        )
    if backend == "remote":
        remote_workspace = os.getenv("ADK_CODING_REMOTE_WORKSPACE", "").strip()
        if not remote_workspace:
            raise ValueError(
                "ADK_CODING_REMOTE_WORKSPACE is required for the remote sandbox"
            )
        transport = remote_transport
        if transport is None:
            endpoint = os.getenv("ADK_CODING_REMOTE_ENDPOINT", "").strip()
            token = os.getenv("ADK_CODING_REMOTE_TOKEN", "").strip()
            if not endpoint or not token:
                raise ValueError(
                    "ADK_CODING_REMOTE_ENDPOINT and ADK_CODING_REMOTE_TOKEN are "
                    "required for the remote sandbox"
                )
            transport = HttpRemoteTransport(
                endpoint,
                bearer_token=token,
                max_response_bytes=int(
                    os.getenv("ADK_CODING_REMOTE_RESPONSE_BYTES", "2000000")
                ),
            )
        return RemoteSandbox(
            workspace,
            artifact_root,
            remote_workspace=remote_workspace,
            transport=transport,
            known_secrets=known_secrets,
            max_output_bytes=int(os.getenv("ADK_CODING_TOOL_OUTPUT_BYTES", "16000")),
        )
    raise ValueError(
        f"unsupported ADK_CODING_SANDBOX={backend!r}; use 'local', 'docker', "
        "'kubernetes', or 'remote'"
    )


__all__ = ["SecretResolver", "create_command_sandbox", "create_configured_command_sandbox"]
