"""Final ADK application assembly, including non-prompt telemetry plugins."""

from __future__ import annotations

import logging
import os

from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import App, EventsCompactionConfig, ResumabilityConfig

from harness.context import prefix_hash
from harness.memory.adk_plugin import VerifiedProjectMemoryPlugin
from harness.telemetry.adk_plugin import HarnessMetricsPlugin, pricing_from_env
from harness.tracing import HarnessTracePlugin, TraceContentMode

from .config import SETTINGS
from .learning import VerifiedTraceLearningPlugin
from .skills import _LEARNING_CONTROLLER
from .workflow import _EVENT_STORE, root_agent

LOGGER = logging.getLogger(__name__)

_METRICS_PLUGIN = HarnessMetricsPlugin(
    database=SETTINGS.state_root / "metrics.db",
    static_prefix_hash=prefix_hash(SETTINGS.static_prefix),
    static_prefix_tokens=len(SETTINGS.static_prefix) // 4,
    default_model=SETTINGS.model,
    default_task_id=SETTINGS.task_id_override,
    pricing=pricing_from_env(),
)
_MEMORY_PLUGIN = VerifiedProjectMemoryPlugin(
    workspace=SETTINGS.workspace,
    state_root=SETTINGS.state_root,
    project_root=SETTINGS.source_repository or SETTINGS.workspace,
    default_task_id=SETTINGS.task_id_override,
)


def _known_trace_secrets() -> list[str]:
    names = {
        name.strip()
        for name in os.getenv("ADK_CODING_REDACT_ENV_VARS", "").split(",")
        if name.strip()
    }
    names.update(
        {
            "GOOGLE_API_KEY",
            "ADK_CODING_REMOTE_TOKEN",
        }
    )
    return [os.environ[name] for name in sorted(names) if os.getenv(name)]


def _build_trace_plugin() -> HarnessTracePlugin | None:
    if SETTINGS.trace_mode == "off":
        return None
    try:
        return HarnessTracePlugin(
            database=SETTINGS.state_root / "traces.db",
            content_mode=(
                TraceContentMode.REDACTED_CONTENT
                if SETTINGS.trace_mode == "redacted"
                else TraceContentMode.METADATA_ONLY
            ),
            max_payload_bytes=SETTINGS.trace_max_content_bytes,
            known_secrets=_known_trace_secrets(),
            default_task_id=SETTINGS.task_id_override,
        )
    except Exception:
        LOGGER.exception("trace storage initialization failed; tracing is disabled")
        return None


_PLUGINS = [_METRICS_PLUGIN, _MEMORY_PLUGIN]
_TRACE_PLUGIN = _build_trace_plugin()
if _TRACE_PLUGIN is not None:
    _PLUGINS.insert(
        0,
        _TRACE_PLUGIN,
    )
if SETTINGS.learning_enabled and _TRACE_PLUGIN is not None:
    _PLUGINS.append(
        VerifiedTraceLearningPlugin(
            event_store=_EVENT_STORE,
            trace_store=_TRACE_PLUGIN.store,
            metrics_store=_METRICS_PLUGIN.store,
            controller=_LEARNING_CONTROLLER,
            minimum_support=SETTINGS.learning_min_support,
            default_task_id=SETTINGS.task_id_override,
        )
    )

app = App(
    name=SETTINGS.app_name,
    root_agent=root_agent,
    plugins=_PLUGINS,
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
