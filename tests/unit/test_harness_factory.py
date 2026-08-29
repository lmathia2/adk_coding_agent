from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import BaseLlm
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.agent.builders import build_coding_worker
from app.agent.config import settings_from_composition
from app.agent.factory import (
    PiCodingHarnessFactory,
    build_harness,
    default_harness_registry,
)
from harness.adk import SteeringPlugin
from harness.agent import (
    AdkHarnessAssembly,
    HarnessBuildInfo,
    HarnessDescriptor,
    HarnessFactory,
    HarnessRegistry,
    RuntimeCapability,
    SteeringCommand,
)
from harness.config import (
    HarnessComposition,
    PiCodingConfig,
    RuntimeBindings,
    load_harness_composition,
    parse_harness_composition,
)
from harness.server import PROTOCOL_VERSION, ServerHello
from harness.state import JsonlEventStore
from harness.tools.adk_adapter import AdkCodingTools


class _FakeHarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root_agent_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    model_name: str = Field(min_length=1, max_length=128)


class _FakeHarnessFactory:
    @property
    def descriptor(self) -> HarnessDescriptor:
        return HarnessDescriptor(
            implementation="fake_adk_v1",
            display_name="Fake ADK harness",
            capabilities=frozenset({RuntimeCapability.STREAMING}),
        )

    @property
    def config_model(self) -> type[BaseModel]:
        return _FakeHarnessConfig

    def build(
        self,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
    ) -> AdkHarnessAssembly:
        del bindings
        config = cast(_FakeHarnessConfig, composition.harness.config)
        root_agent = LlmAgent(name=config.root_agent_name, model=config.model_name)
        return AdkHarnessAssembly(
            descriptor=self.descriptor,
            app=App(name=composition.app.name, root_agent=root_agent),
            build_info=HarnessBuildInfo(
                behavior_sha256=composition.behavior_sha256,
                models={"root": config.model_name},
            ),
        )


def _fake_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "app": {"name": "swappable_harness"},
        "harness": {
            "implementation": "fake_adk_v1",
            "api_version": 1,
            "required_capabilities": ["streaming"],
            "config": {
                "root_agent_name": "fake_root",
                "model_name": "fake-model",
            },
        },
        "server": {
            "protocol": "ag_ui_websocket_v1",
        },
    }


def test_registered_harness_owns_strict_config_and_swaps_behind_same_protocol(
    tmp_path: Path,
) -> None:
    factory = _FakeHarnessFactory()
    registry = HarnessRegistry()
    registry.register(factory)
    composition = parse_harness_composition(
        _fake_payload(),
        config_models=registry.config_models(),
    )

    assembly = build_harness(
        composition,
        RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state"),
        registry=registry,
    )

    assert isinstance(factory, HarnessFactory)
    assert isinstance(composition.harness.config, _FakeHarnessConfig)
    assert assembly.descriptor.implementation == "fake_adk_v1"
    assert assembly.app.name == "swappable_harness"
    assert assembly.app.root_agent is not None
    assert assembly.app.root_agent.name == "fake_root"
    assert assembly.build_info.model_providers == {}
    assert composition.server.protocol == "ag_ui_websocket_v1"
    assert ServerHello(harness=assembly.descriptor).protocol_version == PROTOCOL_VERSION


def test_registered_harness_config_rejects_unknown_fields() -> None:
    registry = HarnessRegistry()
    registry.register(_FakeHarnessFactory())
    payload = _fake_payload()
    harness = cast(dict[str, object], payload["harness"])
    config = cast(dict[str, object], harness["config"])
    config["unexpected"] = True

    with pytest.raises(ValidationError, match="unexpected"):
        parse_harness_composition(payload, config_models=registry.config_models())


def test_registry_rejects_composition_validated_for_a_different_factory(
    tmp_path: Path,
) -> None:
    registry = HarnessRegistry()
    registry.register(_FakeHarnessFactory())
    pi_composition = load_harness_composition()
    mismatched = pi_composition.model_copy(
        update={
            "harness": pi_composition.harness.model_copy(
                update={
                    "implementation": "fake_adk_v1",
                    "required_capabilities": ("streaming",),
                }
            )
        }
    )

    assert isinstance(mismatched.harness.config, PiCodingConfig)
    with pytest.raises(TypeError, match="_FakeHarnessConfig"):
        registry.build(
            mismatched,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state"),
        )


