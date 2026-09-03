# A Pi-Inspired, Token-Efficient Coding Harness on Google ADK 2.x

## Executive conclusion

A competitive coding harness is not primarily a collection of agents, tools, or retrieval systems. It is a **context allocator and deterministic control plane around a strong model**.

Pi's apparent advantage comes from a disciplined combination of:

1. A very small, stable system prompt.
2. Four broadly composable coding tools.
3. Progressive disclosure of project knowledge and skills.
4. Bounded tool outputs rather than full logs in context.
5. An append-oriented transcript that preserves prompt-cache prefixes.
6. Structured compaction that deliberately resets the cache only when necessary.
7. Persistent, branchable sessions.
8. A philosophy of keeping workflow logic outside the model unless the model actually needs to reason about it.

The proposed system should use **Google ADK 2.x as the durable execution substrate**, but should not expose most of ADK's potential complexity to the coding model. ADK should own workflow execution, persistence, resumability, state reduction, policy enforcement, and verification. The coding model should see a stable prompt, a compact task packet, and four tools.

The design can be summarized as:

> **Pi-like model interface, ADK-managed execution, event-sourced memory, compact explicit goal state, and machine-verified completion.**

### Current implementation profile

The four-tool surface described throughout this original design remains the default
and compatibility baseline. The implemented, disabled-by-default notebook-PTC profile
instead exposes one persistent `python` tool. Python composes the same confined file,
Bash/CLI, and registered MCP capabilities through a broker; it does not bypass policy
or verification. Its canonical notebook is a rebuildable, timestamped workbench over
the append-only ledger, while the resident CPython worker alone owns current live
variables. See [the trace-native REPL specification](trace-native-repl-agent.md) for
the authority model, compaction views, recovery protocol, `nb-cli` boundary, and
remaining ablation gate.

---

## 1. What should be copied from Pi

Pi keeps the core small and surrounds it with extensions, skills, prompt templates, and other composable resources. Its default coding interface is deliberately narrow: project instructions, a concise system prompt, and a small set of file and shell primitives.

The most important ideas to preserve are:

| Pi idea | Why it matters |
|---|---|
| Small stable system prompt | Improves prompt-cache reuse and reduces irrelevant instruction competition. |
| Four broadly composable coding tools | Reduces schema tokens, tool-selection ambiguity, and model-specific tool-call failures. |
| Shell as the composition layer | Lets mature programs such as `rg`, `git`, compilers, and test runners do what they already do well. |
| Progressive disclosure | Keeps skills, documentation, and uncommon workflows out of normal context. |
| Append-oriented sessions | Preserves audit history and stable cached prefixes. |
| Structured compaction | Retains task continuity while bounding context. |
| Session branching | Supports experimentation and rollback without contaminating the main task path. |
| Model-independent summaries | Makes sessions portable across providers and models. |
| Explicit token and cost observability | Makes harness optimization measurable. |
| Primitives rather than mandatory workflows | Allows the outer harness to evolve without growing the model-facing interface. |

Pi's default model-visible coding tools are effectively `read`, `bash`, `edit`, and `write`. Search, file discovery, Git inspection, builds, tests, and formatters are composed through shell commands instead of being exposed as a large tool registry.

Pi also treats skills as progressive disclosure. At startup, only names and descriptions are visible. Full `SKILL.md` instructions, scripts, and references load only when relevant.

Its sessions are append-oriented and branchable. Compaction retains a recent raw tail, summarizes older work, preserves file-operation metadata, and writes a structured handoff containing the goal, constraints, progress, decisions, next steps, critical context, and files read or modified.

The underlying caching idea is simple: provider prompt caching works best when the beginning of the request remains byte-for-byte stable. Appending turns preserves the reusable prefix; repeatedly rewriting the prompt, tool registry, memory section, or historical summary does not. Compaction is therefore a deliberate cache reset rather than harmless housekeeping.

---

## 2. What should be adapted rather than copied

Pi deliberately avoids built-in planning and to-do systems. That is sensible for a lightweight interactive terminal agent, but a managed, resumable ADK coding service needs a small amount of explicit control state.

The adaptation should be a **Task Ledger** maintained by the control plane, not a verbose checklist repeatedly managed through natural language. The ledger exists to make execution durable, detect goal drift, and support verification. Only a compact projection of it should enter model context.

Pi also relies heavily on shell search and selective file reads rather than requiring a heavyweight code index. That works well for many repositories. For very large enterprise monorepos, however, the harness should add a compact structural repository map. This map is a navigation aid, not a repository dump.

The proposed harness should combine:

