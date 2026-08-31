from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from google.adk.models import Gemini
from pydantic import ValidationError

from app.agent.factory import default_harness_registry
from harness.ai import (
    ClosedAdkModelProviderRegistry,
    GoogleAdkModelProvider,
    OpenAiCodexModelProvider,
)
from harness.ai.codex_responses import CodexResponsesLlm
from harness.config import (
    ModelConfig,
    PiCodingConfig,
    RuntimeBindings,
    SecretRef,
    load_harness_composition,
)


def test_google_provider_preserves_native_gemini_adapter() -> None:
    model = GoogleAdkModelProvider().build_model(
        ModelConfig(provider="google_adk", name="gemini-test"),
        secrets={},
    )

    assert isinstance(model, Gemini)
    assert model.model == "gemini-test"


def test_openai_codex_provider_uses_runtime_oauth_and_never_api_key(tmp_path: Path) -> None:
    bindings = RuntimeBindings(
        workspace=tmp_path,
        state_root=tmp_path / "state" / "runs" / "run-1",
        auth_state_root=tmp_path / "state",
    )
    model = OpenAiCodexModelProvider().build_model(
        ModelConfig(
            provider="openai_codex",
            name="gpt-5.3-codex-spark",
            reasoning="low",
            client_version="0.147.0",
        ),
        secrets={},
        bindings=bindings,
    )

    codex_model = cast(CodexResponsesLlm, model)
    assert codex_model.model == "gpt-5.3-codex-spark"
    assert codex_model.reasoning_effort == "low"
    assert codex_model.client_version == "0.147.0"
    assert codex_model._credentials.store.path == (
        tmp_path / "state" / "auth" / "openai-codex.json"
    )

    with pytest.raises(ValidationError, match="cannot define base_url or api_key"):
        ModelConfig(
            provider="openai_codex",
            name="gpt-5.3-codex-spark",
            api_key=SecretRef(env="OPENAI_API_KEY"),
        )


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
            provider="custom_provider",
            name="local-coder",
            base_url=base_url,
            api_key=SecretRef(env="CUSTOM_API_KEY"),
        )


def test_factory_does_not_construct_unused_or_disabled_models(tmp_path: Path, monkeypatch) -> None:
    def unexpected_build(*args, **kwargs):
        raise AssertionError("disabled model was constructed")

    monkeypatch.setattr(OpenAiCodexModelProvider, "build_model", unexpected_build)
    providers = ClosedAdkModelProviderRegistry(
        (
            GoogleAdkModelProvider(),
            OpenAiCodexModelProvider(),
        )
    )
    registry = default_harness_registry(model_providers=providers)
    composition = load_harness_composition(config_models=registry.config_models())
    config = cast(PiCodingConfig, composition.harness.config)
    unreachable = ModelConfig(
        provider="openai_codex",
        name="unreachable-local-model",
    )
    agents = {
        **config.agents,
        "final_diff_reviewer": config.agents["final_diff_reviewer"].model_copy(
            update={"model": "unreachable"}
        ),
    }
    configured = composition.model_copy(
        update={
            "harness": composition.harness.model_copy(
                update={
                    "config": config.model_copy(
                        update={
                                "models": {
                                    "coding": config.models["coding"],
                                    "unreachable": unreachable,
                                },
                            "agents": agents,
                        }
                    )
                }
            )
        }
    )

    assembly = registry.build(
        configured,
        RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state"),
    )

    assert tuple(assembly.agents) == ("coding_worker",)


def test_factory_constructs_configured_reviewer_when_enabled(tmp_path: Path) -> None:
    providers = ClosedAdkModelProviderRegistry(
        (
            GoogleAdkModelProvider(),
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
                        update={
                            "models": {**config.models, "reviewer": ModelConfig(provider="google_adk", name="gemini-review")},
                                "workflow": config.workflow.model_copy(
                                    update={
                                        "nodes": {
                                            **config.workflow.nodes,
                                            "review": config.workflow.nodes[
                                                "review"
                                            ].model_copy(update={"enabled": True}),
                                        }
                                    }
                                ),
                                "reviewer": config.reviewer.model_copy(update={"enabled": True}),
                        }
                    )
                }
            )
        }
    )

    assembly = registry.build(
        configured,
        RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state"),
    )

    assert cast(Any, assembly.agents["final_diff_reviewer"]).model.model == ("gemini-review")


def test_provider_registry_is_closed_and_deterministic() -> None:
    registry = ClosedAdkModelProviderRegistry(
        (OpenAiCodexModelProvider(), GoogleAdkModelProvider())
    )

    assert registry.available() == ("google_adk", "openai_codex")
    with pytest.raises(LookupError, match="unknown model provider 'untrusted'"):
        registry.get("untrusted")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(GoogleAdkModelProvider())
