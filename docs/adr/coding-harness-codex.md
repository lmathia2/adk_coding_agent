# Codex coding-harness design

> **Status:** source-grounded implementation reference
> **Repository:** `/Users/mathiasl/src/codex`
> **Revision:** `986ff1cc7ced0081ec5014b700a376333d87f869`
> **Related:** [Pi](coding-harness-pi.md), [OpenCode](coding-harness-opencode.md), [ADK Long Horizon](coding-harness-adk-long-horizon.md), [comparison](coding-harness-comparison.md)

## Scope, revision, and reading conventions

This page specifies the coding harness implemented by the repository at `/Users/mathiasl/src/codex`, inspected at Git revision `986ff1cc7ced0081ec5014b700a376333d87f869` on 2026-09-01. It describes the Rust core and the extension crates that participate directly in an agent run. The TUI, app server, and desktop layers are mentioned only where they own an otherwise important lifecycle primitive. It is deliberately self-contained and does not compare Codex with another harness.

Status labels have precise meanings:

- **Default** means enabled in the checked-in feature catalog or selected by the ordinary configuration path.
- **Stable, opt-in** means declared stable but disabled by default or dependent on explicit configuration.
- **Experimental** means `Experimental` or `UnderDevelopment` in the feature catalog, normally disabled.
- **Process-live** means the state survives turns while the Codex process and object graph remain alive, but the source does not establish crash recovery.
- **Crash-durable** means serialized to rollout JSONL, the thread store, or the state database and reconstructed on resume.
- **Inference** marks an architectural conclusion rather than a direct source-level guarantee.

Line references point to the inspected revision. The highest-level entry points are the [session implementation](/Users/mathiasl/src/codex/codex-rs/core/src/session/mod.rs:1), [turn loop](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:304), [tool-plan builder](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:124), [model client](/Users/mathiasl/src/codex/codex-rs/core/src/client.rs:1), and [rollout facade](/Users/mathiasl/src/codex/codex-rs/core/src/rollout.rs:1).

Two caveats matter throughout:

1. The model catalog can be remotely supplied and can carry the current base-instruction template. Therefore the repository contains an exact fallback template and an exact resolution algorithm, but it does **not** necessarily contain the production text selected for every current remotely catalogued model.
2. Codex is a configurable harness. Its model-visible tool surface is constructed per turn from the model, provider, feature set, MCP servers, plugins/extensions, and dynamic tools; there is no single universal registry that every run exposes.

## Architectural map

```text
CLI / TUI / app server
        |
        v
 ThreadManager ── creates/resumes/forks ──> Session
        |                                    |
        |                                    +─ static base instructions
        |                                    +─ ContextManager / History
        |                                    +─ WorldState baseline + diffs
        |                                    +─ Rollout + state DB
        |                                    +─ process-live queues/services
        |                                                |
        v                                                v
 user input ──> turn loop ──> Prompt + exact ToolRouter ──> ModelClientSession
                      ^                                      |
                      |                              Responses stream
                      |                                      v
                      +──── tool calls <── ToolCallRuntime / handlers
                                            |
                    +-----------------------+-----------------------+
                    |                       |                       |
              unified exec             MCP/extensions          Code Mode
              patch/read/etc.          dynamic tools           V8 nested calls
                    |                       |                       |
                    +──── permission profile / approvals / OS sandbox / hooks

 Context pressure ──> local / remote / Remote V2 / token-budget compaction
 Long horizon     ──> durable goal extension + continuation + multi-agent threads
```

The central separation is between **conversation state** and **authority**. The model receives instructions, history, tool schemas, and environment snapshots, but tool dispatch remains in the host. Every call is resolved through a router and runtime that can apply locking, hooks, approval policy, and sandbox transformation before execution. The tool plan is rebuilt from explicit contributors rather than inferred from prompt prose ([plan construction](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:124), [router construction and collision checks](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:350), [runtime exposure check](/Users/mathiasl/src/codex/codex-rs/core/src/tools/router.rs:74)).

## Base instructions and prompt assembly

### Base-instruction resolution

At session creation the base instructions are selected in this order:

1. an explicit configuration override;
2. the `base_instructions` persisted in resumed conversation metadata;
3. the rendered instruction template on the selected `ModelInfo`.

That precedence is implemented in [session initialization](/Users/mathiasl/src/codex/codex-rs/core/src/session/mod.rs:678). Preserving the persisted prompt on resume is intentional: a catalog refresh must not silently reinterpret an existing conversation. A `ModelInfo` may contain a literal override or a template whose personality placeholder is rendered for the configured personality ([model template rendering](/Users/mathiasl/src/codex/codex-rs/models-manager/src/model_info.rs:17)). The protocol accessor returns the model's configured instruction text ([catalog model accessor](/Users/mathiasl/src/codex/codex-rs/protocol/src/openai_models.rs:518)).

The checked-in representative fallback for the Codex family begins:

> You are Codex, a coding agent based on GPT-5. You are running as an agent in the Codex CLI on a user's computer.

The exact local template is [gpt-5.2-codex instructions](/Users/mathiasl/src/codex/codex-rs/core/templates/model_instructions/gpt-5.2-codex_instructions_template.md:1). Its major contracts are:

- act as a coding agent in a shared workspace;
- communicate progress in commentary and return a self-contained final answer;
- use `rg`/`rg --files` for discovery, parallelize independent reads, and use the patch tool for edits;
- preserve unrelated dirty-worktree changes and avoid destructive Git operations without authorization;
- persist autonomously on build/change requests but do not infer broader authority;
- use plans for sufficiently complex work, and keep them current;
- follow strict local-file link and code-review presentation rules;
- obey special request handling and frontend quality guidance.

This is a **representative repository-local prompt**, not a claim about the byte-for-byte prompt for the currently deployed GPT-5.6 model. The exact production text is whatever `ModelInfo` the active catalog supplies, subject to the resolution order above.

### Request prompt and Responses input

For each model step, Codex constructs a `Prompt` from the normalized history, the exact tool specifications for that step, `parallel_tool_calls: true`, the resolved base instructions, and an optional strict final-output JSON schema ([prompt construction](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:1353)). Thus the API request separates the long-lived base instruction from conversation items and tool schemas; the latter may change during a session.

