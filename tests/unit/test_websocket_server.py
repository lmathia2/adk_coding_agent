from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from starlette.datastructures import Address, Headers

from harness.agent import ControlReceipt, HarnessDescriptor, RuntimeCapability
from harness.server.protocol import (
    AgUiEvent,
    AgUiEventType,
    CancelTaskMessage,
    PauseTaskMessage,
    ServerEnvelope,
    StartTaskMessage,
    SteerTaskMessage,
)
from harness.server.registry import RunRecord, SubscriberBackpressureError
from harness.server.websocket import (
    AuthenticationError,
    LocalBearerAuthenticator,
    WebSocketServerSettings,
    create_websocket_app,
)


async def _authenticated(_websocket: WebSocket) -> str:
    return "server-user"


def _local_websocket(*, token: str, origin: str | None = None) -> Any:
    headers = {"authorization": f"Bearer {token}"}
    if origin is not None:
        headers["origin"] = origin
    return SimpleNamespace(
        client=Address("127.0.0.1", 51_234),
        headers=Headers(headers),
    )


@pytest.mark.asyncio
async def test_local_bearer_authenticator_requires_token_and_rejects_browser_origin() -> None:
    token = "local-token-" + "x" * 32
    authenticator = LocalBearerAuthenticator(token)

    assert await authenticator(_local_websocket(token=token)) == "local-user"
    with pytest.raises(AuthenticationError, match="bearer"):
        await authenticator(_local_websocket(token="wrong-token-" + "y" * 32))
    with pytest.raises(AuthenticationError, match="origin"):
        await authenticator(_local_websocket(token=token, origin="https://attacker.example"))


@pytest.mark.asyncio
async def test_local_bearer_authenticator_accepts_only_explicitly_allowed_origin() -> None:
    token = "local-token-" + "x" * 32
    authenticator = LocalBearerAuthenticator(
        token,
        allowed_origins=frozenset({"http://127.0.0.1:3000"}),
    )

    assert (
        await authenticator(_local_websocket(token=token, origin="http://127.0.0.1:3000"))
        == "local-user"
    )


def _record(run_id: str, *, user_id: str = "server-user") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        request_id="request-1",
        idempotency_key=f"key-{run_id}",
        thread_id=f"thread-{run_id}",
        user_id=user_id,
        session_id=f"session-{run_id}",
        invocation_id=f"invocation-{run_id}",
        input="Do the work",
        input_sha256="0" * 64,
        metadata={},
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _envelope(run_id: str, sequence: int) -> ServerEnvelope:
    return ServerEnvelope(
        sequence=sequence,
        run_id=run_id,
        session_id=f"session-{run_id}",
        invocation_id=f"invocation-{run_id}",
        durable=True,
        event=AgUiEvent(
            type=AgUiEventType.RUN_STARTED,
            thread_id=f"thread-{run_id}",
            run_id=run_id,
        ),
    )