- Pi's shell-first exploration for precision.
- A compact structural map for orientation.
- Semantic retrieval only as a fallback.
- ADK-managed state and workflow control outside the model.

---

## 3. What should not be copied

The managed harness should reject the following defaults:

| Reject | Reason |
|---|---|
| Unrestricted shell execution | Managed agents need workspace, network, secret, and destructive-command controls. |
| Dozens of MCP tools in the prompt | Tool descriptions consume context and increase selection ambiguity. |
| Automatic RAG on every turn | Prompt-dependent memory injection changes the prefix, adds noise, and can destroy cache reuse. |
| Default subagent swarms | They duplicate context, complicate attribution, and often generate more communication than useful work. |
| Model-declared completion | "Done" is a hypothesis that must be checked by deterministic verification. |
| Raw transcript as operational state | A transcript is an audit trail, not a reliable representation of the current task. |
| Continuous rewriting of plans and summaries | Rewriting stable prompt sections forfeits cache reuse. |
| Self-modifying tools in the first version | It expands the safety and evaluation surface before the core loop is proven. |

MCP remains useful for integration, but it should not dictate the model-facing interface. Integrations should normally be hidden behind a broker, CLI, or progressively loaded skill until needed.

---

## 4. Reference architecture

```text
+--------------------------------------------------------------------+
| Clients                                                            |
| CLI | IDE extension | API | code-review bot | managed task queue   |
+-------------------------------+------------------------------------+
                                |
+-------------------------------v------------------------------------+
| Google ADK App                                                     |
| Persistent SessionService | Resumability | Plugins | Telemetry     |
+-------------------------------+------------------------------------+
                                |
+-------------------------------v------------------------------------+
| Dynamic Coding Workflow                                            |
|                                                                    |
| initialize/restore                                                  |
|      |                                                             |
| compile_context -> invoke_coding_agent -> reduce_events             |
|      ^                                  |                           |
|      |                                  +-> continue                |
|      |                                  +-> replan                  |
|      |                                  +-> compact                 |
|      |                                  +-> blocked/HITL            |
|      |                                  +-> verify                  |
|      |                                         |                    |
|      +------------- failed verification <------+                    |
|                                                |                    |
|                                           finish task              |
+---------------+-----------------+-------------------+--------------+
                |                 |                   |
     +----------v------+ +--------v--------+ +--------v------------+
     | Coding Agent    | | Context System  | | Verification System |
     | Stable prompt   | | Task Ledger     | | build/type/lint     |
     | Four tools      | | compaction      | | tests/scope/review  |
     +----------+------+ | repo map/memory | +---------------------+
                |        +--------+--------+
     +----------v-----------------v----------------------------------+
     | Isolated Workspace                                           |
     | Git worktree | sandbox/container | files | compiler | tests  |
     +----------+-----------------+----------------------------------+
                |                 |
     +----------v--------+ +------v---------------------------------+
     | Repository Index  | | Durable Stores                         |
     | symbols/imports   | | events | checkpoints | artifacts       |
     | lexical/graph     | | tool receipts | project memory         |
     +-------------------+ +----------------------------------------+
```

The architecture separates five kinds of information that are often incorrectly merged into one prompt:

1. **Execution log:** what happened.
2. **Task state:** what remains to be accomplished.
3. **Model working set:** what the model needs for the next decision.
4. **Workspace:** the current code and generated artifacts.
5. **Project memory:** stable knowledge likely to matter in future sessions.

A transcript may remain append-only and complete while the model sees only a carefully compiled subset.

---

## 5. Core design principles

### 5.1 The harness is a context compiler

Before each coding work batch, the harness compiles a bounded `ContextPacket` from:

- The original goal and acceptance criteria.
- Current Task Ledger state.
- Relevant repository structure.
- Recently inspected code.
- The latest compacted history.
- Recent unsummarized tool interactions.
- Relevant project memory.
- New user steering messages.

The compiler, not the coding model, decides what fits.

### 5.2 Stable prefix before dynamic context

The request should have a deterministic ordering:

```text
STABLE PREFIX
1. System prompt
2. Four tool definitions
3. Stable project instructions
4. Skill names and descriptions
5. Fixed safety and operating rules

DYNAMIC SUFFIX
6. Task goal and acceptance criteria
7. Compact Task Ledger projection
8. Relevant repository map
9. Compaction summary
10. Recent raw events
11. Latest user steering message
```

Volatile state should not be interpolated into the system instruction on every invocation. Serialize mutable state into the current work packet after the stable prefix.

The serializer should use:

