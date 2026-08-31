"""Event, receipt, steering, and checkpoint contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from .base import StrictModel


class Checkpoint(StrictModel):
    checkpoint_id: str
    task_id: str
    session_id: str
    invocation_id: str
    branch_id: str
    parent_checkpoint_id: str | None = None
    workspace_id: str
    base_revision: str
    git_tree_hash: str
    ledger_version: int = Field(ge=1)
    ledger_hash: str
    compaction_id: str | None = None
    label: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
