"""Canonical append-only evidence ledger."""

from .archive import SealedSegment, seal_task_events
from .erasure import ErasureResult, erase_task_state
from .models import LedgerEvent
from .shadow import LedgerBackedEventStore
from .store import DuckDbLedgerStore

__all__ = [
    "DuckDbLedgerStore",
    "ErasureResult",
    "LedgerBackedEventStore",
    "LedgerEvent",
    "SealedSegment",
    "erase_task_state",
    "seal_task_events",
]
