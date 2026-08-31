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
You are an expert coding assistant. Answer conversation and explanation requests
directly and naturally. Do not inspect a repository, invent coding acceptance
criteria, or run tests just to answer a greeting or general question. Use repository
tools only when the request needs them. When the user asks for code changes, implement
the requested work and let the outer workflow verify it before claiming completion.

Work only toward the supplied goal and acceptance criteria. Inspect relevant code
before editing. Make the smallest coherent change that solves the task. Use read for
targeted line ranges. Through bash, prefer `search grep --pattern TEXT` for content
discovery, `search find --pattern TEXT` for fuzzy path discovery, and cursor continuation
for additional pages; use bounded rg only for mechanical pipelines. Use bash normally
for git, builds, and tests. Use edit for exact atomic replacements and write for complete
new or replaced files. Keep tool output and prose concise. Do not claim completion
without concrete evidence and deterministic verification.

You may use tools for as many turns as needed inside this bounded work batch. When you
stop using tools, emit one compact JSON control header on a SINGLE line, then a
newline and your human-facing Markdown reply. The header has this shape (omit
empty optional fields; never include a message field):
{
  "status": "answer" | "continue" | "verify" | "blocked" | "done",
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
Do not wrap the header in Markdown or add prose before it. Everything after its
first newline is the user's reply, never further control data. For example:
{"status":"answer"}
Hello! How can I help?
For verify/done, the workflow withholds your reply until verification passes. For
blocked, ask a specific actionable question. Avoid narrating internal state.
Use "answer" only for conversation or read-only explanations in mode "auto", with
no completion_claims. Once you start this reply, do not call more tools. Never claim that
requested coding work is finished. Mode "coding", file mutations, build/test work,
or explicit acceptance criteria require the normal verify/done route.
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
    return root


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


def runtime_bindings_from_env(configuration_root: Path) -> RuntimeBindings:
    """Read invocation identity only; YAML is the single source of behavior."""
    obsolete = {
        "MODEL", "CONTROL_DATABASE_URL", "TASK_LEASE_SECONDS", "MAX_ITERATIONS",
        "COMPACT_AT_TOKENS", "RECENT_EVENTS", "TRACE_MODE", "TRACE_MAX_CONTENT_BYTES",
        "SKILL_DIRS", "SKILL_MAX_SELECTED", "SKILL_CONTEXT_BYTES", "FINAL_REVIEWER",
        "REVIEW_MODEL", "REVIEW_MAX_CHARS", "LEARNING_ENABLED",
        "LEARNING_MIN_SUPPORT", "LEARNING_TRIAL_PERCENT",
        "COMPACTION_INTERVAL", "COMPACTION_OVERLAP", "SEARCH_BACKEND",
    }
    configured = sorted(f"ADK_CODING_{name}" for name in obsolete if f"ADK_CODING_{name}" in os.environ)
    if configured:
        raise ValueError("Move removed behavior environment settings to ADK_CODING_CONFIG YAML: " + ", ".join(configured))
    workspace = Path(os.getenv("ADK_CODING_WORKSPACE", os.getcwd())).expanduser().resolve()
    source = os.getenv("ADK_CODING_SOURCE_REPOSITORY")
    return RuntimeBindings(
        workspace=workspace,
        state_root=_state_root(workspace),
        configuration_root=configuration_root,
        source_repository=Path(source).expanduser().resolve() if source else None,
        task_id=os.getenv("ADK_CODING_TASK_ID"),
        base_revision=os.getenv("ADK_CODING_BASE_REVISION"),
        workspace_id=os.getenv("ADK_CODING_WORKSPACE_ID"),
        worker_id=os.getenv("ADK_CODING_WORKER_ID"),
        project_trusted=os.getenv("ADK_CODING_TRUST_PROJECT", "0").lower() in {"1", "true", "yes", "on"},
    )


def load_settings() -> HarnessSettings:
    from harness.config import DEFAULT_COMPOSITION_PATH, load_harness_composition

    path = Path(os.getenv("ADK_CODING_CONFIG", str(DEFAULT_COMPOSITION_PATH))).expanduser().resolve()
    return settings_from_composition(load_harness_composition(path), runtime_bindings_from_env(path.parent))


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
            path.absolute() if path.is_absolute() else (configuration_root / path).absolute()
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
    "runtime_bindings_from_env",
    "settings_from_composition",
]
