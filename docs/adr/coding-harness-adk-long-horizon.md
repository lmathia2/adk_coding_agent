# ADK Long Horizon harness design

> **Status:** source-grounded implementation reference
> **Repository:** `/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness`
> **Revision:** `cf7cadbc537cab9a6105a9b9adf5a2af2da43061`
> **Inspection date:** 2026-09-01
> **Related:** [Pi](coding-harness-pi.md), [OpenCode](coding-harness-opencode.md), [Codex](coding-harness-codex.md), [comparison](coding-harness-comparison.md)

## Scope and evidence boundary

This page describes the implementation under `core/python/long-horizon-harness`, not ADK in general. The sample is a broad personal-agent harness with coding capabilities, scheduled work, sandbox persistence, memory curation, and a web/A2A service. Some primitives are supplied by ADK and merely configured here; others are Horizon-specific callbacks, plugins, tools, and storage adapters.

Terms used below:

- **ADK-owned** means Horizon selects or configures an ADK capability such as `Runner`, resumable `App`, event compaction, context caching, services, or tool confirmation.
- **Horizon-owned** means the sample implements the policy, state projection, wrapper, or orchestration itself.
- **Durable** means reconstructible after process loss from a configured session/task/routine/sandbox backend.
- **Live** means held only by the current Python process or sandbox runtime.
- **Optional** means an environment or deployment choice, not the default in every run.

The repository README calls this a “self-improving long-horizon agent.” That description is directionally useful, but the code is more precise: the model may author workspace skills; telemetry writes promotion/review suggestions to memory; background review agents may curate memories. There is no autonomous code deployment or unreviewed harness rewrite in the root loop.

## Architectural map

~~~text
Web / A2A / ADK Runner
  │
  ├─ durable service plane
  │    ├─ session service: SQLite / SQL / Agent Engine / memory
  │    ├─ memory service: Memory Bank / memory
  │    ├─ artifact service: GCS / local / memory
  │    ├─ A2A task store: SQLite / SQL / memory
  │    └─ routine store: PostgreSQL / memory
  │
  ├─ ADK App (resumable, cache-aware, compacting)
  │    ├─ root Agent + model dispatcher
  │    ├─ before-agent: environment, profile, skill binding
  │    ├─ before-model: model, prune, normalize, commands, prompt, reminders
  │    ├─ before-tool: log → exfil → policy → permission
  │    ├─ after-tool: telemetry
  │    └─ after-agent: memory capture → skill curation → review fork
  │
  ├─ execution plane
  │    ├─ Environment interface
  │    ├─ LocalEnvironment OR per-user Vertex sandbox
  │    ├─ foreground bash + managed background processes
  │    └─ optional separate stateful Python code executor
  │
  └─ work plane
       ├─ root coding/general-purpose loop
       ├─ blocking resumable child delegation
       ├─ process-live background children
       ├─ background memory/review siblings
       └─ durable scheduled routines in fresh isolated sandboxes
~~~

