"""Provider adapters that preserve Google ADK as the model runtime."""

from .contracts import AdkModelProvider, AdkModelProviderRegistry
from .function_call_ids import (
    FunctionCallIdNormalizationError,
    FunctionCallIdNormalizingLlm,
    normalize_llm_request_function_call_ids,
    normalize_llm_response_function_call_ids,
)
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
    "FunctionCallIdNormalizationError",
    "FunctionCallIdNormalizingLlm",
    "GoogleAdkModelProvider",
    "OpenAiCompatibleModelProvider",
    "default_adk_model_provider_registry",
    "normalize_llm_request_function_call_ids",
    "normalize_llm_response_function_call_ids",
]