`parallel_tool_calls: true` is permission for the model to emit multiple calls. It is not a promise that all handlers run concurrently. Runtime locking still serializes handlers that declare themselves unsafe for parallel execution ([parallel dispatcher](/Users/mathiasl/src/codex/codex-rs/core/src/tools/parallel.rs:41)).

## World state, developer context, and project instructions

### Typed world-state assembly

Codex does not append one monolithic mutable developer string on every turn. It constructs typed world-state sections for model instructions, personality, token/context guidance, realtime interaction, project instructions, permission profile, collaboration mode, persistent mode, environment/date, apps, plugins, deferred tools, extension-contributed context, multi-agent guidance, and managed developer instructions ([world-state builder](/Users/mathiasl/src/codex/codex-rs/core/src/session/world_state.rs:33)).

`ContextManager` owns normalized model history, the active world-state baseline, review history, token information, and related bookkeeping ([history manager](/Users/mathiasl/src/codex/codex-rs/core/src/context_manager/history.rs:51)). At a safe boundary it compares the newly built state with the prior baseline and records only the model-facing fragments required to update that baseline ([world-state update](/Users/mathiasl/src/codex/codex-rs/core/src/context_manager/history.rs:191)). World-state values have stable hashes and serializable snapshots; merge changes use an RFC 7386-style merge patch ([world-state hashing and snapshots](/Users/mathiasl/src/codex/codex-rs/core/src/context/world_state/mod.rs:264)).

**Design effect (inference):** unchanged developer context retains a stable prompt prefix, improving provider cache reuse, while changes remain explicit in rollout history. This is cache-oriented diffing, not hidden mutable server state: the baseline and patches are model-visible/persisted conversation artifacts.

Deferred-tool summaries are deliberately compact. The world-state renderer caps the whole deferred-tool section at 4 KiB and namespace descriptions at 250 characters ([deferred-tool world state](/Users/mathiasl/src/codex/codex-rs/core/src/context/world_state/tools.rs:12)). Full schemas are materialized only when the relevant tool becomes exposed.

### `AGENTS.md` discovery, precedence, and trust

Project instructions are discovered from filesystem roots toward the current working directory. At each level `AGENTS.override.md` takes precedence over `AGENTS.md`, followed by configured fallback names; matching files are concatenated in root-to-leaf order ([candidate and precedence logic](/Users/mathiasl/src/codex/codex-rs/core/src/agents_md.rs:185)). Reads are sandbox-aware and the accumulated byte budget is bounded ([sandboxed collection](/Users/mathiasl/src/codex/codex-rs/core/src/agents_md.rs:65)); the default project-document budget is 32 KiB ([configuration defaults](/Users/mathiasl/src/codex/codex-rs/core/src/config/mod.rs:228)).

An untrusted project does not get to inject local repository instructions: discovery returns no project documentation under that trust state ([untrusted-project guard](/Users/mathiasl/src/codex/codex-rs/core/src/agents_md.rs:55)). This is an important trust boundary. `AGENTS.md` is advisory developer context after the project has passed trust policy; it does not itself grant filesystem, network, process, or MCP authority.

### Environment snapshots and steering

Each step receives a `TurnEnvironmentSnapshot` rather than relying solely on the session-start environment. The turn loop drains queued user input only at safe boundaries, builds a fresh snapshot/world-state diff, records it, then samples the model ([main turn loop](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:304)). Steering therefore becomes an ordered conversation item between completed model/tool steps, not an unsafe mutation in the middle of a handler.

## Model, provider, streaming, and session abstraction

`ModelClient` is session-scoped and carries authentication, provider configuration, conversation identity, and transport fallback policy. `ModelClientSession` is turn-scoped and owns reusable transport state such as a WebSocket connection and the sticky `x-codex-turn-state` header ([client object model](/Users/mathiasl/src/codex/codex-rs/core/src/client.rs:235)). Sticky state is reused across retries and continuation samples inside one turn, but not across turns. This gives transport efficiency without making the provider the canonical conversation store.

The client consumes the Responses-style streaming protocol and turns stream events into agent messages, reasoning items, tool calls, token/rate-limit updates, and completion/error signals. The turn sampler retains a response-retry state, updates rate-limit snapshots, recognizes context overflow, and retries transient failures with provider-configured limits and backoff ([sampling and retry loop](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:1382)). On context overflow it marks token use as full so the next control step compacts instead of blindly repeating the same oversized request.

Provider configuration and model metadata remain separate. The provider controls transport/auth/retry and remote-compaction capability; `ModelInfo` controls instruction rendering, context-window characteristics, tool support, and output policy. A session can switch models, but pre-turn logic checks compaction compatibility and context downshifts before continuing ([model-switch and pre-turn compaction](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:1053)).

## Model-visible tools

### Registry construction

The exact tool surface is computed in stages:

1. register eligible core tools;
2. register MCP tools and MCP resource helpers;
3. ask extensions/plugins for contributed tools;
4. add caller-supplied dynamic tools;
5. add provider-hosted tools such as web search when selected;
6. compute whether each tool is direct, deferred behind discovery, available only through Code Mode, or hidden;
7. build the final router, rejecting ambiguous name collisions.

The sequence is explicit in [tool-plan construction](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:124), [exposure policy](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:196), and [final router assembly](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:350). Every dispatched name must be present in the router for the current step ([router dispatch guard](/Users/mathiasl/src/codex/codex-rs/core/src/tools/router.rs:74)).

### Core registry inventory

Depending on model and feature gates, the core planner can expose:

- `exec_command` and `write_stdin` for unified interactive execution, or the one-shot shell variant;
- `apply_patch` when the model supports the freeform patch tool;
- `view_image`;
- `update_plan`;
- `request_user_input` (experimental in relevant modes), `send_user_message_async` for supporting models, and `wait_for_environment`;
- `request_permissions` under managed permission workflows;
- `current_time` and `sleep` where enabled;
- `new_context_window` and `get_context_remaining` for the token-budget context protocol;
- `list_mcp_resources`, `list_mcp_resource_templates`, and `read_mcp_resource`;
- plugin discovery and installation-request tools;
- model-specific or experimental helpers such as `test_sync`;
- either the V1 or V2 multi-agent collaboration family.

