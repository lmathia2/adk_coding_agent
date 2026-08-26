"""Event, receipt, steering, and checkpoint contracts."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from .base import StrictModel
from .tools import ToolStatus


class EventType(StrEnum):
    TASK_CREATED = "task_created"
    WORKSPACE_INITIALIZED = "workspace_initialized"
    CONTEXT_COMPILED = "context_compiled"
    AGENT_STEP_COMPLETED = "agent_step_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    FILE_READ = "file_read"
    FILE_CHANGED = "file_changed"
    INDEX_UPDATED = "index_updated"
    VERIFICATION_COMPLETED = "verification_completed"
    USER_STEERING_RECEIVED = "user_steering_received"
    COMPACTION_CREATED = "compaction_created"
    CHECKPOINT_CREATED = "checkpoint_created"
    TASK_BLOCKED = "task_blocked"
    TASK_FINISHED = "task_finished"


class HarnessEvent(StrictModel):
    event_id: str
    task_id: str
    sequence: int = Field(ge=0)
    event_type: EventType
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class ToolReceipt(StrictModel):
    tool_call_id: str
    task_id: str
    invocation_id: str
    tool_name: str
    normalized_arguments_hash: str
    status: ToolStatus
    result_hash: str | None = None
    artifact_uri: str | None = None
    side_effect_key: str | None = None
    started_at: float = Field(default_factory=time.time)
    completed_at: float | None = None


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


class SteeringMessage(StrictModel):
    message_id: str
    task_id: str
    content: str = Field(min_length=1)
    urgent: bool = True
    delivered: bool = False
    created_at: float = Field(default_factory=time.time)
