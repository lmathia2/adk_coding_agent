from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from pathlib import Path
from typing import cast

import pytest
from google.adk import Runner
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.events import Event
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent.factory import default_harness_registry
from harness.agent import (
    AgentSnapshot,
    ControlCommand,
    ControlReceipt,
    HarnessDescriptor,
    ModelReadiness,
    PublicModelStatus,
    RuntimeCapability,
    SteeringCommand,
)
from harness.ai import ClosedAdkModelProviderRegistry
from harness.config import (
    ModelConfig,
    PiCodingConfig,
    RuntimeBindings,
    SecretRef,
    load_harness_composition,
)
from harness.safety import SecretRedactor
from harness.server import (
    AgUiEvent,
    AgUiEventType,
    CancelTaskMessage,
    PauseTaskMessage,
    RunEventBroker,
    SqliteRunEventStore,
    StartTaskMessage,
    SteerTaskMessage,
)
from harness.server.registry import RunRecord
from harness.server.runtime import (
    AdkRunExecution,
    PublicEventBatch,
    RunCoordinator,
    RunExecution,
    RunInitializationError,
)


def _start(
    *,
    request_id: str = "request-1",
    idempotency_key: str = "start-1",
    input: str = "Fix the parser",
) -> StartTaskMessage:
    return StartTaskMessage(
        type="task.start",
        request_id=request_id,
        idempotency_key=idempotency_key,
        input=input,
    )


def _custom_event(record: RunRecord, value: object) -> AgUiEvent:
    return AgUiEvent(
        type=AgUiEventType.CUSTOM,
        run_id=record.run_id,
        thread_id=record.thread_id,
        name="coding.test.output",
        value=value,
    )


class _QueueExecution:
    def __init__(
        self,
        record: RunRecord,
        coding_model_status: PublicModelStatus | None = None,
    ) -> None:
        self.record = record
        self._coding_model_status = coding_model_status
        self.entered = asyncio.Event()
        self._items: asyncio.Queue[PublicEventBatch | BaseException | None] = asyncio.Queue()
        self.steering: list[SteeringCommand] = []
        self.pauses: list[ControlCommand] = []
        self.closed = False

    @property
    def coding_model_status(self) -> PublicModelStatus | None:
        return self._coding_model_status

    def emit(self, source_key: str, *events: AgUiEvent) -> None:
        self._items.put_nowait(PublicEventBatch(source_key=source_key, events=tuple(events)))

    def fail(self, error: BaseException) -> None:
        self._items.put_nowait(error)

    def finish(self) -> None:
        self._items.put_nowait(None)

    async def events(self) -> AsyncIterator[PublicEventBatch]:
        self.entered.set()
        while True:
            item = await self._items.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    async def steer(self, command: SteeringCommand) -> ControlReceipt:
        self.steering.append(command)
        return ControlReceipt(
            accepted=True,
            command_id=command.idempotency_key or "steer",
            detail="queued",
        )

    async def pause(self, command: ControlCommand) -> ControlReceipt:
        self.pauses.append(command)
        return ControlReceipt(
            accepted=True,
            command_id=command.idempotency_key or "pause",
            detail="safe-point requested",
        )

    async def snapshot(self) -> AgentSnapshot:
        return AgentSnapshot(
            run_id=self.record.run_id,
            sequence=0,
            state={"status": "running"},
        )

    async def aclose(self) -> None:
        self.closed = True


class _FakeExecutionFactory:
    descriptor = HarnessDescriptor(
        implementation="test_harness",
        display_name="Deterministic test harness",
        capabilities=frozenset(
            {
                RuntimeCapability.STREAMING,
                RuntimeCapability.STEERING,
                RuntimeCapability.PAUSE,
                RuntimeCapability.CANCEL,
                RuntimeCapability.REPLAY,
            }
        ),
    )

    def __init__(self) -> None:
        self.executions: dict[str, _QueueExecution] = {}
        self.created: list[RunRecord] = []
        self.initialization_error: BaseException | None = None
        self.coding_model_status = PublicModelStatus(
            provider="openai_compatible",
            name="local-test-model",
            readiness=ModelReadiness.ADAPTER_INITIALIZED,
        )

    @property
    def run_metadata(self) -> Mapping[str, str]:
        return {"coding.factory": "fake"}

    async def create(self, record: RunRecord) -> RunExecution:
        self.created.append(record)
        if self.initialization_error is not None:
            raise self.initialization_error
        execution = _QueueExecution(record, self.coding_model_status)
        self.executions[record.run_id] = execution
        return execution