The actual registrations are grouped as core/model tools ([core registrations](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:973)), shell ([shell registrations](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:1074)), MCP resources ([MCP resource registrations](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:1122)), utilities ([utility registrations](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:1131)), and collaboration ([multi-agent registrations](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:1263)).

This list is the **superset known to the core planner**, not a promise that every name is direct and visible. Tool-search policy, Code Mode-only policy, model support, provider choice, permission profile, and extensions all narrow or enlarge an individual step's schema.

### MCP, hosted, extension, and dynamic tools

MCP server tools are normalized into the same router as native tools and ultimately execute through the same call runtime. MCP resources use explicit list/template/read helpers. Approval policy may require a user decision for an MCP action, and external-context-producing MCP/web activity can mark a memory-enabled thread as polluted when configured ([external-context memory guard](/Users/mathiasl/src/codex/codex-rs/core/src/mcp_tool_call.rs:877)).

Extensions implement typed contributors rather than modifying a global dictionary. The registry supports approval, thread, turn, config, token, skill, context, MCP, input, tool, lifecycle, and turn-item contributions ([extension registry](/Users/mathiasl/src/codex/codex-rs/ext/extension-api/src/registry.rs:25)). Tool contributors are consulted by the tool planner ([extension tool contribution](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:331)); context contributors are consulted by world-state assembly ([extension context contribution](/Users/mathiasl/src/codex/codex-rs/core/src/session/world_state.rs:293)). Dynamic tools supplied for a session and provider-hosted tools share the final collision-checked namespace.

### Deferred discovery and skills

`tool_search` is included only when the plan contains discoverable deferred entries and the model/configuration supports it ([tool-search condition](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:1384)). A search result can make selected schemas direct on a later step; until then the world state carries only bounded descriptions. This limits schema-token overhead while retaining a large installable capability graph.

Skills are instruction packages, not privileged tool handlers. Codex selects explicit invocations, validates paths and scope, injects the selected `SKILL.md` content, and records errors/warnings in the turn ([turn skill handling](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:754), [selection algorithm](/Users/mathiasl/src/codex/codex-rs/skills/src/selection.rs:31)). It can also recognize implicit skill use through shell reads or script invocation ([invocation detection](/Users/mathiasl/src/codex/codex-rs/skills/src/invocation.rs:12)). A skill influences policy and workflow, but its actions still pass through ordinary tools and host authority.

## Direct agent loop, concurrency, interruption, and retry

The direct loop is event-driven rather than a fixed ReAct iteration count:

1. absorb pending user input at a safe boundary;
2. construct/record the current environment and world-state delta;
3. normalize history and build the exact prompt/tool plan;
4. stream a Responses completion;
5. dispatch any tool calls, recording call and output items;
6. continue if the response, tool work, or newly queued user input requires another sample;
7. compact before the next sample when the context threshold is reached;
8. finish or yield control when no continuation remains.

The control flow is in the [turn loop](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:304). No universal fixed maximum model-step count is evident in this path. Long-running behavior is instead bounded by cancellation, context, rate/retry policy, tool/process limits, optional goal budget, and higher-level application lifecycle.

Parallel calls are coordinated by a read/write lock. Tools that declare parallel safety acquire a shared read lock; unsafe tools take the exclusive write lock. Each call has a cancellation token, and aborted calls receive a normalized tool-output item rather than silently disappearing ([parallel tool runtime](/Users/mathiasl/src/codex/codex-rs/core/src/tools/parallel.rs:41)). Unified exec declares parallel support ([exec handler](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs:110)).

Interrupt is cooperative at orchestration boundaries and cancellative at tool boundaries. User steering is queued; an explicit interrupt cancels the active turn/call token. Transient model errors retry under provider limits; context errors switch to compaction behavior rather than ordinary backoff ([sampler retry behavior](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:1382)).

## Code Mode: host-side programmatic tool routing

### What it is

Code Mode exposes a freeform `execute` tool whose argument is raw JavaScript, optionally preceded by a first-line execution pragma ([freeform grammar](/Users/mathiasl/src/codex/codex-rs/core/src/tools/code_mode/execute_spec.rs:8)). The script is evaluated as an async module in a fresh V8 isolate. It can call eligible nested tools through `await tools.<normalized_name>(...)`, compose their results, emit text/image/audio/generated-image blocks, retain session values with `store`/`load`, notify immediately, schedule timers, inspect `ALL_TOOLS`, and explicitly yield control ([model-visible Code Mode description](/Users/mathiasl/src/codex/codex-rs/code-mode-protocol/src/description.rs:15)).

The runtime deliberately does not provide Node.js, filesystem, network, or console APIs. It removes or withholds additional globals including `console`, `Atomics`, `SharedArrayBuffer`, and WebAssembly before installing only the approved helper surface ([V8 globals](/Users/mathiasl/src/codex/codex-rs/code-mode-runtime/src/runtime/globals.rs:15)). The isolate/module lifecycle is implemented in [runtime creation and evaluation](/Users/mathiasl/src/codex/codex-rs/code-mode-runtime/src/runtime/mod.rs:74).

### Nested authority and discovery

Code Mode does not create a second, more privileged execution path. Its dispatch broker delegates nested calls back through `ToolCallRuntime`, so normal routing, hooks, approvals, sandboxing, output policy, and cancellation still apply ([nested-call delegate](/Users/mathiasl/src/codex/codex-rs/core/src/tools/code_mode/delegate.rs:101)). The planner builds a normalized catalog from direct and deferred candidates; under `CodeModeOnly`, ordinary direct schemas can be hidden from the model yet remain callable by JavaScript ([catalog and hiding policy](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:790)).

This is therefore a **host-side programmatic tool-calling (PTC) analogue**: the model writes a short deterministic coordinator program and the host executes it. It is distinct from a provider-native PTC protocol in which the model API itself owns code execution and tool delegation. **Inference from repository search:** this checked-in production path does not expose a provider-native `programmatic_tool_calling` transport primitive; “Code Mode” is Codex's own V8 host facility.

