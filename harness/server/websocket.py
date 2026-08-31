"""Bounded bidirectional WebSocket transport for the public agent protocol."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from harness.agent import ControlReceipt, HarnessDescriptor, PublicModelStatus
from harness.ai.controls import ProviderControlError, ProviderControlRequest

from .models import ModelControlError
from .protocol import (
    PROTOCOL_VERSION,
    AckMessage,
    AgUiEventType,
    AttachTaskMessage,
    CancelTaskMessage,
    ControlResultMessage,
    HelloMessage,
    ModelRequestMessage,
    ModelResultMessage,
    PauseTaskMessage,
    PingMessage,
    PongMessage,
    ProviderRequestMessage,
    ProviderResultMessage,
    ServerEnvelope,
    ServerErrorMessage,
    ServerHello,
    ServerMessage,
    SessionRequestMessage,
    SessionResultMessage,
    StartTaskMessage,
    SteerTaskMessage,
    TaskAcceptedMessage,
    parse_client_message,
)
from .registry import RunRecord, SubscriberBackpressureError


class RunCoordinatorContract(Protocol):
    """Transport-facing subset of :class:`RunCoordinator`."""

    @property
    def descriptor(self) -> HarnessDescriptor: ...

    @property
    def coding_model_status(self) -> PublicModelStatus | None: ...

    async def start(self, message: StartTaskMessage, *, user_id: str) -> tuple[RunRecord, bool]: ...

    def attach(
        self, run_id: str, *, user_id: str, after_sequence: int = 0
    ) -> AsyncIterator[ServerEnvelope]: ...

    async def steer(self, message: SteerTaskMessage, *, user_id: str) -> ControlReceipt: ...

    async def pause(self, message: PauseTaskMessage, *, user_id: str) -> ControlReceipt: ...

    async def cancel(self, message: CancelTaskMessage, *, user_id: str) -> ControlReceipt: ...

    def acknowledge(self, run_id: str, *, user_id: str, through_sequence: int) -> None: ...

    async def aclose(self) -> None: ...


Authenticator = Callable[[WebSocket], Awaitable[str]]


class AuthenticationError(PermissionError):
    """A connection could not be assigned a trusted server-side identity."""


@dataclass(frozen=True, slots=True)
class LocalBearerAuthenticator:
    """Authenticate a native local client without trusting browser ambient access."""

    token: str
    allowed_origins: frozenset[str] = frozenset()
    user_id: str = "local-user"

    def __post_init__(self) -> None:
        if len(self.token.encode("utf-8")) < 32:
            raise ValueError("local bearer tokens must contain at least 32 UTF-8 bytes")
        if not self.user_id or len(self.user_id) > 256:
            raise ValueError("local authenticator user_id must contain 1 to 256 characters")
        if any(not origin for origin in self.allowed_origins):
            raise ValueError("allowed origins cannot contain empty values")

    async def __call__(self, websocket: WebSocket) -> str:
        client = websocket.client
        if client is None:
            raise AuthenticationError("client address is unavailable")
        host = client.host.split("%", 1)[0]
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise AuthenticationError("the default server accepts loopback clients only")

        # Native clients do not send Origin. Browser clients always do; rejecting
        # untrusted origins prevents a remote web page from driving localhost.
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            raise AuthenticationError("the WebSocket origin is not allowed")

        authorization = websocket.headers.get("authorization", "")
        scheme, separator, supplied = authorization.partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not secrets.compare_digest(supplied, self.token)
        ):
            raise AuthenticationError("a valid local bearer token is required")
        return self.user_id


async def authenticate_local_connection(websocket: WebSocket) -> str:
    """Authenticate from the explicit process environment, failing closed.

    The identity never comes from task metadata, headers, query parameters, or a
    client protocol frame. Server bootstrap normally injects a generated or explicit
    token authenticator; direct embedders may set ``ADK_CODING_AGENT_TOKEN``.
    """

    token = os.getenv("ADK_CODING_AGENT_TOKEN", "")
    if not token:
        raise AuthenticationError("ADK_CODING_AGENT_TOKEN is not configured")
    return await LocalBearerAuthenticator(token)(websocket)


@dataclass(frozen=True, slots=True)
class WebSocketServerSettings:
    """Deterministic resource limits for each public socket."""

    path: str = "/ws"
    hello_timeout_seconds: float = 5.0
    max_frame_bytes: int = 64 * 1024
    outbound_queue_capacity: int = 256
    max_connections: int = 64

    def __post_init__(self) -> None:
        if not self.path.startswith("/"):
            raise ValueError("WebSocket path must start with /")
        if self.hello_timeout_seconds <= 0:
            raise ValueError("hello_timeout_seconds must be positive")
        if self.max_frame_bytes < 1:
            raise ValueError("max_frame_bytes must be positive")
        if self.outbound_queue_capacity < 1:
            raise ValueError("outbound_queue_capacity must be positive")
        if self.max_connections < 1:
            raise ValueError("max_connections must be positive")


class _ConnectionBackpressure(RuntimeError):
    pass


class _InboundFrameTooLarge(ValueError):
    pass


class _AgentWebSocketConnection:
    def __init__(
        self,
        *,
        websocket: WebSocket,
        coordinator: RunCoordinatorContract,
        user_id: str,
        settings: WebSocketServerSettings,
    ) -> None:
        self.websocket = websocket
        self.coordinator = coordinator
        self.user_id = user_id
        self.settings = settings
        self.run_id: str | None = None
        self._run_terminal = False
        self._outbound: asyncio.Queue[ServerMessage] = asyncio.Queue(
            maxsize=settings.outbound_queue_capacity
        )
        self._send_lock = asyncio.Lock()
        self._writer: asyncio.Task[None] | None = None
        self._attachment: asyncio.Task[None] | None = None
        self._closing = False

    async def run(self) -> None:
        hello = await self._receive_hello()
        if hello is None:
            return
        await self._send_direct(
            ServerHello(
                protocol_version=PROTOCOL_VERSION,
                harness=self.coordinator.descriptor,
                coding_model=self.coordinator.coding_model_status,
            )
        )
        self._writer = asyncio.create_task(self._write_messages(), name="agent-websocket-writer")
        try:
            await self._receive_messages()
        except _ConnectionBackpressure:
            await self._close(1013, "client is too slow; reconnect and replay")
        except WebSocketDisconnect:
            pass
        finally:
            await self._stop_connection_tasks()

    async def _receive_hello(self) -> HelloMessage | None:
        try:
            payload = await asyncio.wait_for(
                self._receive_payload(), timeout=self.settings.hello_timeout_seconds
            )
        except TimeoutError:
            await self._send_direct(
                ServerErrorMessage(
                    code="hello_timeout",
                    message="client.hello was not received before the handshake timeout",
                )
            )
            await self._close(1008, "client.hello timeout")
            return None
        except _InboundFrameTooLarge:
            await self._send_direct(self._frame_too_large_error())
            await self._close(1009, "inbound frame too large")
            return None
        except WebSocketDisconnect:
            return None

        try:
            message = parse_client_message(payload)
        except (ValidationError, ValueError):
            await self._send_direct(
                ServerErrorMessage(
                    code="invalid_message",
                    message="the first frame must be a valid client.hello message",
                )
            )
            await self._close(1002, "invalid client.hello")
            return None
        if not isinstance(message, HelloMessage):
            await self._send_direct(
                ServerErrorMessage(
                    code="hello_required",
                    message="client.hello must be the first protocol message",
                )
            )
            await self._close(1002, "client.hello required")
            return None
        supported = (
            PROTOCOL_VERSION in message.protocol_versions
            and PROTOCOL_VERSION in self.coordinator.descriptor.protocol_versions
        )
        if not supported:
            await self._send_direct(
                ServerErrorMessage(
                    code="unsupported_protocol",
                    message=f"the server requires protocol version {PROTOCOL_VERSION}",
                )
            )
            await self._close(1002, "unsupported protocol version")
            return None
        return message

    async def _receive_messages(self) -> None:
        while True:
            try:
                payload = await self._receive_payload()
            except _InboundFrameTooLarge:
                # Fatal protocol errors must reach the peer before the close frame;
                # putting this behind ordinary queued output creates a close race.
                await self._send_direct(self._frame_too_large_error())
                await self._close(1009, "inbound frame too large")
                return
            try:
                message = parse_client_message(payload)
            except (ValidationError, ValueError):
                await self._enqueue(
                    ServerErrorMessage(
                        code="invalid_message",
                        message="message is not valid for protocol version 1",
                    )
                )
                continue
            await self._dispatch(message)

    async def _dispatch(self, message: object) -> None:
        if isinstance(message, HelloMessage):
            await self._enqueue(
                ServerErrorMessage(
                    code="already_negotiated",
                    message="client.hello may only be sent once",
                )
            )
        elif isinstance(message, StartTaskMessage):
            await self._start(message)
        elif isinstance(message, AttachTaskMessage):
            await self._attach(message)
        elif isinstance(message, SteerTaskMessage):
            await self._control("steer", message)
        elif isinstance(message, PauseTaskMessage):
            await self._control("pause", message)
        elif isinstance(message, CancelTaskMessage):
            await self._control("cancel", message)
        elif isinstance(message, AckMessage):
            await self._acknowledge(message)
        elif isinstance(message, PingMessage):
            await self._enqueue(PongMessage(nonce=message.nonce))
        elif isinstance(message, SessionRequestMessage):
            await self._session_request(message)
        elif isinstance(message, ProviderRequestMessage):
            await self._provider_request(message)
        elif isinstance(message, ModelRequestMessage):
            await self._model_request(message)

    async def _model_request(self, message: ModelRequestMessage) -> None:
        handler = getattr(self.coordinator, "model_request", None)
        if handler is None:
            await self._enqueue(ServerErrorMessage(code="unsupported_control", message="Model controls are not supported", request_id=message.request_id))
            return
        try:
            result = await handler(message, user_id=self.user_id)
        except ModelControlError as error:
            await self._enqueue(ServerErrorMessage(code="model_request_failed", message=str(error), request_id=message.request_id))
        except Exception:
            await self._enqueue(ServerErrorMessage(code="model_request_failed", message="Model request not confirmed. Reopen /model to check current and saved settings before retrying.", request_id=message.request_id))
        else:
            await self._enqueue(ModelResultMessage(request_id=message.request_id, operation=message.operation, data=result))

    async def _provider_request(self, message: ProviderRequestMessage) -> None:
        handler = getattr(self.coordinator, "provider_request", None)
        if handler is None:
            await self._enqueue(ServerErrorMessage(code="unsupported_control", message="Provider controls are not supported", request_id=message.request_id))
            return
        try:
            command = ProviderControlRequest.model_validate(message.model_dump(exclude={"type", "protocol_version"}))
            result = await handler(command, user_id=self.user_id)
        except ProviderControlError as error:
            await self._enqueue(ServerErrorMessage(code="provider_request_failed", message=str(error), request_id=message.request_id))
        except Exception:
            await self._enqueue(ServerErrorMessage(code="provider_request_failed", message="Provider request failed. Check authentication storage and connectivity, then retry.", request_id=message.request_id))
        else:
            await self._enqueue(ProviderResultMessage(request_id=message.request_id, operation=message.operation, data=result))

    async def _session_request(self, message: SessionRequestMessage) -> None:
        # Session management is a server capability, not a model-visible tool.
        handler = getattr(self.coordinator, "session_request", None)
        if handler is None:
            await self._enqueue(ServerErrorMessage(code="unsupported_control", message="Session controls are not supported", request_id=message.request_id))
            return
        try:
            result = await handler(message, user_id=self.user_id)
        except (KeyError, ValueError) as error:
            await self._enqueue(ServerErrorMessage(code="session_request_failed", message=str(error)[:4096], request_id=message.request_id))
        except Exception:
            await self._enqueue(ServerErrorMessage(code="session_request_failed", message="Session request failed; inspect the server log", request_id=message.request_id, retryable=True))
        else:
            await self._enqueue(SessionResultMessage(request_id=message.request_id, operation=message.operation, data=result))

    async def _start(self, message: StartTaskMessage) -> None:
        await self._release_terminal_attachment()
        if self.run_id is not None:
            await self._already_attached_error(message.request_id)
            return
        try:
            record, created = await self.coordinator.start(message, user_id=self.user_id)
        except ValueError as error:
            await self._enqueue(
                ServerErrorMessage(
                    code="invalid_request",
                    message=str(error)[:4_096] or "task could not be started",
                    request_id=message.request_id,
                )
            )
            return
        except Exception:
            await self._enqueue(
                ServerErrorMessage(
                    code="start_failed",
                    message="task initialization failed",
                    request_id=message.request_id,
                    retryable=True,
                )
            )
            return
        self.run_id = record.run_id
        await self._enqueue(
            TaskAcceptedMessage(
                request_id=message.request_id,
                run_id=record.run_id,
                thread_id=record.thread_id,
                created=created,
            )
        )
        self._start_attachment(record.run_id, after_sequence=0)

    async def _attach(self, message: AttachTaskMessage) -> None:
        await self._release_terminal_attachment()
        if self.run_id is not None:
            await self._already_attached_error()
            return
        self.run_id = message.run_id
        self._start_attachment(message.run_id, after_sequence=message.after_sequence)

    def _start_attachment(self, run_id: str, *, after_sequence: int) -> None:
        self._run_terminal = False
        self._attachment = asyncio.create_task(
            self._stream_run(run_id, after_sequence=after_sequence),
            name=f"agent-websocket-stream:{run_id}",
        )

    async def _stream_run(self, run_id: str, *, after_sequence: int) -> None:
        emitted = False
        try:
            async for envelope in self.coordinator.attach(
                run_id,
                user_id=self.user_id,
                after_sequence=after_sequence,
            ):
                emitted = True
                await self._enqueue(envelope)
                if envelope.event.type in {
                    AgUiEventType.RUN_FINISHED, AgUiEventType.RUN_ERROR,
                }:
                    self._run_terminal = True
        except SubscriberBackpressureError:
            await self._close(1013, "live stream overflow; reconnect and replay")
        except KeyError:
            if not emitted and self.run_id == run_id:
                self.run_id = None
            await self._enqueue_or_close(
                ServerErrorMessage(
                    code="run_not_found",
                    message="the run does not exist or is not owned by this user",
                    run_id=run_id,
                )
            )
        except _ConnectionBackpressure:
            await self._close(1013, "client is too slow; reconnect and replay")
        except asyncio.CancelledError:
            raise
        except Exception:
            if not emitted and self.run_id == run_id:
                self.run_id = None
            await self._enqueue_or_close(
                ServerErrorMessage(
                    code="stream_failed",
                    message="the run stream failed; reconnect using the last sequence",
                    run_id=run_id,
                    retryable=True,
                )
            )

    async def _release_terminal_attachment(self) -> None:
        if not self._run_terminal and self.run_id is not None:
            terminal = getattr(self.coordinator, "is_terminal_run", None)
            if terminal is not None:
                with suppress(KeyError):
                    self._run_terminal = terminal(self.run_id, user_id=self.user_id)
        if not self._run_terminal:
            return
        if self._attachment is not None:
            self._attachment.cancel()
            with suppress(asyncio.CancelledError):
                await self._attachment
            self._attachment = None
        self.run_id = None
        self._run_terminal = False

    async def _control(
        self,
        operation: str,
        message: SteerTaskMessage | PauseTaskMessage | CancelTaskMessage,
    ) -> None:
        if not await self._require_attached_run(message.run_id):
            return
        try:
            if isinstance(message, SteerTaskMessage):
                receipt = await self.coordinator.steer(message, user_id=self.user_id)
                typed_operation = "steer"
            elif isinstance(message, PauseTaskMessage):
                receipt = await self.coordinator.pause(message, user_id=self.user_id)
                typed_operation = "pause"
            else:
                receipt = await self.coordinator.cancel(message, user_id=self.user_id)
                typed_operation = "cancel"
        except KeyError:
            await self._run_not_found_error(message.run_id)
            return
        except Exception:
            await self._enqueue(
                ServerErrorMessage(
                    code="control_failed",
                    message=f"{operation} could not be applied",
                    run_id=message.run_id,
                    retryable=True,
                )
            )
            return
        await self._enqueue(
            ControlResultMessage(
                operation=typed_operation,
                run_id=message.run_id,
                accepted=receipt.accepted,
                # Correlate the public reply to the caller's retry key. A harness
                # may assign a different internal receipt/message identifier.
                command_id=message.idempotency_key,
                detail=receipt.detail,
            )
        )

    async def _acknowledge(self, message: AckMessage) -> None:
        if not await self._require_attached_run(message.run_id):
            return
        try:
            self.coordinator.acknowledge(
                message.run_id,
                user_id=self.user_id,
                through_sequence=message.through_sequence,
            )
        except KeyError:
            await self._run_not_found_error(message.run_id)
        except ValueError as error:
            await self._enqueue(
                ServerErrorMessage(
                    code="invalid_ack",
                    message=str(error)[:4_096] or "acknowledgement is invalid",
                    run_id=message.run_id,
                )
            )

    async def _require_attached_run(self, run_id: str) -> bool:
        if self.run_id == run_id:
            return True
        await self._enqueue(
            ServerErrorMessage(
                code="run_not_attached",
                message="this connection is not attached to the requested run",
                run_id=run_id,
            )
        )
        return False

    async def _already_attached_error(self, request_id: str | None = None) -> None:
        await self._enqueue(
            ServerErrorMessage(
                code="run_already_attached",
                message="a WebSocket connection may attach to only one run",
                request_id=request_id,
                run_id=self.run_id,
            )
        )

    async def _run_not_found_error(self, run_id: str) -> None:
        await self._enqueue(
            ServerErrorMessage(
                code="run_not_found",
                message="the run does not exist or is not owned by this user",
                run_id=run_id,
            )
        )

    def _frame_too_large_error(self) -> ServerErrorMessage:
        return ServerErrorMessage(
            code="frame_too_large",
            message=f"inbound frames are limited to {self.settings.max_frame_bytes} bytes",
        )

    async def _receive_payload(self) -> str | bytes:
        frame = await self.websocket.receive()
        if frame["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(int(frame.get("code", 1000)))
        text = frame.get("text")
        data = frame.get("bytes")
        payload: str | bytes
        if text is not None:
            payload = text
            size = len(text.encode("utf-8"))
        elif data is not None:
            payload = data
            size = len(data)
        else:
            raise ValueError("WebSocket frame has no text or byte payload")
        if size > self.settings.max_frame_bytes:
            raise _InboundFrameTooLarge
        return payload

    async def _enqueue(self, message: ServerMessage) -> None:
        if self._closing:
            raise _ConnectionBackpressure("connection is closing")
        try:
            self._outbound.put_nowait(message)
        except asyncio.QueueFull as error:
            raise _ConnectionBackpressure("outbound queue is full") from error

    async def _enqueue_or_close(self, message: ServerMessage) -> None:
        try:
            await self._enqueue(message)
        except _ConnectionBackpressure:
            await self._close(1013, "client is too slow; reconnect and replay")

    async def _write_messages(self) -> None:
        try:
            while True:
                message = await self._outbound.get()
                await self._send_direct(message)
        except (WebSocketDisconnect, RuntimeError):
            self._closing = True

    async def _send_direct(self, message: ServerMessage) -> None:
        async with self._send_lock:
            await self.websocket.send_text(message.model_dump_json())

    async def _close(self, code: int, reason: str) -> None:
        if self._closing:
            return
        self._closing = True
        async with self._send_lock:
            with suppress(RuntimeError, WebSocketDisconnect):
                await self.websocket.close(code=code, reason=reason)

    async def _stop_connection_tasks(self) -> None:
        tasks = tuple(
            task
            for task in (self._attachment, self._writer)
            if task is not None and task is not asyncio.current_task()
        )
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task


class AgentWebSocketServer:
    """Connection limiter and endpoint around a shared durable coordinator."""

    def __init__(
        self,
        coordinator: RunCoordinatorContract,
        *,
        authenticator: Authenticator = authenticate_local_connection,
        settings: WebSocketServerSettings | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.authenticator = authenticator
        self.settings = settings or WebSocketServerSettings()
        self._connections = 0
        self._connection_lock = asyncio.Lock()

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        if not await self._reserve_connection():
            await websocket.send_text(
                ServerErrorMessage(
                    code="server_busy",
                    message="the server has reached its connection limit",
                    retryable=True,
                ).model_dump_json()
            )
            await websocket.close(code=1013, reason="server connection limit reached")
            return
        try:
            try:
                user_id = await self.authenticator(websocket)
                if not user_id or len(user_id) > 256:
                    raise AuthenticationError("authenticator returned an invalid user id")
            except Exception:
                await websocket.send_text(
                    ServerErrorMessage(
                        code="authentication_failed",
                        message="the connection could not be authenticated",
                    ).model_dump_json()
                )
                await websocket.close(code=1008, reason="authentication failed")
                return
            connection = _AgentWebSocketConnection(
                websocket=websocket,
                coordinator=self.coordinator,
                user_id=user_id,
                settings=self.settings,
            )
            await connection.run()
        finally:
            await self._release_connection()

    async def _reserve_connection(self) -> bool:
        async with self._connection_lock:
            if self._connections >= self.settings.max_connections:
                return False
            self._connections += 1
            return True

    async def _release_connection(self) -> None:
        async with self._connection_lock:
            self._connections -= 1


def create_websocket_app(
    coordinator: RunCoordinatorContract,
    *,
    authenticator: Authenticator = authenticate_local_connection,
    settings: WebSocketServerSettings | None = None,
) -> FastAPI:
    """Build a loopback-safe FastAPI app around one shared coordinator."""

    server = AgentWebSocketServer(
        coordinator,
        authenticator=authenticator,
        settings=settings,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            recover = getattr(coordinator, "recover_interrupted_runs", None)
            if recover is not None:
                recover()
            yield
        finally:
            await coordinator.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.agent_websocket_server = server
    app.add_api_websocket_route(server.settings.path, server.handle)
    return app


__all__ = [
    "AgentWebSocketServer",
    "AuthenticationError",
    "Authenticator",
    "LocalBearerAuthenticator",
    "RunCoordinatorContract",
    "WebSocketServerSettings",
    "authenticate_local_connection",
    "create_websocket_app",
]
