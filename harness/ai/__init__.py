"""Provider adapters that preserve Google ADK as the model runtime."""

from .contracts import AdkModelProvider, AdkModelProviderRegistry
from .providers import (
    ClosedAdkModelProviderRegistry,
    GoogleAdkModelProvider,
    OpenAiCompatibleModelProvider,
    default_adk_model_provider_registry,
)

__all__ = [
    "AdkModelProvider",
    "AdkModelProviderRegistry",
    "ClosedAdkModelProviderRegistry",
    "GoogleAdkModelProvider",
    "OpenAiCompatibleModelProvider",
    "default_adk_model_provider_registry",
]