### Budgets, output, yield, and lifecycle

The first-line pragma can request `yield_time_ms` and `max_output_tokens`; both default to 10,000, and the host can cap them ([Code Mode description](/Users/mathiasl/src/codex/codex-rs/code-mode-protocol/src/description.rs:23)). If evaluation has not completed by the yield deadline, `execute` returns a cell ID; the separate `wait` operation can collect later output or terminate the cell. `yield_control()` returns accumulated output immediately while evaluation remains active.

The `CodeModeService` is session-owned and lazily initialized. It tracks active cells, executes/waits/terminates, and shuts cells down with the session ([session Code Mode service](/Users/mathiasl/src/codex/codex-rs/core/src/tools/code_mode/mod.rs:69)). Shared `store` values live in the service/session abstraction ([protocol session state](/Users/mathiasl/src/codex/codex-rs/code-mode-protocol/src/session.rs:98)). Despite the protocol's “durable session” wording, the in-process implementation does not establish disk persistence. Cells, timers, and stored values should be treated as **process-live**, not crash-durable.

The runtime has an interface for a heap cap, but the in-process host currently initializes it with `max_heap_size_bytes: None` ([in-process service configuration](/Users/mathiasl/src/codex/codex-rs/code-mode-runtime/src/service.rs:47)). There is no separate nested-call-count budget evident in this path. The meaningful enforced bounds are eligible-tool authority, output caps, yield timing, cancellation, normal per-tool limits, and process lifecycle.

### Feature status

`CodeMode` and `CodeModeOnly` are under development and disabled by default; `CodeModeHost` is stable and enabled by default; prewarming and interrupt-related variants are experimental and disabled ([feature catalog](/Users/mathiasl/src/codex/codex-rs/features/src/lib.rs:977)). The stable host means the runtime can be available, not that ordinary sessions expose the model-facing Code Mode tools by default.

## Output bounds and artifact semantics

Unified exec accepts `max_output_tokens`, defaulting to 10,000 ([shell tool schema](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/shell_spec.rs:24)). The process manager additionally defines yield bounds of 250–30,000 ms, background polling up to 300,000 ms, a default 10,000-token response cap, a 1 MiB collection limit, and at most 64 tracked processes ([unified-exec limits](/Users/mathiasl/src/codex/codex-rs/core/src/unified_exec/mod.rs:73)).

Tool results retain raw structured output long enough for handler-specific processing, then apply the minimum of the requested cap and model output policy. Truncation records pre-cap omission metadata and adds a warning/marker ([tool-output capping](/Users/mathiasl/src/codex/codex-rs/core/src/tools/context.rs:345)). History normalization can truncate again to fit the model's tool-output policy ([history truncation](/Users/mathiasl/src/codex/codex-rs/core/src/context_manager/history.rs:223)). MCP structured outputs and image blocks are supported; Code Mode can receive raw structured nested results before composing its own bounded outward result ([MCP/Code Mode output path](/Users/mathiasl/src/codex/codex-rs/core/src/tools/context.rs:100)).

There is **no universal artifact indirection for complete shell logs** in the inspected core. Oversized output is represented by bounded head/tail-style content and omission metadata; a caller that requires a complete artifact must make the command write one intentionally or use another persistence mechanism. This distinction prevents treating a truncation marker as a durable full-output reference.

## Context accounting and compaction

### Trigger and accounting

Codex tracks active token use and computes both an automatic-compaction threshold and a hard effective-context limit. Automatic accounting can consider the total context or the body after a cacheable prefix; the hard limit derives from the resolved model window and effective percentage, with a fallback response buffer. Compaction is considered reached when the buffered automatic or hard cap is reached ([context-window accounting](/Users/mathiasl/src/codex/codex-rs/core/src/session/context_window.rs:25)).

Compaction can occur before a user turn and in the middle of an agent turn before another tool/follow-up sample. If steering is queued at that moment, it is retained until compaction completes ([mid-turn compaction](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:469)). On model switch, the code also checks context-window downshift and compaction-prompt compatibility; it can compact using the prior model to preserve semantic compatibility and then fall back as needed ([pre-turn/model-switch path](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:1053)).

### Strategy selection

The strategy order is:

1. token-budget protocol when that feature is active;
2. provider remote Remote V2 when supported and enabled;
3. provider legacy remote compaction when remote support exists but V2 is off;
4. local model-generated compaction otherwise.

The branch is in [compaction selection](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:1219). Remote V2 is stable and enabled by default; token-budget compaction is under development and disabled ([feature statuses](/Users/mathiasl/src/codex/codex-rs/features/src/lib.rs:1539)). “Remote” here describes a provider compaction endpoint, not a remotely authoritative conversation: the returned items are still installed into Codex history and rollout.

### Local compaction

Local compaction asks the model to produce a continuation-ready summary. The checked-in prompt requests a concise account of progress, decisions, relevant files, and next steps ([local compaction prompt](/Users/mathiasl/src/codex/codex-rs/prompts/templates/compact/prompt.md:1)); the summary is prefixed with a standard marker ([summary prefix](/Users/mathiasl/src/codex/codex-rs/prompts/templates/compact/summary_prefix.md:1)). It calls the model with the same base instructions and reusable turn transport. On a context error it removes the oldest history item and retries under bounded backoff ([local compaction execution](/Users/mathiasl/src/codex/codex-rs/core/src/compact.rs:245)).

The replacement history is not simply “last N messages.” Codex keeps selected user messages, appends the generated assistant summary, installs a compaction checkpoint, and recomputes tokens. The user-message tail is collected newest-first under a 20,000-token budget ([tail retention](/Users/mathiasl/src/codex/codex-rs/core/src/compact.rs:645)). Assistant/tool detail is represented through the summary rather than retained wholesale. Repeated compactions can generate a warning.

### Remote V2 compaction

