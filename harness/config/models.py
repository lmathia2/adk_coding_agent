"""Strict declarative contracts for composing an ADK coding harness."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializeAsAny,
    field_validator,
    model_validator,
)

FOUR_CODING_TOOLS = ("read", "bash", "edit", "write")
HarnessCapability = Literal[
    "streaming",
    "steering",
    "pause",
    "cancel",
    "replay",
    "tool_events",
    "state_snapshots",
    "approvals",
    "artifacts",
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecretRef(FrozenModel):
    """Reference a secret without storing its value in composition state."""

    env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,127}$")


class AppConfig(FrozenModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")


class RetryConfig(FrozenModel):
    attempts: int = Field(default=3, ge=1, le=10)
    exponential_base: float = Field(default=2, ge=1, le=10)
    initial_delay_seconds: float = Field(default=1, ge=0, le=60)
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)


class ModelConfig(FrozenModel):
    """Provider key and ADK model configuration."""

    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    reasoning: str | None = Field(default=None, max_length=32)
    retry: RetryConfig = RetryConfig()
    base_url: str | None = Field(default=None, min_length=8, max_length=2_048)
    api_key: SecretRef | None = None
    client_version: str | None = Field(
        default=None,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$",
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("model base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "model base_url cannot contain credentials, a query, or a fragment"
            )
        return normalized

    @model_validator(mode="after")
    def validate_provider_options(self) -> ModelConfig:
        if self.provider == "openai_compatible":
            if self.base_url is None:
                raise ValueError("openai_compatible models require base_url")
            if self.api_key is None:
                raise ValueError("openai_compatible models require an api_key env reference")
        elif self.provider == "google_adk" and (
            self.base_url is not None
            or self.api_key is not None
            or self.client_version is not None
        ):
            raise ValueError(
                "google_adk models cannot define base_url, api_key, or client_version"
            )
        elif self.provider == "openai_codex" and (
            self.base_url is not None or self.api_key is not None
        ):
            raise ValueError(
                "openai_codex uses the fixed ChatGPT subscription endpoint and cannot "
                "define base_url or api_key"
            )
        elif self.provider != "openai_codex" and self.client_version is not None:
            raise ValueError("client_version is supported only by openai_codex")
        return self


class PromptConfig(FrozenModel):
    source: Literal["builtin", "file"] = "builtin"
    name: str | None = Field(default=None, max_length=128)
    path: Path | None = None

    @model_validator(mode="after")
    def validate_source(self) -> PromptConfig:
        if self.source == "builtin" and not self.name:
            raise ValueError("builtin prompts require a name")
        if self.source == "file" and self.path is None:
            raise ValueError("file prompts require a path")
        if self.source == "builtin" and self.path is not None:
            raise ValueError("builtin prompts cannot define a path")
        if self.source == "file" and self.name is not None:
            raise ValueError("file prompts cannot define a builtin name")
        return self


class AgentConfig(FrozenModel):
    kind: Literal["llm", "reviewer"]
    model: str = Field(min_length=1, max_length=64)
    prompt: PromptConfig
    tools: tuple[Literal["read", "bash", "edit", "write"], ...]
    output_schema: Literal["agent_step", "final_diff_review"]
    mode: Literal["multi_turn", "single_turn"]

    @model_validator(mode="after")
    def validate_agent(self) -> AgentConfig:
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("agent tools must be unique")
        if self.kind == "reviewer" and self.tools:
            raise ValueError("reviewer agents cannot expose tools")
        if self.kind == "reviewer" and self.output_schema != "final_diff_review":
            raise ValueError("reviewer agents require final_diff_review output")
        return self


class NodeKind(StrEnum):
    INITIALIZE = "initialize"
    COMPILE_CONTEXT = "compile_context"
    INVOKE_AGENT = "invoke_agent"
    REDUCE_STEP = "reduce_step"
    ROUTE = "route"
    COMPACT = "compact"
    VERIFY = "verify"
    REVIEW = "review"
    REPLAN = "replan"
    FINISH = "finish"
    BLOCKED = "blocked"
    PARALLEL = "parallel"


class NextNode(FrozenModel):
    kind: Literal[
        NodeKind.INITIALIZE,
        NodeKind.COMPILE_CONTEXT,
        NodeKind.REDUCE_STEP,
        NodeKind.COMPACT,
        NodeKind.REPLAN,
    ]
    next: str = Field(min_length=1, max_length=64)


class InvokeAgentNode(FrozenModel):
    kind: Literal[NodeKind.INVOKE_AGENT]
    agent: str = Field(min_length=1, max_length=64)
    next: str = Field(min_length=1, max_length=64)


class RouteNode(FrozenModel):
    kind: Literal[NodeKind.ROUTE]
    routes: dict[str, str] = Field(min_length=1)


class VerifyNode(FrozenModel):
    kind: Literal[NodeKind.VERIFY]
    routes: dict[Literal["passed", "failed"], str]

    @model_validator(mode="after")
    def validate_routes(self) -> VerifyNode:
        if set(self.routes) != {"passed", "failed"}:
            raise ValueError("verify nodes require passed and failed routes")
        return self


class ReviewNode(FrozenModel):
    kind: Literal[NodeKind.REVIEW]
    agent: str = Field(min_length=1, max_length=64)
    enabled: bool = False
    next: str = Field(min_length=1, max_length=64)


class ParallelNode(FrozenModel):
    """Run registered ADK agents concurrently, then continue to the join node."""

    kind: Literal[NodeKind.PARALLEL]
    agents: tuple[str, ...] = Field(min_length=2)
    next: str = Field(min_length=1, max_length=64)


class TerminalNode(FrozenModel):
    kind: Literal[NodeKind.FINISH, NodeKind.BLOCKED]


WorkflowNodeConfig = Annotated[
    NextNode
    | InvokeAgentNode
    | RouteNode
    | VerifyNode
    | ReviewNode
    | ParallelNode
    | TerminalNode,
    Field(discriminator="kind"),
]


class WorkflowConfig(FrozenModel):
    entry: str = Field(min_length=1, max_length=64)
    max_iterations: int = Field(default=40, ge=1, le=1_000)
    nodes: dict[str, WorkflowNodeConfig]

    @staticmethod
    def _targets(node: WorkflowNodeConfig) -> tuple[str, ...]:
        if isinstance(node, (RouteNode, VerifyNode)):
            return tuple(node.routes.values())
        if isinstance(node, (NextNode, InvokeAgentNode, ReviewNode, ParallelNode)):
            return (node.next,)
        return ()

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowConfig:
        if self.entry not in self.nodes:
            raise ValueError("workflow entry must reference a configured node")
        if not any(node.kind == NodeKind.FINISH for node in self.nodes.values()):
            raise ValueError("workflow must contain a finish node")
        for name, node in self.nodes.items():
            missing = sorted(
                reference
                for reference in self._targets(node)
                if reference not in self.nodes
            )
            if missing:
                raise ValueError(f"workflow node {name!r} references missing nodes: {missing}")

        pending = [(self.entry, False)]
        visited: set[tuple[str, bool]] = set()
        reachable: set[str] = set()
        reached_finish = False
        while pending:
            name, verified = pending.pop()
            state = (name, verified)
            if state in visited:
                continue
            visited.add(state)
            reachable.add(name)
            node = self.nodes[name]
            verified = verified or node.kind == NodeKind.VERIFY
            if node.kind == NodeKind.FINISH:
                reached_finish = True
                if not verified:
                    raise ValueError("every workflow path to finish must pass through verify")
            pending.extend((target, verified) for target in self._targets(node))
        if not reached_finish:
            raise ValueError("workflow entry cannot reach a finish node")
        unreachable = sorted(set(self.nodes) - reachable)
        if unreachable:
            raise ValueError(f"workflow contains unreachable nodes: {unreachable}")
        return self


class ToolOutputConfig(FrozenModel):
    max_bytes: int = Field(default=16_000, ge=1_024, le=1_000_000)


class SearchConfig(FrozenModel):
    backend: Literal["auto", "fff", "disabled"] = "auto"
    default_page_size: int = Field(default=20, ge=1, le=50)
    max_page_size: int = Field(default=50, ge=1, le=50)

    @model_validator(mode="after")
    def validate_page_sizes(self) -> SearchConfig:
        if self.default_page_size > self.max_page_size:
            raise ValueError("default search page size cannot exceed its maximum")
        return self


class ToolSurfaceConfig(FrozenModel):
    visible: tuple[Literal["read", "bash", "edit", "write"], ...] = FOUR_CODING_TOOLS
    read_default_lines: int = Field(default=400, ge=1, le=400)
    bash_default_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    bash_max_timeout_seconds: int = Field(default=600, ge=1, le=3_600)
    output: ToolOutputConfig = ToolOutputConfig()
    search: SearchConfig = SearchConfig()

    @model_validator(mode="after")
    def validate_surface(self) -> ToolSurfaceConfig:
        if self.visible != FOUR_CODING_TOOLS:
            raise ValueError("coding tool surface must be exactly read, bash, edit, write")
        if self.bash_default_timeout_seconds > self.bash_max_timeout_seconds:
            raise ValueError("default bash timeout cannot exceed its maximum")
        return self


class ContextConfig(FrozenModel):
    compact_at_tokens: int = Field(default=80_000, ge=4_096, le=2_000_000)
    work_packet_tokens: int = Field(default=20_000, ge=2_000, le=256_000)
    max_task_input_tokens: int = Field(default=200_000, ge=8_000, le=20_000_000)
    recent_event_limit: int = Field(default=12, ge=1, le=100)
    repository_map_tokens: int = Field(default=1_200, ge=128, le=16_000)
    skill_context_bytes: int = Field(default=24_000, ge=0, le=1_000_000)
    max_selected_skills: int = Field(default=3, ge=0, le=20)
    ledger_tokens: int = Field(default=2_000, ge=200, le=16_000)
    manifest_tokens: int = Field(default=800, ge=100, le=8_000)
    compaction_tokens: int = Field(default=3_000, ge=0, le=64_000)
    recent_event_tokens: int = Field(default=3_500, ge=0, le=64_000)
    steering_tokens: int = Field(default=1_000, ge=0, le=16_000)

    @model_validator(mode="after")
    def validate_work_packet_budget(self) -> ContextConfig:
        if self.max_task_input_tokens < self.work_packet_tokens:
            raise ValueError(
                "max_task_input_tokens cannot be smaller than work_packet_tokens"
            )
        return self


class SafetyConfig(FrozenModel):
    allow_dependency_install: bool = False
    allow_network: bool = False
    allow_git_history_mutation: bool = False
    allow_unknown_commands: bool = False
    destructive_commands: Literal["deny"] = "deny"
    publishing_commands: Literal["approval_required"] = "approval_required"
    redact_environment_names: tuple[str, ...] = ()


class LocalSandboxConfig(FrozenModel):
    kind: Literal["local"] = "local"
    memory_bytes: int = Field(default=4 * 1024**3, ge=64 * 1024**2)
    max_processes: int = Field(default=256, ge=1, le=32_768)
    max_file_bytes: int = Field(default=1024**3, ge=1024**2)


class DockerSandboxConfig(FrozenModel):
    kind: Literal["docker"]
    image: str = Field(min_length=1, max_length=256)
    cpus: float = Field(default=2, gt=0, le=128)
    memory: str = Field(default="4g", pattern=r"^[1-9][0-9]*[kKmMgG]$")
    pids_limit: int = Field(default=256, ge=1, le=32_768)
    network_disabled: Literal[True] = True


class KubernetesSandboxConfig(FrozenModel):
    kind: Literal["kubernetes"]
    namespace: str = Field(min_length=1, max_length=128)
    pod: str = Field(min_length=1, max_length=253)
    container: str | None = Field(default=None, max_length=128)
    remote_workspace: str = Field(pattern=r"^/")
    network_isolated: Literal[True]


class RemoteSandboxConfig(FrozenModel):
    kind: Literal["remote"]
    endpoint: str = Field(pattern=r"^https://")
    token: SecretRef
    remote_workspace: str = Field(pattern=r"^/")
    max_response_bytes: int = Field(default=2_000_000, ge=1_024, le=64_000_000)


SandboxConfig = Annotated[
    LocalSandboxConfig | DockerSandboxConfig | KubernetesSandboxConfig | RemoteSandboxConfig,
    Field(discriminator="kind"),
]


class SteeringConfig(FrozenModel):
    enabled: bool = True
    lease_seconds: int = Field(default=900, ge=30, le=86_400)
    batch_limit: int = Field(default=4, ge=1, le=20)
    max_message_bytes: int = Field(default=4_096, ge=256, le=4_096)
    safe_points: tuple[Literal["before_model", "before_tool", "work_batch_boundary"], ...] = (
        "before_model",
        "before_tool",
        "work_batch_boundary",
    )

    @model_validator(mode="after")
    def validate_safe_points(self) -> SteeringConfig:
        if len(set(self.safe_points)) != len(self.safe_points):
            raise ValueError("steering safe points must be unique")
        if self.enabled and not self.safe_points:
            raise ValueError("enabled steering requires at least one safe point")
        if "before_tool" in self.safe_points and "before_model" not in self.safe_points:
            raise ValueError("before_tool steering requires before_model delivery")
        return self


class ContextCacheConfig(FrozenModel):
    min_tokens: int = Field(default=4_096, ge=0)
    ttl_seconds: int = Field(default=1_800, ge=60)
    cache_intervals: int = Field(default=10, ge=1)


class EventCompactionConfig(FrozenModel):
    interval: int | None = Field(default=None, ge=1)
    overlap: int | None = Field(default=None, ge=0)
    token_threshold: int = Field(default=96_000, ge=4_096)
    retention: int = Field(default=20, ge=1)


class AdkConfig(FrozenModel):
    context_cache: ContextCacheConfig = ContextCacheConfig()
    event_compaction: EventCompactionConfig = EventCompactionConfig()
    resumable: Literal[True] = True


class TraceConfig(FrozenModel):
    mode: Literal["off", "metadata", "redacted"] = "metadata"
    max_content_bytes: int = Field(default=8_192, ge=64, le=1_000_000)


class SkillConfig(FrozenModel):
    project_root_enabled: bool = True
    additional_roots: tuple[Path, ...] = ()


class LearningConfig(FrozenModel):
    enabled: bool = True
    minimum_support: int = Field(default=3, ge=2)
    trial_percent: int = Field(default=20, ge=0, le=100)


class ReviewerConfig(FrozenModel):
    enabled: bool = False
    agent: str | None = "final_diff_reviewer"
    max_chars: int = Field(default=60_000, ge=1_000, le=1_000_000)


class PiCodingConfig(FrozenModel):
    """Typed behavior payload owned by the pi_coding_v1 implementation."""

    models: dict[str, ModelConfig]
    agents: dict[str, AgentConfig]
    workflow: WorkflowConfig
    tools: ToolSurfaceConfig = ToolSurfaceConfig()
    context: ContextConfig = ContextConfig()
    safety: SafetyConfig = SafetyConfig()
    sandbox: SandboxConfig = LocalSandboxConfig()
    steering: SteeringConfig = SteeringConfig()
    adk: AdkConfig = AdkConfig()
    tracing: TraceConfig = TraceConfig()
    skills: SkillConfig = SkillConfig()
    learning: LearningConfig = LearningConfig()
    reviewer: ReviewerConfig = ReviewerConfig()

    @model_validator(mode="after")
    def validate_references(self) -> PiCodingConfig:
        for name, agent in self.agents.items():
            if agent.model not in self.models:
                raise ValueError(f"agent {name!r} references unknown model {agent.model!r}")
        for name, node in self.workflow.nodes.items():
            agent_names: tuple[str, ...] = ()
            if isinstance(node, (InvokeAgentNode, ReviewNode)):
                agent_names = (node.agent,)
            elif isinstance(node, ParallelNode):
                agent_names = node.agents
            missing = sorted(agent for agent in agent_names if agent not in self.agents)
            if missing:
                raise ValueError(f"workflow node {name!r} references unknown agents: {missing}")
        if self.reviewer.enabled and self.reviewer.agent not in self.agents:
            raise ValueError("enabled reviewer references an unknown agent")
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
                    "enabled": self.reviewer.enabled,
                    "next": "finish",
                },
                "finish": {"kind": "finish"},
                "blocked": {"kind": "blocked"},
            },
        }
        actual_workflow = self.workflow.model_dump(
            mode="json",
            exclude={"max_iterations"},
        )
        if actual_workflow != required_workflow:
            raise ValueError(
                "pi_coding_v1 has a fixed verified workflow graph; register another "
                "harness implementation for a different topology"
            )
        if set(self.agents) != {"coding_worker", "final_diff_reviewer"}:
            raise ValueError(
                "pi_coding_v1 accepts only coding_worker and final_diff_reviewer"
            )
        worker = self.agents["coding_worker"]
        reviewer = self.agents["final_diff_reviewer"]
        if (
            worker.kind != "llm"
            or worker.tools != FOUR_CODING_TOOLS
            or worker.output_schema != "agent_step"
            or worker.mode != "multi_turn"
        ):
            raise ValueError("pi_coding_v1 requires the coding_worker contract")
        if (
            reviewer.kind != "reviewer"
            or reviewer.tools
            or reviewer.output_schema != "final_diff_review"
            or reviewer.mode != "single_turn"
        ):
            raise ValueError("pi_coding_v1 requires the final_diff_reviewer contract")
        referenced_models = {agent.model for agent in self.agents.values()}
        if set(self.models) != referenced_models:
            raise ValueError(
                "pi_coding_v1 model entries must be referenced by a configured agent"
            )
        return self


class HarnessSelectionConfig(FrozenModel):
    """Safe implementation key plus its registry-validated typed payload."""

    implementation: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    api_version: int = Field(default=1, ge=1, le=1_000)
    required_capabilities: tuple[HarnessCapability, ...] = (
        "streaming",
        "steering",
        "tool_events",
        "state_snapshots",
        "artifacts",
    )
    config: SerializeAsAny[BaseModel]

    @field_validator("config", mode="before")
    @classmethod
    def require_registry_validated_config(cls, value: object) -> object:
        if not isinstance(value, BaseModel):
            raise ValueError(
                "harness config must be validated through parse_harness_composition"
            )
        return value

    @model_validator(mode="after")
    def validate_capabilities(self) -> HarnessSelectionConfig:
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("required capabilities must be unique")
        return self


class PersistenceConfig(FrozenModel):
    session_backend: Literal["in_memory", "sqlite", "database", "vertex"] = "in_memory"
    session_database_url: SecretRef | None = None
    artifact_backend: Literal["in_memory", "file", "gcs"] = "in_memory"
    memory_backend: Literal["in_memory", "vertex"] = "in_memory"
    gcs_bucket: str | None = Field(default=None, max_length=256)
    cloud_project: str | None = Field(default=None, max_length=128)
    cloud_location: str = Field(default="us-central1", max_length=64)
    agent_engine_id: str | None = Field(default=None, max_length=256)
    memory_bank_id: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_backends(self) -> PersistenceConfig:
        if self.session_backend == "database" and self.session_database_url is None:
            raise ValueError("database sessions require session_database_url")
        if self.artifact_backend == "gcs" and not self.gcs_bucket:
            raise ValueError("GCS artifacts require gcs_bucket")
        if "vertex" in {self.session_backend, self.memory_backend} and (
            not self.cloud_project or not self.agent_engine_id
        ):
            raise ValueError("Vertex persistence requires cloud_project and agent_engine_id")
        return self


class ServerConfig(FrozenModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8_765, ge=1, le=65_535)
    websocket_path: str = Field(default="/v1/agent", pattern=r"^/")
    protocol: Literal["ag_ui_websocket_v1"] = "ag_ui_websocket_v1"
    max_connections: int = Field(default=32, ge=1, le=10_000)
    outbound_queue_size: int = Field(default=256, ge=1, le=100_000)
    first_event_timeout_seconds: float = Field(default=120, gt=0, le=3_600)
    idle_timeout_seconds: float = Field(default=180, gt=0, le=3_600)
    total_timeout_seconds: float = Field(default=1_800, gt=0, le=86_400)
    first_event_retries: int = Field(default=1, ge=0, le=3)
    close_timeout_seconds: float = Field(default=15, gt=0, le=300)

    @model_validator(mode="after")
    def validate_liveness_deadlines(self) -> ServerConfig:
        if self.total_timeout_seconds < self.first_event_timeout_seconds:
            raise ValueError(
                "server total_timeout_seconds cannot be shorter than "
                "first_event_timeout_seconds"
            )
        return self


class HarnessComposition(FrozenModel):
    schema_version: Literal[1] = 1
    app: AppConfig
    harness: HarnessSelectionConfig
    persistence: PersistenceConfig = PersistenceConfig()
    server: ServerConfig = ServerConfig()

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def composition_sha256(self) -> str:
        """Hash the complete portable configuration, including deployment settings."""

        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    @property
    def behavior_sha256(self) -> str:
        """Hash harness behavior without server or persistence deployment details."""

        harness = self.harness.model_dump(mode="json")
        harness["required_capabilities"] = sorted(harness["required_capabilities"])
        payload = json.dumps(
            {"app": self.app.model_dump(mode="json"), "harness": harness},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def resolved_behavior_sha256(self, configuration_root: Path) -> str:
        """Include file-prompt contents without hashing machine-specific absolute paths."""

        harness = self.harness.model_dump(mode="json")
        harness["required_capabilities"] = sorted(harness["required_capabilities"])
        agents = harness["config"]["agents"]
        for agent in agents.values():
            prompt = agent["prompt"]
            if prompt["source"] != "file":
                continue
            portable_path = Path(prompt["path"])
            if portable_path.is_absolute():
                raise ValueError(
                    "file prompt paths must be relative to the configuration root"
                )
            root = configuration_root.expanduser().resolve()
            resolved = (root / portable_path).resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise ValueError("file prompt escapes the configuration root") from error
            content = resolved.read_bytes()
            if len(content) > 128_000:
                raise ValueError("file prompt exceeds 128000 bytes")
            prompt["sha256"] = hashlib.sha256(content).hexdigest()
        payload = json.dumps(
            {"app": self.app.model_dump(mode="json"), "harness": harness},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class RuntimeBindings(FrozenModel):
    """Volatile task/process identity intentionally excluded from YAML behavior."""

    workspace: Path
    state_root: Path
    configuration_root: Path | None = None
    source_repository: Path | None = None
    task_id: str | None = Field(default=None, max_length=256)
    base_revision: str | None = Field(default=None, max_length=256)
    workspace_id: str | None = Field(default=None, max_length=256)
    worker_id: str | None = Field(default=None, max_length=256)
    invocation_id: str | None = Field(default=None, max_length=256)
    control_database_url: SecretStr | None = Field(default=None)
    project_trusted: bool = False


__all__ = [
    "FOUR_CODING_TOOLS",
    "AgentConfig",
    "HarnessCapability",
    "HarnessComposition",
    "HarnessSelectionConfig",
    "ModelConfig",
    "PiCodingConfig",
    "PromptConfig",
    "RuntimeBindings",
    "SandboxConfig",
    "SecretRef",
    "ServerConfig",
    "ToolSurfaceConfig",
    "WorkflowConfig",
    "WorkflowNodeConfig",
]
