from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.approvals import (
    ApprovalDecision,
    ApprovalHTTPRequest,
    ApprovalHTTPTransport,
    ApprovalStore,
    ApprovalSubmission,
    InteractiveApprovalTransport,
    ManagedApprovalQueue,
)


@dataclass
class _Clock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def _clock() -> _Clock:
    return _Clock(datetime(2026, 1, 1, tzinfo=UTC))


def _submission(*, expires_at: str | None = None) -> ApprovalSubmission:
    return ApprovalSubmission(
        task_id="task",
        fingerprint="exact-command-fingerprint",
        operation="deploy --dry-run",
        risk="publishing",
        reason="human authorization required",
        expires_at=expires_at,
    )


def test_request_and_decision_submissions_replay_idempotently(
    tmp_path: Path,
) -> None:
    store = ApprovalStore(tmp_path / "approvals.db", clock=_clock())
    first = store.submit(_submission())
    replay = store.submit(_submission())
    assert replay.request_id == first.request_id
    with pytest.raises(ValueError, match="different request content"):
        store.submit(
            _submission().model_copy(update={"operation": "different command"})
        )

    decision = ApprovalDecision(
        request_id=first.request_id,
        decision="approved",
        actor="operator",
        note="reviewed",
    )
    approved = store.submit_decision(decision)
    assert store.submit_decision(decision) == approved
    with pytest.raises(ValueError, match="already decided as approved"):
        store.submit_decision(
            ApprovalDecision(
                request_id=first.request_id,
                decision="denied",
                actor="other-operator",
            )
        )


def test_expired_request_cannot_be_decided_or_leased(tmp_path: Path) -> None:
    clock = _clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    request = store.submit(
        _submission(expires_at=(clock.current + timedelta(seconds=5)).isoformat())
    )

    clock.advance(seconds=6)

    assert store.get(request.request_id).status == "expired"  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="status expired"):
        store.decide(
            request.request_id,
            decision="approved",
            actor="late-operator",
        )
    assert ManagedApprovalQueue(store).lease("worker") == []


def test_approved_authorization_expires(tmp_path: Path) -> None:
    clock = _clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    request = store.submit(
        _submission(expires_at=(clock.current + timedelta(seconds=5)).isoformat())
    )
    store.decide(
        request.request_id,
        decision="approved",
        actor="operator",
    )
    assert store.is_approved(request.task_id, request.fingerprint)

    clock.advance(seconds=6)

    assert not store.is_approved(request.task_id, request.fingerprint)
    assert store.get(request.request_id).status == "expired"  # type: ignore[union-attr]


def test_interactive_transport_uses_injected_io_and_records_denial(
    tmp_path: Path,
) -> None:
    store = ApprovalStore(tmp_path / "approvals.db", clock=_clock())
    request = store.submit(_submission())
    answers = iter(["no", "unsafe in this environment"])
    output: list[str] = []
    transport = InteractiveApprovalTransport(
        store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    denied = transport.review(request.request_id, actor="cli-operator")

    assert denied.status == "denied"
    assert denied.decided_by == "cli-operator"
    assert denied.decision_note == "unsafe in this environment"
    assert any("deploy --dry-run" in line for line in output)


def test_http_transport_is_framework_neutral_and_idempotent(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "approvals.db", clock=_clock())
    transport = ApprovalHTTPTransport(store)
    submission = _submission().model_dump(mode="json")

    created = transport.handle(
        ApprovalHTTPRequest(
            method="POST",
            path="/approval-requests",
            json_body=submission,
        )
    )
    replayed = transport.handle(
        ApprovalHTTPRequest(
            method="POST",
            path="/approval-requests",
            json_body=submission,
        )
    )

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert isinstance(created.body, dict)
    assert isinstance(replayed.body, dict)
    assert replayed.body["request_id"] == created.body["request_id"]

    decision = transport.handle(
        ApprovalHTTPRequest(
            method="POST",
            path="/approval-decisions",
            json_body={
                "request_id": created.body["request_id"],
                "decision": "denied",
                "actor": "api-operator",
            },
        )
    )
    replayed_decision = transport.handle(
        ApprovalHTTPRequest(
            method="POST",
            path="/approval-decisions",
            json_body={
                "request_id": created.body["request_id"],
                "decision": "denied",
                "actor": "api-operator",
            },
        )
    )
    assert decision.status_code == 200
    assert replayed_decision.body == decision.body


def test_managed_queue_leases_exclusively_and_replays_ack(tmp_path: Path) -> None:
    clock = _clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    request = store.submit(_submission())
    first_queue = ManagedApprovalQueue(store)
    second_queue = ManagedApprovalQueue(store)

    first = first_queue.lease("consumer-a", lease_seconds=5)[0]
    assert second_queue.lease("consumer-b", lease_seconds=5) == []
    with pytest.raises(ValueError, match="undecided"):
        first_queue.ack(first.lease_id, consumer_id="consumer-a")

    clock.advance(seconds=6)
    second = second_queue.lease("consumer-b", lease_seconds=5)[0]
    assert second.request.request_id == request.request_id
    assert second.attempt == 2
    assert second.lease_id != first.lease_id
    with pytest.raises(KeyError):
        first_queue.ack(first.lease_id, consumer_id="consumer-a")

    store.decide(
        request.request_id,
        decision="denied",
        actor="consumer-b",
    )
    acknowledged = second_queue.ack(second.lease_id, consumer_id="consumer-b")
    replayed = second_queue.ack(second.lease_id, consumer_id="consumer-b")
    assert acknowledged.request.status == "denied"
    assert replayed == acknowledged
    assert second_queue.lease("consumer-c") == []