Remote V2 receives provider-produced compaction output, filters/truncates retained items, and appends the server compaction item to local history ([Remote V2 replacement](/Users/mathiasl/src/codex/codex-rs/core/src/compact_remote_v2.rs:485)). Its checked-in bounds include a 64,000-token retained-input budget, a 10,000-token maximum retained agent message, and two retries ([Remote V2 constants](/Users/mathiasl/src/codex/codex-rs/core/src/compact_remote_v2.rs:68)); retention is allocated newest-first ([retention algorithm](/Users/mathiasl/src/codex/codex-rs/core/src/compact_remote_v2.rs:539)). Legacy remote compaction remains a separate compatibility implementation ([legacy remote compaction](/Users/mathiasl/src/codex/codex-rs/core/src/compact_remote.rs:1)).

### Review evidence, replay, and cache behavior

`ContextManager` maintains a review-oriented history in addition to the active compacted model history so approval/review flows can inspect original evidence after model compaction ([review-history intent](/Users/mathiasl/src/codex/codex-rs/core/src/context_manager/history.rs:1)). That does not mean the full evidence is still sent to the next model request.

Compaction checkpoints and replacement items are rollout events, so resume reconstructs the compacted active history rather than rerunning summarization. Stable base instructions, world-state hashing, and diff emission preserve cacheable prefixes where possible. Any change in base instructions, tool schemas, model, or relevant world state can legitimately change the request prefix; Codex does not promise cache identity across those transitions.

## Thread, rollout, state, resume, fork, rollback, and workspaces

### Persistence layers

Codex uses multiple stores with different durability:

| State | Owner | Durability |
|---|---|---|
| conversation items, environment/world-state changes, compaction checkpoints, token updates | rollout JSONL / thread store | crash-durable after flush |
| thread metadata and goal/memory records | state database | crash-durable |
| normalized history and world-state baseline | `ContextManager` | process-live, reconstructable from rollout |
| thread objects and input queues | `ThreadManager` / session | process-live |
| unified-exec process handles | `UnifiedExecProcessManager` | process-live only |
| Code Mode cells, timers, and `store` values | `CodeModeService` | process-live only |

The rollout interface is factored behind a recorder facade ([rollout facade](/Users/mathiasl/src/codex/codex-rs/core/src/rollout.rs:1)). Resume reconstructs persisted base instructions, dynamic tools, history, token information, and interruption state; it then materializes and flushes an active continuation record ([resume/fork initialization](/Users/mathiasl/src/codex/codex-rs/core/src/session/mod.rs:1397)).

### Fork and rollback

`ThreadManager` keeps active thread/session handles and can create a fork from a history snapshot ([thread-manager fork](/Users/mathiasl/src/codex/codex-rs/core/src/thread_manager.rs:164)). Forking can truncate at a selected user-message boundary or preserve an interrupted snapshot, and the child receives its own conversation identity/rollout. A persisted fork is therefore a new durable history lineage, not a shared mutable tail.

Rollback/revert is a first-class session operation that updates model history and persisted thread state rather than merely hiding UI messages ([rollback handler](/Users/mathiasl/src/codex/codex-rs/core/src/session/handlers.rs:254)). Reconstruction, fork, and rollback contracts have deterministic coverage in [session reconstruction/rollback tests](/Users/mathiasl/src/codex/codex-rs/core/src/session/tests.rs:3851).

### Worktrees and execution environments

The core session operates against an explicit working directory and `TurnEnvironmentSnapshot`; tools may target local or supported remote execution environments. Worktree creation/handoff is primarily an app/desktop orchestration concern, not an unconditional model-visible core tool in the registry above. **Inference:** worktrees isolate parallel Git state, but they should not be described as an automatic property of every spawned core subagent. The actual isolation depends on the caller/environment used to create or hand off the thread.

### Crash/recovery semantics

After a crash, a flushed thread can reconstruct transcript, compacted history, persisted metadata, goals, and child-thread identities. It cannot reattach to an operating-system process stored only in the unified-exec map, resume a V8 cell/timer, or recover an unflushed in-memory steering queue. A model/tool call interrupted before a durable output is recorded is reconstructed as interrupted rather than guessed complete. Goal continuation can schedule fresh future work after restart, but it does not resurrect the exact instruction pointer of a lost model sample or process.

## Long-horizon control: goals, plans, progress, and completion

### Durable goals

The goal extension is stable and enabled by default ([goal feature](/Users/mathiasl/src/codex/codex-rs/features/src/lib.rs:1533)). It stores goal state in the state database and exposes create/read/update operations. Creation permits one unfinished goal at a time and validates an optional positive token budget; model-facing updates can mark only `complete` or `blocked` ([goal tool executor](/Users/mathiasl/src/codex/codex-rs/ext/goal/src/tool.rs:191)). Time and token consumption are accounted against durable goal state ([goal accounting](/Users/mathiasl/src/codex/codex-rs/ext/goal/src/tool.rs:316)).

At startup/runtime the extension restores unfinished state and can continue an idle thread ([goal runtime restore](/Users/mathiasl/src/codex/codex-rs/ext/goal/src/runtime.rs:375), [extension lifecycle](/Users/mathiasl/src/codex/codex-rs/ext/goal/src/extension.rs:95)). The continuation prompt treats the objective as data, says the current thread state is authoritative, requires classification of meaningful progress versus waiting/no progress, audits completion evidence, preserves original scope, and permits “blocked” only after three consecutive impasse turns ([goal continuation contract](/Users/mathiasl/src/codex/codex-rs/ext/goal/templates/goals/continuation.md:1)). Near a configured budget, a separate prompt forces an explicit scope-and-evidence audit rather than treating budget exhaustion as success ([budget-limit prompt](/Users/mathiasl/src/codex/codex-rs/ext/goal/templates/goals/budget_limit.md:1)).

This goal machine is Codex's strongest built-in “stay on track” primitive: objective and status are durable, continuations are generated when idle, and premature `blocked`/`complete` claims receive explicit policy. It is still semantically mediated by the model and hooks; it is not a formal proof that repository tests passed.

### Plans and visible progress

`update_plan` is included when planning is enabled ([registration](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:1136)). Its schema permits a short explanation and ordered steps with `pending`, `in_progress`, or `completed`, with at most one in-progress step ([plan schema](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/plan_spec.rs:7)). Plans are model-visible/UI-visible working state, useful for progress communication and steering, but they are not the durable long-running state machine that goals are.

