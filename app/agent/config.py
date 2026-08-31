"""Environment-derived configuration for the Agents CLI application."""

from __future__ import annotations

import hashlib
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from harness.config import (
    HarnessComposition,
    PiCodingConfig,
    PromptConfig,
    RuntimeBindings,
)
from harness.context import build_static_prefix
from harness.repo import collect_project_instructions

_BASE_INSTRUCTION = """
You are an expert coding agent operating in an isolated repository workspace.

Work only toward the supplied goal and acceptance criteria. Inspect relevant code
before editing. Make the smallest coherent change that solves the task. Use read for
targeted line ranges. Through bash, prefer `search grep --pattern TEXT` for content
discovery, `search find --pattern TEXT` for fuzzy path discovery, and cursor continuation
for additional pages; use bounded rg only for mechanical pipelines. Use bash normally
for git, builds, and tests. Use edit for exact atomic replacements and write for complete
new or replaced files. Keep tool output and prose concise. Do not claim completion
without concrete evidence and deterministic verification.

You may use tools for as many turns as needed inside this bounded work batch. When you
stop using tools, your final response MUST be exactly one JSON object with this shape:
{
  "status": "continue" | "verify" | "blocked" | "done",
  "progress": ["concise completed or discovered item"],
  "next_action": "one concrete next action or null",
  "decisions": ["decision and rationale"],
  "questions": ["question requiring user input"],
  "discovered_constraints": ["newly discovered constraint"],
  "files_in_focus": ["repository/relative/path"],
  "completion_claims": [
    {
      "criterion": "exact acceptance criterion",
      "evidence": ["test, command, path, or other concrete evidence; always an array"]
    }
  ]
}

Use status "verify" or "done" once the implementation is ready for the outer
workflow's deterministic checks. Completion claims help diagnosis but never decide
success; the outer workflow—not this response—decides whether the task is complete.
Do not wrap the JSON in Markdown or add explanatory prose before or after it.
""".strip()


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    app_name: str
    model: str
    workspace: Path
    source_repository: Path | None
    state_root: Path
    task_id_override: str | None
    base_revision_override: str | None
    workspace_id_override: str | None
    control_database_url: str | None
    worker_id: str
    task_lease_seconds: int
    max_iterations: int
    compact_at_tokens: int
    recent_event_limit: int
    static_instruction: str
    static_prefix: str
    trace_mode: str
    trace_max_content_bytes: int
    skill_roots: tuple[Path, ...]
    skill_max_selected: int
    skill_context_bytes: int
    project_trusted: bool