The canonical composition root is [`horizon/agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:190). It is unusually valuable as documentation because callback order, tool registration, plugins, cache configuration, resumability, and compaction are visible in one place.

## Root loop and ownership

Horizon does not implement a second agent loop. It builds one ADK `Agent`, wraps it in one ADK `App`, and binds services through `Runner`. Effects still pass through model-visible tools and ordered callbacks. The root composition registers callbacks in explicit order at [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:218):

1. Before agent: resolve/reattach the environment and load session state; rebind the skill toolset.
2. Before model: select the model, prune old outputs, normalize tool schemas, redact artifact URLs, dispatch slash commands, add project context, add volatile reminders, and refresh the subagent description.
3. Before tool: log the in-flight call, block exfiltration, enforce hard/confirmation policies, then resolve interactive permission.
4. After tool: record skill and UI telemetry.
5. After agent: auto-capture the session to memory, run skill-curation thresholds, and start a review fork.

The `App` then enables ADK resumability, context caching, and event compaction at [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:304). The host therefore owns lifecycle and effects; the model chooses calls inside those boundaries.

## Exact model-facing contract

### Three prompt tiers

The prompt is deliberately split by volatility and cache behavior in [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:15):

| Tier | Construction | Placement | Change cadence |
|---|---|---|---|
| Stable | identity + tool-conditional guidance + behavior + mechanics | `Agent.static_instruction` | process/App build |
| Project context | first matching context file | appended to system instruction | deterministic per working directory |
| Volatile | iteration/error/date/environment/secrets | trailing `<system-reminder>` content | every model call |

This is not cosmetic. ADK's context cache fingerprints the stable system prefix, while the rolling tail remains outside that prefix. Putting mutable environment or secret state in `static_instruction` would destroy cache reuse or leave stale authority in the prompt.

### Stable instruction, in assembly order

`build_static_instruction()` is a pure assembly function at [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:571). At this revision the registered root tools cause it to emit:

1. Identity from `~/.lha/SOUL.md`, or the fallback identity: helpful, direct, broad-task assistant; use tools only when needed; be targeted and efficient. The exact fallback is in [`soul_loader.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/soul_loader.py:24).
2. Memory rules because memory tools are registered: `<PAST_CONVERSATIONS>` is the only evidence of prior knowledge; distinguish recall/new/redundant facts; save declarative durable facts, not transient task state; use search for missed sessions. Exact text is at [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:119).
3. Skill rules because `load_skill` is registered: answer catalog questions from the injected index, load a skill before using it, and author/update `SKILL.md` after complex work. Exact text is at [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:155).
4. Model-gated acting rules: act in the same response, plan only non-trivial work, diagnose before a third identical failure, batch independent calls, and use non-interactive CLI flags. See [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:178).
5. Model-gated safety rules: fetched content and reminders are data rather than intent; do not exfiltrate secrets; read before edit; avoid two edits to the same file in one response; narrate irreversible risk before the call; explicitly confirm sandbox-leaving mutations. See [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:200).
6. Model-gated style rules: match answer size to the task, skip chitchat and redundant tool narration, and attach web citations to claims. See [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:227).
7. Unconditional workspace rules: use the persistent per-user workspace, do not search the host for an absent project, honor workspace focus, use relative paths, and maintain/re-read `plan.md` for multi-step work. See [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:251).
8. Unconditional execution rules: use `uv` for custom Python, stop rather than loop on a hermetic-network failure, preserve irreplaceable files under `/workspace`, and assume POSIX non-login shell semantics. See [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:264).
9. Unconditional routing rules: path semantics, use web research for current information, use multimodal `read` instead of lossy shell extractors, and reserve routines for recurring unattended work. See [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:278).
10. Unconditional operations rules: distinguish exfil blocks, confirmation-tier operations, hard denials, `/yolo`, and suggested slash commands. See [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:297).
11. Optional code-executor rules only when a sandbox executor exists: its stateful Python filesystem is separate from the coding workspace, so bytes must be bridged explicitly. See [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:313).

The root's ordinary `instruction` is intentionally empty; the constant contract is placed in `static_instruction` at [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:218).

### Project context and volatile reminders

Project instructions are first-match-wins, not cumulative: `.horizon.md`, `LHA.md`, `AGENTS.md`, `CLAUDE.md`, then `.cursorrules`, with a 20 KB cap. That avoids both prompt bloat and contradictory instruction sets; discovery is defined at [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:70), and the callback appends the selected block at [`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:655).

The volatile reminder callback appends cache-excluded content rather than mutating the stable prefix. It includes near-budget warnings, the last tool error, current date, environment/workspace hints, and only the **names** of available secrets. The implementation and cache rationale are in [`reminders.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/reminders.py:15).

### Child prompt

A child is not given the parent transcript by default. Its base instruction says it is a delegated worker with a self-contained task, should make reasonable assumptions for minor ambiguity, gets one `ask_parent` escalation for a genuine blocker, and must report rather than bypass a guard. It also inherits the secrets/untrusted-content rule. The exact child base is in [`delegate_builder.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/subagents/delegate_builder.py:49). Requested skills and caller instructions are inlined; goal-specific child agents are rebuilt per call rather than cached.

## Model, provider, cache, and streaming

`DispatchingLlm` is a single ADK model object that chooses a registered backend from `llm_request.model` on every call. A session `/model` choice overrides `LHA_ROOT_MODEL`, which overrides the default. The dispatcher is backend-agnostic and applies capability-specific content preparation at [`dispatcher.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/models/dispatcher.py:44); selection and fallback are in [`selector.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/models/selector.py:43).

The inspected registry contains `gemini-3.7-flash` and `gemini-3.1-pro-preview`, both with a declared one-million-token input limit. Backends are lazy-built and cached at [`registry.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/models/registry.py:114).

