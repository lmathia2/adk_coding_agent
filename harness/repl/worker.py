"""A small persistent CPython worker.

The child owns only the Python namespace. All intended workspace effects cross the
pipe and are performed by the parent-owned broker. The source guard is deliberately
defense-in-depth, not a security sandbox; production isolation still belongs outside
this process.
"""

from __future__ import annotations

import ast
import builtins
import io
import multiprocessing
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from types import SimpleNamespace
from typing import Any, Literal, Protocol
from uuid import uuid4


class ReplBroker(Protocol):
    """Parent-side operations exposed to programs through ``agent``."""

    def read(self, path: str, offset: int = 1, limit: int = 400) -> Any: ...

    def write(
        self,
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> Any: ...

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
    ) -> Any: ...

    def bash(self, command: str, timeout_seconds: int = 120) -> Any: ...


@dataclass(frozen=True, slots=True)
class PythonExecutionResult:
    status: Literal["ok", "error", "timeout"]
    stdout: str = ""
    stderr: str = ""
    value_repr: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    traceback: tuple[str, ...] = ()
    duration_ms: int = 0
    effect_unknown: bool = False
    output_truncated: bool = False


class _BoundedText(io.TextIOBase):
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        text = str(value)
        remaining = self._limit - self._size
        if remaining > 0:
            kept = text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
            self._parts.append(kept)
            self._size += len(kept.encode("utf-8"))
        if len(text.encode("utf-8")) > remaining:
            self.truncated = True
        return len(text)

    def getvalue(self) -> str:
        return "".join(self._parts)


_BLOCKED_MODULES = frozenset(
    {
        "asyncio",
        "builtins",
        "ctypes",
        "ftplib",
        "http",
        "importlib",
        "io",
        "multiprocessing",
        "os",
        "pathlib",
        "requests",
        "shutil",
        "signal",
        "socket",
        "subprocess",
        "sys",
        "telnetlib",
        "urllib",
    }
)
_BLOCKED_CALLS = frozenset({"breakpoint", "compile", "eval", "exec", "input", "open", "__import__"})


def _validate_source(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name.split(".", 1)[0] in _BLOCKED_MODULES:
                    raise PermissionError(f"direct import of {name!r} is blocked; use agent capabilities")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _BLOCKED_CALLS
        ):
            raise PermissionError(
                f"direct call to {node.func.id!r} is blocked; use agent capabilities"
            )


def _safe_builtins() -> dict[str, Any]:
    allowed = dict(vars(builtins))
    for name in _BLOCKED_CALLS:
        allowed.pop(name, None)

    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".", 1)[0] in _BLOCKED_MODULES:
            raise PermissionError(f"direct import of {name!r} is blocked; use agent capabilities")
        return original_import(name, *args, **kwargs)

    allowed["__import__"] = guarded_import
    return allowed


class _RemoteOperation:
    def __init__(self, connection: Connection, operation: str) -> None:
        self._connection = connection
        self._operation = operation

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        request_id = uuid4().hex
        self._connection.send(
            {
                "type": "broker_call",
                "id": request_id,
                "operation": self._operation,
                "args": args,
                "kwargs": kwargs,
            }
        )
        response = self._connection.recv()
        if response.get("type") != "broker_result" or response.get("id") != request_id:
            raise RuntimeError("invalid broker response")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error", "broker call failed")))
        return response.get("result")


def _agent_proxy(connection: Connection) -> SimpleNamespace:
    return SimpleNamespace(
        fs=SimpleNamespace(
            read=_RemoteOperation(connection, "fs.read"),
            write=_RemoteOperation(connection, "fs.write"),
            edit=_RemoteOperation(connection, "fs.edit"),
        ),
        shell=SimpleNamespace(run=_RemoteOperation(connection, "shell.run")),
    )


def _execute_cell(
    code: str,
    namespace: dict[str, Any],
    *,
    max_output_bytes: int,
) -> dict[str, Any]:
    stdout = _BoundedText(max_output_bytes)
    stderr = _BoundedText(max_output_bytes)
    try:
        tree = ast.parse(code, filename="<agent-cell>", mode="exec")
        _validate_source(tree)
        final_expression: ast.expr | None = None
        if tree.body:
            last_statement = tree.body[-1]
            if isinstance(last_statement, ast.Expr):
                final_expression = last_statement.value
                tree.body.pop()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            if tree.body:
                exec(compile(tree, "<agent-cell>", "exec"), namespace)
            value = (
                eval(compile(ast.Expression(final_expression), "<agent-cell>", "eval"), namespace)
                if final_expression is not None
                else None
            )
        value_repr = None if value is None else repr(value)
        if value_repr is not None and len(value_repr.encode()) > max_output_bytes:
            value_repr = value_repr.encode()[:max_output_bytes].decode(errors="ignore")
            stdout.truncated = True
        return {
            "status": "ok",
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "value_repr": value_repr,
            "output_truncated": stdout.truncated or stderr.truncated,
        }
    except BaseException as error:
        return {
            "status": "error",
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": tuple(traceback.format_exception(error)),
            "output_truncated": stdout.truncated or stderr.truncated,
        }