def _state_root(workspace: Path) -> Path:
    configured = os.getenv("ADK_CODING_STATE_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        digest = hashlib.sha256(workspace.as_posix().encode()).hexdigest()[:16]
        root = Path.home() / ".cache" / "adk-coding-agent" / digest
    root.mkdir(parents=True, exist_ok=True)
    return root


def _enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _trace_mode() -> str:
    value = os.getenv("ADK_CODING_TRACE_MODE", "metadata").strip().lower()
    if value not in {"off", "metadata", "redacted"}:
        raise ValueError(
            "ADK_CODING_TRACE_MODE must be one of: off, metadata, redacted"
        )
    return value


def _skill_roots(workspace: Path, *, project_trusted: bool) -> tuple[Path, ...]:
    roots = [workspace / ".agents" / "skills"] if project_trusted else []
    configured = os.getenv("ADK_CODING_SKILL_DIRS", "").strip()
    if configured:
        roots.extend(
            Path(value).expanduser()
            for value in configured.split(os.pathsep)
            if value.strip()
        )
    return tuple(dict.fromkeys(path.absolute() for path in roots))


def resolve_prompt_text(
    prompt: PromptConfig,
    *,
    configuration_root: Path,
    builtin_name: str,
    builtin_text: str,
    max_bytes: int = 128_000,
) -> str:
    """Resolve one portable prompt without allowing path or symlink escape."""

    if prompt.source == "builtin":
        if prompt.name != builtin_name:
            raise ValueError(
                f"unsupported builtin prompt {prompt.name!r}; expected {builtin_name!r}"
            )
        return builtin_text
    assert prompt.path is not None
    if prompt.path.is_absolute():
        raise ValueError("file prompt paths must be relative to the configuration root")
    root = configuration_root.expanduser().resolve()
    resolved = (root / prompt.path).resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("file prompt escapes the configuration root") from error
    if not resolved.is_file():
        raise ValueError("file prompt must resolve to a regular file")
    content = resolved.read_bytes()
    if len(content) > max_bytes:
        raise ValueError(f"file prompt exceeds {max_bytes} bytes")
    return content.decode("utf-8").strip()


def load_settings() -> HarnessSettings:
    workspace = Path(os.getenv("ADK_CODING_WORKSPACE", os.getcwd())).resolve()
    source_value = os.getenv("ADK_CODING_SOURCE_REPOSITORY")
    model = os.getenv("ADK_CODING_MODEL", "gemini-3.7-flash")
    worker_id = os.getenv("ADK_CODING_WORKER_ID", "").strip()
    if not worker_id:
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
    project_trusted = _enabled("ADK_CODING_TRUST_PROJECT")
    project_instructions = (
        collect_project_instructions(workspace) if project_trusted else ""
    )
    if len(project_instructions) > 16_000:
        project_instructions = (
            project_instructions[:16_000]
            + "\n[project instructions truncated; read the source files when needed]"
        )
    instruction = _BASE_INSTRUCTION
    if project_instructions:
        instruction += "\n\nStable project instructions:\n" + project_instructions

    state_root = _state_root(workspace)
    return HarnessSettings(
        app_name="pi_inspired_adk_coding_agent",
        model=model,
        workspace=workspace,
        source_repository=(Path(source_value).resolve() if source_value else None),
        state_root=state_root,
        task_id_override=os.getenv("ADK_CODING_TASK_ID"),
        base_revision_override=os.getenv("ADK_CODING_BASE_REVISION"),
        workspace_id_override=os.getenv("ADK_CODING_WORKSPACE_ID"),
        control_database_url=(
            os.getenv("ADK_CODING_CONTROL_DATABASE_URL", "").strip() or None
        ),
        worker_id=worker_id,
        task_lease_seconds=max(
            30,
            int(os.getenv("ADK_CODING_TASK_LEASE_SECONDS", "900")),
        ),
        max_iterations=int(os.getenv("ADK_CODING_MAX_ITERATIONS", "40")),
        compact_at_tokens=int(
            os.getenv("ADK_CODING_COMPACT_AT_TOKENS", "80000")
        ),
        recent_event_limit=int(os.getenv("ADK_CODING_RECENT_EVENTS", "12")),
        static_instruction=instruction,
        static_prefix=build_static_prefix(
            model_name=model,
            instruction=instruction,
        ),
        trace_mode=_trace_mode(),
        trace_max_content_bytes=max(
            64,
            int(os.getenv("ADK_CODING_TRACE_MAX_CONTENT_BYTES", "8192")),
        ),
        skill_roots=_skill_roots(workspace, project_trusted=project_trusted),
        skill_max_selected=max(
            0,
            int(os.getenv("ADK_CODING_SKILL_MAX_SELECTED", "3")),
        ),
        skill_context_bytes=max(
            0,
            int(os.getenv("ADK_CODING_SKILL_CONTEXT_BYTES", "24000")),
        ),
        project_trusted=project_trusted,
    )


def settings_from_composition(
    composition: HarnessComposition,
    bindings: RuntimeBindings,
) -> HarnessSettings:
    """Resolve validated declarative behavior against volatile runtime bindings."""

    config = composition.harness.config
    if not isinstance(config, PiCodingConfig):
        raise TypeError("pi_coding_v1 requires PiCodingConfig")
    workspace = bindings.workspace.expanduser().resolve()
    state_root = bindings.state_root.expanduser().resolve()
    configuration_root = (bindings.configuration_root or workspace).expanduser().resolve()
    worker_config = config.agents["coding_worker"]
    instruction = resolve_prompt_text(
        worker_config.prompt,
        configuration_root=configuration_root,
        builtin_name="coding_worker_v1",
        builtin_text=_BASE_INSTRUCTION,
    )
    project_instructions = (
        collect_project_instructions(workspace) if bindings.project_trusted else ""
    )
    if len(project_instructions) > 16_000:
        project_instructions = (
            project_instructions[:16_000]
            + "\n[project instructions truncated; read the source files when needed]"
        )
    if project_instructions:
        instruction += "\n\nStable project instructions:\n" + project_instructions

    coding_model = config.models[worker_config.model].name
    skill_roots: list[Path] = []
    if config.skills.project_root_enabled and bindings.project_trusted:
        skill_roots.append(workspace / ".agents" / "skills")
    for configured in config.skills.additional_roots:
        path = configured.expanduser()
        skill_roots.append(
            path.resolve() if path.is_absolute() else (configuration_root / path).resolve()
        )
    worker_id = bindings.worker_id
    if not worker_id:
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
    return HarnessSettings(
        app_name=composition.app.name,
        model=coding_model,
        workspace=workspace,
        source_repository=(
            bindings.source_repository.expanduser().resolve()
            if bindings.source_repository is not None
            else None
        ),
        state_root=state_root,
        task_id_override=bindings.task_id,
        base_revision_override=bindings.base_revision,
        workspace_id_override=bindings.workspace_id,
        control_database_url=(
            bindings.control_database_url.get_secret_value()
            if bindings.control_database_url is not None
            else None
        ),
        worker_id=worker_id,
        task_lease_seconds=config.steering.lease_seconds,
        max_iterations=config.workflow.max_iterations,
        compact_at_tokens=config.context.compact_at_tokens,
        recent_event_limit=config.context.recent_event_limit,
        static_instruction=instruction,
        static_prefix=build_static_prefix(
            model_name=coding_model,
            instruction=instruction,
        ),
        trace_mode=config.tracing.mode,
        trace_max_content_bytes=config.tracing.max_content_bytes,
        skill_roots=tuple(dict.fromkeys(skill_roots)),
        skill_max_selected=config.context.max_selected_skills,
        skill_context_bytes=config.context.skill_context_bytes,
        project_trusted=bindings.project_trusted,
    )


__all__ = [
    "HarnessSettings",
    "load_settings",
    "settings_from_composition",
]