Context cache configuration is `min_tokens=4096`, TTL 1,800 seconds, and ten cache intervals at [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:315). This design depends on byte-stable prompt tiers and declaration normalization; cache hit economics are not merely a provider concern.

## Model-visible tools and schema cost

The root registers memory add/search, memory preload, read, write, edit, file search, artifacts, bash, process control, subagents, skills, routines, clarification, web research, plus an optional ADK code executor at [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:190). This is a wider default surface than Pi, OpenCode, or the minimal four-tool ADK harness.

Horizon trims declaration cost without deferred discovery:

- Root tools use ADK's legacy compact declaration path under a narrowly scoped feature override at [`declaration_compaction.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/context/declaration_compaction.py:15).
- A before-model callback strips source indentation from descriptions at [`schema_normalization.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/context/schema_normalization.py:15).
- Skills expose an index and load full bodies on demand.
- The subagent description is regenerated from live profiles and skills, but the root does not implement Pi/Codex-style deferred tool-schema discovery for arbitrary tools.

## Direct tool calls, offloading, and PTC

Normal tool use is standard ADK function calling. The model emits tool calls; ADK invokes them; the callback chain retains authority. Independent calls may be emitted together, but Horizon itself does not add a separate PTC interpreter or a code-mode nested tool API.

There are three different mechanisms that are easy to mislabel as PTC:

1. `bash` lets the model write shell programs that compose OS tools. This is programmatic orchestration, but only through the shell authority already exposed.
2. `AgentEngineSandboxCodeExecutor`, when configured, gives the model a stateful Python kernel. It has its own filesystem and is not wired as a broker for nested Horizon tool calls; construction is at [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:139).
3. `subagent` offloads a goal into another model context. That is semantic delegation, not deterministic PTC.

Therefore the correct classification is: **shell composition: yes; optional stateful code execution: yes; child-agent offloading: yes; host-side PTC over the same tool broker: no.**

## Environment and workspace abstraction

`Environment` extends ADK's base environment with file listing/deletion/directories, upload/download, background process spawn/list/kill, host-filesystem identity, and auth refresh at [`environment/base.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/environment/base.py:32). Tools resolve the active instance through a `ContextVar`, so business logic does not branch on local versus sandbox types; binding is in [`environment_context.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/environment_context.py:15).

The local adapter runs on the host and is explicitly not a production sandbox. The sandbox adapter sends file/process operations to a per-user Vertex runtime. `on_session_start_callback` selects, provisions, reattaches, migrates, and refreshes the environment before each invocation at [`session_start.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/session_start.py:419).

Sandbox discovery is cloud-derived rather than stored in a local registry: a deterministic per-user display name lets cold starts and multiple server instances find the same sandbox. Lifecycle helpers create versioned hermetic/network templates, mint and refresh routing credentials, snapshot full environments, restore, prune, and migrate `/workspace`; see [`lifecycle.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/sandbox/lifecycle.py:15).

This yields two different durability units:

- `/workspace` is the explicit user deliverable and migration boundary.
- A full sandbox snapshot can preserve `$HOME` and runtime state across sandbox teardown, but is deployment- and scheduler-dependent.

## Foreground and background processes

`bash` is foreground by default. If a command exceeds its timeout, it is auto-promoted into the background registry rather than killed; explicitly long-lived work starts through `process(action='spawn')`. The contract is in [`terminal.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/tools/processes/terminal.py:15).