def _coordinator(
    tmp_path: Path,
    *,
    factory: _FakeExecutionFactory | None = None,
    redactor: SecretRedactor | None = None,
) -> tuple[RunCoordinator, _FakeExecutionFactory]:
    resolved_factory = factory or _FakeExecutionFactory()
    return (
        RunCoordinator(
            store=SqliteRunEventStore(tmp_path / "runs.db"),
            broker=RunEventBroker(queue_capacity=32),
            execution_factory=resolved_factory,
            redactor=redactor,
        ),
        resolved_factory,
    )


async def _wait_entered(execution: _QueueExecution) -> None:
    await asyncio.wait_for(execution.entered.wait(), timeout=2)


@pytest.mark.asyncio
async def test_start_persists_lifecycle_and_exact_duplicate_is_not_reexecuted(
    tmp_path: Path,
) -> None:
    coordinator, factory = _coordinator(tmp_path)
    message = _start()

    created, was_created = await coordinator.start(message, user_id="user-1")
    execution = factory.executions[created.run_id]
    await _wait_entered(execution)
    repeated, repeated_created = await coordinator.start(
        message.model_copy(update={"request_id": "retry-request"}),
        user_id="user-1",
    )
    execution.emit("fake:one", _custom_event(created, {"answer": 42}))
    execution.finish()
    finished = await coordinator.wait(created.run_id)

    assert was_created is True
    assert repeated_created is False
    assert repeated.run_id == created.run_id
    assert len(factory.created) == 1
    assert finished.status == "completed"
    envelopes = coordinator.store.replay(created.run_id)
    assert [envelope.sequence for envelope in envelopes] == [1, 2, 3]
    assert [envelope.event.type for envelope in envelopes] == [
        AgUiEventType.RUN_STARTED,
        AgUiEventType.CUSTOM,
        AgUiEventType.RUN_FINISHED,
    ]
    assert envelopes[0].event.metadata == {
        "coding.model": {
            "role": "coding",
            "provider": "openai_compatible",
            "name": "local-test-model",
            "readiness": "adapter_initialized",
        }
    }
    assert all(envelope.durable for envelope in envelopes)
    assert execution.closed is True


@pytest.mark.asyncio
async def test_start_rejects_conflicting_idempotency_key_reuse(tmp_path: Path) -> None:
    coordinator, factory = _coordinator(tmp_path)
    record, _ = await coordinator.start(_start(), user_id="user-1")
    execution = factory.executions[record.run_id]
    await _wait_entered(execution)

    with pytest.raises(ValueError, match="idempotency key"):
        await coordinator.start(
            _start(input="Perform a different task"),
            user_id="user-1",
        )

    execution.finish()
    await coordinator.wait(record.run_id)


@pytest.mark.asyncio
async def test_active_runs_are_serialized_for_the_shared_workspace(
    tmp_path: Path,
) -> None:
    coordinator, factory = _coordinator(tmp_path)
    first, _ = await coordinator.start(_start(), user_id="user-1")
    second, _ = await coordinator.start(
        _start(request_id="request-2", idempotency_key="start-2"),
        user_id="user-1",
    )
    first_execution = factory.executions[first.run_id]
    second_execution = factory.executions[second.run_id]
    await _wait_entered(first_execution)

    assert second_execution.entered.is_set() is False
    first_execution.finish()
    await coordinator.wait(first.run_id)
    await _wait_entered(second_execution)
    second_execution.finish()
    await coordinator.wait(second.run_id)

    assert coordinator.store.get_run(first.run_id).status == "completed"  # type: ignore[union-attr]
    assert coordinator.store.get_run(second.run_id).status == "completed"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_attach_replays_then_streams_live_without_gaps_or_duplicates(
    tmp_path: Path,
) -> None:
    coordinator, factory = _coordinator(tmp_path)
    record, _ = await coordinator.start(_start(), user_id="user-1")
    execution = factory.executions[record.run_id]
    await _wait_entered(execution)

    async def collect() -> list[int]:
        return [
            envelope.sequence
            async for envelope in coordinator.attach(
                record.run_id,
                user_id="user-1",
                after_sequence=0,
            )
        ]

    collecting = asyncio.create_task(collect())
    await asyncio.sleep(0)
    execution.emit("fake:one", _custom_event(record, {"index": 1}))
    execution.emit("fake:two", _custom_event(record, {"index": 2}))
    execution.finish()

    sequences = await asyncio.wait_for(collecting, timeout=2)
    assert sequences == [1, 2, 3, 4]
    assert len(sequences) == len(set(sequences))


