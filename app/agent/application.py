"""Final ADK application assembly, including non-prompt telemetry plugins."""

from __future__ import annotations

import hashlib
import os

from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App, EventsCompactionConfig, ResumabilityConfig

from harness.telemetry.adk_plugin import HarnessMetricsPlugin, pricing_from_env

from .config import SETTINGS
from .workflow import root_agent

_METRICS_PLUGIN = HarnessMetricsPlugin(
    database=SETTINGS.state_root / "metrics.db",
    static_prefix_hash=hashlib.sha256(SETTINGS.static_instruction.encode()).hexdigest(),
    static_prefix_tokens=len(SETTINGS.static_instruction) // 4,
    default_model=SETTINGS.model,
    default_task_id=SETTINGS.task_id_override,
    pricing=pricing_from_env(),
)

app = App(
    name=SETTINGS.app_name,
    root_agent=root_agent,
    plugins=[_METRICS_PLUGIN],
    context_cache_config=ContextCacheConfig(
        min_tokens=int(os.getenv("ADK_CODING_CACHE_MIN_TOKENS", "4096")),
        ttl_seconds=int(os.getenv("ADK_CODING_CACHE_TTL_SECONDS", "1800")),
        cache_intervals=int(os.getenv("ADK_CODING_CACHE_INTERVALS", "10")),
    ),
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=int(
            os.getenv("ADK_CODING_COMPACTION_INTERVAL", "8")
        ),
        overlap_size=int(os.getenv("ADK_CODING_COMPACTION_OVERLAP", "2")),
        token_threshold=int(
            os.getenv("ADK_CODING_ADK_COMPACT_TOKENS", "96000")
        ),
        event_retention_size=int(
            os.getenv("ADK_CODING_EVENT_RETENTION", "20")
        ),
    ),
    resumability_config=ResumabilityConfig(is_resumable=True),
)

__all__ = ["app"]