- Stable field order.
- Canonical path format.
- No current timestamps unless required.
- No random identifiers in model-facing content.
- Stable whitespace.
- No token-count telemetry inserted into the prompt.
- Content hashes internally rather than repeated prose.

Every model request should record:

```text
static_prefix_hash
static_prefix_tokens
dynamic_suffix_tokens
cache_read_tokens
cache_write_tokens
uncached_input_tokens
prefix_mutation_reason
```

The goal is not merely low total tokens. It is a high proportion of reusable prefix tokens.

The executable boundary is the provider request, not the diagnostic
`static_prefix_hash`. The assembled worker snapshots its model, system instruction,
full tool declarations, and tool configuration on the first request and rejects any
later mutation. Codex/OpenResponses routing keys hash the same stable request inputs
(plus structured-output format) while excluding conversation input. Dynamic work
packets therefore cannot change the routing key.

This matches the Google design's stable-context-before-dynamic-query rule. Unlike
the local Codex and Pi implementations, Skein reconstructs a bounded task packet on
each worker step rather than replaying an append-only transcript, so only the system,
tools, and trusted project instructions are guaranteed reusable across every step.
ADK may extend an explicit Gemini cache through unchanged leading contents. Expanding
the guaranteed prefix with a repository snapshot or changing to append-only replay
remains an evaluation-gated context-policy change, not a caching prerequisite.

### 5.3 One main agent, deterministic outer loop

The coding model should be invoked as one bounded worker inside a programmatic ADK workflow. The outer workflow decides whether to:

- Continue implementation.
- Update the repository index.
- Compact context.
- Replan.
- Run verification.
- Ask for human input.
- Finish.

A coding model should not be asked on every step to choose among planner, coder, reviewer, tester, and memory agents. Those are control-flow choices the harness can often make deterministically.

A specialized reviewer may be added later, but it should receive only the task, final diff, and verification evidence, not a duplicate of the entire coding transcript.

---

## 6. Repository discovery, indexing, and code processing

### 6.1 Principle: indexing is a navigation aid, not a repository dump

The index helps the harness decide what to read. It should not automatically inject large quantities of source code.

The system should implement four retrieval tiers.

#### Tier 0: Repository manifest

Generated when the workspace is initialized:

```yaml
repository:
  root: /workspace/repo
  base_revision: 9fe19d...
  branch: agent/task-1842
  dirty: false
languages:
  - python
  - typescript
build_systems:
  - pyproject.toml
  - package.json
commands:
  unit_test: uv run pytest
  typecheck: uv run pyright
  lint: uv run ruff check .
instructions:
  - AGENTS.md
  - services/payments/AGENTS.md
top_level:
  - src/
  - tests/
  - packages/
  - docs/
excluded:
  - node_modules/
  - vendor/
  - dist/
  - generated/
```

The manifest should normally fit within 300-600 tokens.

#### Tier 1: Lexical and version-control search

Use bounded indexed discovery and mature command-line tools:

- in-process FFF grep and fuzzy filename search through the existing `bash` surface
- `rg`
- `git grep`
- `git log`
- `git blame`
- `git diff`
- language-specific build query tools
- test-runner collection commands

The default escalation pattern is:

```text
repository manifest
  -> filename/symbol search
  -> targeted line range
  -> surrounding function/class
  -> complete file only when needed
```

FFF result pages should be grouped, strictly bounded, and cursor-paginated by the
harness rather than injected wholesale. The native dependency remains outside the
sandbox process, so it is enabled only when the host workspace is the authoritative
local or bind-mounted tree. Every result is post-confined and refreshed after a
successful managed edit or write.

#### Tier 2: Structural repository map

For sufficiently large repositories, maintain a structural index.

Extracted entities should include:

```text
File, Module, Namespace, Class, Function, Method, Type, Interface,
Constant, Route, Database model, Test case, Build target
```

Edges should include:

```text
contains, imports, exports, calls, inherits, implements, references,
tests, configured_by, generated_from
```

Tree-sitter can provide a language-neutral baseline. Language servers or compiler indexes can enrich selected languages later.

A starting ranking function:

```text
score =
    0.35 * lexical_relevance
  + 0.20 * dependency_proximity_to_current_files
  + 0.15 * symbol_centrality
  + 0.15 * changed_file_proximity
  + 0.10 * test_adjacency
  + 0.05 * recent_task_usage
```

The inserted map should contain signatures and relationships, not function bodies, and default to a 500-1,500 token budget.

#### Tier 3: Semantic fallback

Semantic retrieval should be used only when lexical and structural methods are insufficient, such as when the task is expressed in business language absent from the code or spans multiple repositories.