The `process` tool provides spawn, list, poll, logs, wait, kill, and stdin write. Local handles use a PTY and a bounded rolling buffer; sandbox handles address runtime-owned sessions. The per-session registry is described at [`environment/registry.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/environment/registry.py:15).

Durability is backend-specific:

- Local subprocess handles are process-live; server restart loses the Python handle even if the OS process survives.
- Sandbox sessions are runtime-owned and can be rediscovered through the environment's process API, so they are less coupled to the web process.
- Neither kind is a durable job ledger with replayable command transitions.

## Staying on track

Horizon combines prompt discipline with mechanical guards:

- The prompt tells the model to maintain `plan.md`, act rather than promise, and diagnose before a third repeated failure.
- `IterationBudgetPlugin` stores iteration/tool-call counters and a sticky halt signal in session state instead of an in-process cache. It enforces a root maximum of 200 tool calls per iteration at [`iteration_budget_plugin.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/iteration_budget_plugin.py:57).
- `GuardrailsPlugin` coordinates repeated-failure, no-progress, and graceful-halt behavior around model/tool callbacks; the contract is summarized in [`guardrails/__init__.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/guardrails/__init__.py:15).
- A graceful halt injects a handoff reminder so the model returns current state and next steps rather than silently stopping; see [`graceful_halt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/graceful_halt.py:36).
- Delegated children have independent time and event-count caps.

What it does **not** have is a host-owned durable goal/task state machine with acceptance criteria and deterministic completion predicates. `plan.md` is model-maintained workspace state, and completion remains a model judgment unless a task's tools or user impose a check.

## Compaction and context budgeting

### Cheap pruning before LLM summarization

Before each model request, stale large tool responses are candidates for deterministic replacement with a prune marker. The newest three user turns and roughly 40,000 recent tokens are protected; skill, subagent, and clarification results are never pruned; pruning only occurs when at least about 20,000 tokens can be reclaimed. Overflow paths survive the replacement. See [`tool_output_pruning.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/context/tool_output_pruning.py:42).

### Trigger

ADK's `EventsCompactionConfig` is configured with:

- a seed threshold of 750,000 tokens;
- 20 retained events;
- an interval trigger every eight user invocations;
- overlap of two events;
- a separate `gemini-3.7-flash` summarizer.

The exact configuration is at [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:325). On every turn, model selection replaces the seed with 75% of that model's declared window, controlled by `LHA_COMPACTION_WINDOW_FRACTION`; see [`compaction_threshold.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/context/compaction_threshold.py:15).

### Summary contract and merge

`HorizonSummarizer` asks for a fixed Markdown handoff with Goal, Constraints & Preferences, Progress (Done/In Progress/Blocked), Key Decisions, Next Steps, Critical Context, and Relevant Files. It caps individual history items at 2,000 characters, merges an earlier summary when present, extracts file paths deterministically from read/write tool calls, and prepends a “REFERENCE ONLY” banner so compacted text is not treated as fresh instruction. The complete algorithm is at [`summarizer.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/context/summarizer.py:46).

### Pre-compaction memory fork

Immediately before summary generation, the summarizer may fire a memory-only sibling agent over the soon-to-be-discarded events. It saves only stable user facts, skips task progress and code-derived facts, and is structurally unable to call other tools. This is best-effort and fire-and-forget, gated by `LHA_PRE_COMPRESS_FLUSH`; see [`flush_fork.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/memory/flush_fork.py:15).

This creates two distinct lossy projections from the same source events:

- the compaction summary preserves task continuity in the active session;
- Memory Bank preserves selected cross-session facts.

The original event stream remains the authoritative input only if the configured session backend retains it. A summary is not a durable event ledger.

## Session, task, artifact, and replay durability

`build_runner()` resolves independent services at [`fast_api_app.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/fast_api_app.py:255):

| State | Default/dev | Optional durable backend |
|---|---|---|
| ADK session/events | local SQLite unless explicitly in-memory | SQL or Agent Engine |
| A2A task records | local SQLite unless explicitly in-memory | SQL |
| Cross-session memory | in-memory when no Memory Bank resource | Vertex Memory Bank |
| Artifacts | local/in-memory depending service factory | GCS |
| Routines | in-memory unless configured | PostgreSQL |
| Background child handles | process singleton | none in this sample |
| Local process handles | process/session registry | none in this sample |
| Sandbox workspace | remote sandbox | snapshot/restore + `/workspace` migration |

The `App` is resumable at the ADK event/tool-confirmation level. A confirmation resumes by sending a `FunctionResponse` for ADK's confirmation call, so already-completed prior effects are not replayed. Integration coverage explicitly asserts this at [`test_delegate_resume_spike.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/tests/integration/test_delegate_resume_spike.py:117).

