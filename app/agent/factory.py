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
from harness.agent.resources import HarnessResources, ResourceItem
from harness.ai import AdkModelProviderRegistry, default_adk_model_provider_registry
from harness.approvals import ApprovalStore
from harness.approvals.waiting import ApprovalWaiter
from harness.config import (
    FOUR_CODING_TOOLS,
    HarnessComposition,
    ModelConfig,
    PiCodingConfig,
    RuntimeBindings,
)
from harness.context import prefix_hash
from harness.repo import StructuralIndex, discover_instruction_files
from harness.safety import ApprovalPolicy, SecretRedactor
from harness.sandbox import create_configured_command_sandbox
from harness.state import CheckpointStore, JsonlEventStore, SteeringQueue, rebuild_ledger
from harness.telemetry.adk_plugin import HarnessMetricsPlugin, ModelPricing
from harness.tools.adk_adapter import create_adk_tools, discover_known_secrets
from harness.tracing import CodingToolArtifactPlugin, HarnessTracePlugin, TraceContentMode
from harness.verification import ManagedValidationExecutor
from harness.workspace import GitWorktreeManager

from .builders import build_coding_worker
from .config import settings_from_composition
from .skills import build_skill_registry
from .streaming import PublicReplies
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
                    RuntimeCapability.APPROVALS,
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

    def coding_model(self, config: BaseModel) -> ModelConfig:
        if not isinstance(config, PiCodingConfig):
            raise TypeError("pi_coding_v1 requires PiCodingConfig")
        return config.models[config.agents["coding_worker"].model]

    def with_coding_model(self, config: BaseModel, model: ModelConfig) -> BaseModel:
        if not isinstance(config, PiCodingConfig):
            raise TypeError("pi_coding_v1 requires PiCodingConfig")
        payload = config.model_dump()
        payload["models"][config.agents["coding_worker"].model] = model.model_dump()
        configured = PiCodingConfig.model_validate(payload)
        self._validate_supported_shape(configured)
        return configured

    def _validate_supported_shape(self, config: PiCodingConfig) -> None:
        PiCodingConfig.model_validate(config.model_dump())
        unknown_providers = sorted(
            {
                model.provider
                for model in config.models.values()
                if model.provider not in self._model_providers.available()
            }
        )
        if unknown_providers:
            raise ValueError(f"unregistered model providers: {unknown_providers}")

    def resources(self, composition: HarnessComposition, bindings: RuntimeBindings) -> HarnessResources:
        """Use the execution loaders, without building a model, tools or state stores."""
        config = composition.harness.config
        if not isinstance(config, PiCodingConfig):
            raise TypeError("pi_coding_v1 requires PiCodingConfig")
        settings = settings_from_composition(composition, bindings)
        items = [ResourceItem(kind="tool", name=name) for name in FOUR_CODING_TOOLS]
        prompt = config.agents["coding_worker"].prompt
        path = (bindings.configuration_root or settings.workspace) / prompt.path if prompt.path else None
        items.append(ResourceItem(kind="prompt", name=prompt.name or "coding_worker", path=str(path.resolve()) if path else None))
        warnings = []
        if settings.project_trusted:
            items.extend(ResourceItem(kind="instruction", name=path.name, path=str(path))
                         for path in discover_instruction_files(settings.workspace))
        else:
            warnings.append("Project instructions and project skills are disabled until the server is launched with --trust-project.")
        enabled = settings.skill_context_bytes > 0
        for root in settings.skill_roots:
            items.append(ResourceItem(kind="skill_root", name=root.name, path=str(root),
                status="missing" if not root.exists() else "available" if enabled else "disabled"))
        if enabled:
            try:
                registry = build_skill_registry(settings)
                items.extend(ResourceItem(kind="skill", name=skill.name, path=str(skill.manifest_path),
                    description=skill.description[:256], status="available" if settings.skill_max_selected > 0 else "disabled") for skill in registry.skills)
            except Exception:
                warnings.append("Skill validation failed; the runtime will omit skill context. Check the configured SKILL.md files and roots.")
        else:
            warnings.append("Skill context is disabled by the configured byte budget.")
        return HarnessResources(items=tuple(items[:128]), warnings=tuple(warnings), truncated=len(items) > 128)

    @staticmethod
    def _known_secrets(config: PiCodingConfig) -> list[str]:
        names = set(config.safety.redact_environment_names)
        names.update(
            model.api_key.env for model in config.models.values() if model.api_key is not None
        )
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
        approvals = None
        if bindings.interactive_approvals:
            approvals = ApprovalWaiter(
                ApprovalStore(settings.state_root / "approvals.db"), settings.task_id_override or "",
                timeout=min(composition.server.approval_wait_timeout_seconds, composition.server.idle_timeout_seconds / 2),
            )
        replies = PublicReplies(SecretRedactor(known_secrets=known_secrets))
        worker = build_coding_worker(
            settings,
            coding_model,
            tools=tools,
            tool_config=config.tools,
            approvals=approvals,
            replies=replies,
        )
        event_store = JsonlEventStore(settings.state_root / "events")
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
            event_store=event_store,
            steering_queue=steering,
            checkpoint_store=checkpoints,
            metrics_store=metrics_plugin.store,
            repository_index=StructuralIndex(
                settings.workspace,
                settings.state_root / "repo-index.json",
            ),
            workspace_manager=workspace_manager,
            coding_worker=worker.agent,
            replies=replies,
            validation_executor=lambda task_id: ManagedValidationExecutor(
                settings.workspace,
                state_root=settings.state_root,
                task_id=task_id,
                sandbox=sandbox,
                policy=policy,
                known_secrets=known_secrets,
            ),
            static_prefix_hash=prefix_hash(settings.static_prefix),
            static_prefix_tokens=len(settings.static_prefix) // 4,
            repository_map_tokens=config.context.repository_map_tokens,
            work_packet_tokens=config.context.work_packet_tokens,
            max_task_input_tokens=config.context.max_task_input_tokens,
            work_packet_section_tokens={
                "TASK": config.context.ledger_tokens,
                "CONVERSATION": config.context.conversation_tokens,
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
            approvals=approvals,
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
                    event_store=event_store,
                    lease_seconds=settings.task_lease_seconds,
                    batch_limit=config.steering.batch_limit,
                    before_model="before_model" in config.steering.safe_points,
                    before_tool="before_tool" in config.steering.safe_points,
                )
            )
        plugins.extend(
            [
                metrics_plugin,
                CodingToolArtifactPlugin(
                    event_store=event_store,
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
            explicit_public_messages=True,
            approvals=approvals,
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