def test_default_pi_factory_builds_from_composition_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ADK_CODING_MODEL", "environment-model-must-not-win")
    environment_state = tmp_path / "environment-selected-state"
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(environment_state))

    assert not environment_state.exists()

    registry = default_harness_registry()
    assert isinstance(PiCodingHarnessFactory(), HarnessFactory)
    assert registry.config_models() == {"pi_coding_v1": PiCodingConfig}
    composition = load_harness_composition(config_models=registry.config_models())
    pi_config = cast(PiCodingConfig, composition.harness.config)
    configured_model = "composition-selected-model"
    models = dict(pi_config.models)
    models["coding"] = models["coding"].model_copy(update={"name": configured_model})
    configured = composition.model_copy(
        update={
            "app": composition.app.model_copy(update={"name": "configured_app"}),
            "harness": composition.harness.model_copy(
                update={
                    "config": pi_config.model_copy(
                        update={
                            "models": models,
                            "workflow": pi_config.workflow.model_copy(update={"max_iterations": 7}),
                            "context": pi_config.context.model_copy(
                                update={"compact_at_tokens": 12_345}
                            ),
                        }
                    )
                }
            ),
        }
    )
    bindings = RuntimeBindings(
        workspace=tmp_path / "workspace",
        state_root=tmp_path / "bound-state",
        worker_id="test-worker",
    )
    bindings.workspace.mkdir()

    settings = settings_from_composition(configured, bindings)
    assembly = registry.build(configured, bindings)

    assert settings.app_name == "configured_app"
    assert settings.model == configured_model
    assert settings.max_iterations == 7
    assert settings.compact_at_tokens == 12_345
    assert settings.state_root == bindings.state_root.resolve()
    assert isinstance(assembly, AdkHarnessAssembly)
    assert isinstance(assembly.app, App)
    assert assembly.descriptor.implementation == "pi_coding_v1"
    assert assembly.app.name == "configured_app"
    assert assembly.build_info.behavior_sha256 == configured.behavior_sha256
    assert assembly.build_info.models["coding"] == configured_model
    assert assembly.build_info.model_providers["coding"] == "google_adk"
    assert assembly.build_info.tool_names == ("read", "bash", "edit", "write")
    assert assembly.build_info.max_iterations == 7
    assert assembly.build_info.compact_at_tokens == 12_345
    assert "environment-model-must-not-win" not in assembly.build_info.models.values()
    assert registry.available() == ("pi_coding_v1",)
    assert not environment_state.exists()


def test_pi_factory_executes_file_prompt_and_agent_model_bindings(
    tmp_path: Path,
) -> None:
    registry = default_harness_registry()
    payload = load_harness_composition(
        config_models=registry.config_models()
    ).model_dump(mode="python")
    config = cast(dict[str, Any], cast(dict[str, Any], payload["harness"])["config"])
    models = cast(dict[str, Any], config["models"])
    agents = cast(dict[str, Any], config["agents"])
    config["models"] = {
        "primary": models["coding"],
        "critic": models["reviewer"],
    }
    agents["coding_worker"]["model"] = "primary"
    agents["final_diff_reviewer"]["model"] = "critic"
    prompt_root = tmp_path / "configuration"
    prompt = prompt_root / "prompts" / "worker.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text(
        "Return the required AgentStep JSON and keep changes extremely small.",
        encoding="utf-8",
    )
    agents["coding_worker"]["prompt"] = {
        "source": "file",
        "path": Path("prompts/worker.md"),
    }
    configured = parse_harness_composition(
        payload,
        config_models=registry.config_models(),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assembly = registry.build(
        configured,
        RuntimeBindings(
            workspace=workspace,
            state_root=tmp_path / "state",
            configuration_root=prompt_root,
        ),
    )

    worker = cast(Any, assembly.agents["coding_worker"])
    assert worker.static_instruction.startswith("Return the required AgentStep JSON")
    assert assembly.build_info.models == {
        "critic": models["reviewer"]["name"],
        "primary": models["coding"]["name"],
    }


def test_composition_loads_project_instructions_only_after_explicit_trust(
    tmp_path: Path,
) -> None:
    registry = default_harness_registry()
    composition = load_harness_composition(config_models=registry.config_models())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "TRUSTED PROJECT INSTRUCTION",
        encoding="utf-8",
    )

    untrusted = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=tmp_path / "state-untrusted"),
    )
    trusted = settings_from_composition(
        composition,
        RuntimeBindings(
            workspace=workspace,
            state_root=tmp_path / "state-trusted",
            project_trusted=True,
        ),
    )

    assert not untrusted.project_trusted
    assert "TRUSTED PROJECT INSTRUCTION" not in untrusted.static_instruction
    assert untrusted.skill_roots == ()
    assert trusted.project_trusted
    assert "TRUSTED PROJECT INSTRUCTION" in trusted.static_instruction
    assert trusted.skill_roots == (workspace / ".agents" / "skills",)