class FakeCoordinator:
    def __init__(self) -> None:
        self.coding_model_status = None
        self.descriptor = HarnessDescriptor(
            implementation="fake_harness",
            display_name="Fake Harness",
            capabilities=frozenset(
                {
                    RuntimeCapability.STREAMING,
                    RuntimeCapability.STEERING,
                    RuntimeCapability.REPLAY,
                }
            ),
        )
        self.records: dict[str, RunRecord] = {}
        self.events: dict[str, tuple[ServerEnvelope, ...]] = {}
        self.start_calls: list[tuple[StartTaskMessage, str]] = []
        self.attach_calls: list[tuple[str, str, int]] = []
        self.control_calls: list[tuple[str, str, str]] = []
        self.ack_calls: list[tuple[str, str, int]] = []
        self.cancel_calls = 0
        self.closed = 0
        self.raise_backpressure_for: str | None = None
        self.hold_attachment_for: str | None = None
        self.attachment_started = threading.Event()
        self.attachment_closed = threading.Event()

    async def start(self, message: StartTaskMessage, *, user_id: str) -> tuple[RunRecord, bool]:
        self.start_calls.append((message, user_id))
        run_id = f"run-{message.idempotency_key}"
        created = run_id not in self.records
        record = self.records.setdefault(run_id, _record(run_id, user_id=user_id))
        self.events.setdefault(run_id, (_envelope(run_id, 1),))
        return record, created

    async def attach(
        self, run_id: str, *, user_id: str, after_sequence: int = 0
    ) -> AsyncIterator[ServerEnvelope]:
        self.attach_calls.append((run_id, user_id, after_sequence))
        record = self.records.get(run_id)
        if record is None or record.user_id != user_id:
            raise KeyError(run_id)
        if self.raise_backpressure_for == run_id:
            raise SubscriberBackpressureError("overflow")
        for event in self.events.get(run_id, ()):
            if event.sequence > after_sequence:
                yield event
        if self.hold_attachment_for == run_id:
            self.attachment_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.attachment_closed.set()

    async def steer(self, message: SteerTaskMessage, *, user_id: str) -> ControlReceipt:
        self._require_owner(message.run_id, user_id)
        self.control_calls.append(("steer", message.run_id, user_id))
        return ControlReceipt(
            accepted=True,
            command_id="internal-steering-receipt",
            detail="queued for the next safe point",
        )

    async def pause(self, message: PauseTaskMessage, *, user_id: str) -> ControlReceipt:
        self._require_owner(message.run_id, user_id)
        self.control_calls.append(("pause", message.run_id, user_id))
        return ControlReceipt(
            accepted=True,
            command_id=message.idempotency_key,
        )

    async def cancel(self, message: CancelTaskMessage, *, user_id: str) -> ControlReceipt:
        self._require_owner(message.run_id, user_id)
        self.cancel_calls += 1
        self.control_calls.append(("cancel", message.run_id, user_id))
        return ControlReceipt(
            accepted=True,
            command_id=message.idempotency_key,
            detail="best effort",
        )

    def acknowledge(self, run_id: str, *, user_id: str, through_sequence: int) -> None:
        self._require_owner(run_id, user_id)
        high_water = max((event.sequence for event in self.events.get(run_id, ())), default=0)
        if through_sequence > high_water:
            raise ValueError("acknowledgement exceeds durable high-water sequence")
        self.ack_calls.append((run_id, user_id, through_sequence))

    async def aclose(self) -> None:
        self.closed += 1

    def is_terminal_run(self, run_id: str, *, user_id: str) -> bool:
        self._require_owner(run_id, user_id)
        return self.records[run_id].status in {"completed", "cancelled", "failed"}

    def _require_owner(self, run_id: str, user_id: str) -> None:
        record = self.records.get(run_id)
        if record is None or record.user_id != user_id:
            raise KeyError(run_id)


def _hello(socket: Any, versions: list[int] | None = None) -> dict[str, Any]:
    socket.send_json(
        {
            "type": "client.hello",
            "protocol_versions": versions or [1],
            "client_name": "test-client",
        }
    )
    return socket.receive_json()


def test_completed_turn_can_be_followed_by_another_on_the_same_socket() -> None:
    coordinator = FakeCoordinator()
    for key in ("first", "second"):
        run_id = f"run-{key}"
        terminal = _envelope(run_id, 2).model_copy(update={"event": AgUiEvent(
            type=AgUiEventType.RUN_FINISHED, thread_id="conversation", run_id=run_id,
        )})
        coordinator.events[run_id] = (_envelope(run_id, 1), terminal)
    app = create_websocket_app(coordinator, authenticator=_authenticated)
    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        _hello(socket)
        for key in ("first", "second"):
            socket.send_json({"type": "task.start", "request_id": key,
                "idempotency_key": key, "thread_id": "conversation", "input": key})
            assert socket.receive_json()["type"] == "task.accepted"
            assert socket.receive_json()["event"]["type"] == "RUN_STARTED"
            assert socket.receive_json()["event"]["type"] == "RUN_FINISHED"
            socket.send_json({"type": "events.ack", "run_id": f"run-{key}", "through_sequence": 2})
    assert len(coordinator.start_calls) == 2
    assert all(message.thread_id == "conversation" for message, _ in coordinator.start_calls)
    assert coordinator.cancel_calls == 0


