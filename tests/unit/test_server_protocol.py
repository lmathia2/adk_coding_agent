from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from harness.agent import HarnessDescriptor, RuntimeCapability
from harness.server import (
    PROTOCOL_VERSION,
    AckMessage,
    AgUiEvent,
    AgUiEventType,
    AttachTaskMessage,
    HelloMessage,
    ServerEnvelope,
    ServerHello,
    StartTaskMessage,
    SteerTaskMessage,
    parse_client_message,
)


def _descriptor() -> HarnessDescriptor:
    return HarnessDescriptor(
        implementation="pi_coding_v1",
        display_name="Pi coding harness",
        capabilities=frozenset(
            {
                RuntimeCapability.STREAMING,
                RuntimeCapability.STEERING,
                RuntimeCapability.REPLAY,
            }
        ),
        protocol_versions=(PROTOCOL_VERSION,),
    )


def test_parser_discriminates_json_and_mapping_client_messages() -> None:
    hello = parse_client_message(
        {"type": "client.hello", "protocol_versions": [1], "client_name": "test-tui"}
    )
    start = parse_client_message(
        json.dumps(
            {
                "type": "task.start",
                "protocol_version": 1,
                "request_id": "request-1",
                "idempotency_key": "start-1",
                "input": "Fix the parser",
            }
        )
    )

    assert isinstance(hello, HelloMessage)
    assert hello.protocol_versions == (1,)
    assert isinstance(start, StartTaskMessage)
    assert start.input == "Fix the parser"


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "unknown", "protocol_version": 1},
        {
            "type": "task.start",
            "protocol_version": 2,
            "request_id": "request-1",
            "idempotency_key": "start-1",
            "input": "Fix it",
        },
        {
            "type": "task.attach",
            "protocol_version": 1,
            "run_id": "run-1",
            "after_sequence": 0,
            "unexpected": True,
        },
    ],
)
def test_parser_fails_closed_for_unknown_messages_versions_and_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        parse_client_message(payload)


def test_replay_and_ack_cursors_are_nonnegative() -> None:
    attach = parse_client_message(
        {
            "type": "task.attach",
            "protocol_version": 1,
            "run_id": "run-1",
            "after_sequence": 17,
        }
    )
    ack = parse_client_message(
        {
            "type": "events.ack",
            "protocol_version": 1,
            "run_id": "run-1",
            "through_sequence": 17,
        }
    )

    assert isinstance(attach, AttachTaskMessage)
    assert isinstance(ack, AckMessage)
    assert attach.after_sequence == ack.through_sequence == 17

    with pytest.raises(ValidationError):
        parse_client_message(
            {
                "type": "task.attach",
                "protocol_version": 1,
                "run_id": "run-1",
                "after_sequence": -1,
            }
        )


def test_steering_limit_is_utf8_bytes_and_idempotency_is_required() -> None:
    message = parse_client_message(
        {
            "type": "task.steer",
            "protocol_version": 1,
            "run_id": "run-1",
            "content": "Keep the API stable",
            "idempotency_key": "steer-1",
        }
    )
    assert isinstance(message, SteerTaskMessage)

    with pytest.raises(ValidationError, match="4096 UTF-8 bytes"):
        parse_client_message(
            {
                "type": "task.steer",
                "protocol_version": 1,
                "run_id": "run-1",
                "content": "é" * 3_000,
                "idempotency_key": "steer-2",
            }
        )
    with pytest.raises(ValidationError, match="idempotency_key"):
        parse_client_message(
            {
                "type": "task.steer",
                "protocol_version": 1,
                "run_id": "run-1",
                "content": "Keep the API stable",
            }
        )


def test_ag_ui_text_and_tool_deltas_require_correlation_ids() -> None:
    text = AgUiEvent(
        type=AgUiEventType.TEXT_MESSAGE_CONTENT,
        run_id="run-1",
        message_id="message-1",
        delta="hello",
    )
    tool = AgUiEvent(
        type=AgUiEventType.TOOL_CALL_ARGS,
        run_id="run-1",
        tool_call_id="tool-1",
        delta='{"path":"README.md"}',
    )

    assert text.message_id == "message-1"
    assert tool.tool_call_id == "tool-1"

    with pytest.raises(ValidationError, match="message_id and delta"):
        AgUiEvent(type=AgUiEventType.TEXT_MESSAGE_CONTENT, delta="orphan")
    with pytest.raises(ValidationError, match="tool_call_id and delta"):
        AgUiEvent(type=AgUiEventType.TOOL_CALL_ARGS, delta="{}")


def test_coding_specific_events_use_a_namespaced_custom_event() -> None:
    checkpoint = AgUiEvent(
        type=AgUiEventType.CUSTOM,
        run_id="run-1",
        name="coding.checkpoint.created",
        value={"checkpoint_id": "checkpoint-1"},
    )
    assert checkpoint.name == "coding.checkpoint.created"

    with pytest.raises(ValidationError, match=r"coding\."):
        AgUiEvent(
            type=AgUiEventType.CUSTOM,
            name="checkpoint.created",
            value={"checkpoint_id": "checkpoint-1"},
        )
    with pytest.raises(ValidationError, match="require a value"):
        AgUiEvent(type=AgUiEventType.CUSTOM, name="coding.checkpoint.created")


def test_server_hello_negotiates_capabilities_without_tui_knowledge() -> None:
    hello = ServerHello(harness=_descriptor())

    assert hello.protocol_version == PROTOCOL_VERSION
    assert RuntimeCapability.STEERING in hello.harness.capabilities
    assert "tui" not in hello.model_dump_json()


def test_server_envelope_round_trips_normalized_ag_ui_event() -> None:
    envelope = ServerEnvelope(
        sequence=23,
        run_id="run-1",
        session_id="session-1",
        invocation_id="invocation-1",
        durable=True,
        event=AgUiEvent(
            type=AgUiEventType.STATE_SNAPSHOT,
            run_id="run-1",
            snapshot={"phase": "verify"},
        ),
    )

    restored = ServerEnvelope.model_validate_json(envelope.model_dump_json())

    assert restored == envelope
    assert restored.sequence == 23
    assert restored.event.type == AgUiEventType.STATE_SNAPSHOT
