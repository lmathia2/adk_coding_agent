from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from harness.ai import (
    FunctionCallIdNormalizationError,
    FunctionCallIdNormalizingLlm,
    normalize_llm_request_function_call_ids,
    normalize_llm_response_function_call_ids,
)


def _call(call_id: str | None, name: str, **arguments: object) -> types.Part:
    part = types.Part.from_function_call(name=name, args=arguments)
    assert part.function_call is not None
    part.function_call.id = call_id
    return part


def _response(call_id: str | None, name: str) -> types.Part:
    part = types.Part.from_function_response(name=name, response={"status": "ok"})
    assert part.function_response is not None
    part.function_response.id = call_id
    return part


def _ids(contents: list[types.Content]) -> tuple[list[str], list[str]]:
    calls: list[str] = []
    responses: list[str] = []
    for content in contents:
        for part in content.parts or ():
            if part.function_call is not None:
                calls.append(str(part.function_call.id))
            if part.function_response is not None:
                responses.append(str(part.function_response.id))
    return calls, responses


def test_history_normalization_scopes_reused_ids_and_preserves_pairs() -> None:
    request = LlmRequest(
        contents=[
            types.Content(
                role="model",
                parts=[
                    _call("call_icn_0", "bash", command="pwd"),
                    _call("call_icn_1", "read", path="README.md"),
                ],
            ),
            types.Content(
                role="user",
                parts=[
                    _response("call_icn_1", "read"),
                    _response("call_icn_0", "bash"),
                ],
            ),
            types.Content(
                role="model",
                parts=[_call("call_icn_0", "bash", command="git status --short")],
            ),
            types.Content(
                role="user",
                parts=[_response("call_icn_0", "bash")],
            ),
        ]
    )

    normalized, next_ordinal = normalize_llm_request_function_call_ids(request)

    call_ids, response_ids = _ids(normalized.contents)
    assert next_ordinal == 3
    assert len(set(call_ids)) == 3
    assert response_ids == [call_ids[1], call_ids[0], call_ids[2]]
    assert all(identifier.startswith("hc_") and len(identifier) == 35 for identifier in call_ids)
    assert _ids(request.contents) == (
        ["call_icn_0", "call_icn_1", "call_icn_0"],
        ["call_icn_1", "call_icn_0", "call_icn_0"],
    )

    repeated, repeated_ordinal = normalize_llm_request_function_call_ids(request)
    assert repeated_ordinal == next_ordinal
    assert _ids(repeated.contents) == (call_ids, response_ids)


@pytest.mark.parametrize(
    "contents, message",
    [
        (
            [types.Content(role="user", parts=[_response("missing", "bash")])],
            "orphan",
        ),
        (
            [
                types.Content(
                    role="model",
                    parts=[
                        _call("same", "bash", command="pwd"),
                        _call("same", "bash", command="ls"),
                    ],
                )
            ],
            "ambiguous",
        ),
        (
            [
                types.Content(role="model", parts=[_call("call", "bash", command="pwd")]),
                types.Content(role="user", parts=[_response("call", "read")]),
            ],
            "does not match",
        ),
    ],
)
def test_history_normalization_fails_closed_on_invalid_correlation(
    contents: list[types.Content],
    message: str,
) -> None:
    with pytest.raises(FunctionCallIdNormalizationError, match=message):
        normalize_llm_request_function_call_ids(LlmRequest(contents=contents))


def test_response_normalization_is_deterministic_and_preserves_partial_and_originals() -> None:
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(text="working"),
                _call("call_icn_0", "bash", command="pwd"),
                _call("call_icn_1", "read", path="README.md"),
            ],
        ),
        model_version="local-coder",
    )

    first = normalize_llm_response_function_call_ids(response, first_ordinal=4)
    second = normalize_llm_response_function_call_ids(response, first_ordinal=4)
    assert first == second
    first_ids, _ = _ids([first.content])  # type: ignore[list-item]
    assert len(set(first_ids)) == 2
    assert _ids([response.content])[0] == ["call_icn_0", "call_icn_1"]  # type: ignore[list-item]

    partial = LlmResponse(
        content=types.Content(role="model", parts=[_call("call_icn_0", "bash", command="pwd")]),
        partial=True,
    )
    normalized_partial = normalize_llm_response_function_call_ids(partial, first_ordinal=0)
    assert normalized_partial == partial
    assert normalized_partial is not partial
    assert normalized_partial.content is not partial.content


