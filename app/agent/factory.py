"""Registered, configuration-driven assembly of the Pi coding harness."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import App, EventsCompactionConfig, ResumabilityConfig
from google.adk.plugins.base_plugin import BasePlugin
from pydantic import BaseModel

from harness.adk import SteeringPlugin
from harness.agent import (
    AdkHarnessAssembly,
    AgentSnapshot,
    ControlCommand,
    ControlReceipt,
    HarnessBuildInfo,
    HarnessDescriptor,
    HarnessRegistry,
    RuntimeCapability,
    SteeringCommand,
)
from harness.ai import AdkModelProviderRegistry, default_adk_model_provider_registry
from harness.config import (
    FOUR_CODING_TOOLS,
    HarnessComposition,
    PiCodingConfig,
    RuntimeBindings,
)
from harness.context import prefix_hash
from harness.memory.adk_plugin import VerifiedProjectMemoryPlugin
from harness.repo import StructuralIndex
from harness.safety import ApprovalPolicy
from harness.sandbox import create_configured_command_sandbox
from harness.state import CheckpointStore, SteeringQueue, rebuild_ledger
from harness.state.factory import create_control_state_backend
from harness.telemetry.adk_plugin import HarnessMetricsPlugin, ModelPricing
from harness.tools.adk_adapter import create_adk_tools, discover_known_secrets
from harness.tracing import CodingToolArtifactPlugin, HarnessTracePlugin, TraceContentMode
from harness.workspace import GitWorktreeManager

from .builders import (
    FINAL_REVIEW_INSTRUCTION,
    build_coding_worker,
    build_final_diff_reviewer,
)
from .config import resolve_prompt_text, settings_from_composition
from .learning import VerifiedTraceLearningPlugin
from .skills import build_learning_controller
from .workflow import PiWorkflowDependencies, build_root_agent

LOGGER = logging.getLogger(__name__)


class _PiControlHooks:
    def __init__(
        self,
        *,
        steering: SteeringQueue,
        deps: PiWorkflowDependencies,
        enabled: bool,
        max_message_bytes: int,
    ) -> None:
        self._steering = steering
        self._deps = deps
        self._enabled = enabled
        self._max_message_bytes = max_message_bytes

    async def steer(self, command: SteeringCommand) -> ControlReceipt:
        if not self._enabled:
            return ControlReceipt(
                accepted=False,
                command_id=command.idempotency_key or f"steer:{command.run_id}",
                detail="steering is disabled by harness configuration",
            )
        if len(command.content.encode("utf-8")) > self._max_message_bytes:
            return ControlReceipt(
                accepted=False,
                command_id=command.idempotency_key or f"steer:{command.run_id}",
                detail=(
                    "steering content exceeds the configured limit of "
                    f"{self._max_message_bytes} UTF-8 bytes"
                ),
            )
        message = self._steering.enqueue(
            command.run_id,
            command.content,
            priority=command.priority,
            idempotency_key=command.idempotency_key,
        )
        return ControlReceipt(accepted=True, command_id=message.message_id)

    async def pause(self, command: ControlCommand) -> ControlReceipt:
        return ControlReceipt(
            accepted=False,
            command_id=command.idempotency_key or f"pause:{command.run_id}",
            detail="pause is not supported by pi_coding_v1",
        )

    async def cancel(self, command: ControlCommand) -> ControlReceipt:
        return ControlReceipt(
            accepted=False,
            command_id=command.idempotency_key or f"cancel:{command.run_id}",
            detail="cancellation is owned by the shared ADK runner",
        )

    async def snapshot(self, run_id: str) -> AgentSnapshot:
        events = self._deps.event_store.read(run_id)
        state: dict[str, object] = {}
        if events:
            try:
                state = rebuild_ledger(events).model_dump(mode="json")
            except ValueError:
                state = {}
        return AgentSnapshot(
            run_id=run_id,
            sequence=events[-1].sequence if events else 0,
            state=state,
        )


class PiCodingHarnessFactory:
    """Build isolated ADK Apps from validated Pi behavior and runtime bindings."""

    def __init__(
        self,
        *,
        pricing: Mapping[str, ModelPricing] | None = None,
        model_providers: AdkModelProviderRegistry | None = None,
    ) -> None:
        self._pricing = dict(pricing or {})
        self._model_providers = model_providers or default_adk_model_provider_registry()
        self._descriptor = HarnessDescriptor(
            implementation="pi_coding_v1",
            api_version=1,
            display_name="Pi-inspired ADK coding harness",
            capabilities=frozenset(
                {
                    RuntimeCapability.STREAMING,
                    RuntimeCapability.STEERING,
                    RuntimeCapability.TOOL_EVENTS,
                    RuntimeCapability.STATE_SNAPSHOTS,
                    RuntimeCapability.ARTIFACTS,
                }
            ),
            protocol_versions=(1,),
        )

    @property
    def descriptor(self) -> HarnessDescriptor:
        return self._descriptor

    @property
    def config_model(self) -> type[BaseModel]:
        return PiCodingConfig

    def _validate_supported_shape(self, config: PiCodingConfig) -> None:
        required_workflow = {
            "entry": "initialize",
            "nodes": {
                "initialize": {"kind": "initialize", "next": "compile"},
                "compile": {"kind": "compile_context", "next": "code"},
                "code": {
                    "kind": "invoke_agent",
                    "agent": "coding_worker",
                    "next": "reduce",
                },
                "reduce": {"kind": "reduce_step", "next": "route"},
                "route": {
                    "kind": "route",
                    "routes": {
                        "continue": "compile",
                        "compact": "compact",
                        "replan": "replan",
                        "verify": "verify",
                        "blocked": "blocked",
                    },
                },
                "compact": {"kind": "compact", "next": "compile"},
                "replan": {"kind": "replan", "next": "compile"},
                "verify": {
                    "kind": "verify",
                    "routes": {"passed": "review", "failed": "compile"},
                },
                "review": {
                    "kind": "review",
                    "agent": "final_diff_reviewer",
                    "enabled": config.reviewer.enabled,
                    "next": "finish",
                },
                "finish": {"kind": "finish"},
                "blocked": {"kind": "blocked"},
            },
        }
        actual_workflow = config.workflow.model_dump(
            mode="json",
            exclude={"max_iterations"},
        )
        if actual_workflow != required_workflow:
            raise ValueError(
                "pi_coding_v1 workflow topology, edges, routes, and agent bindings "
                "are fixed; register another harness implementation for a different "
                "topology"
            )
        worker = config.agents.get("coding_worker")
        if (
            worker is None
            or worker.kind != "llm"
            or worker.tools != FOUR_CODING_TOOLS
            or worker.output_schema != "agent_step"
            or worker.mode != "multi_turn"
            or (
                worker.prompt.source == "builtin"
                and worker.prompt.name != "coding_worker_v1"
            )
        ):
            raise ValueError("pi_coding_v1 requires the fixed coding_worker contract")
        reviewer = config.agents.get("final_diff_reviewer")
        if (
            reviewer is None
            or reviewer.kind != "reviewer"
            or reviewer.tools
            or reviewer.output_schema != "final_diff_review"
            or reviewer.mode != "single_turn"
            or (
                reviewer.prompt.source == "builtin"
                and reviewer.prompt.name != "final_diff_review_v1"
            )
        ):
            raise ValueError("pi_coding_v1 requires the fixed final reviewer contract")
        if set(config.agents) != {"coding_worker", "final_diff_reviewer"}:
            raise ValueError(
                "pi_coding_v1 accepts only coding_worker and final_diff_reviewer; "
                "register another harness for additional agents"
            )
        referenced_models = {agent.model for agent in config.agents.values()}
        if set(config.models) != referenced_models:
            raise ValueError(
                "pi_coding_v1 model entries must be referenced by a configured agent"
            )
        unknown_providers = sorted(
            {
                model.provider
                for model in config.models.values()
                if model.provider not in self._model_providers.available()
            }
        )
        if unknown_providers:
            raise ValueError(f"unregistered model providers: {unknown_providers}")

    @staticmethod
    def _known_secrets(config: PiCodingConfig) -> list[str]:
        names = set(config.safety.redact_environment_names)
        names.update(
            model.api_key.env for model in config.models.values() if model.api_key is not None
        )
        token = getattr(config.sandbox, "token", None)
        if token is not None:
            names.add(token.env)
        return discover_known_secrets(sorted(names))

    def build(
        self,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
    ) -> AdkHarnessAssembly:
        config = composition.harness.config
        if not isinstance(config, PiCodingConfig):
            raise TypeError("pi_coding_v1 requires PiCodingConfig")
        self._validate_supported_shape(config)
        settings = settings_from_composition(composition, bindings)
        settings.state_root.mkdir(parents=True, exist_ok=True)
        known_secrets = self._known_secrets(config)
        sandbox = create_configured_command_sandbox(
            settings.workspace,
            settings.state_root,
            config.sandbox,
            max_output_bytes=config.tools.output.max_bytes,
            known_secrets=known_secrets,
        )
        policy = ApprovalPolicy(
            allow_dependency_install=config.safety.allow_dependency_install,
            allow_network=config.safety.allow_network,
            allow_git_history_mutation=config.safety.allow_git_history_mutation,
            allow_unknown=config.safety.allow_unknown_commands,
        )
        tools = create_adk_tools(
            settings.workspace,
            state_root=settings.state_root,
            sandbox=sandbox,
            search_mode=config.tools.search.backend,
            policy=policy,
            known_secrets=known_secrets,
            task_scope=settings.task_id_override,
            bash_max_timeout_seconds=config.tools.bash_max_timeout_seconds,
            search_default_page_size=config.tools.search.default_page_size,
            search_max_page_size=config.tools.search.max_page_size,
        )

        def build_model(model_name: str):
            model_config = config.models[model_name]
            return self._model_providers.get(model_config.provider).build_model(
                model_config,
                secrets=(
                    {"api_key": model_config.api_key} if model_config.api_key is not None else {}
                ),
                bindings=bindings,
            )

        worker_config = config.agents["coding_worker"]
        coding_model = build_model(worker_config.model)
        worker = build_coding_worker(
            settings,
            coding_model,
            tools=tools,
            tool_config=config.tools,
        )
        reviewer_name = config.reviewer.agent or "final_diff_reviewer"
        reviewer_config = config.agents.get(reviewer_name)
        if reviewer_config is None:
            reviewer_config = config.agents["final_diff_reviewer"]
        reviewer_model_config = config.models[reviewer_config.model]
        configuration_root = (
            bindings.configuration_root or bindings.workspace
        ).expanduser().resolve()
        reviewer_instruction = resolve_prompt_text(
            reviewer_config.prompt,
            configuration_root=configuration_root,
            builtin_name="final_diff_review_v1",
            builtin_text=FINAL_REVIEW_INSTRUCTION,
        )
        reviewer = (
            build_final_diff_reviewer(
                build_model(reviewer_config.model),
                model_name=reviewer_model_config.name,
                instruction=reviewer_instruction,
            )
            if config.reviewer.enabled
            else None
        )

        control_state = create_control_state_backend(
            state_root=settings.state_root,
            database_url=settings.control_database_url,
        )
        steering = SteeringQueue(settings.state_root / "state.db")
        checkpoints = CheckpointStore(settings.state_root / "state.db")
        metrics_plugin = HarnessMetricsPlugin(
            database=settings.state_root / "metrics.db",
            static_prefix_hash=prefix_hash(settings.static_prefix),
            static_prefix_tokens=len(settings.static_prefix) // 4,
            default_model=settings.model,
            default_task_id=settings.task_id_override,
            pricing=self._pricing,
        )
        workspace_manager = (
            GitWorktreeManager(settings.source_repository, settings.state_root)
            if settings.source_repository is not None
            else None
        )
        deps = PiWorkflowDependencies(
            settings=settings,
            control_state=control_state,
            steering_queue=steering,
            checkpoint_store=checkpoints,
            metrics_store=metrics_plugin.store,
            repository_index=StructuralIndex(
                settings.workspace,
                settings.state_root / "repo-index.json",
            ),
            workspace_manager=workspace_manager,
            coding_worker=worker.agent,
            final_diff_reviewer=reviewer.agent if reviewer is not None else None,
            learning_controller=build_learning_controller(settings),
            static_prefix_hash=prefix_hash(settings.static_prefix),
            static_prefix_tokens=len(settings.static_prefix) // 4,
            review_prefix_hash=(
                prefix_hash(reviewer.static_prefix) if reviewer is not None else None
            ),
            review_prefix_tokens=(len(reviewer.static_prefix) // 4 if reviewer is not None else 0),
            repository_map_tokens=config.context.repository_map_tokens,
            work_packet_tokens=config.context.work_packet_tokens,
            max_task_input_tokens=config.context.max_task_input_tokens,
            work_packet_section_tokens={
                "TASK": config.context.ledger_tokens,
                "SELECTED SKILLS": max(
                    config.context.skill_context_bytes // 4,
                    0,
                ),
                "REPOSITORY MANIFEST": config.context.manifest_tokens,
                "REPOSITORY MAP": config.context.repository_map_tokens,
                "COMPACTED HISTORY": config.context.compaction_tokens,
                "RECENT EVENTS": config.context.recent_event_tokens,
                "USER STEERING": config.context.steering_tokens,
            },
            steering_batch_limit=config.steering.batch_limit,
            steering_enabled=config.steering.enabled,
            steering_at_work_batch_boundary=("work_batch_boundary" in config.steering.safe_points),
        )
        root_agent = build_root_agent(deps)
        plugins: list[BasePlugin] = []
        if config.steering.enabled and {
            "before_model",
            "before_tool",
        }.intersection(config.steering.safe_points):
            plugins.append(
                SteeringPlugin(
                    queue=steering,
                    event_store=control_state.event_store,
                    lease_seconds=settings.task_lease_seconds,
                    batch_limit=config.steering.batch_limit,
                    before_model="before_model" in config.steering.safe_points,
                    before_tool="before_tool" in config.steering.safe_points,
                )
            )
        plugins.extend(
            [
                metrics_plugin,
                VerifiedProjectMemoryPlugin(
                    workspace=settings.workspace,
                    state_root=settings.state_root,
                    project_root=settings.source_repository or settings.workspace,
                    default_task_id=settings.task_id_override,
                    event_store=control_state.event_store,
                ),
                CodingToolArtifactPlugin(
                    event_store=control_state.event_store,
                    default_task_id=settings.task_id_override,
                ),
            ]
        )
        trace_plugin: HarnessTracePlugin | None = None
        if config.tracing.mode != "off":
            try:
                trace_plugin = HarnessTracePlugin(
                    database=settings.state_root / "traces.db",
                    content_mode=(
                        TraceContentMode.REDACTED_CONTENT
                        if config.tracing.mode == "redacted"
                        else TraceContentMode.METADATA_ONLY
                    ),
                    max_payload_bytes=config.tracing.max_content_bytes,
                    known_secrets=known_secrets,
                    default_task_id=settings.task_id_override,
                )
            except Exception:
                LOGGER.exception("trace storage initialization failed; tracing is disabled")
        if trace_plugin is not None:
            plugins.insert(1, trace_plugin)
            if config.learning.enabled:
                plugins.append(
                    VerifiedTraceLearningPlugin(
                        event_store=control_state.event_store,
                        trace_store=trace_plugin.store,
                        metrics_store=metrics_plugin.store,
                        controller=deps.learning_controller,
                        minimum_support=config.learning.minimum_support,
                        default_task_id=settings.task_id_override,
                    )
                )

        app = App(
            name=composition.app.name,
            root_agent=root_agent,
            plugins=plugins,
            context_cache_config=ContextCacheConfig(
                min_tokens=config.adk.context_cache.min_tokens,
                ttl_seconds=config.adk.context_cache.ttl_seconds,
                cache_intervals=config.adk.context_cache.cache_intervals,
            ),
            events_compaction_config=EventsCompactionConfig(
                compaction_interval=config.adk.event_compaction.interval,
                overlap_size=config.adk.event_compaction.overlap,
                token_threshold=config.adk.event_compaction.token_threshold,
                event_retention_size=config.adk.event_compaction.retention,
            ),
            resumability_config=ResumabilityConfig(is_resumable=config.adk.resumable),
        )
        agents = {"coding_worker": worker.agent}
        if reviewer is not None:
            agents["final_diff_reviewer"] = reviewer.agent
        return AdkHarnessAssembly(
            descriptor=self.descriptor,
            app=app,
            build_info=HarnessBuildInfo(
                behavior_sha256=composition.behavior_sha256,
                models={name: model.name for name, model in sorted(config.models.items())},
                model_providers={
                    name: model.provider for name, model in sorted(config.models.items())
                },
                tool_names=FOUR_CODING_TOOLS,
                max_iterations=config.workflow.max_iterations,
                compact_at_tokens=config.context.compact_at_tokens,
            ),
            agents=agents,
            controls=_PiControlHooks(
                steering=steering,
                deps=deps,
                enabled=config.steering.enabled,
                max_message_bytes=config.steering.max_message_bytes,
            ),
        )


def default_harness_registry(
    *,
    model_providers: AdkModelProviderRegistry | None = None,
) -> HarnessRegistry:
    registry = HarnessRegistry()
    registry.register(PiCodingHarnessFactory(model_providers=model_providers))
    return registry


def build_harness(
    composition: HarnessComposition,
    bindings: RuntimeBindings,
    *,
    registry: HarnessRegistry | None = None,
) -> AdkHarnessAssembly:
    active_registry = registry or default_harness_registry()
    return active_registry.build(composition, bindings)


__all__ = [
    "PiCodingHarnessFactory",
    "build_harness",
    "default_harness_registry",
]
