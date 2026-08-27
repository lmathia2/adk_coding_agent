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
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlsplit

from harness.approvals import ApprovalStore
from harness.safety import ApprovalAction, ApprovalPolicy, SecretRedactor
from harness.sandbox import CommandSandbox, SandboxRequest, create_command_sandbox
from harness.state import ToolReceiptStore
from harness.tools.output import bound_output

_CONTENT_ARTIFACT_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.[A-Za-z0-9]{1,12}$")
_COMMAND_ARTIFACT_NAME = re.compile(r"^command-(?P<digest>[0-9a-f]{64})\.log$")
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_MAX_ARTIFACT_OUTPUT_CHARS = 31_000


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


class _ArtifactResolver:
    """Resolve only harness-owned content-addressed artifacts for managed read."""

    def __init__(self, *, workspace: Path, state_root: Path) -> None:
        self.workspace_artifact_root = (
            workspace / ".artifacts" / "tool-output"
        ).resolve()
        self.command_artifact_root = (
            state_root / "artifacts" / "commands"
        ).resolve()

    @staticmethod
    def _confined_file(root: Path, candidate: Path) -> Path:
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ValueError("artifact URI is outside managed artifact roots") from exc
        if not resolved.is_file():
            raise ValueError("artifact URI does not identify a regular file")
        return resolved

    def _target(self, uri: str) -> tuple[Path, str]:
        try:
            parsed = urlsplit(uri)
        except ValueError as exc:
            raise ValueError("invalid artifact URI") from exc
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("artifact URI cannot contain credentials, query, or fragment")

        if parsed.scheme == "artifact":
            if parsed.netloc != "tool-output":
                raise ValueError("unsupported artifact collection")
            match = _CONTENT_ARTIFACT_NAME.fullmatch(parsed.path.removeprefix("/"))
            if match is None or parsed.path.count("/") != 1:
                raise ValueError("artifact URI must use a content-addressed filename")
            target = self._confined_file(
                self.workspace_artifact_root,
                self.workspace_artifact_root / parsed.path.removeprefix("/"),
            )
            return target, match.group("digest")

        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                raise ValueError("file artifact URI must be local")
            decoded = unquote(parsed.path)
            if not decoded.startswith("/") or any(ord(char) < 32 for char in decoded):
                raise ValueError("file artifact URI must contain a safe absolute path")
            candidate = Path(decoded)
            match = _COMMAND_ARTIFACT_NAME.fullmatch(candidate.name)
            if match is None:
                raise ValueError("file artifact URI must use a command content hash")
            target = self._confined_file(self.command_artifact_root, candidate)
            return target, match.group("digest")

        raise ValueError("unsupported artifact URI scheme")

    def read(self, uri: str, *, offset: int, limit: int) -> dict[str, Any]:
        if offset < 1:
            raise ValueError("offset must be at least 1")
        if limit < 1 or limit > 400:
            raise ValueError("limit must be between 1 and 400 lines")
        target, expected_digest = self._target(uri)
        with target.open("rb") as stream:
            content = stream.read(_MAX_ARTIFACT_BYTES + 1)
        if len(content) > _MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds the managed read byte limit")
        size = len(content)
        actual_digest = hashlib.sha256(content).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError("artifact content hash does not match its URI")
        if b"\x00" in content[:8_192]:
            raise ValueError("binary artifacts cannot be read as text")

        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = min(offset - 1, len(lines))
        selected = lines[start : start + limit]
        rendered = "\n".join(
            f"{start + index + 1:>6} | {line}"
            for index, line in enumerate(selected)
        )
        bounded = bound_output(
            rendered,
            max_chars=_MAX_ARTIFACT_OUTPUT_CHARS,
            max_lines=400,
        )
        has_more = start + len(selected) < len(lines)
        header = (
            f"{uri}\nsha256: {actual_digest}\n"
            f"lines: {start + 1}-{start + len(selected)} of {len(lines)}"
        )
        if has_more:
            header += f"\n[more available: read offset={start + len(selected) + 1}]"
        selected_bytes = len("\n".join(selected).encode("utf-8", errors="replace"))
        omitted_bytes = max(0, size - selected_bytes) + min(
            selected_bytes,
            bounded.omitted_bytes,
        )
        return {
            "status": "ok",
            "model_text": f"{header}\n\n{bounded.text}",
            "truncated": has_more or bounded.truncated,
            "omitted_bytes": omitted_bytes,
            "artifact_uri": uri,
            "content_hashes": {uri: actual_digest},
            "ui_details": {"artifact": True, "total_lines": len(lines)},
        }


