"""Deterministic tool-call ID normalization for OpenAI-compatible ADK models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field


class FunctionCallIdNormalizationError(ValueError):
    """Raised when tool-call history cannot be correlated unambiguously."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise FunctionCallIdNormalizationError(
            "function call arguments must be JSON serializable"
        ) from error


def _normalized_id(*, ordinal: int, name: str | None, arguments: object) -> str:
    payload = _canonical_json(
        {
            "arguments": arguments,
            "name": name or "",
            "ordinal": ordinal,
            "schema": "openai_function_call_id_v1",
        }
    )
    return "hc_" + hashlib.sha256(payload.encode()).hexdigest()[:32]


def _copy_contents(contents: Sequence[types.Content]) -> list[types.Content]:
    return [content.model_copy(deep=True) for content in contents]


def _normalize_history(
    contents: Sequence[types.Content],
) -> tuple[list[types.Content], int]:
    normalized = _copy_contents(contents)
    pending: dict[str, tuple[str, str | None]] = {}
    ordinal = 0

    for content in normalized:
        for part in content.parts or ():
            function_call = part.function_call
            if function_call is not None:
                raw_id = function_call.id
                if not raw_id:
                    raise FunctionCallIdNormalizationError("historical function call has no ID")
                if raw_id in pending:
                    raise FunctionCallIdNormalizationError(
                        f"ambiguous historical function call ID: {raw_id}"
                    )
                normalized_id = _normalized_id(
                    ordinal=ordinal,
                    name=function_call.name,
                    arguments=function_call.args or {},
                )
                ordinal += 1
                pending[raw_id] = (normalized_id, function_call.name)
                function_call.id = normalized_id
                continue

            function_response = part.function_response
            if function_response is None:
                continue
            raw_id = function_response.id
            if not raw_id or raw_id not in pending:
                raise FunctionCallIdNormalizationError(
                    f"orphan historical function response ID: {raw_id or '<missing>'}"
                )
            normalized_id, expected_name = pending.pop(raw_id)
            if (
                expected_name is not None
                and function_response.name is not None
                and function_response.name != expected_name
            ):
                raise FunctionCallIdNormalizationError(
                    "historical function response name does not match its call: "
                    f"{function_response.name!r} != {expected_name!r}"
                )
            function_response.id = normalized_id

    if pending:
        unresolved = ", ".join(sorted(pending))
        raise FunctionCallIdNormalizationError(
            f"historical function calls have no responses: {unresolved}"
        )
    return normalized, ordinal


def normalize_llm_request_function_call_ids(
    request: LlmRequest,
) -> tuple[LlmRequest, int]:
    """Return a request copy with globally unique, paired history IDs."""

    contents, next_ordinal = _normalize_history(request.contents)
    return request.model_copy(update={"contents": contents}), next_ordinal


def normalize_llm_response_function_call_ids(
    response: LlmResponse,
    *,
    first_ordinal: int,
) -> LlmResponse:
    """Return a response copy with deterministic IDs for a complete model turn."""

    if response.content is None:
        return response.model_copy(deep=True)
    content = response.content.model_copy(deep=True)
    normalized = response.model_copy(update={"content": content})
    if response.partial:
        return normalized

    ordinal = first_ordinal
    seen_raw_ids: set[str] = set()
    for part in content.parts or ():
        function_call = part.function_call
        if function_call is None:
            continue
        raw_id = function_call.id
        if raw_id and raw_id in seen_raw_ids:
            raise FunctionCallIdNormalizationError(f"ambiguous model function call ID: {raw_id}")
        if raw_id:
            seen_raw_ids.add(raw_id)
        function_call.id = _normalized_id(
            ordinal=ordinal,
            name=function_call.name,
            arguments=function_call.args or {},
        )
        ordinal += 1
    return normalized


class FunctionCallIdNormalizingLlm(BaseLlm):
    """ADK model decorator that confines provider-local tool-call IDs per turn."""

    delegate: BaseLlm = Field(exclude=True, repr=False)

    @property
    def capabilities(self) -> Any:
        return self.delegate.capabilities

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        normalized_request, next_ordinal = normalize_llm_request_function_call_ids(llm_request)
        async for response in self.delegate.generate_content_async(
            normalized_request,
            stream=stream,
        ):
            yield normalize_llm_response_function_call_ids(
                response,
                first_ordinal=next_ordinal,
            )


def normalize_openai_compatible_model(model: BaseLlm) -> BaseLlm:
    """Wrap an ADK model exactly once with OpenAI history normalization."""

    if isinstance(model, FunctionCallIdNormalizingLlm):
        return model
    return FunctionCallIdNormalizingLlm(model=model.model, delegate=model)


__all__ = [
    "FunctionCallIdNormalizationError",
    "FunctionCallIdNormalizingLlm",
    "normalize_llm_request_function_call_ids",
    "normalize_llm_response_function_call_ids",
    "normalize_openai_compatible_model",
]
