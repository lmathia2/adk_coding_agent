from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.sandbox import KubernetesSandbox, SandboxRequest, create_command_sandbox


def test_kubernetes_requires_external_network_isolation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="NetworkPolicy"):
        KubernetesSandbox(
            tmp_path,
            tmp_path / "artifacts",
            namespace="tasks",
            pod="task-123",
            remote_workspace="/workspace",
            network_isolated=False,
        )


def test_kubernetes_builds_deterministic_bounded_exec_command(tmp_path: Path) -> None:
    sandbox = KubernetesSandbox(
        tmp_path,
        tmp_path / "artifacts",
        namespace="tasks",
        pod="task-123",
        container="worker",
        remote_workspace="/workspace/task",
        network_isolated=True,
        environment={"ZED": "last", "ALPHA": "first"},
    )

    command = sandbox.build_command(
        SandboxRequest(
            command="pytest -q",
            timeout_seconds=12,
            environment={"REQUEST_VALUE": "request"},
        )
    )

    assert command[:7] == [
        "kubectl",
        "--request-timeout=22s",
        "--namespace",
        "tasks",
        "exec",
        "task-123",
        "--container",
    ]
    assert command[7] == "worker"
    assert command[command.index("--") + 1] == "/usr/bin/env"
    assert command.index("ALPHA=first") < command.index("ZED=last")
    assert command[command.index("--kill-after=5s") + 1] == "12s"
    assert command[-3:] == ["--norc", "-lc", "pytest -q"]
    assert command[-1] == "pytest -q"
    assert "/workspace/task" in command


def test_kubernetes_execution_is_injected_and_redacts_output(tmp_path: Path) -> None:
    captured: list[tuple[list[str], int]] = []

    def fake_runner(command, **kwargs):
        captured.append((command, kwargs["timeout"]))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="plain-sensitive-value",
            stderr="",
        )

    sandbox = KubernetesSandbox(
        tmp_path,
        tmp_path / "artifacts",
        namespace="tasks",
        pod="task-123",
        remote_workspace="/workspace",
        network_isolated=True,
        runner=fake_runner,
    )

    result = sandbox.execute(
        SandboxRequest(
            command="run-tests",
            timeout_seconds=9,
            environment={"CUSTOM_TOKEN": "plain-sensitive-value"},
        )
    )

    assert result.status == "ok"
    assert result.stdout == "<redacted>"
    assert captured[0][1] == 24
    assert captured[0][0][-1] == "run-tests"


def test_kubernetes_timeout_never_falls_back_to_local_execution(tmp_path: Path) -> None:
    def timeout_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output="partial",
            stderr="remote still running",
        )

    sandbox = KubernetesSandbox(
        tmp_path,
        tmp_path / "artifacts",
        namespace="tasks",
        pod="task-123",
        remote_workspace="/workspace",
        network_isolated=True,
        runner=timeout_runner,
    )

    result = sandbox.execute(SandboxRequest(command="slow", timeout_seconds=1))

    assert result.status == "timeout"
    assert result.exit_code == 124
    assert "Kubernetes command timed out" in result.stderr


def test_kubernetes_timeout_redacts_before_artifact_spill(tmp_path: Path) -> None:
    secret = "kubernetes-sensitive-value"

    def timeout_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output=secret + ("x" * 3_000),
            stderr="remote still running",
        )

    sandbox = KubernetesSandbox(
        tmp_path,
        tmp_path / "artifacts",
        namespace="tasks",
        pod="task-123",
        remote_workspace="/workspace",
        network_isolated=True,
        known_secrets=[secret],
        max_output_bytes=512,
        runner=timeout_runner,
    )

    result = sandbox.execute(SandboxRequest(command="slow", timeout_seconds=1))

    assert result.artifact_uri
    artifact = Path(result.artifact_uri.removeprefix("file://"))
    assert secret not in artifact.read_text(encoding="utf-8")
    assert "<redacted>" in artifact.read_text(encoding="utf-8")


def test_kubernetes_rejects_invalid_request_before_invoking_kubectl(
    tmp_path: Path,
) -> None:
    invoked = False

    def fake_runner(command, **kwargs):
        nonlocal invoked
        invoked = True
        return subprocess.CompletedProcess(command, 0, "", "")

    sandbox = KubernetesSandbox(
        tmp_path,
        tmp_path / "artifacts",
        namespace="tasks",
        pod="task-123",
        remote_workspace="/workspace",
        network_isolated=True,
        runner=fake_runner,
    )

    result = sandbox.execute(SandboxRequest(command="bad\x00command"))

    assert result.status == "blocked"
    assert result.exit_code is None
    assert not invoked


def test_factory_configures_kubernetes_without_contacting_a_cluster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADK_CODING_SANDBOX", "kubernetes")
    monkeypatch.setenv("ADK_CODING_K8S_NAMESPACE", "tasks")
    monkeypatch.setenv("ADK_CODING_K8S_POD", "task-123")
    monkeypatch.setenv("ADK_CODING_K8S_WORKSPACE", "/workspace")
    monkeypatch.setenv("ADK_CODING_K8S_NETWORK_ISOLATED", "true")

    sandbox = create_command_sandbox(tmp_path, tmp_path / "state")

    assert isinstance(sandbox, KubernetesSandbox)
    assert sandbox.pod == "task-123"
