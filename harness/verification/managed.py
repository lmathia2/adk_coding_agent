"""Managed validation executor using the same boundary as model shell commands."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from harness.approvals import ApprovalStore
from harness.safety import ApprovalAction, ApprovalPolicy, CommandRisk, SecretRedactor
from harness.sandbox import CommandSandbox, SandboxRequest, create_command_sandbox
from harness.telemetry import MetricsStore, ToolUsageSample

from .contracts import CommandResult, ValidationCommand


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _state_root(root: Path) -> Path:
    configured = os.getenv("ADK_CODING_STATE_DIR")
    if configured:
        state = Path(configured).expanduser().resolve()
    else:
        digest = hashlib.sha256(root.resolve().as_posix().encode()).hexdigest()[:16]
        state = Path.home() / ".cache" / "adk-coding-agent" / digest
    state.mkdir(parents=True, exist_ok=True)
    return state


def _task_id(root: Path) -> str:
    configured = os.getenv("ADK_CODING_TASK_ID")
    if configured:
        return configured
    return hashlib.sha256(root.resolve().as_posix().encode()).hexdigest()[:24]


def _known_secrets() -> list[str]:
    names = {
        name.strip()
        for name in os.getenv("ADK_CODING_REDACT_ENV_VARS", "").split(",")
        if name.strip()
    }
    for name in os.environ:
        upper = name.upper()
        if any(
            marker in upper
            for marker in (
                "API_KEY",
                "ACCESS_TOKEN",
                "AUTH_TOKEN",
                "CLIENT_SECRET",
                "PASSWORD",
            )
        ):
            names.add(name)
    return [
        value
        for name in sorted(names)
        if (value := os.getenv(name)) and len(value) >= 4
    ]


def _fingerprint(validation: ValidationCommand) -> str:
    canonical = json.dumps(
        {
            "category": validation.category,
            "command": validation.command,
            "source": validation.source,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sandbox_environment() -> dict[str, str]:
    environment = {"UV_OFFLINE": "1"}
    configured_cache = os.getenv("ADK_CODING_UV_CACHE_DIR")
    backend = os.getenv("ADK_CODING_SANDBOX", "local").strip().lower()
    if configured_cache:
        environment["UV_CACHE_DIR"] = configured_cache
    elif backend == "local":
        environment["UV_CACHE_DIR"] = str(
            Path(tempfile.gettempdir()) / "adk-coding-agent-uv-cache"
        )
    return environment


class ManagedValidationExecutor:
    """Run completion checks under approval, sandbox, redaction, and telemetry."""

    def __init__(
        self,
        root: Path,
        *,
        sandbox: CommandSandbox | None = None,
    ) -> None:
        self.root = root.resolve()
        state = _state_root(self.root)
        self.task_id = _task_id(self.root)
        self.redactor = SecretRedactor(known_secrets=_known_secrets())
        self.approvals = ApprovalStore(state / "approvals.db")
        self.metrics = MetricsStore(state / "metrics.db")
        self.sandbox = sandbox or create_command_sandbox(self.root, state)
        approved = {
            item.strip()
            for item in os.getenv(
                "ADK_CODING_APPROVED_COMMAND_FINGERPRINTS", ""
            ).split(",")
            if item.strip()
        }
        self.policy = ApprovalPolicy(
            allow_dependency_install=_truthy(
                "ADK_CODING_ALLOW_DEPENDENCY_INSTALL"
            ),
            allow_network=_truthy("ADK_CODING_ALLOW_NETWORK"),
            allow_git_history_mutation=_truthy("ADK_CODING_ALLOW_GIT_MUTATION"),
            allow_unknown=_truthy("ADK_CODING_ALLOW_UNKNOWN_COMMANDS"),
            approved_fingerprints=approved,
        )

    def _record(self, validation: ValidationCommand, result: CommandResult) -> None:
        payload = result.model_dump(mode="json")
        self.metrics.record_tool_usage(
            ToolUsageSample(
                task_id=self.task_id,
                invocation_id=os.getenv("ADK_CODING_INVOCATION_ID", "verification"),
                tool_name=f"verify:{validation.category}",
                status=result.status,
                arguments_hash=_fingerprint(validation),
                result_hash=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
                duration_ms=result.duration_ms,
                model_visible_bytes=len(
                    (result.stdout + result.stderr).encode("utf-8", errors="replace")
                ),
                omitted_bytes=result.omitted_bytes,
            )
        )

    def _blocked(
        self,
        validation: ValidationCommand,
        *,
        risk: CommandRisk,
        reason: str,
        request_id: str | None,
    ) -> CommandResult:
        details = f"Validation command not executed ({risk.value}): {reason}."
        if request_id:
            details += (
                f" Approval request: {request_id}. Review with "
                "`python -m harness.approvals list --status pending`."
            )
        result = CommandResult(
            category=validation.category,
            command=validation.command,
            source=validation.source,
            status="blocked",
            exit_code=126,
            stderr=details,
            approval_request_id=request_id,
        )
        self._record(validation, result)
        return result

    def __call__(self, validation: ValidationCommand) -> CommandResult:
        fingerprint = _fingerprint(validation)
        persisted = self.approvals.for_fingerprint(self.task_id, fingerprint)
        if persisted and persisted.status == "approved":
            self.policy.approved_fingerprints.add(fingerprint)
        elif persisted and persisted.status == "denied":
            return self._blocked(
                validation,
                risk=CommandRisk(persisted.risk),
                reason=(
                    f"approval denied by {persisted.decided_by or 'reviewer'}"
                    + (
                        f": {persisted.decision_note}"
                        if persisted.decision_note
                        else ""
                    )
                ),
                request_id=persisted.request_id,
            )

        decision = self.policy.decide(
            validation.command,
            fingerprint=fingerprint,
        )
        if decision.action != ApprovalAction.ALLOW:
            request_id: str | None = None
            if decision.action == ApprovalAction.REQUIRE_APPROVAL:
                request = self.approvals.request(
                    task_id=self.task_id,
                    fingerprint=fingerprint,
                    operation=self.redactor.redact_text(validation.command),
                    risk=decision.risk.value,
                    reason=(
                        f"{decision.reason}; required by {validation.source}"
                    ),
                )
                request_id = request.request_id
            return self._blocked(
                validation,
                risk=decision.risk,
                reason=decision.reason,
                request_id=request_id,
            )

        sandbox_result = self.sandbox.execute(
            SandboxRequest(
                command=validation.command,
                timeout_seconds=validation.timeout_seconds,
                environment=_sandbox_environment(),
            )
        )
        result = CommandResult(
            category=validation.category,
            command=validation.command,
            source=validation.source,
            status=sandbox_result.status,
            exit_code=sandbox_result.exit_code,
            stdout=self.redactor.redact_text(sandbox_result.stdout),
            stderr=self.redactor.redact_text(sandbox_result.stderr),
            duration_ms=sandbox_result.duration_ms,
            truncated=sandbox_result.truncated,
            omitted_bytes=sandbox_result.omitted_bytes,
            artifact_uri=sandbox_result.artifact_uri,
        )
        self._record(validation, result)
        return result


def managed_executor_from_env(root: Path) -> ManagedValidationExecutor:
    return ManagedValidationExecutor(root)


__all__ = ["ManagedValidationExecutor", "managed_executor_from_env"]
