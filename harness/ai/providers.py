"""Closed ADK model-provider registry and built-in provider adapters."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from google.adk.models import BaseLlm, Gemini
from google.genai import types

from harness.config import ModelConfig, SecretRef

from .contracts import AdkModelProvider
from .function_call_ids import normalize_openai_compatible_model


class _ModelFactory(Protocol):
    def __call__(self, model: str, **kwargs: Any) -> BaseLlm: ...


class GoogleAdkModelProvider:
    """Build the native ADK Gemini adapter used by existing configurations."""

    @property
    def provider_id(self) -> str:
        return "google_adk"

    def build_model(
        self,
        config: ModelConfig,
        *,
        secrets: Mapping[str, SecretRef],
    ) -> BaseLlm:
        del secrets
        retry = config.retry
        return Gemini(
            model=config.name,
            retry_options=types.HttpRetryOptions(
                attempts=retry.attempts,
                exp_base=retry.exponential_base,
                initial_delay=retry.initial_delay_seconds,
                http_status_codes=list(retry.retry_statuses),
            ),
        )


def _load_litellm_model(model: str, **kwargs: Any) -> BaseLlm:
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover - depends on installed extras
        raise RuntimeError(
            "openai_compatible models require the project's local-models extra; "
            "run `uv sync --extra local-models`"
        ) from exc
    return LiteLlm(model=model, **kwargs)


class OpenAiCompatibleModelProvider:
    """Route an OpenAI-compatible endpoint through ADK's LiteLlm adapter."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        model_factory: _ModelFactory | None = None,
    ) -> None:
        self._environment = os.environ if environment is None else environment
        self._model_factory = model_factory or _load_litellm_model

    @property
    def provider_id(self) -> str:
        return "openai_compatible"

    def build_model(
        self,
        config: ModelConfig,
        *,
        secrets: Mapping[str, SecretRef],
    ) -> BaseLlm:
        if config.base_url is None:
            raise ValueError("openai_compatible models require base_url")
        secret_ref = secrets.get("api_key")
        if secret_ref is None or config.api_key != secret_ref:
            raise ValueError("openai_compatible models require their configured api_key ref")
        api_key = self._environment.get(secret_ref.env, "")
        if not api_key:
            raise ValueError(
                f"environment variable {secret_ref.env} is required for model authentication"
            )
        model_name = config.name if config.name.startswith("openai/") else f"openai/{config.name}"
        options: dict[str, Any] = {
            "api_base": config.base_url,
            "api_key": api_key,
            "drop_params": True,
            "num_retries": max(0, config.retry.attempts - 1),
        }
        if config.reasoning is not None:
            options["reasoning_effort"] = config.reasoning
        return normalize_openai_compatible_model(self._model_factory(model_name, **options))


class ClosedAdkModelProviderRegistry:
    """Deterministic provider registry; configuration cannot import arbitrary code."""

    def __init__(self, providers: Iterable[AdkModelProvider] = ()) -> None:
        self._providers: dict[str, AdkModelProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: AdkModelProvider) -> None:
        provider_id = provider.provider_id
        if provider_id in self._providers:
            raise ValueError(f"model provider {provider_id!r} is already registered")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> AdkModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            available = ", ".join(self.available()) or "<none>"
            raise LookupError(
                f"unknown model provider {provider_id!r}; available: {available}"
            ) from exc

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


def default_adk_model_provider_registry() -> ClosedAdkModelProviderRegistry:
    return ClosedAdkModelProviderRegistry(
        (GoogleAdkModelProvider(), OpenAiCompatibleModelProvider())
    )


__all__ = [
    "ClosedAdkModelProviderRegistry",
    "GoogleAdkModelProvider",
    "OpenAiCompatibleModelProvider",
    "default_adk_model_provider_registry",
]
