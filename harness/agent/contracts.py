"""ADK-first harness assembly and shared runtime contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from google.adk.apps import App
from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.config import HarnessComposition, RuntimeBindings


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeCapability(StrEnum):
    STREAMING = "streaming"
    STEERING = "steering"
    PAUSE = "pause"
    CANCEL = "cancel"
    REPLAY = "replay"
    TOOL_EVENTS = "tool_events"
    STATE_SNAPSHOTS = "state_snapshots"
    APPROVALS = "approvals"
    ARTIFACTS = "artifacts"


class HarnessDescriptor(FrozenModel):
    implementation: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    api_version: int = Field(default=1, ge=1)
    display_name: str = Field(min_length=1, max_length=128)
    capabilities: frozenset[RuntimeCapability]
    protocol_versions: tuple[int, ...] = (1,)


class AgentRunRequest(FrozenModel):
    run_id: str = Field(min_length=1, max_length=256)
    thread_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    input: str = Field(min_length=1, max_length=50_000)
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentEventType(StrEnum):
    RUN_STARTED = "run_started"
    TEXT_DELTA = "text_delta"
    TOOL = "tool"
    STATE = "state"
    CHECKPOINT = "checkpoint"
    VERIFICATION = "verification"
    RUN_FINISHED = "run_finished"
    ERROR = "error"


class AgentEvent(FrozenModel):
    type: AgentEventType
    run_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=0)
    payload: dict[str, object] = Field(default_factory=dict)
    durable: bool = False


class SteeringCommand(FrozenModel):
    run_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    priority: int = Field(default=0, ge=-100, le=100)
    idempotency_key: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_content_bytes(self) -> SteeringCommand:
        if len(self.content.encode("utf-8")) > 4_096:
            raise ValueError("steering content exceeds 4096 UTF-8 bytes")
        return self


class ControlCommand(FrozenModel):
    run_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str | None = Field(default=None, max_length=256)


class ControlReceipt(FrozenModel):
    accepted: bool
    command_id: str = Field(min_length=1, max_length=256)
    detail: str | None = Field(default=None, max_length=2_048)


class AgentSnapshot(FrozenModel):
    run_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=0)
    state: dict[str, object]


@runtime_checkable
class HarnessControlHooks(Protocol):
    async def steer(self, command: SteeringCommand) -> ControlReceipt: ...

    async def pause(self, command: ControlCommand) -> ControlReceipt: ...

    async def cancel(self, command: ControlCommand) -> ControlReceipt: ...

    async def snapshot(self, run_id: str) -> AgentSnapshot: ...


@dataclass(frozen=True, slots=True)
class AdkHarnessAssembly:
    """A harness-specific ADK App plus optional controls; Runner stays shared."""

    descriptor: HarnessDescriptor
    app: App
    controls: HarnessControlHooks | None = None


@runtime_checkable
class AgentRuntime(Protocol):
    """Shared server runtime implemented once by the ADK Runner adapter."""

    @property
    def descriptor(self) -> HarnessDescriptor: ...

    def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]: ...

    async def steer(self, command: SteeringCommand) -> ControlReceipt: ...

    async def pause(self, command: ControlCommand) -> ControlReceipt: ...

    async def cancel(self, command: ControlCommand) -> ControlReceipt: ...

    async def snapshot(self, run_id: str) -> AgentSnapshot: ...


@runtime_checkable
class HarnessFactory(Protocol):
    """Build an ADK App assembly registered under a safe YAML key."""

    @property
    def descriptor(self) -> HarnessDescriptor: ...

    def build(
        self,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
    ) -> AdkHarnessAssembly: ...


__all__ = [
    "AdkHarnessAssembly",
    "AgentEvent",
    "AgentEventType",
    "AgentRunRequest",
    "AgentRuntime",
    "AgentSnapshot",
    "ControlCommand",
    "ControlReceipt",
    "HarnessControlHooks",
    "HarnessDescriptor",
    "HarnessFactory",
    "RuntimeCapability",
    "SteeringCommand",
]