Semantic search should return ranked paths, symbols, and short snippets. It should not automatically add full chunks to the prompt.

### 6.2 Incremental indexing

Each indexed file should be keyed by the Git blob SHA or a content hash. After a successful edit or write:

1. Mark the file dirty.
2. Reparse only that file.
3. Recompute its outgoing edges.
4. Update affected reverse edges.
5. Refresh the compact repo map at the next work-batch boundary, not after every tool call.

### 6.3 Test and build adjacency

The index should record likely validation relationships between source files, tests, build targets, endpoints, schemas, and packages. These relationships can be inferred from naming, imports, build files, test collection metadata, and historical co-change patterns.

---

## 7. Context, memory, and long-running tasks

### 7.1 Five-layer memory model

#### Layer 1: Immutable event log

ADK events are the authoritative execution history.

Example event types:

```text
TASK_CREATED
WORKSPACE_INITIALIZED
CONTEXT_COMPILED
AGENT_STEP_COMPLETED
TOOL_STARTED
TOOL_COMPLETED
FILE_READ
FILE_CHANGED
INDEX_UPDATED
VERIFICATION_COMPLETED
USER_STEERING_RECEIVED
COMPACTION_CREATED
CHECKPOINT_CREATED
TASK_BLOCKED
TASK_FINISHED
```

The event log should be append-only. Current state is reconstructed through reducers.

#### Layer 2: Task Ledger

The Task Ledger is a compact, structured projection of the event log:

```python
class TaskLedger(BaseModel):
    task_id: str
    goal: str
    acceptance_criteria: list[str]
    constraints: list[str] = []
    non_goals: list[str] = []
    base_revision: str
    workspace_id: str
    branch_id: str
    phase: str
    plan: list[PlanStep] = []
    current_step_id: str | None = None
    completed_step_ids: list[str] = []
    decisions: list[Decision] = []
    blockers: list[str] = []
    open_questions: list[str] = []
    files_read: list[str] = []
    files_modified: list[str] = []
    validations: list[ValidationResult] = []
    next_action: str | None = None
    iteration: int = 0
    no_progress_count: int = 0
    recent_action_fingerprints: list[str] = []
    status: str
```

Only a compact projection enters model context:

```text
Goal
Acceptance criteria
Current phase
Current step
Completed milestones
Key constraints
Open blockers
Files currently in focus
Latest validation result
Next expected action
```

#### Layer 3: Compaction summaries

Compaction uses a coding-specific structured format:

```markdown
## Goal
[Stable task objective]

## Acceptance Criteria
- [Criterion and current evidence status]

## Constraints and Non-Goals
- [Important boundaries]

## Progress
### Completed
- [Completed implementation]
### In Progress
- [Current work]
### Blocked
- [Unresolved issue]

## Key Decisions
- **Decision:** rationale and affected files

## Current Code State
- Base revision:
- Workspace tree hash:
- Files modified:
- Important interfaces changed:

## Validation
- Commands run:
- Passing:
- Failing:
- Remaining:

## Next Action
[One concrete next action]

## Critical Context
[Details that cannot be reconstructed cheaply]

<read-files>
...
</read-files>

<modified-files>
...
</modified-files>
```

Compaction should include the previous summary plus only newly aged-out events rather than resummarizing the entire history.

#### Layer 4: Artifacts

Large data remains outside model context:

- Complete compiler and test logs.
- Coverage reports.
- Full diffs.
- Generated plans.
- Screenshots and binary files.
- Search result sets.
- Static-analysis reports.

The model receives a bounded summary and an artifact identifier.

#### Layer 5: Project memory

Long-term memory contains stable, reusable project knowledge:

```text
Canonical build and test commands
Repository conventions
Architecture boundaries
Known flaky tests
Recurring failure causes
Important design decisions
Service ownership
Safe migration procedures
Preferred development workflows
```

Memory writes should occur only at explicit boundaries: task completion, confirmed decisions, diagnosed recurring failures, explicit user requests, or compaction identifying a durable fact.

### 7.2 Compaction policy

For models with 128,000 or more context tokens, a reasonable starting profile is:

| Context component | Initial budget |
|---|---:|
| System prompt and tool definitions | Under 1,200 tokens |
| Project instructions | Up to 2,000 tokens |
| Task Ledger projection | 500-1,200 tokens |
| Repository map | 500-1,500 tokens |
| Compaction summary | 2,000-4,000 tokens |
| Recent raw tail | Approximately 20,000 tokens |
| Completion reserve | Approximately 16,000 tokens |
| Preferred total working set | 48,000-64,000 tokens |

