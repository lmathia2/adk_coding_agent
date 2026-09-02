"""Registered, configuration-driven assembly of Skein."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence

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
    RuntimeBindings,
    SkeinConfig,
)
from harness.context import prefix_hash
from harness.environment import (
    ExecutionRuntime,
    LocalRepositoryRuntime,
    LocalWorkspaceEnvironment,
)
from harness.ledger import LedgerBackedEventStore, LedgerStore, open_ledger
from harness.ledger.importers import (
    import_approval,
    import_checkpoint,
    import_metric,
    import_steering,
    import_tool_receipt,
    import_trace_span,
)
from harness.repo import discover_instruction_files
from harness.safety import ApprovalPolicy, SecretRedactor
from harness.sandbox import create_configured_command_sandbox
from harness.state import CheckpointStore, JsonlEventStore, SteeringQueue, rebuild_ledger
from harness.telemetry.adk_plugin import (
    HarnessMetricsPlugin,
    ModelPricing,
    pricing_from_env,
)
from harness.tools.adk_adapter import create_adk_tools, discover_known_secrets
from harness.tracing import CodingToolArtifactPlugin, HarnessTracePlugin, TraceContentMode
from harness.verification import ManagedValidationExecutor
from harness.workspace import GitWorktreeManager

from .builders import build_coding_worker
from .config import HarnessSettings, settings_from_composition
from .skills import build_skill_registry
from .streaming import PublicReplies
from .workflow import SkeinWorkflowDependencies, build_root_agent

LOGGER = logging.getLogger(__name__)

ExecutionRuntimeFactory = Callable[
    [HarnessSettings, SkeinConfig, Sequence[str]], ExecutionRuntime
]


class _SkeinControlHooks:
    def __init__(
        self,
        *,
        steering: SteeringQueue,
        deps: SkeinWorkflowDependencies,
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
            detail="pause is not supported by skein_v1",
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


class SkeinHarnessFactory:
    """Build isolated ADK Apps from validated Skein behavior and runtime bindings."""

    def __init__(
        self,
        *,
        pricing: Mapping[str, ModelPricing] | None = None,
        model_providers: AdkModelProviderRegistry | None = None,
        execution_runtime_factory: ExecutionRuntimeFactory | None = None,
    ) -> None:
        self._pricing = dict(pricing or {})
        self._model_providers = model_providers or default_adk_model_provider_registry()
        self._execution_runtime_factory = execution_runtime_factory
        self._descriptor = HarnessDescriptor(
            implementation="skein_v1",
            api_version=1,
            display_name="Skein",
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
        return SkeinConfig

    def coding_model(self, config: BaseModel) -> ModelConfig:
        if not isinstance(config, SkeinConfig):
            raise TypeError("skein_v1 requires SkeinConfig")
        return config.models[config.agents["coding_worker"].model]

    def with_coding_model(self, config: BaseModel, model: ModelConfig) -> BaseModel:
        if not isinstance(config, SkeinConfig):
            raise TypeError("skein_v1 requires SkeinConfig")
        payload = config.model_dump()
        payload["models"][config.agents["coding_worker"].model] = model.model_dump()
        configured = SkeinConfig.model_validate(payload)
        self._validate_supported_shape(configured)
        return configured

    def _validate_supported_shape(self, config: SkeinConfig) -> None:
        SkeinConfig.model_validate(config.model_dump())
        if config.notebook_ptc.enabled and config.sandbox.kind != "local":
            raise ValueError("notebook-native PTC currently requires the local sandbox")
        if config.memory.enabled and config.memory.retrieval == "lance":
            raise ValueError(
                "live Lance retrieval requires a configured embedding provider; "
                "use lexical retrieval until that provider is wired"
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

    def resources(
        self, composition: HarnessComposition, bindings: RuntimeBindings
    ) -> HarnessResources:
        """Use the execution loaders, without building a model, tools or state stores."""
        config = composition.harness.config
        if not isinstance(config, SkeinConfig):
            raise TypeError("skein_v1 requires SkeinConfig")
        settings = settings_from_composition(composition, bindings)
        tool_names = ("python",) if config.notebook_ptc.enabled else FOUR_CODING_TOOLS
        items = [ResourceItem(kind="tool", name=name) for name in tool_names]
        items.append(ResourceItem(kind="prompt", name="coding-worker"))
        warnings = []
        if settings.project_trusted:
            items.extend(
                ResourceItem(kind="instruction", name=path.name, path=str(path))
                for path in discover_instruction_files(settings.workspace)
            )
        else:
            warnings.append(
                "Project instructions and project skills are disabled until the server is launched with --trust-project."
            )
        enabled = settings.skill_context_bytes > 0
        for root in settings.skill_roots:
            items.append(
                ResourceItem(
                    kind="skill_root",
                    name=root.name,
                    path=str(root),
                    status="missing"
                    if not root.exists()
                    else "available"
                    if enabled
                    else "disabled",
                )
            )
        if enabled:
            try:
                registry = build_skill_registry(settings)
                items.extend(
                    ResourceItem(
                        kind="skill",
                        name=skill.name,
                        path=str(skill.manifest_path),
                        description=skill.description[:256],
                        status="available" if settings.skill_max_selected > 0 else "disabled",
                    )
                    for skill in registry.skills
                )
            except Exception:
                warnings.append(
                    "Skill validation failed; the runtime will omit skill context. Check the configured SKILL.md files and roots."
                )
        else:
            warnings.append("Skill context is disabled by the configured byte budget.")
        return HarnessResources(
            items=tuple(items[:128]), warnings=tuple(warnings), truncated=len(items) > 128
        )

    @staticmethod
    def _known_secrets(config: SkeinConfig) -> list[str]:
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
        if not isinstance(config, SkeinConfig):
            raise TypeError("skein_v1 requires SkeinConfig")
        self._validate_supported_shape(config)
        settings = settings_from_composition(composition, bindings)
        settings.state_root.mkdir(parents=True, exist_ok=True)
        known_secrets = self._known_secrets(config)
        if self._execution_runtime_factory is None:
            sandbox = create_configured_command_sandbox(
                settings.workspace,
                settings.state_root,
                config.sandbox,
                max_output_bytes=config.tools.output.max_bytes,
                known_secrets=known_secrets,
            )
            execution = ExecutionRuntime(
                files=LocalWorkspaceEnvironment(settings.workspace),
                commands=sandbox,
                repository=LocalRepositoryRuntime(settings.workspace),
            )
        else:
            execution = self._execution_runtime_factory(settings, config, known_secrets)
            sandbox = execution.commands
        policy = ApprovalPolicy(
            allow_dependency_install=config.safety.allow_dependency_install,
            allow_network=config.safety.allow_network,
            allow_git_history_mutation=config.safety.allow_git_history_mutation,
            allow_unknown=config.safety.allow_unknown_commands,
        )
        canonical_ledger: LedgerStore | None = (
            open_ledger(settings.state_root, config.memory.ledger)
            if config.memory.enabled
            else None
        )
        tools = create_adk_tools(
            settings.workspace,
            state_root=settings.state_root,
            sandbox=sandbox,
            environment=execution.files,
            search_mode=config.tools.search.backend,
            policy=policy,
            known_secrets=known_secrets,
            task_scope=settings.task_id_override,
            bash_max_timeout_seconds=config.tools.bash_max_timeout_seconds,
            search_default_page_size=config.tools.search.default_page_size,
            search_max_page_size=config.tools.search.max_page_size,
            receipt_sink=(
                (lambda receipt: import_tool_receipt(canonical_ledger, receipt))
                if canonical_ledger is not None
                else None
            ),
            approval_sink=(
                (lambda approval: import_approval(canonical_ledger, approval))
                if canonical_ledger is not None
                else None
            ),
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
                ApprovalStore(
                    settings.state_root / "approvals.db",
                    on_change=(
                        (lambda approval: import_approval(canonical_ledger, approval))
                        if canonical_ledger is not None
                        else None
                    ),
                ),
                settings.task_id_override or "",
                timeout=min(
                    composition.server.approval_wait_timeout_seconds,
                    composition.server.idle_timeout_seconds / 2,
                ),
            )
        replies = PublicReplies(SecretRedactor(known_secrets=known_secrets))
        operational_events = JsonlEventStore(settings.state_root / "events")
        event_store = (
            LedgerBackedEventStore(operational_events, canonical_ledger)
            if canonical_ledger is not None
            else operational_events
        )
        if config.notebook_ptc.enabled:
            worker = build_coding_worker(
                settings,
                coding_model,
                tools=tools,
                generation_config=worker_config.generation,
                tool_config=config.tools,
                ptc_config=config.notebook_ptc,
                event_store=event_store,
                approvals=approvals,
                replies=replies,
            )
        else:
            worker = build_coding_worker(
                settings,
                coding_model,
                tools=tools,
                generation_config=worker_config.generation,
                tool_config=config.tools,
                approvals=approvals,
                replies=replies,
            )
        steering = SteeringQueue(
            settings.state_root / "state.db",
            on_change=(
                (lambda message: import_steering(canonical_ledger, message))
                if canonical_ledger is not None
                else None
            ),
        )
        checkpoints = CheckpointStore(
            settings.state_root / "state.db",
            on_save=(
                (lambda checkpoint: import_checkpoint(canonical_ledger, checkpoint))
                if canonical_ledger is not None
                else None
            ),
        )
        metrics_plugin = HarnessMetricsPlugin(
            database=settings.state_root / "metrics.db",
            static_prefix_hash=prefix_hash(settings.static_prefix),
            static_prefix_tokens=len(settings.static_prefix) // 4,
            default_model=settings.model,
            default_task_id=settings.task_id_override,
            pricing=self._pricing,
            metric_sink=(
                (lambda sample: import_metric(canonical_ledger, sample))
                if canonical_ledger is not None
                else None
            ),
        )
        workspace_manager = (
            GitWorktreeManager(settings.source_repository, settings.state_root)
            if settings.source_repository is not None
            and self._execution_runtime_factory is None
            else None
        )
        deps = SkeinWorkflowDependencies(
            settings=settings,
            event_store=event_store,
            steering_queue=steering,
            checkpoint_store=checkpoints,
            metrics_store=metrics_plugin.store,
            workspace_manager=workspace_manager,
            repository=execution.repository,
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
                "COMPACTED HISTORY": config.context.compaction_tokens,
                "RECENT EVENTS": config.context.recent_event_tokens,
                "USER STEERING": config.context.steering_tokens,
            },
            progress_history_limit=config.workflow.progress.action_history_limit,
            progress_replan_threshold=config.workflow.progress.replan_after_no_progress,
            progress_human_threshold=config.workflow.progress.block_after_no_progress,
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
                    span_sink=(
                        (lambda span: import_trace_span(canonical_ledger, span))
                        if canonical_ledger is not None
                        else None
                    ),
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
                tool_names=("python",) if config.notebook_ptc.enabled else FOUR_CODING_TOOLS,
                max_iterations=config.workflow.max_iterations,
                compact_at_tokens=config.context.compact_at_tokens,
            ),
            agents=agents,
            explicit_public_messages=True,
            approvals=approvals,
            close=worker.close,
            controls=_SkeinControlHooks(
                steering=steering,
                deps=deps,
                enabled=config.steering.enabled,
                max_message_bytes=config.steering.max_message_bytes,
            ),
        )


def default_harness_registry(
    *,
    model_providers: AdkModelProviderRegistry | None = None,
    execution_runtime_factory: ExecutionRuntimeFactory | None = None,
) -> HarnessRegistry:
    registry = HarnessRegistry()
    registry.register(
        SkeinHarnessFactory(
            model_providers=model_providers,
            pricing=pricing_from_env(),
            execution_runtime_factory=execution_runtime_factory,
        )
    )
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
    "SkeinHarnessFactory",
    "build_harness",
    "default_harness_registry",
]
