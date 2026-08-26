from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

pytest.importorskip("google.adk")

from harness.telemetry.adk_plugin import (  # noqa: E402
    HarnessMetricsPlugin,
    ModelPricing,
    estimate_cost,
    usage_counts,
)


@dataclass
class _Usage:
    prompt_token_count: int = 1_000
    candidates_token_count: int = 200
    cached_content_token_count: int = 800
    thoughts_token_count: int = 50


@dataclass
class _Response:
    usage_metadata: _Usage


@dataclass
class _Request:
    model: str = "test-model"


@dataclass
class _Context:
    invocation_id: str = "invocation-1"
    session_id: str = "session-1"
    state: dict[str, object] | None = None


def test_usage_extraction_and_optional_pricing() -> None:
    counts = usage_counts(_Response(usage_metadata=_Usage()))
    assert counts == {
        "input_tokens": 1_000,
        "output_tokens": 200,
        "cache_read_tokens": 800,
        "cache_write_tokens": 0,
        "reasoning_tokens": 50,
    }
    cost = estimate_cost(
        counts,
        ModelPricing(input=1.0, output=2.0, cache_read=0.1, reasoning=3.0),
    )
    assert cost == pytest.approx((1_000 + 400 + 80 + 150) / 1_000_000)


def test_plugin_records_one_model_call(tmp_path) -> None:
    plugin = HarnessMetricsPlugin(
        database=tmp_path / "metrics.db",
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="test-model",
        default_task_id="task-1",
        pricing={"test-model": ModelPricing(input=1.0)},
    )
    context = _Context(
        state={
            "task_id": "task-1",
            "dynamic_context_tokens_estimate": 250,
        }
    )

    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=_Request(),
        )
    )
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(usage_metadata=_Usage()),
        )
    )

    summary = plugin.store.task_summary("task-1")
    assert summary["model_calls"] == 1
    assert summary["input_tokens"] == 1_000
    assert summary["cache_read_tokens"] == 800
    assert summary["prefix_versions"] == 1
