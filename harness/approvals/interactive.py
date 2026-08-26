"""Injectable interactive CLI transport for human approval decisions."""

from __future__ import annotations

from collections.abc import Callable

from .contracts import ApprovalDecision, ApprovalRequest
from .store import ApprovalStore


class InteractiveApprovalTransport:
    def __init__(
        self,
        store: ApprovalStore,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.store = store
        self.input_fn = input_fn
        self.output_fn = output_fn

    def review(
        self,
        request_id: str,
        *,
        actor: str,
    ) -> ApprovalRequest:
        request = self.store.get(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.status != "pending":
            return request

        self.output_fn(f"Operation: {request.operation}")
        self.output_fn(f"Risk: {request.risk}")
        self.output_fn(f"Reason: {request.reason}")
        while True:
            answer = self.input_fn("Approve this exact operation? [y/N]: ").strip().lower()
            if answer in {"y", "yes", "approve"}:
                verdict = "approved"
                break
            if answer in {"", "n", "no", "deny"}:
                verdict = "denied"
                break
            self.output_fn("Enter yes or no.")
        note = self.input_fn("Decision note (optional): ").strip() or None
        return self.store.submit_decision(
            ApprovalDecision(
                request_id=request.request_id,
                decision=verdict,
                actor=actor,
                note=note,
            )
        )

    def review_next(
        self,
        *,
        actor: str,
        task_id: str | None = None,
    ) -> ApprovalRequest | None:
        pending = self.store.list(task_id=task_id, status="pending")
        if not pending:
            return None
        return self.review(pending[-1].request_id, actor=actor)


__all__ = ["InteractiveApprovalTransport"]