“Resumable” is consequently scoped: it covers a persisted ADK session and resumable invocation. It does not magically make a live local subprocess, in-memory background child, or in-memory routine store restart-safe.

## Subagents and long-running agent work

### One public tool, two execution modes

`subagent` is a single model-visible dispatcher at [`subagent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/subagents/subagent.py:67):

- default: blocking `delegate`, with a 120-second default timeout;
- `background=True`: process-live child with a task ID and 300-second execution default;
- management: status, result, wait, cancel, and list.

Children default to file and shell toolsets. They cannot recursively delegate, clarify directly with the user, write/read parent memory, or mutate the skill registry. The `explore` profile is a deny-by-default read/search-only worker at [`profiles.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/subagents/profiles.py:31).

### Blocking child resume and approval resurfacing

When the parent has a durable session, stable function-call ID, and confirmation channel, blocking delegation creates a deterministic child session ID from the parent function call. The child runs until completion or its first pending approval. If it pauses, the parent stores the child's confirmation ID and asks the human. On resume, the parent forwards a confirmation `FunctionResponse` into the **same child session**, then deletes the child session after completion. It never starts a fresh child when the resume channel is lost, because that could repeat effects. The full two-invocation protocol is at [`delegate.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/subagents/delegate.py:286).

Only one resurfaced approval is allowed per delegate call; a second distinct approval halts and asks the parent to split or pre-grant the work. This is a deliberate bounded-interaction contract, not arbitrary nested HITL.

### Background child lifecycle

Background children are `asyncio.Task`s stored in a process-wide `SubAgentRegistry`. The registry supports shielded waits, completion/error envelopes, cancellation of direct descendants, and “wait for first completion” to avoid polling. See [`registry.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/subagents/registry.py:15).

This mode is **not durable across server restart** and is headless: it cannot bubble approval to a user turn. It is useful for parallel context isolation within one live service process, not as a general distributed job system.

### Review siblings

Memory review and pre-compaction flush use a separate `SiblingAgentPlugin`. These are host-started, narrowly tooled background agents, not model-visible arbitrary workers. Shutdown drains the plugin before closing the routine store, reducing but not eliminating process-loss risk.

## Routines and scheduler

Routines are the sample's genuinely unattended work primitive. The model-visible tool can test, create, list, and cancel. Creation validates a five-field cron schedule, asks for human confirmation, writes a manifest into `.lha/routines`, and registers a store row; exact behavior is at [`routines/tools.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/routines/tools.py:15).

A test run uses the real root `App` under routine isolation but ephemeral in-memory services, with a five-minute timeout, at [`run_once.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/routines/run_once.py:15). The fire path is headless and uses a fresh `lhart-*` sandbox. Only declared secrets enter the async context; shell confirmations that survive hard guards are allowed inside that isolated sandbox, while non-shell confirmations fail closed.

Routine durability depends on `RoutineStore`: the in-memory implementation is for development; the PostgreSQL implementation provides multi-instance claiming. Cloud Scheduler hits authenticated HTTP endpoints that claim due rows and run through the same app/service composition.

This separation is important: a routine is not “keep the current chat alive.” It is a new scheduled invocation with an explicit task, identity, sandbox, secret scope, and delivery contract.

## Memory and self-improvement

Horizon uses ADK memory services rather than inventing a second transcript database:

- `HorizonPreloadMemoryTool` injects a bounded `<PAST_CONVERSATIONS>` tail: at most 20 memories and 4,000 characters by default, at [`preload.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/memory/preload.py:47).
- `memory` adds declarative facts or searches sessions/memories; the prompt constrains what should be saved.
- `auto_capture_callback` adds the completed session to memory after an agent run.
- `review_fork_callback` starts a throttled background reviewer over the transcript.
- nightly “dream review” gathers recent real sessions and asks Vertex Memory Bank to generate/consolidate a structured user profile at [`dream_review.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/memory/dream_review.py:15).
- skill telemetry records views/edits; the curator writes promotion/review **memory entries** at numeric thresholds rather than editing production code at [`skill_curator.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/memory/skill_curator.py:15).

