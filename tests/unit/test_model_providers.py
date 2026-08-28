from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest
from google.adk.models import BaseLlm, Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import ValidationError

from app.agent.factory import default_harness_registry
from harness.ai import (
    ClosedAdkModelProviderRegistry,
    GoogleAdkModelProvider,
    OpenAiCompatibleModelProvider,
)
from harness.config import (
    ModelConfig,
    PiCodingConfig,
    RuntimeBindings,
    SecretRef,
    load_harness_composition,
)


class _TestLlm(BaseLlm):
    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        if False:
            yield LlmResponse()


class _CapturingFactory:
    def __init__(self) -> None:
        self.model: str | None = None
        self.options: dict[str, Any] = {}

    def __call__(self, model: str, **kwargs: Any) -> BaseLlm:
        self.model = model
        self.options = kwargs
        return _TestLlm(model=model)


def _magnitude_model() -> ModelConfig:
    return ModelConfig(
        provider="openai_compatible",
        name="local-coder",
        base_url="http://127.0.0.1:10100/inference/v1/",
        api_key=SecretRef(env="MAGNITUDE_API_KEY"),
        reasoning="high",
    )


def test_google_provider_preserves_native_gemini_adapter() -> None:
    model = GoogleAdkModelProvider().build_model(
        ModelConfig(provider="google_adk", name="gemini-test"),
        secrets={},
    )

    assert isinstance(model, Gemini)
    assert model.model == "gemini-test"


def test_openai_compatible_provider_builds_adk_model_without_network() -> None:
    capture = _CapturingFactory()
    config = _magnitude_model()
    provider = OpenAiCompatibleModelProvider(
        environment={"MAGNITUDE_API_KEY": "magnitude-local"},
        model_factory=capture,
    )

    model = provider.build_model(
        config,
        secrets={"api_key": cast(SecretRef, config.api_key)},
    )

    assert isinstance(model, BaseLlm)
    assert capture.model == "openai/local-coder"
    assert capture.options == {
        "api_base": "http://127.0.0.1:10100/inference/v1",
        "api_key": "magnitude-local",
        "drop_params": True,
        "num_retries": 2,
        "reasoning_effort": "high",
    }


def test_openai_compatible_secret_is_an_environment_reference() -> None:
    with pytest.raises(ValidationError, match="api_key"):
        ModelConfig.model_validate(
            {
                "provider": "openai_compatible",
                "name": "local-coder",
                "base_url": "http://127.0.0.1:10100/inference/v1",
                "api_key": "literal-secret-is-forbidden",
            }
        )

    config = _magnitude_model()
    with pytest.raises(ValueError, match="MAGNITUDE_API_KEY"):
        OpenAiCompatibleModelProvider(
            environment={},
            model_factory=_CapturingFactory(),
        ).build_model(
            config,
            secrets={"api_key": cast(SecretRef, config.api_key)},
        )
    assert "magnitude-local" not in config.model_dump_json()


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///tmp/socket",
        "http://user:password@127.0.0.1:10100/v1",
        "http://127.0.0.1:10100/v1?token=secret",
        "http://127.0.0.1:10100/v1#secret",
    ],
)
def test_model_base_url_rejects_non_http_or_embedded_credentials(base_url: str) -> None:
    with pytest.raises(ValidationError, match="base_url"):
        ModelConfig(
            provider="openai_compatible",
            name="local-coder",
            base_url=base_url,
            api_key=SecretRef(env="MAGNITUDE_API_KEY"),
        )


def test_harness_yaml_can_select_openai_compatible_adk_model(tmp_path: Path) -> None:
    capture = _CapturingFactory()
    providers = ClosedAdkModelProviderRegistry(
        (
            GoogleAdkModelProvider(),
            OpenAiCompatibleModelProvider(
                environment={"MAGNITUDE_API_KEY": "magnitude-local"},
                model_factory=capture,
            ),
        )
    )
    registry = default_harness_registry(model_providers=providers)
    composition = load_harness_composition(config_models=registry.config_models())
    config = cast(PiCodingConfig, composition.harness.config)
    configured = composition.model_copy(
        update={
            "harness": composition.harness.model_copy(
                update={
                    "config": config.model_copy(
                        update={"models": {**config.models, "coding": _magnitude_model()}}
                    )
                }
            )
        }
    )

    assembly = registry.build(
        configured,
        RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state"),
    )

    coding_worker = assembly.agents["coding_worker"]
    assert cast(Any, coding_worker).model.model == "openai/local-coder"
    assert assembly.build_info.models["coding"] == "local-coder"
    assert capture.options["api_base"] == "http://127.0.0.1:10100/inference/v1"


def test_magnitude_example_is_a_valid_portable_composition() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    composition = load_harness_composition(repository_root / "examples" / "magnitude.yaml")
    config = cast(PiCodingConfig, composition.harness.config)
    model = config.models["coding"]

    assert model.provider == "openai_compatible"
    assert model.base_url == "http://127.0.0.1:10100/inference/v1"
    assert model.api_key == SecretRef(env="MAGNITUDE_API_KEY")
    assert "magnitude-local" not in composition.canonical_json()


def test_provider_registry_is_closed_and_deterministic() -> None:
    registry = ClosedAdkModelProviderRegistry(
        (OpenAiCompatibleModelProvider(environment={}), GoogleAdkModelProvider())
    )

    assert registry.available() == ("google_adk", "openai_compatible")
    with pytest.raises(LookupError, match="unknown model provider 'untrusted'"):
        registry.get("untrusted")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(GoogleAdkModelProvider())
