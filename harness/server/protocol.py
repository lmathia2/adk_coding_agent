"""Versioned WebSocket controls and AG-UI-compatible server events."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from harness.agent import HarnessDescriptor

PROTOCOL_VERSION = 1


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HelloMessage(FrozenModel):
    type: Literal["client.hello"]
    protocol_versions: tuple[int, ...] = Field(min_length=1)
    client_name: str = Field(min_length=1, max_length=128)


class StartTaskMessage(FrozenModel):
    type: Literal["task.start"]
    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
    input: str = Field(min_length=1, max_length=50_000)
    thread_id: str | None = Field(default=None, max_length=256)
    metadata: dict[str, str] = Field(default_factory=dict)


class AttachTaskMessage(FrozenModel):
    type: Literal["task.attach"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    after_sequence: int = Field(default=0, ge=0)


class SteerTaskMessage(FrozenModel):
    type: Literal["task.steer"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    priority: int = Field(default=0, ge=-100, le=100)
    idempotency_key: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_content_bytes(self) -> SteerTaskMessage:
        if len(self.content.encode("utf-8")) > 4_096:
            raise ValueError("steering content exceeds 4096 UTF-8 bytes")
        return self


class PauseTaskMessage(FrozenModel):
    type: Literal["task.pause"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)


class CancelTaskMessage(FrozenModel):
    type: Literal["task.cancel"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)


class AckMessage(FrozenModel):
    type: Literal["events.ack"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    through_sequence: int = Field(ge=0)


class PingMessage(FrozenModel):
    type: Literal["ping"]
    protocol_version: Literal[1] = 1
    nonce: str = Field(min_length=1, max_length=256)


ClientMessage = Annotated[
    HelloMessage
    | StartTaskMessage
    | AttachTaskMessage
    | SteerTaskMessage
    | PauseTaskMessage
    | CancelTaskMessage
    | AckMessage
    | PingMessage,
    Field(discriminator="type"),
]
_CLIENT_MESSAGE_ADAPTER = TypeAdapter(ClientMessage)


def parse_client_message(value: str | bytes | dict[str, object]) -> ClientMessage:
    if isinstance(value, (str, bytes)):
        return _CLIENT_MESSAGE_ADAPTER.validate_json(value)
    return _CLIENT_MESSAGE_ADAPTER.validate_python(value)


class AgUiEventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    CUSTOM = "CUSTOM"


class AgUiEvent(FrozenModel):
    """Normalized public event; raw provider and ADK event objects never cross it."""

    type: AgUiEventType
    thread_id: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    message_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=256)
    tool_name: str | None = Field(default=None, max_length=128)
    delta: str | None = None
    name: str | None = Field(default=None, max_length=256)
    value: dict[str, object] | None = None
    snapshot: dict[str, object] | None = None

    @model_validator(mode="after")
    def validate_event_shape(self) -> AgUiEvent:
        if self.type == AgUiEventType.TEXT_MESSAGE_CONTENT and (
            self.message_id is None or self.delta is None
        ):
            raise ValueError("text message content requires message_id and delta")
        if self.type == AgUiEventType.TOOL_CALL_ARGS and (
            self.tool_call_id is None or self.delta is None
        ):
            raise ValueError("tool call args require tool_call_id and delta")
        if self.type == AgUiEventType.STATE_SNAPSHOT and self.snapshot is None:
            raise ValueError("state snapshot events require snapshot")
        if self.type == AgUiEventType.CUSTOM:
            if not self.name or not self.name.startswith("coding."):
                raise ValueError("custom event names must use the coding. namespace")
            if self.value is None:
                raise ValueError("custom events require a value")
        return self


class ServerHello(FrozenModel):
    type: Literal["server.hello"] = "server.hello"
    protocol_version: Literal[1] = 1
    harness: HarnessDescriptor


class ServerEnvelope(FrozenModel):
    type: Literal["event"] = "event"
    protocol_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=256)
    session_id: str | None = Field(default=None, max_length=256)
    invocation_id: str | None = Field(default=None, max_length=256)
    durable: bool
    event: AgUiEvent


__all__ = [
    "PROTOCOL_VERSION",
    "AckMessage",
    "AgUiEvent",
    "AgUiEventType",
    "AttachTaskMessage",
    "CancelTaskMessage",
    "ClientMessage",
    "HelloMessage",
    "PauseTaskMessage",
    "PingMessage",
    "ServerEnvelope",
    "ServerHello",
    "StartTaskMessage",
    "SteerTaskMessage",
    "parse_client_message",
]
