"""Legacy Agents CLI exports built through the side-effect-free reviewer builder."""

from __future__ import annotations

from harness.ai import default_adk_model_provider_registry
from harness.config import ModelConfig

from .bootstrap import SETTINGS
from .builders import (
    FINAL_REVIEW_INSTRUCTION,
    build_final_diff_reviewer,
    build_review_input,
    parse_final_diff_review,
)

_MODEL_CONFIG = ModelConfig(provider="google_adk", name=SETTINGS.review_model)
_MODEL = default_adk_model_provider_registry().get("google_adk").build_model(
    _MODEL_CONFIG,
    secrets={},
)
_BUNDLE = build_final_diff_reviewer(_MODEL, model_name=_MODEL_CONFIG.name)
final_diff_reviewer = _BUNDLE.agent
FINAL_REVIEW_STATIC_PREFIX = _BUNDLE.static_prefix

__all__ = [
    "FINAL_REVIEW_INSTRUCTION",
    "FINAL_REVIEW_STATIC_PREFIX",
    "build_review_input",
    "final_diff_reviewer",
    "parse_final_diff_review",
]
