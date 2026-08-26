"""Google ADK plugin that records provider model usage without prompt mutation."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from google.adk.plugins.base_plugin import BasePlugin

from .metrics import MetricsStore, ModelUsageSample


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD per one million tokens for optional local cost accounting."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    reasoning: float = 0.0


def _integer(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _attribute(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        result = getattr(value, name, None)
        if result is not None:
            return result
    return default


def usage_counts(response: Any) -> dict[str, int]:
    """Extract usage from ADK or google-genai response variants."""

    usage = _attribute(
        response,
        "usage_metadata",
        "usageMetadata",
        "usage",
        default=response,
    )
    input_tokens = _integer(
        _attribute(
            usage,
            "prompt_token_count",
            "promptTokenCount",
            "input_tokens",
            "inputTokens",
        )
    )
    output_tokens = _integer(
        _attribute(
            usage,
            "candidates_token_count",
            "candidatesTokenCount",
            "output_tokens",
            "outputTokens",
        )
    )
    cache_read_tokens = _integer(
        _attribute(
            usage,
            "cached_content_token_count",
            "cachedContentTokenCount",
            "cache_read_tokens",
            "cacheReadTokens",
        )
    )
    cache_write_tokens = _integer(
        _attribute(
            usage,
            "cache_write_tokens",
            "cacheWriteTokens",
        )
    )
    reasoning_tokens = _integer(
        _attribute(
            usage,
            "thoughts_token_count",
            "thoughtsTokenCount",
            "reasoning_tokens",
            "reasoningTokens",
        )
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def estimate_cost(counts: Mapping[str, int], pricing: ModelPricing) -> float:
    total = (
        counts.get("input_tokens", 0) * pricing.input
        + counts.get("output_tokens", 0) * pricing.output
        + counts.get("cache_read_tokens", 0) * pricing.cache_read
        + counts.get("cache_write_tokens", 0) * pricing.cache_write
        + counts.get("reasoning_tokens", 0) * pricing.reasoning
    )
    return max(total / 1_000_000, 0.0)


def pricing_from_env() -> dict[str, ModelPricing]:
    """Load a model-to-pricing map from JSON without embedding stale prices."""

    raw = os.getenv("ADK_CODING_MODEL_PRICING_JSON", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("ADK_CODING_MODEL_PRICING_JSON must be a JSON object")
    result: dict[str, ModelPricing] = {}
    for model, value in parsed.items():
        if not isinstance(value, dict):
            raise ValueError(f"pricing for {model!r} must be an object")
        result[str(model)] = ModelPricing(
            input=float(value.get("input", 0.0)),
            output=float(value.get("output", 0.0)),
            cache_read=float(value.get("cache_read", 0.0)),
            cache_write=float(value.get("cache_write", 0.0)),
            reasoning=float(value.get("reasoning", 0.0)),
        )
    return result


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    value = getattr(context, name, None)
    if value is not None:
        return value
    state = getattr(context, "state", None)
    if isinstance(state, Mapping):
        return state.get(name, default)
    return default


class HarnessMetricsPlugin(BasePlugin):
    """Record one metrics row for every completed model call."""

    def __init__(
        self,
        *,
        database: Path,
        static_prefix_hash: str,
        static_prefix_tokens: int,
        default_model: str,
        default_task_id: str | None = None,
        pricing: Mapping[str, ModelPricing] | None = None,
    ) -> None:
        super().__init__(name="harness_metrics")
        self.store = MetricsStore(database)
        self.static_prefix_hash = static_prefix_hash
        self.static_prefix_tokens = max(static_prefix_tokens, 0)
        self.default_model = default_model
        self.default_task_id = default_task_id
        self.pricing = dict(pricing or {})
        self._starts: dict[str, deque[tuple[float, str, str]]] = defaultdict(deque)
        self._lock = threading.RLock()

    @staticmethod
    def _invocation_id(context: Any) -> str:
        value = _context_value(context, "invocation_id")
        if value:
            return str(value)
        return uuid4().hex

    def _task_id(self, context: Any) -> str:
        value = _context_value(context, "task_id")
        if value:
            return str(value)
        if self.default_task_id:
            return self.default_task_id
        session_id = _context_value(context, "session_id")
        return str(session_id or "unknown")

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        invocation_id = self._invocation_id(callback_context)
        model = str(
            _attribute(
                llm_request,
                "model",
                "model_name",
                "modelName",
                default=self.default_model,
            )
        )
        task_id = self._task_id(callback_context)
        with self._lock:
            self._starts[invocation_id].append((time.monotonic(), model, task_id))
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: Any,
    ) -> None:
        invocation_id = self._invocation_id(callback_context)
        with self._lock:
            if self._starts[invocation_id]:
                started, model, task_id = self._starts[invocation_id].popleft()
            else:
                started = time.monotonic()
                model = self.default_model
                task_id = self._task_id(callback_context)
        counts = usage_counts(llm_response)
        dynamic_tokens = _integer(
            _context_value(callback_context, "dynamic_context_tokens_estimate", 0)
        )
        pricing = self.pricing.get(model, ModelPricing())
        self.store.record_model_usage(
            ModelUsageSample(
                task_id=task_id,
                invocation_id=invocation_id,
                model=model,
                static_prefix_hash=self.static_prefix_hash,
                static_prefix_tokens=self.static_prefix_tokens,
                dynamic_suffix_tokens=dynamic_tokens,
                input_tokens=counts["input_tokens"],
                output_tokens=counts["output_tokens"],
                cache_read_tokens=counts["cache_read_tokens"],
                cache_write_tokens=counts["cache_write_tokens"],
                reasoning_tokens=counts["reasoning_tokens"],
                cost_usd=estimate_cost(counts, pricing),
                latency_ms=max(int((time.monotonic() - started) * 1_000), 0),
            )
        )
        return None


__all__ = [
    "HarnessMetricsPlugin",
    "ModelPricing",
    "estimate_cost",
    "pricing_from_env",
    "usage_counts",
]