def test_start_stream_controls_ack_and_server_derived_identity() -> None:
    coordinator = FakeCoordinator()
    app = create_websocket_app(coordinator, authenticator=_authenticated)

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        hello = _hello(socket)
        assert hello["type"] == "server.hello"
        assert hello["protocol_version"] == 1

        socket.send_json(
            {
                "type": "task.start",
                "request_id": "request-1",
                "idempotency_key": "one",
                "input": "Do the work",
                "metadata": {"user_id": "attacker-controlled"},
            }
        )
        accepted = socket.receive_json()
        streamed = socket.receive_json()
        assert accepted == {
            "type": "task.accepted",
            "protocol_version": 1,
            "request_id": "request-1",
            "run_id": "run-one",
            "thread_id": "thread-run-one",
            "created": True,
        }
        assert streamed["type"] == "event"
        assert streamed["sequence"] == 1
        assert coordinator.start_calls[0][1] == "server-user"

        socket.send_json(
            {
                "type": "task.steer",
                "run_id": "run-one",
                "content": "Use the smaller implementation",
                "priority": 5,
                "idempotency_key": "steer-1",
            }
        )
        control = socket.receive_json()
        assert control["type"] == "control.result"
        assert control["operation"] == "steer"
        assert control["accepted"] is True
        assert control["command_id"] == "steer-1"

        socket.send_json(
            {
                "type": "events.ack",
                "run_id": "run-one",
                "through_sequence": 1,
            }
        )
        socket.send_json({"type": "ping", "nonce": "still-responsive"})
        assert socket.receive_json() == {
            "type": "pong",
            "protocol_version": 1,
            "nonce": "still-responsive",
        }
        assert coordinator.ack_calls == [("run-one", "server-user", 1)]

        socket.send_json(
            {
                "type": "task.start",
                "request_id": "request-2",
                "idempotency_key": "two",
                "input": "Another task",
            }
        )
        assert socket.receive_json()["code"] == "run_already_attached"

    assert coordinator.cancel_calls == 0
    assert coordinator.closed == 1


def test_attach_after_terminal_cursor_does_not_block_the_next_turn() -> None:
    coordinator = FakeCoordinator()
    coordinator.records["old"] = _record("old").model_copy(update={"status": "completed"})
    coordinator.events["old"] = (_envelope("old", 1).model_copy(update={"event": AgUiEvent(
        type=AgUiEventType.RUN_FINISHED, run_id="old", thread_id="thread-old",
    )}),)
    app = create_websocket_app(coordinator, authenticator=_authenticated)
    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        _hello(socket)
        socket.send_json({"type": "task.attach", "run_id": "old", "after_sequence": 1})
        # The replay has no remaining event: terminality must come from the
        # authoritative run record, not from seeing another RUN_FINISHED frame.
        socket.send_json({"type": "task.start", "request_id": "new", "idempotency_key": "new", "input": "next"})
        assert socket.receive_json()["type"] == "task.accepted"
        assert socket.receive_json()["event"]["type"] == "RUN_STARTED"


def test_attach_replays_after_cursor_and_validates_ack() -> None:
    coordinator = FakeCoordinator()
    coordinator.records["run-existing"] = _record("run-existing")
    coordinator.events["run-existing"] = (
        _envelope("run-existing", 7),
        _envelope("run-existing", 8),
    )
    app = create_websocket_app(coordinator, authenticator=_authenticated)

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        _hello(socket)
        socket.send_json(
            {
                "type": "task.attach",
                "run_id": "run-existing",
                "after_sequence": 7,
            }
        )
        assert socket.receive_json()["sequence"] == 8
        assert coordinator.attach_calls == [("run-existing", "server-user", 7)]

        socket.send_json(
            {
                "type": "events.ack",
                "run_id": "run-existing",
                "through_sequence": 99,
            }
        )
        invalid_ack = socket.receive_json()
        assert invalid_ack["code"] == "invalid_ack"
        assert invalid_ack["run_id"] == "run-existing"

        socket.send_json(
            {
                "type": "task.pause",
                "run_id": "another-run",
                "idempotency_key": "pause-1",
            }
        )
        assert socket.receive_json()["code"] == "run_not_attached"


@pytest.mark.parametrize(
    ("message", "operation"),
    [
        (
            {
                "type": "task.pause",
                "run_id": "run-control",
                "idempotency_key": "pause-1",
            },
            "pause",
        ),
        (
            {
                "type": "task.cancel",
                "run_id": "run-control",
                "idempotency_key": "cancel-1",
            },
            "cancel",
        ),
    ],
)
def test_pause_and_cancel_return_control_receipts(
    message: dict[str, object], operation: str
) -> None:
    coordinator = FakeCoordinator()
    coordinator.records["run-control"] = _record("run-control")
    app = create_websocket_app(coordinator, authenticator=_authenticated)

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        _hello(socket)
        socket.send_json({"type": "task.attach", "run_id": "run-control", "after_sequence": 0})
        socket.send_json(message)
        result = socket.receive_json()
        assert result["type"] == "control.result"
        assert result["operation"] == operation
        assert result["accepted"] is True


