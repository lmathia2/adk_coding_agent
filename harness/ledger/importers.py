"""Deterministic adapters from existing durable records."""

from __future__ import annotations

from datetime import datetime

from harness.models.checkpoint import Checkpoint
from harness.state.events import HarnessEvent
from harness.state.receipts import ToolReceipt
from harness.tracing.store import TraceSpan

from .models import EventStatus, LedgerEvent
from .store import DuckDbLedgerStore


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def import_harness_event(store: DuckDbLedgerStore, event: HarnessEvent) -> LedgerEvent:
    return store.append(
        task_id=event.task_id,
        source="harness_event",
        source_id=event.event_id,
        kind=event.kind,
        payload=event.payload,
        observed_at=event.timestamp,
        idempotency_key=event.idempotency_key or f"harness:{event.event_id}",
        recorded_at=event.timestamp,
    )


def import_trace_span(store: DuckDbLedgerStore, span: TraceSpan) -> LedgerEvent:
    phases: dict[str, EventStatus] = {
        "requested": "requested",
        "started": "started",
        "completed": "completed",
        "result": "completed",
        "failed": "failed",
        "blocked": "blocked",
        "timeout": "timeout",
        "open": "open",
    }
    status = phases.get(span.phase, "observed")
    return store.append(
        task_id=span.task_id,
        source="trace_span",
        source_id=span.span_id,
        kind=f"trace.{span.category}.{span.name}",
        status=status,
        observed_at=_time(span.timestamp),
        correlation_id=span.correlation_id,
        parent_event_id=span.parent_span_id,
        payload={"content_hash": span.content_hash, "data": span.payload(), "omitted_bytes": span.omitted_bytes},
        idempotency_key=f"trace:{span.idempotency_key}",
        recorded_at=_time(span.timestamp),
    )


def import_tool_receipt(store: DuckDbLedgerStore, receipt: ToolReceipt) -> LedgerEvent:
    effect = "applied" if receipt.status == "completed" and receipt.side_effect_key else "intended" if receipt.side_effect_key else "none"
    return store.append(
        task_id=receipt.task_id,
        source="tool_receipt",
        source_id=receipt.tool_call_id,
        kind=f"tool.{receipt.tool_name}",
        status=receipt.status,
        effect=effect,
        observed_at=_time(receipt.completed_at or receipt.started_at),
        correlation_id=receipt.invocation_id,
        payload=receipt.model_dump(mode="json"),
        idempotency_key=f"receipt:{receipt.tool_call_id}:{receipt.status}",
        recorded_at=_time(receipt.completed_at or receipt.started_at),
    )


def import_checkpoint(store: DuckDbLedgerStore, checkpoint: Checkpoint) -> LedgerEvent:
    return store.append(
        task_id=checkpoint.task_id,
        source="checkpoint",
        source_id=checkpoint.checkpoint_id,
        kind="checkpoint.created",
        status="completed",
        observed_at=checkpoint.created_at,
        payload=checkpoint.model_dump(mode="json"),
        idempotency_key=f"checkpoint:{checkpoint.checkpoint_id}",
        recorded_at=checkpoint.created_at,
    )