### Verification and completion control

Codex provides the primitives to run tests, inspect diffs, use review hooks, require approvals, and constrain final output with a JSON schema. It does **not** impose a universal deterministic completion gate in the core turn loop. **Inference:** whether a completion claim is mechanically verified depends on repository instructions, invoked tools, extension hooks, an evaluator, or a product-layer workflow. Guardian can approve/reject risky actions; it does not prove functional correctness. Goal completion prompts demand evidence, but the final classification remains a model action recorded by the host.

## Multi-agent orchestration

### Versions and limits

V1 collaboration is stable and enabled by default. V2 is stable but disabled by default ([collaboration feature flags](/Users/mathiasl/src/codex/codex-rs/features/src/lib.rs:1209)). Default configuration permits six spawned V1 threads at depth one; V2 uses a total concurrency limit of four (including the root), and wait defaults to 30 seconds with a 10-second minimum and one-hour maximum ([collaboration defaults](/Users/mathiasl/src/codex/codex-rs/core/src/config/mod.rs:228), [effective V2 child capacity](/Users/mathiasl/src/codex/codex-rs/core/src/config/mod.rs:1562)).

V1 exposes spawn/send-input/wait/resume/close operations. V2 exposes spawn, message, follow-up, wait, list, and interrupt operations ([multi-agent schemas](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs:65)). V2 tools are direct-only by default, whereas V1 can participate in deferred/direct exposure policy ([registration policy](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:1263)).

### Spawn, inheritance, communication, and waiting

V2 spawn can choose how much completed turn history to fork, set a role, and apply permitted model/reasoning overrides. It assigns a canonical agent path and creates a child thread/session ([V2 spawn](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs:93)). Common child configuration inherits the current model/provider, reasoning effort, approval policy, working directory, permission profile, and other safe runtime context ([child configuration](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_common.rs:170)).

Messages enter a child mailbox; a follow-up task both queues the prompt and triggers work when idle; interrupt cancels an active child turn. Wait blocks for mailbox/progress/final activity within bounded time and exits early on root steering ([V2 wait](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs:39)). This favors event-driven orchestration over aggressive status polling.

Child histories are durable when backed by normal rollouts/thread storage. The live mailbox, manager handles, and currently running task are process-live. After restart the child may be resumed as a thread, but a pending in-memory wait or instruction pointer is not crash-durable. Depth and total-concurrency limits bound recursive fan-out; shared-directory children can still conflict at the filesystem level unless the caller gives them isolated worktrees/environments.

## Unified execution and live processes

`exec_command` unifies one-shot and interactive command execution. A command that finishes during the initial yield returns output and exit status; a continuing command returns a session ID. `write_stdin` sends bytes or polls that live process, with separate timing/output bounds ([shell specifications](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/shell_spec.rs:24)). The handler supports the selected local/remote environment, obtains approvals, applies sandbox policy, and chooses interactive versus one-shot execution ([unified exec handler](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs:94)).

The process manager is an in-memory map capped at 64 entries ([manager storage](/Users/mathiasl/src/codex/codex-rs/core/src/unified_exec/mod.rs:149)). Session IDs are capabilities into that map, not operating-system-global durable identities. Cancellation and explicit termination are supported, but a Codex process crash loses the map even if the child OS process survives independently.

## Permissions, approvals, OS sandbox, Guardian, and trust

### Permission profiles

`PermissionProfile` has three top-level modes: managed filesystem/network policy, disabled host enforcement, and externally enforced policy. Built-in projections cover read-only, workspace-write, and dangerous/full-access patterns ([permission models](/Users/mathiasl/src/codex/codex-rs/protocol/src/models.rs:401)). A managed policy names readable/writable roots and network authority; it is evaluated relative to the current working directory and transformed into platform enforcement.

### OS sandbox and escalation

The sandbox manager selects macOS Seatbelt, a Linux seccomp/helper path, or Windows restricted-token enforcement ([platform selection](/Users/mathiasl/src/codex/codex-rs/sandboxing/src/manager.rs:35)). It rewrites the command invocation with the chosen platform sandbox and permission profile ([sandbox transform](/Users/mathiasl/src/codex/codex-rs/sandboxing/src/manager.rs:300)). The local environment adapter's strength therefore depends on OS support and selected profile; “workspace write” is not just a prompt convention.

Approval logic first consults stored policy, determines whether a proposed call requires approval, asks when required, and can retry an operation with escalated authority. Denied reads are carried into the escalation path so approval cannot accidentally erase evidence about what failed ([approval store and request](/Users/mathiasl/src/codex/codex-rs/core/src/tools/sandboxing.rs:39), [escalation evidence](/Users/mathiasl/src/codex/codex-rs/core/src/tools/sandboxing.rs:238)). Approval action types cover command execution/stdin/execve, patching, MCP, network, and general permission requests ([approval actions](/Users/mathiasl/src/codex/codex-rs/core/src/tools/approvals.rs:54)).

### Guardian and hooks

Guardian can review approval requests. Its review session receives a deliberately narrow tool plan when running under managed sandboxing ([Guardian tool restriction](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:973)). Guardian V2-related features are under development and disabled by default ([Guardian feature entries](/Users/mathiasl/src/codex/codex-rs/features/src/lib.rs:1521)); deployments can still configure other approval reviewers. The source should not be read as saying every default command is automatically adjudicated by an LLM reviewer.

Hooks can run around turn/tool/permission/compaction/session lifecycle. Plugin-origin hooks are tracked, and a trust-bypass gate controls whether untrusted project hooks can participate ([plugin hook/trust wiring](/Users/mathiasl/src/codex/codex-rs/core/src/session/mod.rs:4658)). Hooks may observe, modify permitted extension context, reject, or stop according to their typed contract. They augment enforcement; they do not replace the permission profile or OS sandbox.

## Memory, plugins, and other extension points

### Long-term memory