The strength is layered recall and bounded preload. The risk is that some writes are LLM-selected, best-effort, and eventually consistent. Memory is useful context, not a verifier or authoritative task ledger.

## Secrets, permissions, sandbox, and trust

### Secrets

`SecretStore` is a per-user protocol with Secret Manager and in-memory implementations. The GCP backend stores one 64 KB JSON blob per user, names the secret by a hash of user ID, and maintains a short read cache at [`store.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/secrets/store.py:15). The model sees available variable names, never values.

At command execution time, values are resolved for the active user and injected as environment variables. Routine execution narrows the set through a `ContextVar` allowlist; the exact scoping is at [`inject.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/secrets/inject.py:25).

### Ordered security gates

The root order is security-significant:

1. `exfil_guard` blocks secret-bearing outbound commands, metadata access, and non-allowlisted hosts.
2. `policies_guard` applies hard-deny and confirmation-tier command/file policies.
3. `permission_guard` evaluates persisted overlays, session grants, command segments, agent scope, `/yolo`, and interactive outcomes.

The order is fixed at [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:273). Interactive outcomes are once, session, persisted always, or decline. Headless routine rules allow shell asks only because the command is confined to the routine sandbox and earlier guards remain active; non-shell asks are denied. See [`permission_guard.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/guardrails/permission_guard.py:237).

The prompt is not the security boundary. Guards and environment adapters are. The local backend remains a host process with policy checks, not an OS sandbox.

## Output bounding and artifacts

Several layers prevent large or sensitive outputs from permanently occupying context:

- file/shell tools spill overflow and retain recovery paths;
- local process handles use a rolling output cap;
- stale tool outputs can be replaced with a marker while keeping overflow paths;
- compaction caps each inlined history item at 2,000 characters;
- memory preload is count/character bounded;
- signed artifact URLs are delivered to the client and then redacted from the next model request at [`artifact_url_redaction.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/context/artifact_url_redaction.py:26).

The invariant is progressive disclosure: the model receives enough metadata to decide whether to re-read, while full bytes live in workspace, artifact storage, or an overflow file.

## Steering, interruption, cancellation, and failure semantics

- A new ADK turn can include slash commands intercepted before model execution.
- HITL pauses a tool invocation and resumes through ADK function responses.
- Root iteration/tool budgets create a sticky graceful halt rather than an exception-only stop.
- Repeated identical failures and no-progress model turns can halt the run.
- Child delegates have time and event caps; background children can be cancelled, including direct descendants.
- Processes can be polled, waited, sent stdin, or killed.
- Routines can be cancelled in the durable store and their manifest removed.

There is no unified cancellation token spanning root model stream, every child, every OS process, every sibling review, and every routine. Each subsystem owns its own cancellation semantics.

## Observability and evaluation

The sample records structured in-flight tool state, bounded tool/delegate/memory activity for the UI, OpenTelemetry setup, A2A task state, and service-layer status endpoints. It also distinguishes deterministic and behavioral checks:

- unit tests pin prompts, compaction prompts/thresholds, tool schemas, guard decisions, process contracts, secret scope, subagent registries, routine stores, and sandbox lifecycle;
- integration tests drive resumable runners, nested child approval, parallel confirmations, context-cache configuration, and background process lifecycle;
- ADK evalsets cover model behavior for compression consumption, guardrail response, background-process selection, memory recall, and delegation.

