"""Stateful, bounded normalization from Google ADK events to AG-UI events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from google.adk.events import Event

from harness.safety import SecretRedactor

from .protocol import AgUiEvent, AgUiEventType

_MAX_PUBLIC_TEXT_CHARS = 32_000
_MAX_PUBLIC_VALUE_CHARS = 64_000
_DEFAULT_PUBLIC_STATE_KEYS = frozenset(
    {
        "checkpoint_id",
        "dynamic_context_tokens_estimate",
        "stable_instruction_sha256",
        "static_prefix_tokens_estimate",
        "task_route",
        "workspace_fingerprint",
    }
)


def _event_identity(event: Event) -> str:
    if event.id:
        return event.id
    payload = json.dumps(
        {
            "author": event.author,
            "invocation_id": event.invocation_id,
            "node_path": event.node_info.path,
            "timestamp": event.timestamp,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


class AdkAgUiNormalizer:
    """Reduce an ordered ADK event stream into a safe public AG-UI stream.

    ADK streaming text is stateful: partial chunks open one message and a later
    aggregate event closes it. Tool call/result identifiers are also remembered
    so a resumed ADK stream cannot duplicate or silently alter public events.
    """

    def __init__(
        self,
        *,
        run_id: str,
        thread_id: str,
        public_state_keys: Iterable[str] = _DEFAULT_PUBLIC_STATE_KEYS,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.run_id = run_id
        self.thread_id = thread_id
        self.public_state_keys = frozenset(public_state_keys)
        self.redactor = redactor or SecretRedactor()
        self._active_message_id: str | None = None
        self._active_author: str | None = None
        self._active_text = ""
        self._tool_calls: dict[str, tuple[str, str]] = {}
        self._tool_results: dict[str, str] = {}

    def _event(self, event_type: AgUiEventType, **fields: object) -> AgUiEvent:
        return AgUiEvent.model_validate(
            {
                "type": event_type,
                "thread_id": self.thread_id,
                "run_id": self.run_id,
                **fields,
            }
        )

    def _bounded_text(
        self,
        value: object,
        *,
        limit: int = _MAX_PUBLIC_TEXT_CHARS,
    ) -> str:
        text = self.redactor.redact_text(str(value))
        if len(text) <= limit:
            return text
        return text[:limit] + "…"

    def _public_value(self, value: object) -> object:
        """Make a recursively redacted, JSON-safe, bounded public copy."""

        safe = self.redactor.redact(value)
        try:
            encoded = json.dumps(
                safe,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            encoded = json.dumps(
                self.redactor.redact_text(str(safe)),
                ensure_ascii=False,
            )
        if len(encoded) > _MAX_PUBLIC_VALUE_CHARS:
            return {
                "preview": encoded[:_MAX_PUBLIC_VALUE_CHARS] + "…",
                "truncated": True,
            }
        return json.loads(encoded)

    def _close_message(self) -> list[AgUiEvent]:
        if self._active_message_id is None:
            return []
        closed = self._event(
            AgUiEventType.TEXT_MESSAGE_END,
            message_id=self._active_message_id,
        )
        self._active_message_id = None
        self._active_author = None
        self._active_text = ""
        return [closed]

    def _push_text(
        self,
        *,
        event: Event,
        event_id: str,
        part_index: int,
        text: object,
    ) -> list[AgUiEvent]:
        delta = self._bounded_text(text)
        if not delta:
            return []

        public: list[AgUiEvent] = []
        if self._active_message_id is None or self._active_author != event.author:
            public.extend(self._close_message())
            self._active_message_id = f"{event_id}:message:{part_index}"
            self._active_author = event.author
            self._active_text = ""
            public.append(
                self._event(
                    AgUiEventType.TEXT_MESSAGE_START,
                    message_id=self._active_message_id,
                    role="assistant",
                )
            )

        # ADK commonly emits partial deltas followed by the aggregate final text.
        # Emit only the suffix in that case so clients never display it twice.
        if event.partial is not True and self._active_text:
            if delta == self._active_text:
                delta = ""
            elif delta.startswith(self._active_text):
                delta = delta[len(self._active_text) :]

        if delta:
            assert self._active_message_id is not None
            public.append(
                self._event(
                    AgUiEventType.TEXT_MESSAGE_CONTENT,
                    message_id=self._active_message_id,
                    delta=delta,
                )
            )
            self._active_text = (self._active_text + delta)[-_MAX_PUBLIC_TEXT_CHARS:]
        if event.partial is not True:
            public.extend(self._close_message())
        return public

    def _push_tool_call(
        self,
        *,
        event_id: str,
        part_index: int,
        call: object,
    ) -> list[AgUiEvent]:
        call_id = str(getattr(call, "id", "") or f"{event_id}:tool:{part_index}")
        name = self._bounded_text(getattr(call, "name", "tool"), limit=128)
        arguments = json.dumps(
            self._public_value(getattr(call, "args", {}) or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prior = self._tool_calls.get(call_id)
        if prior is not None:
            if prior != (name, arguments):
                raise ValueError(f"ADK tool call id was reused with different content: {call_id}")
            return []
        self._tool_calls[call_id] = (name, arguments)
        return [
            self._event(
                AgUiEventType.TOOL_CALL_START,
                tool_call_id=call_id,
                tool_call_name=name,
            ),
            self._event(
                AgUiEventType.TOOL_CALL_ARGS,
                tool_call_id=call_id,
                delta=arguments,
            ),
            self._event(AgUiEventType.TOOL_CALL_END, tool_call_id=call_id),
        ]

    def _push_tool_result(
        self,
        *,
        event_id: str,
        part_index: int,
        response: object,
    ) -> list[AgUiEvent]:
        call_id = str(
            getattr(response, "id", "") or f"{event_id}:tool-result:{part_index}"
        )
        content = json.dumps(
            self._public_value(getattr(response, "response", {}) or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prior = self._tool_results.get(call_id)
        if prior is not None:
            if prior != content:
                raise ValueError(
                    f"ADK tool result id was reused with different content: {call_id}"
                )
            return []
        self._tool_results[call_id] = content
        return [
            self._event(
                AgUiEventType.TOOL_CALL_RESULT,
                message_id=f"{event_id}:tool-result-message:{part_index}",
                tool_call_id=call_id,
                content=content,
                role="tool",
            )
        ]

    def push(self, event: Event) -> tuple[AgUiEvent, ...]:
        """Normalize the next ADK event in stream order."""

        if event.author.strip().lower() == "user":
            return ()

        event_id = _event_identity(event)
        public: list[AgUiEvent] = []
        if event.error_code or event.error_message:
            public.extend(self._close_message())
            public.append(
                self._event(
                    AgUiEventType.RUN_ERROR,
                    code=self._bounded_text(event.error_code or "adk_error", limit=256),
                    message=self._bounded_text(event.error_message or "ADK run failed"),
                )
            )

        content = event.content
        if content is not None:
            for index, part in enumerate(content.parts or ()):  # type: ignore[union-attr]
                if getattr(part, "thought", False):
                    continue
                text = getattr(part, "text", None)
                if text:
                    public.extend(
                        self._push_text(
                            event=event,
                            event_id=event_id,
                            part_index=index,
                            text=text,
                        )
                    )
                call = getattr(part, "function_call", None)
                if call is not None:
                    public.extend(self._close_message())
                    public.extend(
                        self._push_tool_call(
                            event_id=event_id,
                            part_index=index,
                            call=call,
                        )
                    )
                response = getattr(part, "function_response", None)
                if response is not None:
                    public.extend(self._close_message())
                    public.extend(
                        self._push_tool_result(
                            event_id=event_id,
                            part_index=index,
                            response=response,
                        )
                    )

        state_delta = getattr(event.actions, "state_delta", None)
        if state_delta:
            allowed = {
                key: value
                for key, value in state_delta.items()
                if key in self.public_state_keys
            }
            value = self._public_value(allowed)
            if isinstance(value, dict) and value:
                delta = [
                    {
                        "op": "add",
                        "path": "/" + str(key).replace("~", "~0").replace("/", "~1"),
                        "value": item,
                    }
                    for key, item in sorted(value.items())
                ]
                public.append(self._event(AgUiEventType.STATE_DELTA, delta=delta))

        if event.output is not None:
            output = self._public_value(event.output)
            value = output if isinstance(output, dict) else {"output": output}
            public.append(
                self._event(
                    AgUiEventType.CUSTOM,
                    name="coding.workflow.output",
                    value=value,
                )
            )
        return tuple(public)

    def finish(self) -> tuple[AgUiEvent, ...]:
        """Close any message left open by an interrupted partial stream."""

        return tuple(self._close_message())


def map_adk_event(
    event: Event,
    *,
    run_id: str,
    thread_id: str,
) -> tuple[AgUiEvent, ...]:
    """Backward-compatible single-event normalization helper."""

    normalizer = AdkAgUiNormalizer(run_id=run_id, thread_id=thread_id)
    return normalizer.push(event) + normalizer.finish()


__all__ = ["AdkAgUiNormalizer", "map_adk_event"]