class _RecordingLlm(BaseLlm):
    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    @property
    def requests(self) -> tuple[LlmRequest, ...]:
        return tuple(self._requests)

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        assert stream
        self._requests.append(llm_request)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text="thinking")]),
            partial=True,
        )
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[_call("call_icn_0", "bash", command="git status --short")],
            ),
            model_version=self.model,
        )


@pytest.mark.asyncio
async def test_model_decorator_repairs_history_and_normalizes_new_calls_without_mutation() -> None:
    request = LlmRequest(
        contents=[
            types.Content(role="model", parts=[_call("call_icn_0", "bash", command="pwd")]),
            types.Content(role="user", parts=[_response("call_icn_0", "bash")]),
        ]
    )
    delegate = _RecordingLlm(model="openai/local-coder")
    model = FunctionCallIdNormalizingLlm(model=delegate.model, delegate=delegate)

    responses = [response async for response in model.generate_content_async(request, stream=True)]

    assert len(delegate.requests) == 1
    historical_calls, historical_responses = _ids(delegate.requests[0].contents)
    assert historical_calls == historical_responses
    new_calls, _ = _ids([responses[-1].content])  # type: ignore[list-item]
    assert new_calls[0] not in historical_calls
    assert responses[0].partial is True
    partial_content = responses[0].content
    assert partial_content is not None
    assert partial_content.parts is not None
    assert partial_content.parts[0].text == "thinking"
    assert _ids(request.contents) == (["call_icn_0"], ["call_icn_0"])
    assert "delegate" not in model.model_dump()


def test_response_normalization_rejects_duplicate_ids_in_one_model_turn() -> None:
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                _call("same", "bash", command="pwd"),
                _call("same", "read", path="README.md"),
            ],
        )
    )

    with pytest.raises(FunctionCallIdNormalizationError, match="ambiguous"):
        normalize_llm_response_function_call_ids(response, first_ordinal=0)


class _StrictReusingToolIdLlm(BaseLlm):
    _turn: int = PrivateAttr(default=0)
    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    @property
    def requests(self) -> tuple[LlmRequest, ...]:
        return tuple(self._requests)

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del stream
        calls, responses = _ids(llm_request.contents)
        assert len(calls) == len(set(calls))
        assert set(responses).issubset(calls)
        self._requests.append(llm_request)
        if self._turn < 2:
            self._turn += 1
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[_call("call_icn_0", "echo", value=f"turn-{self._turn}")],
                ),
                model_version=self.model,
            )
            return
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="finished")],
            ),
            model_version=self.model,
        )


@pytest.mark.asyncio
async def test_real_adk_tool_loop_survives_provider_ids_reused_across_turns() -> None:
    def echo(value: str) -> dict[str, str]:
        """Return the supplied value."""

        return {"value": value}

    delegate = _StrictReusingToolIdLlm(model="openai/local-coder")
    model = FunctionCallIdNormalizingLlm(model=delegate.model, delegate=delegate)
    agent = LlmAgent(name="worker", model=model, tools=[echo])
    sessions = InMemorySessionService()
    await sessions.create_session(
        app_name="tool_id_test",
        user_id="user-1",
        session_id="session-1",
    )
    runner = Runner(
        app_name="tool_id_test",
        agent=agent,
        session_service=sessions,
        auto_create_session=False,
    )

    try:
        events = [
            event
            async for event in runner.run_async(
                user_id="user-1",
                session_id="session-1",
                new_message=types.Content(
                    role="user", parts=[types.Part.from_text(text="use the tool twice")]
                ),
            )
        ]
    finally:
        await runner.close()

    assert len(delegate.requests) == 3
    persisted_calls, persisted_responses = _ids(
        [event.content for event in events if event.content is not None]
    )
    assert len(persisted_calls) == 2
    assert len(set(persisted_calls)) == 2
    assert persisted_responses == persisted_calls
    assert any(
        part.text == "finished"
        for event in events
        if event.content is not None
        for part in event.content.parts or ()
    )