class _ManagedTools:
    def __init__(
        self,
        workspace: Path,
        *,
        sandbox: CommandSandbox | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        legacy = _legacy_module()
        self.base = legacy.create_adk_tools(self.workspace)
        state_root = _state_root(self.workspace)
        known_secrets = _known_secrets()
        self.sandbox = sandbox or create_command_sandbox(
            self.workspace,
            state_root,
            known_secrets=known_secrets,
        )
        self.receipts = ToolReceiptStore(state_root / "managed-tools.db")
        self.approvals = ApprovalStore(state_root / "approvals.db")
        self.redactor = SecretRedactor(known_secrets=known_secrets)
        self.artifacts = _ArtifactResolver(
            workspace=self.workspace,
            state_root=state_root,
        )
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
        self.task_scope = os.getenv("ADK_CODING_TASK_ID") or hashlib.sha256(
            self.workspace.as_posix().encode()
        ).hexdigest()[:24]

    def _redact(self, value: Any) -> dict[str, Any]:
        normalized = _normalize_result(value)
        return self.redactor.redact(normalized)

    def read(self, path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
        if path.startswith(("artifact://", "file://")):
            return self._redact(self.artifacts.read(path, offset=offset, limit=limit))
        if "://" in path:
            raise ValueError("unsupported read URI scheme")
        return self._redact(self.base.read(path=path, offset=offset, limit=limit))

    def bash(self, command: str, timeout_seconds: int = 120) -> dict[str, Any]:
        fingerprint = _canonical_hash("bash", {"command": command})
        persisted = self.approvals.for_fingerprint(self.task_scope, fingerprint)
        if persisted and persisted.status == "approved":
            self.policy.approved_fingerprints.add(fingerprint)
        elif persisted and persisted.status == "denied":
            return {
                "status": "blocked",
                "model_text": self.redactor.redact_text(
                    "Command not executed: approval denied by "
                    f"{persisted.decided_by or 'reviewer'}."
                ),
                "approval_required": False,
                "approval_request_id": persisted.request_id,
                "risk": persisted.risk,
            }
        decision = self.policy.decide(command, fingerprint=fingerprint)
        if decision.action != ApprovalAction.ALLOW:
            request_id: str | None = None
            if decision.action == ApprovalAction.REQUIRE_APPROVAL:
                request = self.approvals.request(
                    task_id=self.task_scope,
                    fingerprint=fingerprint,
                    operation=self.redactor.redact_text(command),
                    risk=decision.risk.value,
                    reason=decision.reason,
                )
                request_id = request.request_id
            return {
                "status": "blocked",
                "model_text": (
                    f"Command not executed ({decision.risk.value}): {decision.reason}. "
                    f"Approval fingerprint: {fingerprint}"
                ),
                "approval_required": decision.action == ApprovalAction.REQUIRE_APPROVAL,
                "approval_request_id": request_id,
                "risk": decision.risk.value,
            }
        return self._redact(
            self.sandbox.execute(
                SandboxRequest(
                    command=command,
                    timeout_seconds=timeout_seconds,
                )
            ).to_tool_result()
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


def create_adk_tools(
    workspace: Path,
    *,
    sandbox: CommandSandbox | None = None,
) -> AdkCodingTools:
    managed = _ManagedTools(workspace, sandbox=sandbox)
    return AdkCodingTools(
        read=managed.read,
        bash=managed.bash,
        edit=managed.edit,
        write=managed.write,
    )


__all__ = ["AdkCodingTools", "create_adk_tools"]
