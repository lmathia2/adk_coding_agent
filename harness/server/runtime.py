"""Shared per-run ADK execution and durable public event coordination."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing, suppress
from dataclasses import dataclass
from typing import Any, Protocol

from google.adk import Runner
from google.adk.agents._streaming_mode import StreamingMode
from google.adk.agents.run_config import RunConfig
from google.genai import types

from harness.agent import (
    AgentSnapshot,
    ControlCommand,
    ControlReceipt,
    HarnessControlHooks,
    HarnessDescriptor,
    HarnessRegistry,
    ModelReadiness,
    PublicModelStatus,
    SteeringCommand,
)
from harness.config import HarnessComposition, RuntimeBindings
from harness.persistence import AdkServiceBundle
from harness.safety import SecretRedactor

from .adk_mapper import AdkAgUiNormalizer
from .protocol import (
    AgUiEvent,
    AgUiEventType,
    CancelTaskMessage,
    PauseTaskMessage,
    ServerEnvelope,
    StartTaskMessage,
    SteerTaskMessage,
)
from .registry import (
    DurableRunEventJournal,
    RunEventBroker,
    RunRecord,
    SqliteRunEventStore,
)


@dataclass(frozen=True, slots=True)
class PublicEventBatch:
    """One replay-stable group produced from a single runtime event."""

    source_key: str
    events: tuple[AgUiEvent, ...]


class RunExecution(Protocol):
    """One isolated harness assembly and ADK Runner."""

    def events(self) -> AsyncIterator[PublicEventBatch]: ...

    @property
    def coding_model_status(self) -> PublicModelStatus | None: ...

    async def steer(self, command: SteeringCommand) -> ControlReceipt: ...

    async def pause(self, command: ControlCommand) -> ControlReceipt: ...

    async def snapshot(self) -> AgentSnapshot: ...

    async def aclose(self) -> None: ...


class RunExecutionFactory(Protocol):
    @property
    def descriptor(self) -> HarnessDescriptor: ...

    @property
    def run_metadata(self) -> Mapping[str, str]: ...

    async def create(self, record: RunRecord) -> RunExecution: ...


class RunInitializationError(RuntimeError):
    """A run could not be assembled; its public detail is intentionally generic."""


class AdkRunExecution:
    """Thin ADK Runner adapter for one durable run."""

    def __init__(
        self,
        *,
        record: RunRecord,
        runner: Runner,
        app_name: str,
        session_service: Any,
        controls: HarnessControlHooks | None,
        max_llm_calls: int,
        coding_model_status: PublicModelStatus | None = None,
    ) -> None:
        self.record = record
        self.runner = runner
        self.app_name = app_name
        self.session_service = session_service
        self.controls = controls
        self.max_llm_calls = max_llm_calls
        self._coding_model_status = coding_model_status
        self._closed = False

    @property
    def coding_model_status(self) -> PublicModelStatus | None:
        return self._coding_model_status

    async def _ensure_session(self) -> None:
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.record.user_id,
            session_id=self.record.session_id,
        )
        if session is None:
            await self.session_service.create_session(
                app_name=self.app_name,
                user_id=self.record.user_id,
                session_id=self.record.session_id,
                state={
                    "invocation_id": self.record.invocation_id,
                    "run_id": self.record.run_id,
                    "task_id": self.record.run_id,
                    "thread_id": self.record.thread_id,
                },
            )

    async def events(self) -> AsyncIterator[PublicEventBatch]:
        await self._ensure_session()
        normalizer = AdkAgUiNormalizer(
            run_id=self.record.run_id,
            thread_id=self.record.thread_id,
        )
        message = types.Content(
            role="user",
            parts=[types.Part(text=self.record.input)],
        )
        generator = self.runner.run_async(
            user_id=self.record.user_id,
            session_id=self.record.session_id,
            invocation_id=self.record.invocation_id,
            new_message=message,
            state_delta={
                "invocation_id": self.record.invocation_id,
                "run_id": self.record.run_id,
                "task_id": self.record.run_id,
                "thread_id": self.record.thread_id,
            },
            run_config=RunConfig(
                streaming_mode=StreamingMode.SSE,
                max_llm_calls=self.max_llm_calls,
            ),
            yield_user_message=False,
        )
        async with aclosing(generator) as adk_events:
            responding_reported = False
            event_id_occurrences: dict[str, int] = {}
            async for event in adk_events:
                occurrence = event_id_occurrences.get(event.id, 0)
                event_id_occurrences[event.id] = occurrence + 1
                normalized = normalizer.push(event)
                if (
                    not responding_reported
                    and self.coding_model_status is not None
                    and event.model_version
                    and not event.error_code
                ):
                    responding_reported = True
                    normalized = (
                        AgUiEvent(
                            type=AgUiEventType.CUSTOM,
                            thread_id=self.record.thread_id,
                            run_id=self.record.run_id,
                            name="coding.model.status",
                            value=self.coding_model_status.model_copy(
                                update={"readiness": ModelReadiness.RESPONDING}
                            ).model_dump(mode="json"),
                        ),
                        *normalized,
                    )
                if normalized:
                    yield PublicEventBatch(
                        source_key=f"adk:{event.id}:{occurrence}",
                        events=normalized,
                    )
        trailing = normalizer.finish()
        if trailing:
            yield PublicEventBatch(
                source_key="server:normalizer-finish",
                events=trailing,
            )

    async def steer(self, command: SteeringCommand) -> ControlReceipt:
        if self.controls is None:
            return ControlReceipt(
                accepted=False,
                command_id=command.idempotency_key or f"steer:{command.run_id}",
                detail="selected harness does not expose steering controls",
            )
        return await self.controls.steer(command)

    async def pause(self, command: ControlCommand) -> ControlReceipt:
        if self.controls is None:
            return ControlReceipt(
                accepted=False,
                command_id=command.idempotency_key or f"pause:{command.run_id}",
                detail="selected harness does not expose pause controls",
            )
        return await self.controls.pause(command)

    async def snapshot(self) -> AgentSnapshot:
        if self.controls is None:
            return AgentSnapshot(run_id=self.record.run_id, sequence=0, state={})
        return await self.controls.snapshot(self.record.run_id)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.runner.close()


class AdkRunExecutionFactory:
    """Build one isolated harness assembly while sharing ADK persistence services."""

    def __init__(
        self,
        *,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
        registry: HarnessRegistry,
        services: AdkServiceBundle,
    ) -> None:
        self.composition = composition
        self.bindings = bindings
        self.registry = registry
        self.services = services
        implementation = composition.harness.implementation
        self._descriptor = registry.descriptor(implementation)

    @property
    def descriptor(self) -> HarnessDescriptor:
        return self._descriptor

    @property
    def run_metadata(self) -> Mapping[str, str]:
        configuration_root = self.bindings.configuration_root or self.bindings.workspace
        return {
            "coding.behavior_sha256": self.composition.resolved_behavior_sha256(
                configuration_root.expanduser().resolve()
            ),
            "coding.composition_sha256": self.composition.composition_sha256,
            "coding.harness_api_version": str(self.descriptor.api_version),
            "coding.harness_implementation": self.descriptor.implementation,
            "coding.workspace_identity": hashlib.sha256(
                self.bindings.workspace.expanduser().resolve().as_posix().encode()
            ).hexdigest(),
        }

    async def create(self, record: RunRecord) -> AdkRunExecution:
        state_root = self.bindings.state_root.expanduser().resolve() / "runs" / record.run_id
        run_bindings = self.bindings.model_copy(
            update={
                "invocation_id": record.invocation_id,
                "state_root": state_root,
                "task_id": record.run_id,
            }
        )
        assembly = await asyncio.to_thread(
            self.registry.build,
            self.composition,
            run_bindings,
        )
        runner = Runner(
            app=assembly.app,
            session_service=self.services.session_service,
            artifact_service=self.services.artifact_service,
            memory_service=self.services.memory_service,
            auto_create_session=False,
        )
        config = self.composition.harness.config
        max_iterations = int(getattr(getattr(config, "workflow", None), "max_iterations", 40))
        coding_model_name = assembly.build_info.models.get("coding")
        coding_model_provider = assembly.build_info.model_providers.get("coding")
        coding_model_status = (
            PublicModelStatus(
                provider=coding_model_provider,
                name=coding_model_name,
                readiness=ModelReadiness.ADAPTER_INITIALIZED,
            )
            if coding_model_name is not None and coding_model_provider is not None
            else None
        )
        return AdkRunExecution(
            record=record,
            runner=runner,
            app_name=assembly.app.name,
            session_service=self.services.session_service,
            controls=assembly.controls,
            max_llm_calls=max(1, min(5_000, max_iterations * 4)),
            coding_model_status=coding_model_status,
        )


@dataclass(slots=True)
class _ActiveRun:
    execution: RunExecution
    task: asyncio.Task[None]


class RunCoordinator:
    """Own run lifecycle, controls, durable publication, and gap-free attachment."""

    def __init__(
        self,
        *,
        store: SqliteRunEventStore,
        broker: RunEventBroker,
        execution_factory: RunExecutionFactory,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.store = store
        self.broker = broker
        self.journal = DurableRunEventJournal(store, broker)
        self.execution_factory = execution_factory
        self.redactor = redactor or SecretRedactor()
        self._active: dict[str, _ActiveRun] = {}
        self._workspace_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def descriptor(self) -> HarnessDescriptor:
        return self.execution_factory.descriptor

    @staticmethod
    def _default_thread_id(user_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"thread\0{user_id}\0{idempotency_key}".encode()).hexdigest()[:32]
        return f"thread-{digest}"

    def _owned_run(self, run_id: str, user_id: str) -> RunRecord:
        record = self.store.get_run(run_id)
        if record is None or record.user_id != user_id:
            raise KeyError(f"unknown run: {run_id}")
        return record

    async def start(
        self,
        message: StartTaskMessage,
        *,
        user_id: str,
    ) -> tuple[RunRecord, bool]:
        thread_id = message.thread_id or self._default_thread_id(user_id, message.idempotency_key)
        metadata = dict(message.metadata)
        metadata.update(self.execution_factory.run_metadata)
        record, created = self.store.create_run(
            request_id=message.request_id,
            idempotency_key=message.idempotency_key,
            thread_id=thread_id,
            user_id=user_id,
            input=message.input,
            metadata=metadata,
        )
        async with self._lifecycle_lock:
            if record.status == "running" and record.run_id not in self._active:
                restart_error = "server restarted during an active run; automatic rerun refused"
                self.journal.terminalize(
                    record.run_id,
                    status="failed",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_ERROR,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        code="server_restarted",
                        message=restart_error,
                    ),
                    source_key="server:run-error",
                    expected_status="running",
                    error=restart_error,
                )
                record = self.store.get_run(record.run_id)
                assert record is not None
                return record, created
            if record.status == "queued" and record.run_id not in self._active:
                try:
                    execution = await self.execution_factory.create(record)
                except Exception as error:
                    safe_error = self.redactor.redact_text(str(error))[:4_096]
                    self.journal.terminalize(
                        record.run_id,
                        status="failed",
                        event=AgUiEvent(
                            type=AgUiEventType.RUN_ERROR,
                            thread_id=record.thread_id,
                            run_id=record.run_id,
                            code="runtime_initialization_failed",
                            message=safe_error or "runtime initialization failed",
                        ),
                        source_key="server:run-error",
                        expected_status="queued",
                        error=safe_error,
                    )
                    raise RunInitializationError("task initialization failed") from error
                task = asyncio.create_task(
                    self._drive(record, execution),
                    name=f"agent-run:{record.run_id}",
                )
                self._active[record.run_id] = _ActiveRun(
                    execution=execution,
                    task=task,
                )
        return record, created

    async def _drive(self, record: RunRecord, execution: RunExecution) -> None:
        try:
            async with self._workspace_lock:
                self.store.update_status(
                    record.run_id,
                    "running",
                    expected_status="queued",
                )
                run_started = AgUiEvent(
                    type=AgUiEventType.RUN_STARTED,
                    thread_id=record.thread_id,
                    run_id=record.run_id,
                )
                if execution.coding_model_status is not None:
                    run_started = AgUiEvent(
                        type=AgUiEventType.RUN_STARTED,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        metadata={
                            "coding.model": self.redactor.redact(
                                execution.coding_model_status.model_dump(mode="json")
                            )
                        },
                    )
                self.journal.append_event(
                    record.run_id,
                    run_started,
                    source_key="server:run-started",
                )
                result: object | None = None
                runtime_error: str | None = None
                async for batch in execution.events():
                    durable_events = tuple(
                        event for event in batch.events if event.type != AgUiEventType.RUN_ERROR
                    )
                    source_keys = tuple(
                        f"{batch.source_key}:{index}" for index in range(len(durable_events))
                    )
                    if durable_events:
                        self.journal.append_events(
                            record.run_id,
                            durable_events,
                            source_keys=source_keys,
                        )
                    for event in batch.events:
                        if event.type == AgUiEventType.RUN_ERROR:
                            runtime_error = event.message or "ADK run failed"
                        elif (
                            event.type == AgUiEventType.CUSTOM
                            and event.name == "coding.workflow.output"
                        ):
                            result = event.value
                if runtime_error is not None:
                    self.journal.terminalize(
                        record.run_id,
                        status="failed",
                        event=AgUiEvent(
                            type=AgUiEventType.RUN_ERROR,
                            thread_id=record.thread_id,
                            run_id=record.run_id,
                            code="adk_run_failed",
                            message=runtime_error,
                        ),
                        source_key="server:run-error",
                        expected_status="running",
                        error=runtime_error,
                    )
                else:
                    self.journal.terminalize(
                        record.run_id,
                        status="completed",
                        event=AgUiEvent(
                            type=AgUiEventType.RUN_FINISHED,
                            thread_id=record.thread_id,
                            run_id=record.run_id,
                            result=result,
                        ),
                        source_key="server:run-finished",
                        expected_status="running",
                    )
        except asyncio.CancelledError:
            current = self.store.get_run(record.run_id)
            if current is not None and current.status in {"queued", "running"}:
                self.journal.append_event(
                    record.run_id,
                    AgUiEvent(
                        type=AgUiEventType.CUSTOM,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        name="coding.run.cancelled",
                        value={"cancellation": "best_effort"},
                    ),
                    source_key="server:run-cancelled",
                )
                self.journal.terminalize(
                    record.run_id,
                    status="cancelled",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_FINISHED,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        result={"status": "cancelled"},
                    ),
                    source_key="server:run-finished",
                    expected_status=("queued" if current.status == "queued" else "running"),
                )
            raise
        except Exception as error:
            safe_error = self.redactor.redact_text(str(error))[:4_096]
            current = self.store.get_run(record.run_id)
            if current is not None and current.status in {"queued", "running"}:
                self.journal.terminalize(
                    record.run_id,
                    status="failed",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_ERROR,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        code="runtime_failed",
                        message=safe_error or "runtime failed",
                    ),
                    source_key="server:run-error",
                    expected_status=("queued" if current.status == "queued" else "running"),
                    error=safe_error or "runtime failed",
                )
        finally:
            try:
                await execution.aclose()
            finally:
                self._active.pop(record.run_id, None)

    async def wait(self, run_id: str) -> RunRecord:
        active = self._active.get(run_id)
        if active is not None:
            with suppress(asyncio.CancelledError):
                await active.task
        record = self.store.get_run(run_id)
        if record is None:
            raise KeyError(f"unknown run: {run_id}")
        return record

    async def steer(
        self,
        message: SteerTaskMessage,
        *,
        user_id: str,
    ) -> ControlReceipt:
        self._owned_run(message.run_id, user_id)
        active = self._active.get(message.run_id)
        if active is None:
            return ControlReceipt(
                accepted=False,
                command_id=message.idempotency_key,
                detail="run is not active",
            )
        return await active.execution.steer(
            SteeringCommand(
                run_id=message.run_id,
                content=message.content,
                priority=message.priority,
                idempotency_key=message.idempotency_key,
            )
        )

    async def pause(
        self,
        message: PauseTaskMessage,
        *,
        user_id: str,
    ) -> ControlReceipt:
        self._owned_run(message.run_id, user_id)
        active = self._active.get(message.run_id)
        if active is None:
            return ControlReceipt(
                accepted=False,
                command_id=message.idempotency_key,
                detail="run is not active",
            )
        return await active.execution.pause(
            ControlCommand(
                run_id=message.run_id,
                idempotency_key=message.idempotency_key,
            )
        )

    async def cancel(
        self,
        message: CancelTaskMessage,
        *,
        user_id: str,
    ) -> ControlReceipt:
        record = self._owned_run(message.run_id, user_id)
        active = self._active.get(message.run_id)
        if active is None:
            if record.status == "queued":
                self.journal.terminalize(
                    record.run_id,
                    status="cancelled",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_FINISHED,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        result={"status": "cancelled"},
                    ),
                    source_key="server:run-finished",
                    expected_status="queued",
                )
                return ControlReceipt(
                    accepted=True,
                    command_id=message.idempotency_key,
                    detail="queued run cancelled",
                )
            return ControlReceipt(
                accepted=False,
                command_id=message.idempotency_key,
                detail=f"run is already {record.status}",
            )
        active.task.cancel()
        with suppress(asyncio.CancelledError):
            await active.task
        return ControlReceipt(
            accepted=True,
            command_id=message.idempotency_key,
            detail=(
                "cancellation accepted; an already-running synchronous subprocess "
                "may continue until its sandbox call returns"
            ),
        )

    async def attach(
        self,
        run_id: str,
        *,
        user_id: str,
        after_sequence: int = 0,
    ) -> AsyncIterator[ServerEnvelope]:
        """Replay a fixed snapshot, then stream live events without a race gap."""

        self._owned_run(run_id, user_id)
        subscription = self.broker.subscribe(run_id)
        cursor = after_sequence
        high_water: int | None = None
        try:
            while True:
                page = self.store.replay_page(
                    run_id,
                    after_sequence=cursor,
                    high_water_sequence=high_water,
                )
                if high_water is None:
                    high_water = page.high_water_sequence
                for envelope in page.events:
                    cursor = envelope.sequence
                    yield envelope
                if not page.has_more:
                    break
            current = self._owned_run(run_id, user_id)
            if current.status in {"completed", "cancelled", "failed"}:
                tail = self.store.replay(run_id, after_sequence=cursor)
                for envelope in tail:
                    cursor = envelope.sequence
                    yield envelope
                return
            async for envelope in subscription:
                if envelope.sequence <= cursor:
                    continue
                cursor = envelope.sequence
                yield envelope
                if envelope.event.type in {
                    AgUiEventType.RUN_FINISHED,
                    AgUiEventType.RUN_ERROR,
                }:
                    return
        finally:
            self.broker.unsubscribe(run_id, subscription)

    def acknowledge(self, run_id: str, *, user_id: str, through_sequence: int) -> None:
        self._owned_run(run_id, user_id)
        high_water = self.store.replay_page(
            run_id,
            after_sequence=through_sequence,
            limit=1,
        ).high_water_sequence
        if through_sequence > high_water:
            raise ValueError("acknowledgement exceeds the durable high-water sequence")

    async def aclose(self) -> None:
        active = tuple(self._active.values())
        for item in active:
            item.task.cancel()
        for item in active:
            with suppress(asyncio.CancelledError):
                await item.task


__all__ = [
    "AdkRunExecution",
    "AdkRunExecutionFactory",
    "PublicEventBatch",
    "RunCoordinator",
    "RunExecution",
    "RunExecutionFactory",
    "RunInitializationError",
]
