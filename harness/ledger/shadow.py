"""Compatibility adapter while operational projections migrate to the ledger."""

from __future__ import annotations

from typing import Any

from harness.state.event_store import EventStore
from harness.state.events import HarnessEvent

from .importers import import_harness_event
from .store import DuckDbLedgerStore


class LedgerBackedEventStore:
    """Keep the existing reducer API while every append enters the canonical ledger."""

    def __init__(self, operational: EventStore, ledger: DuckDbLedgerStore) -> None:
        self.operational = operational
        self.ledger = ledger

    def read(self, task_id: str, *, after_sequence: int = 0) -> list[HarnessEvent]:
        return self.operational.read(task_id, after_sequence=after_sequence)

    def append(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> HarnessEvent:
        event = self.operational.append(
            task_id, kind, payload, idempotency_key=idempotency_key
        )
        import_harness_event(self.ledger, event)
        return event