@pytest.mark.asyncio
async def test_model_tool_input_error_is_recoverable_instead_of_crashing_adk(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    composition = load_harness_composition()
    settings = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=tmp_path / "state"),
    )
    worker = build_coding_worker(settings, cast(BaseLlm, "test-model"))

    result = await worker.read(str(tmp_path / "outside-verifier.py"))

    assert result["status"] == "error"
    assert result["ui_details"] == {
        "error_type": "ValueError",
        "recoverable": True,
    }
    assert "path escapes workspace" in result["model_text"]


@pytest.mark.asyncio
async def test_model_tools_do_not_block_the_server_event_loop(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    composition = load_harness_composition()
    settings = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=tmp_path / "state"),
    )

    def slow_bash(**_: Any) -> dict[str, Any]:
        time.sleep(0.08)
        return {"status": "ok", "model_text": "done"}

    def unavailable(**_: Any) -> dict[str, Any]:
        return {"status": "error", "model_text": "unused"}

    tools = AdkCodingTools(
        read=unavailable,
        bash=slow_bash,
        edit=unavailable,
        write=unavailable,
    )
    worker = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=tools,
    )

    started = time.monotonic()
    task = asyncio.create_task(worker.bash("python3 -m py_compile target.py"))
    await asyncio.sleep(0.02)
    event_loop_delay = time.monotonic() - started
    result = await task

    assert event_loop_delay < 0.06
    assert result["status"] == "ok"