@pytest.mark.asyncio
async def test_steering_and_pause_delegate_exact_commands_to_active_execution(
    tmp_path: Path,
) -> None:
    coordinator, factory = _coordinator(tmp_path)
    record, _ = await coordinator.start(_start(), user_id="user-1")
    execution = factory.executions[record.run_id]
    await _wait_entered(execution)

    steer = await coordinator.steer(
        SteerTaskMessage(
            type="task.steer",
            run_id=record.run_id,
            content="Prioritize the parser edge case",
            priority=7,
            idempotency_key="steer-1",
        ),
        user_id="user-1",
    )
    pause = await coordinator.pause(
        PauseTaskMessage(
            type="task.pause",
            run_id=record.run_id,
            idempotency_key="pause-1",
        ),
        user_id="user-1",
    )

    assert steer.accepted is True
    assert execution.steering == [
        SteeringCommand(
            run_id=record.run_id,
            content="Prioritize the parser edge case",
            priority=7,
            idempotency_key="steer-1",
        )
    ]
    assert pause.accepted is True
    assert execution.pauses == [ControlCommand(run_id=record.run_id, idempotency_key="pause-1")]
    execution.finish()
    await coordinator.wait(record.run_id)


@pytest.mark.asyncio
async def test_cancellation_is_terminal_durable_and_closes_execution(
    tmp_path: Path,
) -> None:
    coordinator, factory = _coordinator(tmp_path)
    record, _ = await coordinator.start(_start(), user_id="user-1")
    execution = factory.executions[record.run_id]
    await _wait_entered(execution)

    receipt = await coordinator.cancel(
        CancelTaskMessage(
            type="task.cancel",
            run_id=record.run_id,
            idempotency_key="cancel-1",
        ),
        user_id="user-1",
    )
    finished = await coordinator.wait(record.run_id)
    events = [item.event for item in coordinator.store.replay(record.run_id)]

    assert receipt.accepted is True
    assert finished.status == "cancelled"
    assert [event.type for event in events] == [
        AgUiEventType.RUN_STARTED,
        AgUiEventType.CUSTOM,
        AgUiEventType.RUN_FINISHED,
    ]
    assert events[1].name == "coding.run.cancelled"
    assert events[-1].result == {"status": "cancelled"}
    assert execution.closed is True


@pytest.mark.asyncio
async def test_run_ownership_is_enforced_for_controls_attach_and_ack(
    tmp_path: Path,
) -> None:
    coordinator, factory = _coordinator(tmp_path)
    record, _ = await coordinator.start(_start(), user_id="owner")
    execution = factory.executions[record.run_id]
    await _wait_entered(execution)

    with pytest.raises(KeyError, match="unknown run"):
        await coordinator.steer(
            SteerTaskMessage(
                type="task.steer",
                run_id=record.run_id,
                content="steal",
                idempotency_key="steer-foreign",
            ),
            user_id="intruder",
        )
    with pytest.raises(KeyError, match="unknown run"):
        await anext(coordinator.attach(record.run_id, user_id="intruder"))
    with pytest.raises(KeyError, match="unknown run"):
        coordinator.acknowledge(record.run_id, user_id="intruder", through_sequence=0)
    with pytest.raises(KeyError, match="unknown run"):
        await coordinator.cancel(
            CancelTaskMessage(
                type="task.cancel",
                run_id=record.run_id,
                idempotency_key="cancel-foreign",
            ),
            user_id="intruder",
        )

    execution.finish()
    await coordinator.wait(record.run_id)


@pytest.mark.asyncio
async def test_acknowledgement_cannot_exceed_durable_high_water(tmp_path: Path) -> None:
    coordinator, factory = _coordinator(tmp_path)
    record, _ = await coordinator.start(_start(), user_id="user-1")
    execution = factory.executions[record.run_id]
    await _wait_entered(execution)
    execution.finish()
    await coordinator.wait(record.run_id)
    high_water = coordinator.store.replay_page(record.run_id).high_water_sequence

    coordinator.acknowledge(
        record.run_id,
        user_id="user-1",
        through_sequence=high_water,
    )
    with pytest.raises(ValueError, match="high-water"):
        coordinator.acknowledge(
            record.run_id,
            user_id="user-1",
            through_sequence=high_water + 1,
        )