Trigger compaction when:

```text
estimated_next_request_tokens > model_context_window - completion_reserve
```

Also compact at meaningful boundaries such as completion of a major plan step, before model switches or branches, before a long verification phase, or after large exploratory phases.

Do not compact after each tool call.

Compaction algorithm:

1. Estimate the next request size.
2. Select a cut point at a complete interaction boundary.
3. Never separate a tool call from its result.
4. Retain the newest raw events up to the recent-tail budget.
5. Serialize older events into a neutral transcript representation.
6. Truncate large historical tool outputs before summarization.
7. Summarize using a cheaper model when quality is sufficient.
8. Merge the previous summary with newly summarized history.
9. Persist structured JSON and readable Markdown.
10. Record files read, files modified, validations, and decisions.
11. Generate a new prefix hash and mark the compaction as an intentional cache reset.
12. Continue from summary plus retained tail.

ADK's built-in event/token compaction should remain as an overflow safety net, while the coding-aware compactor is the primary policy.

### 7.3 Session branching and checkpoints

A checkpoint binds conversational state to code state:

```python
class Checkpoint(BaseModel):
    checkpoint_id: str
    task_id: str
    session_id: str
    invocation_id: str
    branch_id: str
    parent_checkpoint_id: str | None
    workspace_id: str
    base_revision: str
    git_tree_hash: str
    ledger_version: int
    ledger_hash: str
    compaction_id: str | None
    label: str | None
```

Creating a branch should:

1. Select a checkpoint.
2. Create a child ADK session.
3. Create or reset a Git worktree to the checkpoint's tree.
4. Copy the compact ledger snapshot.
5. Add a branch summary describing the alternate path.
6. Continue with a fresh dynamic suffix.

The session and workspace must always be coupled.

---

## 8. Staying on goal

### 8.1 Goal contract

Every task begins with a normalized contract:

```python
class TaskRequest(BaseModel):
    goal: str
    acceptance_criteria: list[str]
    constraints: list[str] = []
    non_goals: list[str] = []
    permitted_paths: list[str] | None = None
    forbidden_paths: list[str] = []
    verification_requirements: list[str] = []
    max_cost_usd: float | None = None
    max_iterations: int | None = None
```

When the user supplies only a natural-language request, initialization derives a provisional contract and records which criteria were inferred.

### 8.2 Structured agent step

The coding agent returns a compact structured outcome:

```python
class AgentStep(BaseModel):
    status: Literal["continue", "verify", "blocked", "done"]
    progress: list[str]
    next_action: str | None = None
    decisions: list[str] = []
    questions: list[str] = []
    discovered_constraints: list[str] = []
    files_in_focus: list[str] = []
    completion_claims: list[str] = []
```

The outer workflow reduces this into the Task Ledger. `status="done"` never directly completes the task; it routes to verification.

### 8.3 Progress invariant

Each work batch must produce at least one of:

- New repository evidence.
- A meaningful code change.
- A validation result.
- A documented design decision.
- An explicit blocker.
- A material replan.

Repeated action fingerprints with no new evidence increment `no_progress_count`:

```text
no_progress_count == 1: concise reflection request
no_progress_count == 2: rebuild context and request a different approach
no_progress_count >= 3: replan or request human input
```

### 8.4 User steering

At the end of each work batch:

1. Drain urgent steering messages.
2. Update the goal contract if necessary.
3. Record previous and new criteria.
4. Invalidate only the dynamic context suffix.
5. Resume without rebuilding the stable prompt.

### 8.5 Phase machine

```text
understand -> plan -> implement -> verify -> review -> complete
```

Transitions may move backward after failed verification, review findings, blockers, or user changes.

---

## 9. Minimal coding tool surface

The model sees exactly four tools by default.

### `read`

```python
read(path: str, offset: int = 1, limit: int = 400)
```

Behavior:

- Resolve paths inside the workspace.
- Reject traversal outside permitted roots.
- Detect binary files.
- Return line-numbered content.
- Cap lines and bytes.
- Include file SHA and total line count.
- State clearly when output is truncated.

### `edit`

```python
edit(path: str, old_text: str, new_text: str, expected_sha256: str | None = None)
```

Behavior:

- Require an exact, unique match.
- Optionally require the preimage hash.
- Apply atomically.
- Return a concise diff.
- Refuse ambiguous replacements.
- Treat an already-applied result as idempotent success.
- Queue mutations per file.

### `write`

```python
write(path: str, content: str, expected_sha256: str | None = None, expected_absent: bool = False)
```