def _worker_main(connection: Connection, max_output_bytes: int) -> None:
    namespace: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "__name__": "__agent_repl__",
        "agent": _agent_proxy(connection),
    }
    while True:
        try:
            request = connection.recv()
        except EOFError:
            return
        if request.get("type") == "close":
            return
        if request.get("type") != "execute":
            continue
        result = _execute_cell(
            str(request.get("code", "")),
            namespace,
            max_output_bytes=max_output_bytes,
        )
        connection.send({"type": "execution_result", "id": request.get("id"), **result})


class PersistentPythonWorker:
    """Own one restartable CPython subprocess and its durable-in-process namespace."""

    def __init__(self, *, max_output_bytes: int = 64_000) -> None:
        if max_output_bytes < 1_024:
            raise ValueError("max_output_bytes must be at least 1024")
        self.max_output_bytes = max_output_bytes
        self._process: BaseProcess | None = None
        self._connection: Connection | None = None
        self._lock = threading.Lock()

    def _start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._discard()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_worker_main,
            args=(child, self.max_output_bytes),
            daemon=True,
            name="agent-cpython-worker",
        )
        process.start()
        child.close()
        self._connection = parent
        self._process = process

    def _discard(self) -> None:
        connection, process = self._connection, self._process
        self._connection = None
        self._process = None
        if connection is not None:
            connection.close()
        if process is not None:
            if process.is_alive():
                process.kill()
            process.join(timeout=1)

    @staticmethod
    def _broker_operation(broker: ReplBroker, operation: str) -> Callable[..., Any]:
        names: Mapping[str, str] = {
            "fs.read": "read",
            "fs.write": "write",
            "fs.edit": "edit",
            "shell.run": "bash",
        }
        name = names.get(operation)
        if name is None:
            raise ValueError(f"unsupported broker operation: {operation}")
        candidate = getattr(broker, name, None)
        if not callable(candidate):
            raise TypeError(f"broker does not implement {name}")
        return candidate

    def execute(
        self,
        code: str,
        broker: ReplBroker,
        timeout_seconds: float = 120,
    ) -> PythonExecutionResult:
        if not isinstance(code, str):
            raise TypeError("code must be a string")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        started = time.monotonic()
        deadline = started + timeout_seconds
        with self._lock:
            self._start()
            assert self._connection is not None
            request_id = uuid4().hex
            try:
                self._connection.send({"type": "execute", "id": request_id, "code": code})
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self._connection.poll(min(remaining, 0.05)):
                        if remaining > 0:
                            continue
                        self._discard()
                        return PythonExecutionResult(
                            status="timeout",
                            error_type="TimeoutError",
                            error_message="Python execution timed out; worker state was discarded",
                            duration_ms=int((time.monotonic() - started) * 1_000),
                            effect_unknown=True,
                        )
                    response = self._connection.recv()
                    if response.get("type") == "broker_call":
                        try:
                            operation = self._broker_operation(
                                broker, str(response.get("operation", ""))
                            )
                            result = operation(
                                *tuple(response.get("args", ())),
                                **dict(response.get("kwargs", {})),
                            )
                            reply = {
                                "type": "broker_result",
                                "id": response.get("id"),
                                "ok": True,
                                "result": result,
                            }
                        except BaseException as error:
                            reply = {
                                "type": "broker_result",
                                "id": response.get("id"),
                                "ok": False,
                                "error": f"{type(error).__name__}: {error}",
                            }
                        self._connection.send(reply)
                        continue
                    if response.get("type") != "execution_result" or response.get("id") != request_id:
                        raise RuntimeError("invalid worker response")
                    return PythonExecutionResult(
                        status=response["status"],
                        stdout=response.get("stdout", ""),
                        stderr=response.get("stderr", ""),
                        value_repr=response.get("value_repr"),
                        error_type=response.get("error_type"),
                        error_message=response.get("error_message"),
                        traceback=tuple(response.get("traceback", ())),
                        duration_ms=int((time.monotonic() - started) * 1_000),
                        output_truncated=bool(response.get("output_truncated", False)),
                    )
            except (EOFError, BrokenPipeError, OSError) as error:
                self._discard()
                return PythonExecutionResult(
                    status="error",
                    error_type=type(error).__name__,
                    error_message="Python worker exited unexpectedly; worker state was discarded",
                    duration_ms=int((time.monotonic() - started) * 1_000),
                    effect_unknown=True,
                )

    def close(self) -> None:
        with self._lock:
            if self._connection is not None and self._process is not None:
                try:
                    self._connection.send({"type": "close"})
                    self._process.join(timeout=1)
                except (BrokenPipeError, OSError):
                    pass
            self._discard()

    def __enter__(self) -> PersistentPythonWorker:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["PersistentPythonWorker", "PythonExecutionResult", "ReplBroker"]