@pytest.mark.asyncio
async def test_initialization_failure_is_redacted_and_durably_terminal(
    tmp_path: Path,
) -> None:
    factory = _FakeExecutionFactory()
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    factory.initialization_error = ValueError(f"provider rejected {secret}")
    coordinator, _ = _coordinator(
        tmp_path,
        factory=factory,
        redactor=SecretRedactor(known_secrets=(secret,)),
    )

    with pytest.raises(RunInitializationError, match="task initialization failed") as caught:
        await coordinator.start(_start(), user_id="user-1")
    assert secret not in str(caught.value)

    record = factory.created[0]
    stored = coordinator.store.get_run(record.run_id)
    events = coordinator.store.replay(record.run_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == "provider rejected <redacted>"
    assert len(events) == 1
    assert events[0].event.type == AgUiEventType.RUN_ERROR
    assert events[0].event.message == "provider rejected <redacted>"
    assert secret not in events[0].model_dump_json()


@pytest.mark.asyncio
async def test_run_started_model_status_is_redacted_before_persistence(
    tmp_path: Path,
) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    factory = _FakeExecutionFactory()
    factory.coding_model_status = PublicModelStatus(
        provider="openai_compatible",
        name=secret,
        readiness=ModelReadiness.ADAPTER_INITIALIZED,
    )
    coordinator, _ = _coordinator(
        tmp_path,
        factory=factory,
        redactor=SecretRedactor(known_secrets=(secret,)),
    )

    record, _ = await coordinator.start(_start(), user_id="user-1")
    execution = factory.executions[record.run_id]
    await _wait_entered(execution)
    execution.finish()
    await coordinator.wait(record.run_id)

    started = coordinator.store.replay(record.run_id)[0].event
    assert started.metadata == {
        "coding.model": {
            "role": "coding",
            "provider": "openai_compatible",
            "name": "<redacted>",
            "readiness": "adapter_initialized",
        }
    }
    assert secret not in started.model_dump_json()


@pytest.mark.asyncio
async def test_stale_running_run_is_atomically_failed_with_terminal_event(
    tmp_path: Path,
) -> None:
    factory = _FakeExecutionFactory()
    store = SqliteRunEventStore(tmp_path / "runs.db")
    message = _start()
    thread_id = RunCoordinator._default_thread_id("user-1", message.idempotency_key)
    stale, _ = store.create_run(
        request_id=message.request_id,
        idempotency_key=message.idempotency_key,
        thread_id=thread_id,
        user_id="user-1",
        input=message.input,
        metadata={"coding.factory": "fake"},
    )
    store.update_status(stale.run_id, "running", expected_status="queued")
    store.append_event(
        stale.run_id,
        AgUiEvent(
            type=AgUiEventType.RUN_STARTED,
            run_id=stale.run_id,
            thread_id=stale.thread_id,
        ),
        source_key="server:run-started",
    )
    coordinator = RunCoordinator(
        store=store,
        broker=RunEventBroker(queue_capacity=32),
        execution_factory=factory,
    )

    recovered, created = await coordinator.start(message, user_id="user-1")

    assert created is False
    assert recovered.status == "failed"
    assert recovered.error == ("server restarted during an active run; automatic rerun refused")
    events = store.replay(stale.run_id)
    assert [item.event.type for item in events] == [
        AgUiEventType.RUN_STARTED,
        AgUiEventType.RUN_ERROR,
    ]
    assert events[-1].event.code == "server_restarted"
    assert factory.created == []


@pytest.mark.asyncio
async def test_run_failure_is_redacted_and_durably_terminal(tmp_path: Path) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    coordinator, factory = _coordinator(
        tmp_path,
        redactor=SecretRedactor(known_secrets=(secret,)),
    )
    record, _ = await coordinator.start(_start(), user_id="user-1")
    execution = factory.executions[record.run_id]
    await _wait_entered(execution)
    execution.fail(RuntimeError(f"tool failed with {secret}"))

    finished = await coordinator.wait(record.run_id)
    events = coordinator.store.replay(record.run_id)
    assert finished.status == "failed"
    assert finished.error == "tool failed with <redacted>"
    assert events[-1].event.type == AgUiEventType.RUN_ERROR
    assert events[-1].event.message == "tool failed with <redacted>"
    assert execution.closed is True
    assert secret not in "".join(event.model_dump_json() for event in events)


@pytest.mark.asyncio
async def test_close_cancels_all_active_runs_and_is_idempotent(tmp_path: Path) -> None:
    coordinator, factory = _coordinator(tmp_path)
    first, _ = await coordinator.start(_start(), user_id="user-1")
    second, _ = await coordinator.start(
        _start(request_id="request-2", idempotency_key="start-2"),
        user_id="user-1",
    )
    await _wait_entered(factory.executions[first.run_id])

    await coordinator.aclose()
    await coordinator.aclose()

    assert coordinator.store.get_run(first.run_id).status == "cancelled"  # type: ignore[union-attr]
    assert coordinator.store.get_run(second.run_id).status == "cancelled"  # type: ignore[union-attr]
    assert all(execution.closed for execution in factory.executions.values())


class _CredentialFreeAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        yield Event(
            id="deterministic-event",
            invocation_id=ctx.invocation_id,
            author=self.name,
            model_version="test-local-model",
            content=types.Content(
                role="assistant",
                parts=[types.Part(text="credential-free output")],
            ),
        )


class _BlockingTestLlm(BaseLlm):
    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=json.dumps(
                            {
                                "status": "blocked",
                                "questions": ["credential-free test stop"],
                            },
                            sort_keys=True,
                        )
                    )
                ],
            )
        )


