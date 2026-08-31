from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from harness.agent import HarnessDescriptor, RuntimeCapability
from harness.approvals import ApprovalStore
from harness.approvals.waiting import ApprovalWaiter
from harness.server.bootstrap import build_server_assembly
from harness.server.protocol import (
    ApprovalRequestMessage,
    parse_client_message,
    parse_server_message,
)
from harness.server.registry import RunEventBroker, SqliteRunEventStore
from harness.server.runtime import RunCoordinator


@pytest.mark.asyncio
async def test_approval_controls_check_owner_binding_fingerprint_and_liveness(tmp_path) -> None:
    metadata = {"coding.workspace_identity": "workspace", "coding.harness_implementation": "fixture_v1"}
    factory = SimpleNamespace(descriptor=HarnessDescriptor(implementation="fixture_v1", display_name="Fixture",
        capabilities=frozenset({RuntimeCapability.APPROVALS})), run_metadata=metadata)
    coordinator = RunCoordinator(store=SqliteRunEventStore(tmp_path / "runs.db"), broker=RunEventBroker(), execution_factory=factory)
    record, _ = coordinator.store.create_run(request_id="run", idempotency_key="run", thread_id="thread", user_id="owner", input="request", metadata=metadata)
    waiter = ApprovalWaiter(ApprovalStore(tmp_path / "approvals.db"), record.run_id)
    request = waiter.store.request(task_id=record.run_id, fingerprint="a" * 64, operation="printf approved", risk="unknown", reason="review")
    task = asyncio.create_task(waiter.wait(request.request_id, record.run_id))
    coordinator._active[record.run_id] = SimpleNamespace(execution=SimpleNamespace(approvals=waiter), task=task)
    message = ApprovalRequestMessage(type="approval.request", operation="decide", request_id="control", run_id=record.run_id,
        approval_id=request.request_id, fingerprint=request.fingerprint, decision="approved")
    try:
        async with asyncio.timeout(3):
            while not waiter.pending():
                await asyncio.sleep(0.005)
        with pytest.raises(KeyError):
            await coordinator.approval_request(message, user_id="other")
        factory.run_metadata = {**metadata, "coding.workspace_identity": "different"}
        with pytest.raises(ValueError, match="another workspace"):
            await coordinator.approval_request(message, user_id="owner")
        factory.run_metadata = metadata
        with pytest.raises(ValueError, match="does not match"):
            await coordinator.approval_request(message.model_copy(update={"fingerprint": "b" * 64}), user_id="owner")
        assert waiter.store.get(request.request_id).status == "pending"
        result = await coordinator.approval_request(message, user_id="owner")
        assert result["request"]["decided_by"] == "owner"
        assert (await task).status == "approved"
        with pytest.raises(ValueError, match="no longer accepting"):
            await coordinator.approval_request(message, user_id="owner")
        status = await coordinator.approval_request(ApprovalRequestMessage(type="approval.request", operation="list", request_id="list", run_id=record.run_id), user_id="owner")
        assert status["requests"] == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_socket_remains_responsive_during_approval_control_and_hides_private_errors(tmp_path, monkeypatch) -> None:
    entered, release = threading.Event(), threading.Event()
    async def slow(*args, **kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, 3)
        raise ValueError("PRIVATE_DECISION_DETAIL")
    assembly = build_server_assembly(workspace=tmp_path, state_root=tmp_path / "state")
    monkeypatch.setattr(assembly.coordinator, "approval_request", slow)
    token = assembly.auth_token_path.read_text().strip()
    try:
        with (TestClient(assembly.app, client=("127.0.0.1", 12345)) as client,
              client.websocket_connect("/v1/agent", headers={"authorization": f"Bearer {token}"}) as socket):
            socket.send_json({"type": "client.hello", "protocol_versions": [1], "client_name": "approval-test"})
            assert "approvals" in socket.receive_json()["harness"]["capabilities"]
            message = {"type": "approval.request", "request_id": "control", "operation": "list", "run_id": "run"}
            parse_client_message(message)
            socket.send_json(message)
            assert entered.wait(3)
            socket.send_json({"type": "ping", "nonce": "responsive"})
            assert socket.receive_json()["nonce"] == "responsive"
            release.set()
            frame = socket.receive_json()
            parse_server_message(frame)
            assert frame["code"] == "approval_request_failed"
            assert frame["request_id"] == "control" and "PRIVATE" not in str(frame)
    finally:
        release.set()


def test_approval_control_cannot_supply_actor_or_partial_decisions() -> None:
    base = {"type": "approval.request", "request_id": "control", "operation": "decide", "run_id": "run"}
    with pytest.raises(ValueError):
        parse_client_message(base)
    valid = {**base, "approval_id": "id", "fingerprint": "a" * 64, "decision": "approved"}
    parse_client_message(valid)
    for fields in ({"actor": "owner"}, {"operation": "list"}, {"fingerprint": "other"}):
        with pytest.raises(ValueError):
            parse_client_message({**valid, **fields})