Behavior:

- Write through a temporary file and atomic rename.
- Create parent directories only inside permitted roots.
- Support optimistic concurrency.
- Return no-op success when contents already match.
- Return a compact diff or creation summary rather than echoing the file.

### `bash`

```python
bash(command: str, timeout_seconds: int = 120)
```

Behavior:

- Execute in an isolated workspace.
- Enforce time and resource limits.
- Capture stdout and stderr separately.
- Strip ANSI noise.
- Preserve exit code and duration.
- Summarize repetitive output.
- Return actionable errors and first/last sections.
- Store full output as an artifact.
- Classify commands for policy enforcement.

Every tool returns an internal envelope:

```python
class ToolEnvelope(BaseModel):
    status: Literal["ok", "error", "blocked", "timeout"]
    model_text: str
    ui_details: dict = {}
    exit_code: int | None = None
    duration_ms: int
    truncated: bool = False
    omitted_bytes: int = 0
    artifact_uri: str | None = None
    changed_paths: list[str] = []
    content_hashes: dict[str, str] = {}
```

Only `model_text` enters model context. Full output and telemetry remain available to the UI and control plane.

Recommended initial model-facing output limits:

| Output | Limit |
|---|---:|
| File read | 400 lines or 32 KB |
| Indexed search output | 20 matches by default, 50 maximum, then 12,000 characters or 200 lines |
| Generic shell output | 16 KB |
| Test output | Aggregate plus first 10 failures |
| Compiler output | First actionable diagnostics plus summary |
| Diff | Changed-file summary plus relevant hunks |
| Historical tool result during compaction | About 2,000 characters |

Add dedicated tools only when an ablation shows measurable gains.

---

## 10. Verification and completion

Completion is a deterministic gate. A task completes only when:

1. Every required acceptance criterion maps to evidence.
2. Required validation commands have run.
3. Relevant tests pass.
4. Type checking and linting meet policy.
5. `git diff --check` passes.
6. No forbidden or unexpected files changed.
7. The workspace is based on the expected revision.
8. No unresolved blocker remains.
9. The final diff is internally consistent.
10. The verifier, not the coding agent, produces `passed=True`.

```python
class CriterionEvidence(BaseModel):
    criterion: str
    satisfied: bool
    evidence: list[str]
    notes: str | None = None

class VerificationReport(BaseModel):
    passed: bool
    criteria: list[CriterionEvidence]
    commands_run: list[str]
    tests_passed: int
    tests_failed: int
    scope_violations: list[str] = []
    unresolved_diagnostics: list[str] = []
    recommended_next_action: str | None = None
```

Use a validation ladder:

```text
1. Parse or syntax check
2. Formatter check
3. Type checker on changed package
4. Targeted unit tests
5. Affected integration tests
6. Package test suite
7. Repository-wide checks
```

An optional reviewer can receive the original task contract, final ledger projection, changed-file list, final diff, verification report, and relevant conventions. It should not receive the full exploratory transcript.

---

## 11. Safety, isolation, and resumability

### 11.1 Workspace isolation

Every task executes in a dedicated Git worktree inside a sandbox or container.

Recommended defaults:

```text
Filesystem: read/write only inside workspace and artifact directory
Network: disabled by default; allowlisted registries when required
Secrets: injected only for approved commands; scrubbed from outputs
Processes: CPU, memory, process-count, and timeout limits
Git: local operations allowed; push/force-push/release/merge require approval
Cloud/deployment: blocked unless task policy explicitly enables them
```

### 11.2 Command classification

Before executing shell commands, classify them:

```text
READ_ONLY
WORKSPACE_MUTATION
BUILD_OR_TEST
DEPENDENCY_INSTALL
NETWORK_ACCESS
GIT_HISTORY_MUTATION
PUBLISH_OR_DEPLOY
DESTRUCTIVE
UNKNOWN
```

Policy defaults:

| Class | Default |
|---|---|
| Read-only | Allow |
| Workspace mutation | Allow in sandbox |
| Build/test | Allow |
| Dependency install | Approved registries only |
| Network access | Deny or allowlist |
| Git history mutation | Require approval |
| Publish/deploy | Require explicit human approval |
| Destructive | Deny |
| Unknown | Block and inspect |

### 11.3 Idempotency and resume

Persist a tool receipt table:

```text
tool_call_id
task_id
invocation_id
tool_name
normalized_arguments_hash
started_at
completed_at
status
result_hash
artifact_uri
side_effect_key
```

Resume rules:

- `read` is repeatable.
- `edit` checks expected preimage and recognizes already-applied output.
- `write` checks expected content or absence.
- Tests and builds may be rerun.
- Dependency installs use lockfiles and content-addressed caches.
- External writes require idempotency keys.
- Publish or deployment operations are never automatically replayed.
- Resume validates the workspace tree against the latest checkpoint.

---

## 12. Mapping to Google ADK 2.x

ADK provides the workflow substrate without becoming the model-facing abstraction.

| Harness concern | ADK mechanism |
|---|---|
| Root application configuration | `App` |
| Deterministic control flow | `Workflow` |
| Loops and conditional routing | Dynamic `@node` |
| Calling the coding model | `Agent` through `ctx.run_node` |
| Persistent execution history | Session events |
| Current Task Ledger | Session state |
| Durable sessions | `DatabaseSessionService` |
| Resume after interruption | `ResumabilityConfig` and checkpointed nodes |
| Long-term project knowledge | `MemoryService` |
| Large logs and reports | Artifact service |
| Policy and telemetry | Plugins and callbacks |
| User steering | Durable queue plus state/event updates |
| Context overflow protection | ADK compaction as backstop |
| Provider-side Gemini caching | ADK context-cache configuration |

The coding agent should run as one bounded work batch:

```text
inspect -> reason -> tool calls -> observe -> coherent change or decision -> AgentStep
```

After the batch, the ADK workflow reduces events into state, drains steering, updates the index, checks budgets, and decides whether to compact, continue, verify, or block.

For Gemini-backed execution, configure ADK context caching for sufficiently large stable prefixes. Keep an independent prefix hash because provider cache semantics vary and cannot compensate for a mutating prompt.

Model switching should occur at checkpoints or compaction boundaries.

---

## 13. Suggested package structure

```text
skein/
├── pyproject.toml
├── README.md
├── agents-cli-manifest.yaml
├── app/
│   ├── __init__.py
│   ├── agent.py
│   └── fast_api_app.py
├── harness/
│   ├── workflow.py
│   ├── config.py
│   ├── models/
│   ├── context/
│   ├── repo/
│   ├── tools/
│   ├── state/
│   ├── verification/
│   ├── persistence/
│   ├── callbacks/
│   └── evals/
├── skills/
├── docs/design/
└── tests/
    ├── unit/
    ├── integration/
    ├── resume/
    ├── security/
    └── eval/
```

---

## 14. Persistence schema

A relational database is sufficient for the control plane.

Core tables:

```text
tasks
task_ledger_versions
checkpoints
tool_receipts
compactions
repo_files
repo_symbols
repo_edges
project_memories
```

SQLite can support a local MVP. PostgreSQL is preferable for concurrent agents and repositories. Source code remains in Git worktrees rather than being copied into the session database.

---

## 15. Implementation plan

### Phase 0: benchmark and baseline

Deliver:

- Reproducible task runner.
- Stock ADK coding-agent baseline.
- Task corpus from recent human pull requests.
- Held-out tests.
- Token, cache, cost, latency, tool-call, and pass-rate instrumentation.

Primary metric: **cost per passed task**.

### Phase 1: minimal Pi-like coding loop

Deliver:

- Dedicated Git worktree per task.
- Sandbox execution.
- Four model-visible tools.
- Lean stable prompt.
- Dynamic ADK orchestration node.
- Structured `AgentStep`.
- Task Ledger reducer.
- Basic targeted verification.
- Tool-output truncation and artifact storage.

### Phase 2: context compiler and cache discipline

Deliver:

- Deterministic context serializer.
- Stable-prefix hashing.
- Hierarchical project-instruction discovery.
- Skill manifest with on-demand loading.
- Context-token budgeter.
- Recent-event selection.
- Tool-output deduplication.
- Cache-efficiency dashboard.

### Phase 3: durable long-running execution

Deliver:

- Persistent session service.
- Event reducers and versioned Task Ledger.
- Tool receipts.
- ADK resumability.
- Workspace/checkpoint reconciliation.
- Pi-style coding-aware compaction.
- Steering queue.
- Branch and fork support.
- Long-term project-memory policy.

### Phase 4: structural repository navigation

Deliver:

- Incremental parsers for priority languages.
- Symbol and edge index.
- Compact repository-map generator.
- Task-to-symbol ranking.
- Changed-file and test-adjacency graph.
- Optional semantic fallback.

### Phase 5: verification, policy, and enterprise hardening

Deliver:

- Build/test discovery.
- Affected-test selector.
- Acceptance-criterion evidence mapping.
- Scope checker.
- Final diff validation.
- Optional narrow reviewer.
- Command classifier.
- Network and secret controls.
- Human approval for external actions.
- Redaction and audit logs.