def test_unknown_or_unowned_run_gets_typed_error_and_can_retry() -> None:
    coordinator = FakeCoordinator()
    coordinator.records["owned"] = _record("owned")
    coordinator.events["owned"] = (_envelope("owned", 1),)
    app = create_websocket_app(coordinator, authenticator=_authenticated)

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        _hello(socket)
        socket.send_json({"type": "task.attach", "run_id": "missing", "after_sequence": 0})
        error = socket.receive_json()
        assert error["code"] == "run_not_found"
        assert "missing" not in error["message"]

        socket.send_json({"type": "task.attach", "run_id": "owned", "after_sequence": 0})
        assert socket.receive_json()["run_id"] == "owned"


@pytest.mark.parametrize(
    ("first_message", "expected_code"),
    [
        ({"type": "ping", "nonce": "early"}, "hello_required"),
        ({"broken": True}, "invalid_message"),
    ],
)
def test_client_hello_must_be_first(first_message: dict[str, object], expected_code: str) -> None:
    coordinator = FakeCoordinator()
    app = create_websocket_app(coordinator, authenticator=_authenticated)

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        socket.send_json(first_message)
        assert socket.receive_json()["code"] == expected_code
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1002


def test_protocol_negotiation_and_invalid_json_are_typed() -> None:
    coordinator = FakeCoordinator()
    app = create_websocket_app(coordinator, authenticator=_authenticated)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json(
                {
                    "type": "client.hello",
                    "protocol_versions": [2],
                    "client_name": "future-client",
                }
            )
            assert socket.receive_json()["code"] == "unsupported_protocol"

        with client.websocket_connect("/ws") as socket:
            _hello(socket)
            socket.send_text("not-json")
            assert socket.receive_json()["code"] == "invalid_message"
            socket.send_json({"type": "ping", "nonce": "recovered"})
            assert socket.receive_json()["nonce"] == "recovered"


def test_inbound_frame_limit_closes_with_1009() -> None:
    coordinator = FakeCoordinator()
    app = create_websocket_app(
        coordinator,
        authenticator=_authenticated,
        settings=WebSocketServerSettings(max_frame_bytes=256),
    )

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        _hello(socket)
        socket.send_text("x" * 257)
        assert socket.receive_json()["code"] == "frame_too_large"
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1009


def test_broker_backpressure_closes_with_retry_code() -> None:
    coordinator = FakeCoordinator()
    coordinator.records["run-overflow"] = _record("run-overflow")
    coordinator.raise_backpressure_for = "run-overflow"
    app = create_websocket_app(coordinator, authenticator=_authenticated)

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        _hello(socket)
        socket.send_json({"type": "task.attach", "run_id": "run-overflow", "after_sequence": 0})
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1013


def test_connection_limit_is_enforced_without_disturbing_existing_socket() -> None:
    coordinator = FakeCoordinator()
    app = create_websocket_app(
        coordinator,
        authenticator=_authenticated,
        settings=WebSocketServerSettings(max_connections=1),
    )

    with TestClient(app) as client, client.websocket_connect("/ws") as first:
        _hello(first)
        with client.websocket_connect("/ws") as second:
            busy = second.receive_json()
            assert busy["code"] == "server_busy"
            assert busy["retryable"] is True
            with pytest.raises(WebSocketDisconnect) as closed:
                second.receive_json()
            assert closed.value.code == 1013
        first.send_json({"type": "ping", "nonce": "first-survives"})
        assert first.receive_json()["nonce"] == "first-survives"


def test_disconnect_cancels_attachment_but_never_cancels_run() -> None:
    coordinator = FakeCoordinator()
    coordinator.records["run-live"] = _record("run-live")
    coordinator.events["run-live"] = (_envelope("run-live", 1),)
    coordinator.hold_attachment_for = "run-live"
    app = create_websocket_app(coordinator, authenticator=_authenticated)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as socket:
            _hello(socket)
            socket.send_json({"type": "task.attach", "run_id": "run-live", "after_sequence": 0})
            assert socket.receive_json()["sequence"] == 1
            assert coordinator.attachment_started.wait(timeout=1)
        assert coordinator.attachment_closed.wait(timeout=1)
        assert coordinator.cancel_calls == 0


def test_default_authenticator_rejects_non_loopback_test_peer() -> None:
    coordinator = FakeCoordinator()
    app = create_websocket_app(coordinator)

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        assert socket.receive_json()["code"] == "authentication_failed"
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008


def test_hello_timeout_is_typed_and_closes_connection() -> None:
    coordinator = FakeCoordinator()
    app = create_websocket_app(
        coordinator,
        authenticator=_authenticated,
        settings=WebSocketServerSettings(hello_timeout_seconds=0.01),
    )

    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        assert socket.receive_json()["code"] == "hello_timeout"
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008