Codex has a two-stage, file-backed memory subsystem plus read/search/list/note extension tools. The feature is declared stable but disabled by default ([memory feature](/Users/mathiasl/src/codex/codex-rs/features/src/lib.rs:1067)); when enabled, separate configuration controls generating and using memories ([memory config defaults](/Users/mathiasl/src/codex/codex-rs/config/src/types.rs:325)). State records store stage-one extraction output and job claims ([memory state model](/Users/mathiasl/src/codex/codex-rs/state/src/model/memories.rs:8)); consolidation runs as an internal subagent/session rather than mutating the foreground turn.

Memory citations are parsed into agent messages, and a thread can be marked polluted/disabled for future memory generation after configured external context such as MCP or web search ([session memory-mode guard](/Users/mathiasl/src/codex/codex-rs/core/src/session/mod.rs:1515)). This prevents accidentally treating third-party transient content as trusted project memory. Memory is thus crash-durable when enabled, but it is an opt-in cross-thread aid, not part of the default prompt/history mechanism.

### Plugins and extensions

Plugins can contribute skills, MCP servers, hooks, context, and native extension tools through the typed registry. A model may receive a recommendation/request-install tool for known but absent plugins, but installation is host-authorized rather than an implicit side effect. Name collisions are rejected at final tool-router construction, keeping the model-to-handler mapping deterministic.

The extension API is intentionally broader than model-visible tools: lifecycle and token contributors can influence scheduling/accounting without adding schemas, while context/skill/tool contributors can affect prompt/tool composition ([extension registry queries](/Users/mathiasl/src/codex/codex-rs/ext/extension-api/src/registry.rs:163)). This is the main product-level customization boundary.

## Observability, telemetry, cost, and evaluation

Session telemetry records model/provider, authentication/session source, sandbox/approval configuration, feature state, terminal/app metadata, and other run dimensions ([session telemetry metadata](/Users/mathiasl/src/codex/codex-rs/otel/src/events/session_telemetry.rs:93)). Turn telemetry records duration, token usage, and estimated cost dimensions ([turn cost metric](/Users/mathiasl/src/codex/codex-rs/otel/src/events/session_telemetry.rs:277)); token facts distinguish cached and cache-write categories ([analytics token facts](/Users/mathiasl/src/codex/codex-rs/analytics/src/facts.rs:479)). Tool calls, Code Mode, and multi-agent activity have analytics facts ([analytics client](/Users/mathiasl/src/codex/codex-rs/analytics/src/client.rs:357)).

Rollout tracing connects model samples and tool/Code Mode dispatch to a thread timeline ([tool dispatch trace](/Users/mathiasl/src/codex/codex-rs/core/src/tools/tool_dispatch_trace.rs:29), [Code Mode trace](/Users/mathiasl/src/codex/codex-rs/core/src/tools/code_mode/execute_handler.rs:94)). Observability is separable from semantics: telemetry failure should not become conversation state, while rollout failure can affect crash durability.

The repository contains deterministic tests for tool-spec construction and collision behavior ([tool-plan tests](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan_tests.rs:1)), world-state/history normalization, local and remote compaction, sandbox transformation, rollout reconstruction, rollback, goals, Code Mode protocol/runtime, and memory. These are contract tests around deterministic harness behavior. Model quality, task completion quality, and prompt effectiveness still require higher-level evals; unit tests do not assert that an arbitrary natural-language coding task succeeds.

## Limitations and explicit non-features

- The repository-local prompt is not guaranteed to equal the latest remotely catalogued model prompt.
- The tool surface is per-step and configuration-dependent; documentation that lists a single fixed Codex tool set is incomplete.
- `parallel_tool_calls: true` does not override handler locks or filesystem conflict risk.
- There is no evident universal fixed step limit in the core loop and no universal deterministic “tests must pass” completion gate.
- Code Mode is an experimental model-facing facility backed by a stable host; its cells and key/value store are not crash-durable.
- The in-process Code Mode host does not currently set a V8 heap cap, and no independent nested-call-count quota is evident.
- Shell truncation does not automatically create a complete-output artifact.
- Unified-exec process sessions, input queues, live child mailboxes, and current V8 cells cannot be reconstructed from rollout after a crash.
- A shared working directory is not multi-agent isolation. Worktree isolation must be supplied by the thread/application environment.
- Plan state is progress metadata, not a verifier; goal completion is durable but semantically model-mediated.
- OS sandbox guarantees vary by selected permission profile, operating system, and whether enforcement is managed, disabled, or external.
- Long-term memory is stable but opt-in and can be suppressed after external context; it is not a default replacement for rollout/history.

## Primitive inventory