Examples include the no-repeat resume assertion at [`test_delegate_resume_spike.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/tests/integration/test_delegate_resume_spike.py:117) and the compaction consumer eval at [`compression_quality.evalset.json`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/tests/eval/evalsets/compression_quality.evalset.json:1).

What is less explicit than Codex/OpenCode is a unified per-turn cost ledger and budget enforcement across root, compactor, memory siblings, and child agents. Token usage exists in ADK events, but the harness does not expose one host-owned economic control plane.

## Primitive inventory

| Primitive | Status | Owner | Durability |
|---|---|---|---|
| ADK root loop | default | ADK | session-dependent |
| Three-tier prompt | default | Horizon + ADK cache placement | stable tier process-built; context per turn |
| Provider/model routing | default | Horizon | session choice durable with session |
| Context caching | default config | ADK | provider cache TTL |
| Direct tool calls | default | ADK + callbacks | event-dependent |
| Deferred arbitrary tool discovery | absent | — | — |
| Host-side PTC | absent | — | — |
| Stateful Python executor | optional | ADK/Vertex | executor-session live; separate FS |
| Environment abstraction | default | Horizon | adapter-dependent |
| Per-user sandbox | optional deployment | Horizon + Vertex | remote + snapshots |
| Managed background processes | default | Horizon environment | local live; sandbox runtime-dependent |
| Cheap output pruning | default, env-disableable | Horizon | mutates active event projection |
| Structured compaction | default | ADK trigger + Horizon summarizer | compacted event/session-dependent |
| Pre-compaction memory flush | default, best-effort | Horizon sibling | Memory Bank-dependent |
| Resumable root/HITL | default | ADK | session-service-dependent |
| Blocking child | default subagent mode | Horizon + ADK | temporary durable child during HITL |
| Background child | optional call mode | Horizon | process-live only |
| Scheduled routine | optional user-created | Horizon | store/sandbox-dependent |
| Cross-session memory | service-dependent | ADK + Horizon policy | Memory Bank-dependent |
| Secret store and scoped injection | default backend choice | Horizon | Secret Manager-dependent |
| Exfil/policy/permission chain | default | Horizon | rules/grants vary |
| Goal/acceptance state machine | absent | — | — |
| Deterministic completion verifier | absent as universal primitive | task/tool-specific | — |
| Unified cost budget | absent | — | — |

## Design invariants

1. Stable instructions, project context, and volatile state must occupy different cache tiers.
2. Tools resolve an `Environment`; they do not own backend selection.
3. Security gates run before interactive permission, and `/yolo` cannot bypass hard denials.
4. Secret values remain host-side; the model sees names and references variables.
5. Resuming approval must continue the same durable invocation or halt, never rerun a fresh child silently.
6. A blocking child receives a self-contained brief, not ambient parent history, unless explicitly requested.
7. Headless work gets a smaller authority surface and explicit secret scope.
8. Cheap deterministic pruning happens before expensive lossy summarization.
9. Compacted history is reference material, not a new instruction source.
10. Workspace bytes, session events, memories, live process handles, background child handles, and scheduled routines are different state classes with different recovery guarantees.

## Limitations and explicit non-features

- The default root tool surface is broad and pays schema/choice complexity even after declaration compaction.
- There is no host-side PTC path that calls the existing broker from a deterministic program.
- The optional code executor is a separate filesystem/authority island, not a unified coding workspace.
- Background subagents are in-memory tasks and vanish on service restart.
- Local background process handles are not a durable job ledger.
- Root completion is not gated by a universal deterministic verifier.
- `plan.md` helps continuity but is model-maintained, not typed host state.
- Memory/review forks are best-effort and can be lost on abrupt termination.
- The pre-compaction memory fork races with compaction; it is scheduled, not synchronously committed before discard.
- No single global budget covers root calls, compaction, review forks, web research, and children.
- Provider switching has a registry/window contract, but not a general semantic migration contract across tokenizers, tool-call formats, or non-Gemini backends.
- Cancellation and status vocabularies are subsystem-specific rather than one task tree.
- Sandbox security is optional by deployment; the local adapter is not containment.

## What makes it a long-horizon harness

Horizon’s distinctive primitive is not “a very large context window.” It is the composition of multiple lifetimes:

1. ADK events make conversation/HITL resumable.
2. Prompt tiers and context caching make repeated long turns affordable.
3. pruning and compaction keep the active projection bounded.
4. Memory Bank carries selected facts across sessions.
5. `/workspace` and sandbox snapshots carry artifacts and environment state.
6. process tools carry commands across turns.
7. blocking children isolate context and can resurface one approval safely.
8. routines create fresh, durable, least-secret unattended invocations.

The implementation is strongest where these lifetimes are named and deliberately separate. Its main architectural gaps appear where the labels are broader than the persistence contract: “background” children are not durable jobs, “self-improvement” is not verified promotion, and “resumable” does not cover every live external effect.