def test_importing_pi_factory_does_not_build_environment_singletons(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    environment_state = tmp_path / "environment-selected-state"
    environment = os.environ.copy()
    environment.pop("GOOGLE_API_KEY", None)
    environment.pop("GEMINI_API_KEY", None)
    environment["ADK_CODING_WORKSPACE"] = str(tmp_path)
    environment["ADK_CODING_STATE_DIR"] = str(environment_state)
    environment["PYTHONPATH"] = str(repository_root)

    completed = subprocess.run(
        [sys.executable, "-c", "import app.agent.factory"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert not environment_state.exists()


def test_pi_factory_builds_are_isolated(tmp_path: Path) -> None:
    registry = default_harness_registry()
    composition = load_harness_composition(config_models=registry.config_models())
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_workspace = first_root / "workspace"
    second_workspace = second_root / "workspace"
    first_workspace.mkdir(parents=True)
    second_workspace.mkdir(parents=True)

    first = registry.build(
        composition,
        RuntimeBindings(workspace=first_workspace, state_root=first_root / "state"),
    )
    second = registry.build(
        composition,
        RuntimeBindings(workspace=second_workspace, state_root=second_root / "state"),
    )

    assert first is not second
    assert first.app is not second.app
    assert first.app.root_agent is not second.app.root_agent
    assert first.controls is not second.controls


def test_pi_factory_rejects_ignored_workflow_edge_changes(tmp_path: Path) -> None:
    registry = default_harness_registry()
    composition = load_harness_composition(config_models=registry.config_models())
    config = cast(PiCodingConfig, composition.harness.config)
    nodes = dict(config.workflow.nodes)
    route = nodes["route"]
    route_payload = route.model_dump(mode="python")
    routes = cast(dict[str, str], route_payload["routes"])
    nodes["route"] = route.model_copy(update={"routes": {**routes, "verify": "blocked"}})
    altered = composition.model_copy(
        update={
            "harness": composition.harness.model_copy(
                update={
                    "config": config.model_copy(
                        update={"workflow": config.workflow.model_copy(update={"nodes": nodes})}
                    )
                }
            )
        }
    )

    with pytest.raises(ValueError, match="edges, routes"):
        registry.build(
            altered,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state"),
        )


@pytest.mark.asyncio
async def test_pi_factory_wires_tool_defaults_limits_and_dynamic_task_scope(
    tmp_path: Path,
) -> None:
    registry = default_harness_registry()
    composition = load_harness_composition(config_models=registry.config_models())
    config = cast(PiCodingConfig, composition.harness.config)
    configured = composition.model_copy(
        update={
            "harness": composition.harness.model_copy(
                update={
                    "config": config.model_copy(
                        update={
                            "tools": config.tools.model_copy(
                                update={
                                    "read_default_lines": 123,
                                    "bash_default_timeout_seconds": 17,
                                    "bash_max_timeout_seconds": 19,
                                }
                            )
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
    worker_tools = cast(Any, assembly.agents["coding_worker"]).tools
    tools = {tool.__name__: tool for tool in worker_tools}

    assert inspect.signature(tools["read"]).parameters["limit"].default == 123
    assert inspect.signature(tools["bash"]).parameters["timeout_seconds"].default == 17
    first = await tools["write"](
        "result.txt",
        "stable\n",
        expected_absent=True,
        tool_context=SimpleNamespace(
            state={"task_id": "task-a"},
            invocation_id="invocation-a",
        ),
    )
    (tmp_path / "result.txt").unlink()
    second = await tools["write"](
        "result.txt",
        "stable\n",
        expected_absent=True,
        tool_context=SimpleNamespace(
            state={"task_id": "task-b"},
            invocation_id="invocation-b",
        ),
    )
    assert first.get("replayed") is not True
    assert second.get("replayed") is not True


def test_pi_factory_disables_steering(tmp_path: Path) -> None:
    registry = default_harness_registry()
    composition = load_harness_composition(config_models=registry.config_models())
    config = cast(PiCodingConfig, composition.harness.config)
    configured = composition.model_copy(
        update={
            "harness": composition.harness.model_copy(
                update={
                    "config": config.model_copy(
                        update={
                            "steering": config.steering.model_copy(
                                update={"enabled": False, "max_message_bytes": 256}
                            )
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

    assert not any(isinstance(plugin, SteeringPlugin) for plugin in assembly.app.plugins)
    assert assembly.controls is not None
    receipt = asyncio.run(
        assembly.controls.steer(SteeringCommand(run_id="run-1", content="turn left"))
    )
    assert receipt.accepted is False
    assert "disabled" in (receipt.detail or "")


def test_pi_factory_enforces_configured_steering_message_limit(tmp_path: Path) -> None:
    registry = default_harness_registry()
    composition = load_harness_composition(config_models=registry.config_models())
    config = cast(PiCodingConfig, composition.harness.config)
    configured = composition.model_copy(
        update={
            "harness": composition.harness.model_copy(
                update={
                    "config": config.model_copy(
                        update={
                            "steering": config.steering.model_copy(
                                update={"max_message_bytes": 256}
                            )
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

    assert assembly.controls is not None
    receipt = asyncio.run(
        assembly.controls.steer(SteeringCommand(run_id="run-1", content="x" * 257))
    )
    assert receipt.accepted is False
    assert "256 UTF-8 bytes" in (receipt.detail or "")


def test_pi_factory_forwards_control_database_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.factory as factory_module

    calls: list[tuple[Path, str | None]] = []
    backend = SimpleNamespace(
        event_store=JsonlEventStore(tmp_path / "events"),
        task_lease_store=None,
    )

    def create_backend(*, state_root: Path, database_url: str | None):
        calls.append((state_root, database_url))
        return backend

    monkeypatch.setattr(factory_module, "create_control_state_backend", create_backend)
    registry = default_harness_registry()
    composition = load_harness_composition(config_models=registry.config_models())
    database_url = SecretStr("postgresql://control.example/harness")
    registry.build(
        composition,
        RuntimeBindings(
            workspace=tmp_path,
            state_root=tmp_path / "state",
            control_database_url=database_url,
        ),
    )

    assert calls == [
        ((tmp_path / "state").resolve(), database_url.get_secret_value())
    ]