| Primitive | Responsibility | Default/status | Durable boundary | Primary implementation |
|---|---|---|---|---|
| `Session` | Own conversation identity, configuration, context, services, rollout | default | reconstructable | [session](/Users/mathiasl/src/codex/codex-rs/core/src/session/mod.rs:678) |
| `ModelInfo` instruction renderer | Resolve catalog template/personality | default | base text persisted on thread | [model info](/Users/mathiasl/src/codex/codex-rs/models-manager/src/model_info.rs:17) |
| `ContextManager` | Normalize history, tokens, world-state baseline, review evidence | default | process-live; reconstructed | [history manager](/Users/mathiasl/src/codex/codex-rs/core/src/context_manager/history.rs:51) |
| World-state snapshots/diffs | Typed mutable developer/environment context with stable hashes | default | rollout-durable | [world state](/Users/mathiasl/src/codex/codex-rs/core/src/context/world_state/mod.rs:264) |
| `AGENTS.md` loader | Hierarchical project instructions with trust/budget | default in trusted projects | content recorded into conversation state | [loader](/Users/mathiasl/src/codex/codex-rs/core/src/agents_md.rs:55) |
| `ModelClientSession` | Responses stream, retry and turn-local transport reuse | default | transport state process-live | [client](/Users/mathiasl/src/codex/codex-rs/core/src/client.rs:235) |
| Turn loop | Steering-safe sample/tool/continue/compact state machine | default | events recorded to rollout | [turn](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:304) |
| Tool plan/router | Assemble exact schemas, exposure, handlers, collision checks | default | rebuilt per step | [spec plan](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:124) |
| Parallel dispatcher | Shared/exclusive tool locking and cancellation | default | process-live | [parallel runtime](/Users/mathiasl/src/codex/codex-rs/core/src/tools/parallel.rs:41) |
| Deferred `tool_search` | Discover large tool graph without sending all schemas | conditional | selection affects following steps | [search gate](/Users/mathiasl/src/codex/codex-rs/core/src/tools/spec_plan.rs:1384) |
| Skill selection | Scoped progressive instruction injection | conditional | invocation recorded | [selection](/Users/mathiasl/src/codex/codex-rs/skills/src/selection.rs:31) |
| Code Mode | V8 programmatic nested-tool routing | experimental model surface; stable host | cells/store process-live | [service](/Users/mathiasl/src/codex/codex-rs/core/src/tools/code_mode/mod.rs:69) |
| Output policy | Per-tool/model caps, omission metadata, history truncation | default | truncated output persisted | [tool output](/Users/mathiasl/src/codex/codex-rs/core/src/tools/context.rs:345) |
| Context-window accounting | Prefix/body/hard-limit token thresholds | default | token records durable | [accounting](/Users/mathiasl/src/codex/codex-rs/core/src/session/context_window.rs:25) |
| Local compaction | Summary plus selected user tail/checkpoint | fallback/default when no remote | crash-durable replacement | [local compact](/Users/mathiasl/src/codex/codex-rs/core/src/compact.rs:245) |
| Remote V2 compaction | Provider compaction plus bounded local retention | stable, default where supported | crash-durable replacement | [remote V2](/Users/mathiasl/src/codex/codex-rs/core/src/compact_remote_v2.rs:485) |
| Token-budget context | Explicit window/budget protocol | under development, off | protocol events durable | [selection branch](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:1219) |
| Rollout/thread store | Event/history persistence, replay, resume/fork | default | crash-durable after flush | [rollout](/Users/mathiasl/src/codex/codex-rs/core/src/rollout.rs:1) |
| Goal extension | Durable objective, budget, continuation, completion/block audit | stable, default on | state DB | [goal runtime](/Users/mathiasl/src/codex/codex-rs/ext/goal/src/runtime.rs:375) |
| `update_plan` | Current step plan/progress display | conditional/default policy | conversation/UI state | [plan schema](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/plan_spec.rs:7) |
| Multi-agent V1 | Spawn/input/wait/resume/close child threads | stable, default on | child rollout durable; live handles not | [tool spec](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs:65) |
| Multi-agent V2 | Hierarchical spawn/message/follow-up/wait/list/interrupt | stable, default off | same split | [spawn](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs:93) |
| Unified exec | One-shot/background/interactive process execution | default | process handles process-live | [manager](/Users/mathiasl/src/codex/codex-rs/core/src/unified_exec/mod.rs:73) |
| Permission profile | Filesystem/network authority model | default | config/thread metadata | [protocol](/Users/mathiasl/src/codex/codex-rs/protocol/src/models.rs:401) |
| Sandbox manager | OS-specific enforcement and command transform | managed profiles | execution-time only | [manager](/Users/mathiasl/src/codex/codex-rs/sandboxing/src/manager.rs:35) |
| Approval/Guardian | Escalation decisions and optional automated review | configuration-dependent | decisions/events recorded | [approvals](/Users/mathiasl/src/codex/codex-rs/core/src/tools/approvals.rs:54) |
| Extension registry/hooks | Typed plugin contribution and lifecycle interception | default framework; plugins optional | plugin-dependent | [registry](/Users/mathiasl/src/codex/codex-rs/ext/extension-api/src/registry.rs:25) |
| Long-term memory | Extract/consolidate/read project memory | stable, opt-in/off by default | files + state DB | [feature](/Users/mathiasl/src/codex/codex-rs/features/src/lib.rs:1067) |
| Telemetry/rollout trace | Usage, cost, cache, model, tools, latency | configuration-dependent | telemetry backend / rollout | [telemetry](/Users/mathiasl/src/codex/codex-rs/otel/src/events/session_telemetry.rs:93) |

## Design invariants

1. **The host owns authority.** Prompt text, skills, and Code Mode programs can request actions; only routed handlers under permission, approval, hook, and sandbox policy can perform them.
2. **A model can call only the current router.** Core, MCP, dynamic, hosted, and extension tools share one collision-checked namespace and an explicit exposure policy.
3. **Conversation truth is replayable.** Model-visible state transitions, tool calls/outputs, compaction, and metadata changes are recorded as rollout/thread events rather than depending on opaque provider memory.
4. **Transport reuse never becomes canonical state.** WebSocket and sticky turn state improve one turn's retries/continuations, while Codex history remains authoritative.
5. **Stable context is diffed; changed context is explicit.** Typed hashes and world-state patches preserve cacheable prefixes without hiding developer/environment changes.
6. **Project instructions do not grant capabilities.** Trust controls whether they are loaded; runtime authority is separate.
7. **Parallelism is declared by handlers.** The API may invite parallel calls, but the host serializes unsafe operations and propagates cancellation outputs.
8. **Compaction replaces model context, not audit history.** Active context becomes summary plus bounded retained items; persisted review evidence can remain available outside the next prompt.
9. **Durability is per primitive, not per session label.** Rollouts, state DB goals, and memory can survive restart; live processes, mailboxes, queues, and V8 cells cannot.
10. **Long-running work has a durable objective but semantic completion.** Goals persist scope, budget, and continuation policy; repository-specific deterministic verification must still be invoked.
11. **Experimental surfaces are not implied by stable hosts.** A stable Code Mode runtime or V2 implementation does not mean its model-visible feature is on by default.
12. **Model switching is an explicit context transition.** Instruction persistence, compaction compatibility, and context-window downshift are checked rather than silently reinterpreting history.
13. **Output is bounded before it re-enters context.** Tool and history policies cap data and expose omission metadata; truncation is not mistaken for artifact persistence.
14. **Multi-agent fan-out is bounded and identity-preserving.** Children have canonical paths/threads and inherited authority, with explicit depth/concurrency limits and environment-dependent isolation.
15. **Extensions are typed contributors.** Plugins can add tools, context, skills, hooks, and lifecycle behavior, but they enter through registries, trust gates, and collision checks rather than arbitrary prompt mutation.