class _BlockingModelProvider:
    @property
    def provider_id(self) -> str:
        return "blocking_test"

    def build_model(
        self,
        config: ModelConfig,
        *,
        secrets: Mapping[str, SecretRef],
    ) -> BaseLlm:
        del secrets
        return _BlockingTestLlm(model=config.name)


@pytest.mark.asyncio
async def test_real_adk_runner_creates_session_and_maps_output_without_credentials(
    tmp_path: Path,
) -> None:
    store = SqliteRunEventStore(tmp_path / "record.db")
    record, _ = store.create_run(
        request_id="request-adk",
        idempotency_key="start-adk",
        thread_id="thread-adk",
        user_id="user-adk",
        input="Run deterministic workflow",
    )
    service = InMemorySessionService()
    app = App(name="credential_free_app", root_agent=_CredentialFreeAgent(name="worker"))
    runner = Runner(app=app, session_service=service, auto_create_session=False)
    execution = AdkRunExecution(
        record=record,
        runner=runner,
        app_name=app.name,
        session_service=service,
        controls=None,
        max_llm_calls=1,
        coding_model_status=PublicModelStatus(
            provider="openai_compatible",
            name="test-local-model",
            readiness=ModelReadiness.ADAPTER_INITIALIZED,
        ),
    )

    batches = [batch async for batch in execution.events()]
    session = await service.get_session(
        app_name=app.name,
        user_id=record.user_id,
        session_id=record.session_id,
    )
    await execution.aclose()

    assert session is not None
    assert session.state["run_id"] == record.run_id
    events = [event for batch in batches for event in batch.events]
    assert [event.type for event in events] == [
        AgUiEventType.CUSTOM,
        AgUiEventType.TEXT_MESSAGE_START,
        AgUiEventType.TEXT_MESSAGE_CONTENT,
        AgUiEventType.TEXT_MESSAGE_END,
    ]
    assert events[0].name == "coding.model.status"
    assert events[0].value == {
        "role": "coding",
        "provider": "openai_compatible",
        "name": "test-local-model",
        "readiness": "responding",
    }
    assert events[2].delta == "credential-free output"


@pytest.mark.asyncio
async def test_real_adk_runner_unwraps_content_for_pi_workflow_root(
    tmp_path: Path,
) -> None:
    providers = ClosedAdkModelProviderRegistry((_BlockingModelProvider(),))
    registry = default_harness_registry(model_providers=providers)
    composition = load_harness_composition(config_models=registry.config_models())
    config = cast(PiCodingConfig, composition.harness.config)
    models = {
        name: model.model_copy(update={"provider": "blocking_test"})
        for name, model in config.models.items()
    }
    configured = composition.model_copy(
        update={
            "harness": composition.harness.model_copy(
                update={"config": config.model_copy(update={"models": models})}
            )
        }
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    assembly = registry.build(
        configured,
        RuntimeBindings(
            workspace=workspace,
            state_root=state_root,
            task_id="task-adk-workflow-input",
        ),
    )
    service = InMemorySessionService()
    runner = Runner(
        app=assembly.app,
        session_service=service,
        auto_create_session=False,
    )
    store = SqliteRunEventStore(tmp_path / "record.db")
    record, _ = store.create_run(
        request_id="request-adk-workflow",
        idempotency_key="start-adk-workflow",
        thread_id="thread-adk-workflow",
        user_id="user-adk-workflow",
        input="Inspect the workspace",
    )
    execution = AdkRunExecution(
        record=record,
        runner=runner,
        app_name=assembly.app.name,
        session_service=service,
        controls=assembly.controls,
        max_llm_calls=4,
    )

    try:
        batches = [batch async for batch in execution.events()]
    finally:
        await execution.aclose()

    public_events = [event for batch in batches for event in batch.events]
    outputs = [
        event.value
        for event in public_events
        if event.type == AgUiEventType.CUSTOM and event.name == "coding.workflow.output"
    ]
    assert outputs
    workflow_output = json.loads(cast(str, outputs[-1]["output"]))
    assert workflow_output["status"] == "blocked"
    assert workflow_output["task_id"] == "task-adk-workflow-input"
