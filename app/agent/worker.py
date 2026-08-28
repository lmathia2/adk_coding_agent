"""Legacy Agents CLI exports built through the side-effect-free worker builder."""

from __future__ import annotations

from harness.ai import default_adk_model_provider_registry
from harness.config import ModelConfig

from .bootstrap import SETTINGS
from .builders import build_coding_worker

_MODEL_CONFIG = ModelConfig(provider="google_adk", name=SETTINGS.model)
_MODEL = default_adk_model_provider_registry().get("google_adk").build_model(
    _MODEL_CONFIG,
    secrets={},
)
_BUNDLE = build_coding_worker(SETTINGS, _MODEL)
coding_worker = _BUNDLE.agent
read = _BUNDLE.read
bash = _BUNDLE.bash
edit = _BUNDLE.edit
write = _BUNDLE.write

__all__ = ["bash", "coding_worker", "edit", "read", "write"]
