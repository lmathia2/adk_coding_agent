"""Side-effect-free ADK agent builders shared by factory and legacy bootstrap."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from google.adk import Agent
from google.adk.models import BaseLlm
from google.adk.tools import ToolContext

from harness.approvals.waiting import ApprovalWaiter
from harness.config import NotebookPtcConfig, ToolSurfaceConfig
from harness.notebook import externalize_mime_bundle, materialize_notebook, reduce_notebook
from harness.repl import PersistentPythonWorker
from harness.state import EventKind, EventStore, JsonlEventStore
from harness.tools.adk_adapter import AdkCodingTools, create_adk_tools
from harness.tools.output import bound_output

from .config import HarnessSettings
from .streaming import PublicReplies

LOGGER = logging.getLogger(__name__)

ToolFunction = Callable[..., Awaitable[dict[str, Any]]]


def _replay_policy(code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "safe"  # Failed cells are never restored.
    uses_agent = any(
        isinstance(node, ast.Name) and node.id == "agent" for node in ast.walk(tree)
    )
    return "never" if uses_agent else "safe"


@dataclass(frozen=True, slots=True)
class CodingWorkerBundle:
    agent: Agent
    read: ToolFunction
    bash: ToolFunction
    edit: ToolFunction
    write: ToolFunction
    python: ToolFunction | None = None
    close: Callable[[], None] | None = None


def build_coding_worker(
    settings: HarnessSettings,
    model: BaseLlm,
    *,
    tools: AdkCodingTools | None = None,
    tool_config: ToolSurfaceConfig | None = None,
    ptc_config: NotebookPtcConfig | None = None,
    event_store: EventStore | None = None,
    approvals: ApprovalWaiter | None = None,
    replies: PublicReplies | None = None,
    capabilities: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
) -> CodingWorkerBundle:
    active_tools = tools or create_adk_tools(
        settings.workspace,
        state_root=settings.state_root,
    )
    active_tool_config = tool_config or ToolSurfaceConfig()
    active_ptc_config = ptc_config or NotebookPtcConfig()
    active_event_store = event_store or JsonlEventStore(settings.state_root / "events")
    read_default_lines = active_tool_config.read_default_lines
    bash_default_timeout = active_tool_config.bash_default_timeout_seconds
    capability_handlers = capabilities or {}

    def _invoke_tool(operation: str, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Keep expected and unexpected tool failures inside the tool protocol."""

        try:
            return call()
        except Exception as error:
            LOGGER.info(
                "model tool %s returned a recoverable %s",
                operation,
                type(error).__name__,
            )
            raw_message = " ".join(str(error).split())
            message = raw_message[:1_000]
            return {
                "status": "error",
                "model_text": f"{operation} failed: {type(error).__name__}: {message}",
                "truncated": len(raw_message) > 1_000,
                "omitted_bytes": max(
                    0,
                    len(raw_message.encode()) - len(message.encode()),
                ),
                "ui_details": {
                    "error_type": type(error).__name__,
                    "recoverable": True,
                },
            }

    def _runtime_identity(
        tool_context: ToolContext | None,
    ) -> tuple[str | None, str | None]:
        if tool_context is None:
            return settings.task_id_override, None
        task_id = tool_context.state.get("task_id") or settings.task_id_override
        invocation_id = getattr(tool_context, "invocation_id", None)
        return (
            str(task_id) if task_id else None,
            str(invocation_id) if invocation_id else None,
        )

    def _require_verification(tool_context: ToolContext | None) -> None:
        if tool_context is not None:
            task_scope, _ = _runtime_identity(tool_context)
            tool_context.state["verification_required_task"] = task_scope

    async def read(
        path: str,
        offset: int = 1,
        limit: int = read_default_lines,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Read a bounded range from a workspace file or recoverable artifact URI."""

        if replies is not None:
            replies.guard_tool(tool_context)
        del tool_context
        return await asyncio.to_thread(
            _invoke_tool,
            "read",
            lambda: active_tools.read(path=path, offset=offset, limit=limit),
        )

    async def bash(
        command: str,
        timeout_seconds: int = bash_default_timeout,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run a bounded command or an in-process indexed search operation."""

        task_scope, _ = _runtime_identity(tool_context)
        if replies is not None:
            replies.guard_tool(tool_context)
        # Even apparently read-only shell can contain redirections/substitutions.
        # The direct-answer path conservatively permits only the read primitive.
        _require_verification(tool_context)
        def invoke() -> dict[str, Any]:
            return _invoke_tool("bash", lambda: active_tools.bash(
                command=command,
                timeout_seconds=timeout_seconds,
                task_scope=task_scope,
            ))
        result = await asyncio.to_thread(invoke)
        if approvals is not None and result.get("approval_required") is True:
            decision = await approvals.wait(str(result["approval_request_id"]), task_scope or "")
            if decision.status == "approved":
                return await asyncio.to_thread(invoke)
            return {**result, "approval_required": False,
                    "model_text": f"Command not executed: approval {decision.status}."}
        return result

    async def edit(
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Atomically replace one exact, unique preimage in a workspace file."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        if replies is not None:
            replies.guard_tool(tool_context)
        _require_verification(tool_context)
        return await asyncio.to_thread(
            _invoke_tool,
            "edit",
            lambda: active_tools.edit(
                path=path,
                old_text=old_text,
                new_text=new_text,
                expected_sha256=expected_sha256,
                task_scope=task_scope,
                invocation_id=invocation_id,
            ),
        )

    async def write(
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Atomically write a complete file with optimistic concurrency."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        if replies is not None:
            replies.guard_tool(tool_context)
        _require_verification(tool_context)
        return await asyncio.to_thread(
            _invoke_tool,
            "write",
            lambda: active_tools.write(
                path=path,
                content=content,
                expected_sha256=expected_sha256,
                expected_absent=expected_absent,
                task_scope=task_scope,
                invocation_id=invocation_id,
            ),
        )

    python_worker = (
        PersistentPythonWorker(max_output_bytes=active_ptc_config.max_output_bytes)
        if active_ptc_config.enabled
        else None
    )
    restored_kernel_epoch: str | None = None

    class _RestoreBroker:
        @staticmethod
        def _blocked(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise PermissionError("capabilities are disabled while restoring replay-safe cells")

        read = write = edit = bash = call = _blocked

    class _CellBroker:
        def __init__(
            self,
            *,
            task_id: str,
            invocation_id: str | None,
            notebook_id: str,
            cell_id: str,
            attempt_id: str,
            event_loop: asyncio.AbstractEventLoop,
        ) -> None:
            self.task_id = task_id
            self.invocation_id = invocation_id
            self.notebook_id = notebook_id
            self.cell_id = cell_id
            self.attempt_id = attempt_id
            self.event_loop = event_loop
            self.call_index = 0
            self.effects: list[str] = []
            self.artifact_refs: set[str] = set()

        def _call(
            self,
            operation: str,
            arguments: dict[str, Any],
            invoke: Callable[[], dict[str, Any]],
        ) -> dict[str, Any]:
            self.call_index += 1
            operation_id = f"{self.attempt_id}:{self.call_index}"
            arguments_hash = hashlib.sha256(
                json.dumps(arguments, sort_keys=True, default=str).encode()
            ).hexdigest()
            common = {
                "notebook_id": self.notebook_id,
                "cell_id": self.cell_id,
                "attempt_id": self.attempt_id,
                "operation_id": operation_id,
                "operation": operation,
                "arguments_sha256": arguments_hash,
            }
            active_event_store.append(
                self.task_id,
                EventKind.CAPABILITY_REQUESTED,
                common,
                idempotency_key=f"capability:{operation_id}:requested",
            )
            try:
                result = invoke()
            except Exception as error:
                active_event_store.append(
                    self.task_id,
                    EventKind.CAPABILITY_FAILED,
                    {**common, "status": "failed", "effect": "unknown", "error": type(error).__name__},
                    idempotency_key=f"capability:{operation_id}:failed",
                )
                self.effects.append("unknown")
                raise
            status = str(result.get("status", "error"))
            if status == "blocked":
                kind, effect = EventKind.CAPABILITY_BLOCKED, "none"
            elif status == "ok":
                kind = EventKind.CAPABILITY_COMPLETED
                effect = "changed" if result.get("changed_paths") else "observed"
            else:
                kind, effect = EventKind.CAPABILITY_FAILED, "unknown"
            refs = {
                str(value)
                for key in ("artifact_uri", "artifact_uris")
                for value in (
                    result.get(key, [])
                    if isinstance(result.get(key), list)
                    else [result.get(key)]
                )
                if isinstance(value, str) and value.startswith(("artifact://", "file://"))
            }
            self.artifact_refs.update(refs)
            self.effects.append(effect)
            active_event_store.append(
                self.task_id,
                kind,
                {**common, "status": status, "effect": effect, "artifact_refs": sorted(refs)},
                idempotency_key=f"capability:{operation_id}:terminal",
            )
            return result

        def read(self, path: str, offset: int = 1, limit: int = read_default_lines) -> dict[str, Any]:
            return self._call(
                "fs.read",
                {"path": path, "offset": offset, "limit": limit},
                lambda: active_tools.read(path=path, offset=offset, limit=limit),
            )

        def bash(self, command: str, timeout_seconds: int = bash_default_timeout) -> dict[str, Any]:
            def invoke() -> dict[str, Any]:
                result = active_tools.bash(
                    command=command,
                    timeout_seconds=timeout_seconds,
                    task_scope=self.task_id,
                )
                if approvals is not None and result.get("approval_required") is True:
                    decision = asyncio.run_coroutine_threadsafe(
                        approvals.wait(str(result["approval_request_id"]), self.task_id),
                        self.event_loop,
                    ).result()
                    if decision.status == "approved":
                        return active_tools.bash(
                            command=command,
                            timeout_seconds=timeout_seconds,
                            task_scope=self.task_id,
                        )
                    return {
                        **result,
                        "approval_required": False,
                        "model_text": f"Command not executed: approval {decision.status}.",
                    }
                return result

            return self._call(
                "shell.run",
                {"command": command, "timeout_seconds": timeout_seconds},
                invoke,
            )

        def edit(
            self,
            path: str,
            old_text: str,
            new_text: str,
            expected_sha256: str | None = None,
        ) -> dict[str, Any]:
            return self._call(
                "fs.edit",
                {
                    "path": path,
                    "old_text_sha256": hashlib.sha256(old_text.encode()).hexdigest(),
                    "new_text_sha256": hashlib.sha256(new_text.encode()).hexdigest(),
                    "expected_sha256": expected_sha256,
                },
                lambda: active_tools.edit(
                    path=path,
                    old_text=old_text,
                    new_text=new_text,
                    expected_sha256=expected_sha256,
                    task_scope=self.task_id,
                    invocation_id=self.invocation_id,
                ),
            )

        def write(
            self,
            path: str,
            content: str,
            expected_sha256: str | None = None,
            expected_absent: bool = False,
        ) -> dict[str, Any]:
            return self._call(
                "fs.write",
                {
                    "path": path,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "expected_sha256": expected_sha256,
                    "expected_absent": expected_absent,
                },
                lambda: active_tools.write(
                    path=path,
                    content=content,
                    expected_sha256=expected_sha256,
                    expected_absent=expected_absent,
                    task_scope=self.task_id,
                    invocation_id=self.invocation_id,
                ),
            )

        def call(self, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
            handler = capability_handlers.get(capability)
            if handler is None:
                return self._call(
                    "mcp.call",
                    {"capability": capability},
                    lambda: {
                        "status": "blocked",
                        "model_text": f"Unknown or unavailable capability: {capability}",
                    },
                )
            return self._call(
                "mcp.call",
                {
                    "capability": capability,
                    "arguments_sha256": hashlib.sha256(
                        json.dumps(arguments, sort_keys=True, default=str).encode()
                    ).hexdigest(),
                },
                lambda: handler(arguments),
            )

        @property
        def effect(self) -> str:
            if "unknown" in self.effects:
                return "unknown"
            if "changed" in self.effects:
                return "changed"
            return "observed" if self.effects else "none"

    async def python(
        code: str,
        timeout_seconds: int = active_ptc_config.default_timeout_seconds,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Append and execute one durable programmatic-tool-calling notebook cell."""

        nonlocal restored_kernel_epoch

        if python_worker is None:
            return {"status": "blocked", "model_text": "notebook-native PTC is disabled"}
        worker = python_worker
        if not 1 <= timeout_seconds <= active_ptc_config.max_timeout_seconds:
            return {
                "status": "error",
                "model_text": (
                    "timeout_seconds must be between 1 and "
                    f"{active_ptc_config.max_timeout_seconds}"
                ),
            }
        if len(code.encode()) > 128_000:
            return {"status": "error", "model_text": "Python cell exceeds 128000 UTF-8 bytes"}
        if replies is not None:
            replies.guard_tool(tool_context)
        _require_verification(tool_context)
        task_scope, invocation_id = _runtime_identity(tool_context)
        task_id = task_scope or "unscoped"
        notebook_id = hashlib.sha256(task_id.encode()).hexdigest()[:32]
        cell_id = uuid4().hex
        attempt_id = uuid4().hex
        kernel_epoch = await asyncio.to_thread(lambda: worker.kernel_epoch)
        if restored_kernel_epoch != kernel_epoch:
            previous = reduce_notebook(active_event_store.read(task_id), notebook_id)
            restored_cells: list[str] = []
            for prior_cell in previous.cells:
                if prior_cell.status != "completed" or prior_cell.replay_policy != "safe":
                    continue
                restored = await asyncio.to_thread(
                    worker.execute,
                    prior_cell.source,
                    _RestoreBroker(),
                    min(timeout_seconds, active_ptc_config.default_timeout_seconds),
                )
                if restored.status != "ok":
                    return {
                        "status": "blocked",
                        "model_text": "Could not restore a replay-safe notebook cell",
                        "cell_id": prior_cell.cell_id,
                        "error_type": restored.error_type,
                    }
                restored_cells.append(prior_cell.cell_id)
            if restored_cells:
                active_event_store.append(
                    task_id,
                    EventKind.REPL_STATE_RESTORED,
                    {
                        "projection_notebook_id": notebook_id,
                        "kernel_epoch": kernel_epoch,
                        "restored_cell_ids": restored_cells,
                    },
                    idempotency_key=f"repl-restore:{kernel_epoch}",
                )
            restored_kernel_epoch = kernel_epoch
        replay_policy = _replay_policy(code)
        cell_payload = {
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "source": code,
            "attempt_id": attempt_id,
            "kernel_epoch": kernel_epoch,
            "replay_policy": replay_policy,
        }
        active_event_store.append(
            task_id,
            EventKind.NOTEBOOK_CELL_ADDED,
            cell_payload,
            idempotency_key=f"notebook-cell:{attempt_id}",
        )
        active_event_store.append(
            task_id,
            EventKind.REPL_CELL_SUBMITTED,
            cell_payload,
            idempotency_key=f"repl-cell:{attempt_id}:submitted",
        )
        broker = _CellBroker(
            task_id=task_id,
            invocation_id=invocation_id,
            notebook_id=notebook_id,
            cell_id=cell_id,
            attempt_id=attempt_id,
            event_loop=asyncio.get_running_loop(),
        )
        result = await asyncio.to_thread(
            worker.execute,
            code,
            broker,
            timeout_seconds,
        )
        if result.status == "ok":
            terminal_kind = EventKind.REPL_CELL_COMPLETED
        elif result.status == "timeout":
            terminal_kind = EventKind.REPL_CELL_TIMEOUT
        else:
            terminal_kind = EventKind.REPL_CELL_FAILED
        effect = "unknown" if result.effect_unknown else broker.effect
        display_data = result.display_data
        if display_data is not None:
            display_data, display_refs = externalize_mime_bundle(
                display_data,
                artifact_root=settings.state_root / "artifacts" / "sha256",
                max_inline_bytes=active_ptc_config.max_output_bytes,
            )
            broker.artifact_refs.update(display_refs)
        terminal_payload: dict[str, Any] = {
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "attempt_id": attempt_id,
            "kernel_epoch": kernel_epoch,
            "effect": effect,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "artifact_refs": sorted(broker.artifact_refs),
        }
        if display_data is not None:
            terminal_payload["display"] = display_data
        elif result.value_repr is not None:
            terminal_payload["display"] = {"text/plain": result.value_repr}
        if result.error_type is not None:
            terminal_payload["exception"] = {
                "ename": result.error_type,
                "evalue": result.error_message or "",
                "traceback": list(result.traceback),
            }
        active_event_store.append(
            task_id,
            terminal_kind,
            terminal_payload,
            idempotency_key=f"repl-cell:{attempt_id}:terminal",
        )
        notebook_state = reduce_notebook(active_event_store.read(task_id), notebook_id)
        notebook_path = settings.state_root / "notebooks" / f"{notebook_id}.ipynb"
        notebook_bytes = await asyncio.to_thread(
            materialize_notebook,
            notebook_state,
            notebook_path,
        )
        notebook_hash = hashlib.sha256(notebook_bytes).hexdigest()
        active_event_store.append(
            task_id,
            EventKind.NOTEBOOK_MATERIALIZED,
            {
                "projection_notebook_id": notebook_id,
                "path": str(notebook_path),
                "source_watermark": notebook_state.source_watermark,
                "content_sha256": notebook_hash,
            },
            idempotency_key=f"notebook-materialized:{attempt_id}",
        )
        visible = "\n".join(
            part
            for part in (
                result.stdout,
                result.stderr,
                result.value_repr,
                (
                    f"{result.error_type}: {result.error_message}"
                    if result.error_type is not None
                    else None
                ),
            )
            if part
        )
        bounded = bound_output(
            visible,
            max_chars=active_ptc_config.max_output_bytes,
            max_lines=400,
        )
        return {
            "status": result.status,
            "model_text": bounded.text,
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "attempt_id": attempt_id,
            "kernel_epoch": kernel_epoch,
            "effect": effect,
            "notebook_path": str(notebook_path),
            "notebook_sha256": notebook_hash,
            "artifact_uris": sorted(broker.artifact_refs),
            "duration_ms": result.duration_ms,
            "truncated": result.output_truncated or bounded.truncated,
            "omitted_bytes": bounded.omitted_bytes,
        }

    model_tools: list[Any] = (
        [python] if active_ptc_config.enabled else [read, bash, edit, write]
    )
    agent = Agent(
        name="coding_worker",
        model=model,
        description=(
            "Executes notebook-native PTC in one persistent CPython tool."
            if active_ptc_config.enabled
            else "Executes one bounded coding work batch with four composable tools."
        ),
        static_instruction=settings.static_instruction,
        instruction="",
        tools=model_tools,
        before_model_callback=replies.before_model if replies is not None else None,
        after_model_callback=replies.after_model if replies is not None else None,
    )
    return CodingWorkerBundle(
        agent=agent,
        read=read,
        bash=bash,
        edit=edit,
        write=write,
        python=python if active_ptc_config.enabled else None,
        close=python_worker.close if python_worker is not None else None,
    )


__all__ = ["CodingWorkerBundle", "build_coding_worker"]
