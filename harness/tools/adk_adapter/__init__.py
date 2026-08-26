"""Managed ADK adapter for the four Pi-style coding tools.

This package intentionally shadows the earlier compatibility module of the same name.
It reuses that tested adapter, then adds deterministic approval, secret redaction, and
replay-safe receipts without expanding the model-visible tool surface.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from harness.safety import ApprovalAction, ApprovalPolicy, SecretRedactor
from harness.state import ToolReceiptStore


@dataclass(frozen=True, slots=True)
class AdkCodingTools:
    read: Callable[..., dict[str, Any]]
    bash: Callable[..., dict[str, Any]]
    edit: Callable[..., dict[str, Any]]
    write: Callable[..., dict[str, Any]]


def _legacy_module() -> ModuleType:
    source = Path(__file__).resolve().parent.parent / "adk_adapter.py"
    spec = importlib.util.spec_from_file_location(
        "harness.tools._legacy_adk_adapter",
        source,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load legacy ADK adapter from {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _state_root(workspace: Path) -> Path:
    configured = os.getenv("ADK_CODING_STATE_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        digest = hashlib.sha256(workspace.as_posix().encode()).hexdigest()[:16]
        root = Path.home() / ".cache" / "adk-coding-agent" / digest
    root.mkdir(parents=True, exist_ok=True)
    return root


def _known_secrets() -> list[str]:
    explicit_names = {
        name.strip()
        for name in os.getenv("ADK_CODING_REDACT_ENV_VARS", "").split(",")
        if name.strip()
    }
    for name in os.environ:
        upper = name.upper()
        if any(
            marker in upper
            for marker in ("API_KEY", "ACCESS_TOKEN", "AUTH_TOKEN", "CLIENT_SECRET", "PASSWORD")
        ):
            explicit_names.add(name)
    return [
        value
        for name in sorted(explicit_names)
        if (value := os.getenv(name)) and len(value) >= 4
    ]


def _canonical_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _normalize_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {"status": "ok", "model_text": str(value)}


class _ManagedTools:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        legacy = _legacy_module()
        self.base = legacy.create_adk_tools(self.workspace)
        self.receipts = ToolReceiptStore(_state_root(self.workspace) / "managed-tools.db")
        self.redactor = SecretRedactor(known_secrets=_known_secrets())
        approved = {
            item.strip()
            for item in os.getenv("ADK_CODING_APPROVED_COMMAND_FINGERPRINTS", "").split(",")
            if item.strip()
        }
        self.policy = ApprovalPolicy(
            allow_dependency_install=_truthy("ADK_CODING_ALLOW_DEPENDENCY_INSTALL"),
            allow_network=_truthy("ADK_CODING_ALLOW_NETWORK"),
            allow_git_history_mutation=_truthy("ADK_CODING_ALLOW_GIT_MUTATION"),
            allow_unknown=_truthy("ADK_CODING_ALLOW_UNKNOWN_COMMANDS"),
            approved_fingerprints=approved,
        )
        self.task_scope = hashlib.sha256(self.workspace.as_posix().encode()).hexdigest()[:24]

    def _redact(self, value: Any) -> dict[str, Any]:
        normalized = _normalize_result(value)
        return self.redactor.redact(normalized)

    def read(self, path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
        return self._redact(self.base.read(path=path, offset=offset, limit=limit))

    def bash(self, command: str, timeout_seconds: int = 120) -> dict[str, Any]:
        fingerprint = _canonical_hash("bash", {"command": command})
        decision = self.policy.decide(command, fingerprint=fingerprint)
        if decision.action != ApprovalAction.ALLOW:
            return {
                "status": "blocked",
                "model_text": (
                    f"Command not executed ({decision.risk.value}): {decision.reason}. "
                    f"Approval fingerprint: {fingerprint}"
                ),
                "approval_required": decision.action == ApprovalAction.REQUIRE_APPROVAL,
                "risk": decision.risk.value,
            }
        return self._redact(
            self.base.bash(command=command, timeout_seconds=timeout_seconds)
        )

    def _mutate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        operation: Callable[[], Any],
    ) -> dict[str, Any]:
        arguments_hash = _canonical_hash(tool_name, arguments)
        tool_call_id = arguments_hash[:32]
        receipt = self.receipts.begin(
            task_id=self.task_scope,
            invocation_id="content-addressed",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_hash=arguments_hash,
            side_effect_key=arguments_hash,
        )
        if receipt.status == "completed":
            return {
                "status": "ok",
                "model_text": f"{tool_name} already completed for this exact content",
                "replayed": True,
                "result_hash": receipt.result_hash,
                "artifact_uri": receipt.artifact_uri,
            }
        try:
            result = self._redact(operation())
        except Exception as exc:
            self.receipts.finish(
                task_id=self.task_scope,
                tool_call_id=tool_call_id,
                status="failed",
                error=self.redactor.redact_text(str(exc)),
            )
            raise
        result_hash = hashlib.sha256(
            json.dumps(result, sort_keys=True, default=str).encode()
        ).hexdigest()
        self.receipts.finish(
            task_id=self.task_scope,
            tool_call_id=tool_call_id,
            status="completed",
            result_hash=result_hash,
            artifact_uri=result.get("artifact_uri"),
        )
        result["receipt_id"] = tool_call_id
        return result

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        arguments = {
            "path": path,
            "old_text_sha256": hashlib.sha256(old_text.encode()).hexdigest(),
            "new_text_sha256": hashlib.sha256(new_text.encode()).hexdigest(),
            "expected_sha256": expected_sha256,
        }
        return self._mutate(
            "edit",
            arguments,
            lambda: self.base.edit(
                path=path,
                old_text=old_text,
                new_text=new_text,
                expected_sha256=expected_sha256,
            ),
        )

    def write(
        self,
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> dict[str, Any]:
        arguments = {
            "path": path,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "expected_sha256": expected_sha256,
            "expected_absent": expected_absent,
        }
        return self._mutate(
            "write",
            arguments,
            lambda: self.base.write(
                path=path,
                content=content,
                expected_sha256=expected_sha256,
                expected_absent=expected_absent,
            ),
        )


def create_adk_tools(workspace: Path) -> AdkCodingTools:
    managed = _ManagedTools(workspace)
    return AdkCodingTools(
        read=managed.read,
        bash=managed.bash,
        edit=managed.edit,
        write=managed.write,
    )


__all__ = ["AdkCodingTools", "create_adk_tools"]