### Phase 6: routing and optional extensions

Only after the single-agent harness is benchmarked, add:

- Difficulty-based model routing.
- Cheaper compaction model.
- Narrow review model.
- Provider adapters.
- Semantic retrieval.
- Domain-specific skills and CLIs.
- Carefully scoped parallel discovery.

---

## 16. Evaluation and ablation strategy

Evaluate the harness as a system.

Primary metric:

```text
cost per passed task
```

Supporting metrics:

```text
held-out test pass rate
acceptance-criterion satisfaction
regression and scope-violation rates
input/output/uncached/cache tokens
context tokens per work batch
stable-prefix reuse rate
compactions per task
tool calls and duplicate actions
files read before first correct edit
resume success and duplicated side effects
wall-clock latency
```

Required ablations:

| Variant | Added capability |
|---|---|
| A | Stock ADK coding agent |
| B | Lean prompt and four tools |
| C | Bounded tool outputs and artifacts |
| D | Task Ledger and deterministic outer loop |
| E | Structured verification gate |
| F | Stable-prefix compiler and caching |
| G | Pi-style compaction |
| H | Structural repository map |
| I | Semantic fallback |
| J | Optional reviewer model |

Long-horizon stress tests should require 50+ tool calls, multiple repair loops, at least two compactions, interruption/resume, user steering, branching, model switching at a checkpoint, and external workspace changes.

---

## 17. Copy, adapt, and reject summary

### Copy directly from Pi

- Minimal system prompt.
- Four default coding tools.
- Shell-first composition.
- Progressive skill disclosure.
- Hierarchical project instructions.
- Append-oriented history.
- Structured compaction.
- Recent raw tail after compaction.
- File tracking across compactions.
- Steering and follow-up queues.
- Branch summaries and checkpoints.
- Token/cache/cost visibility.

### Adapt for ADK and enterprise repositories

- Compact control-plane Task Ledger.
- ADK child sessions coupled to Git checkpoints and worktrees.
- Token-bounded structural map for large monorepos.
- Deterministic verification node.
- Callback-enforced sandbox and approval policy.
- Event-sourced state plus curated long-term memory.
- Coding-aware primary compactor plus ADK overflow backstop.
- Model switching only at checkpoints or compaction boundaries.
- Narrow isolated reviewer instead of a standing agent team.

### Reject as defaults

- Large always-visible MCP registry.
- Per-turn automatic RAG.
- Recursive agent teams.
- Self-generated tools and plugins.
- Unrestricted network and shell.
- Model-only completion decisions.
- Raw transcript as current state.
- Rewritten full plan every turn.
- Full logs in model context.

---

## 18. Recommended minimum viable product

The first useful release should contain only:

1. One ADK dynamic workflow.
2. One coding Agent.
3. Four tools: `read`, `bash`, `edit`, and `write`.
4. One isolated Git worktree.
5. One persistent Task Ledger.
6. One deterministic context compiler.
7. One verification node.
8. Bounded outputs with artifact storage.
9. Persistent sessions, tool receipts, and resume.
10. Token, cache, cost, and pass-rate telemetry.

Do not begin with semantic indexing, broad multi-agent collaboration, a large plugin marketplace, self-improving tools, or elaborate model routing.

The likely competitive advantage comes from:

```text
less irrelevant context
+ more stable cached prefixes
+ better tool-output control
+ explicit task continuity
+ machine-verified completion
```

The build order should therefore be:

```text
minimal loop
-> bounded context
-> explicit task state
-> verification
-> durability and compaction
-> structural repository map
-> optional routing and specialization
```

A coding harness becomes competitive by controlling what the model must process, not by maximizing what the harness can theoretically expose.

---

## References

- Pi coding agent: https://github.com/earendil-works/pi
- Pi design post: https://mariozechner.at/posts/2025-11-30-pi-coding-agent/
- Pi prompt caching: https://earendil.com/posts/prompt-caching/
- Pi compaction: https://earendil.com/posts/compaction-in-pi/
- Google Agents CLI skills: https://github.com/google/agents-cli/tree/main/skills
- Google ADK samples, long-horizon harness: https://github.com/google/adk-samples/tree/main/core/python/long-horizon-harness
- Google ADK documentation: https://adk.dev/
- Aider repository map: https://aider.chat/docs/repomap.html
- Databricks coding harness benchmark: https://www.databricks.com/blog/benchmarking-coding-agents-databricks-multi-million-line-codebase
