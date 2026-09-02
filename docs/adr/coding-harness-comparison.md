# Coding-harness comparison and a simplified ADK design

> **Status:** source-grounded synthesis and design recommendation
> **Compared revisions:** Pi `853a80d26c90a14c1886f0ebb8ffaae133ca2185`; OpenCode `8e0f1c253b6b7292b419505af849d06747c0e049`; Codex `986ff1cc7ced0081ec5014b700a376333d87f869`; ADK Long Horizon `cf7cadbc537cab9a6105a9b9adf5a2af2da43061`
> **Standalone designs:** [Pi](coding-harness-pi.md), [OpenCode](coding-harness-opencode.md), [Codex](coding-harness-codex.md), [ADK Long Horizon](coding-harness-adk-long-horizon.md)

## Executive result

The four harnesses are popular for different reasons, but their successful core is the same:

> A coding harness is a host-owned effect and context control plane around a model—not primarily a prompt, tool list, or swarm of agents.

The common primitives are a provider-normalized model loop, explicit tool authority, bounded outputs, a reconstructible conversation, cache-conscious prompt assembly, lossy compaction over a durable source, interruption at owned boundaries, and extensibility that cannot silently bypass the effect broker.

Their centers of gravity differ:

- **Pi** optimizes for a small understandable core: four broad tools, progressive disclosure, an append-only branchable JSONL session, and excellent terminal ergonomics.
- **OpenCode** optimizes for product orchestration: database-backed sessions, workspace snapshots/revert, agent modes, todo/plan discipline, child tasks, permissions, plugins, and an explicit CodeMode/PTC path.
- **Codex** optimizes for a typed, deeply defended execution platform: provider/session separation, typed world state, rollout reconstruction, unified process control, dynamic/deferred tools, OS sandbox/approvals, goals, multi-agent threads, and host-side Code Mode.
- **ADK Long Horizon** optimizes for multiple lifetimes on an ADK service substrate: cache-tiered prompts, resumable HITL, per-user environments/sandboxes, scoped secrets, process tools, child approval resurfacing, Memory Bank, review forks, and scheduled routines.

No implementation is best on every axis. The best simplified ADK design is not their union. It is Pi's small default surface plus Codex/OpenCode's single-broker PTC and process semantics, Horizon's prompt tiers/HITL/environment/routine boundaries, and this repository's deterministic ledger/receipts/verification—while leaving multi-agent swarms, broad memory automation, and distributed scheduling out until a measured need appears.

## Source and support-level discipline

The comparison distinguishes:

- shipping default behavior;
- optional but implemented features;
- experimental/developmental paths;
- examples that demonstrate an extension but are not core;
- proposals or inferences.

This matters for two frequently overstated claims:

1. Pi's developmental `AgentHarness` contains durable-operation schemas but its central methods remain unimplemented; shipping Pi resumes finalized transcript state, not an interrupted instruction pointer ([Pi dossier](coding-harness-pi.md#crash-semantics)).
2. Codex Code Mode has a stable host runtime, but model-facing Code Mode remains feature-gated; its cells and `store` values are process-live, not disk-durable ([Codex dossier](coding-harness-codex.md#feature-status)).

## Four architectural mental models

| Harness | Smallest accurate mental model | Canonical durable source | Important live state |
|---|---|---|---|
| Pi | transcript tree + direct model/tool loop | append-only session JSONL | active provider stream and tool execution |
| OpenCode | database session/event projection + processor | SQLite records plus workspace snapshots | shell handles, active processor, child runtime |
| Codex | typed session/world state + rollout + tool runtime | rollout/thread store plus state DB | model transport, active processes, Code Mode cells, input queues |
| Horizon | resumable ADK App + services + environment + callbacks/plugins | configured ADK session/task/routine/memory services | environment cache, local processes, background children/review siblings |

The architectural mistake to avoid is treating all state as “conversation memory.” A long-running agent has at least six state classes:

1. conversation and model projection;
2. task intent/progress/acceptance;
3. effect receipts and approval decisions;
4. workspace/artifact bytes and identity;
5. live process/child handles;
6. cross-session learned memory.

Each has a different replay and deletion contract.

## Primitive comparison matrix

| Dimension | Pi | OpenCode | Codex | ADK Long Horizon |
|---|---|---|---|---|
| Default loop | direct agent loop | session processor | typed turn loop | ADK `Runner`/`App` |
| Default prompt style | compact generated coding prompt | provider/model prompt template + env/instructions | persisted/model-catalog base + typed world state | stable static + project context + volatile reminder |
| Project instructions | hierarchical context files | references/instruction loaders | scoped `AGENTS.md` chain | first-match one of five files |
| Default coding tools | read, bash, edit, write | broad conditional registry | broad planned registry | broad general/coding registry |
| Progressive tool disclosure | skills/context; extension-set active tools | MCP/skills + CodeMode discovery | deferred tools/skills/MCP + Code Mode catalog | skills; compact schemas, no arbitrary deferred discovery |
| Direct tool concurrency | read-only parallel, mutating ordered | processor/provider dependent | planner/runtime policy | ADK parallel calls, callback-governed |
| Host-side PTC | no core PTC | implemented CodeMode | feature-gated Code Mode | absent |
| Stateful code executor | shell only in core | CodeMode runtime | V8 Code Mode runtime | optional separate Python sandbox |
| Unified nested authority | n/a | yes for CodeMode broker | yes for Code Mode delegate | n/a; executor does not call Horizon tools |
| Output bounds | head/tail + temp artifact | tool-specific truncation/artifacts | token/output caps + structured blocks | overflow paths, rolling buffers, prune markers, artifacts |
| Cheap history pruning | limited | explicit tool-output pruning | history normalization/truncation | explicit stale tool-output pruning |
| Compaction | local incremental structured summary + exact tail | summary + retained tail | local/remote strategies + retained user tail | ADK trigger + structured merge + retained events |
| Durable pre-compaction evidence | original JSONL remains | DB/event state remains | rollout/review history remains | session backend; optional best-effort memory flush |
| Branch/fork/rollback | branchable session tree | snapshots/revert/session operations | durable fork/rollback/worktrees | no equivalent general conversation branch primitive in sample |
| Durable task goal | absent in core | plan/todo but no universal acceptance ledger | goal extension | absent in Horizon root |
| Deterministic completion gate | absent | absent as universal primitive | review/verification workflow dependent | absent as universal primitive |
| Foreground process | bash | shell | unified exec | bash |
| Background process | absent core | implemented but live | unified exec, live | local live or sandbox-runtime-backed |
| Blocking child | example only | task/agent | multi-agent thread | resumable delegate |
| Background child | example only/no session | task lifecycle | multi-agent/app orchestration | process-live registry |
| Child HITL | none core | permission/task dependent | approvals/tool runtime | one approval/ask resurfaced into parent |
| Scheduled unattended work | absent | external/integration dependent | app automation outside core | explicit routine + scheduler + fresh sandbox |
| Cross-session memory | context files/extensions | storage/plugins | memory extension/state | Memory Bank + preload/review/profile |
| Secrets | environment/provider convention | permission/config ecosystem | host/app credential stores | per-user store + name-only prompt + scoped injection |
| OS sandbox | none core | environment-dependent | explicit OS sandbox profiles | optional Vertex sandbox; local not sandboxed |
| Permission model | extension/host choice | first-class rules | approvals + sandbox + Guardian/hooks | exfil → policy → permission chain |
| Resumable HITL | no core operation resume | session-dependent | rollout/tool-call dependent | ADK resumability + FunctionResponse |
| Cost accounting | session usage/cost stats | usage records | tokens/rate limits/telemetry | ADK usage exists; no unified harness budget |
| Extension philosophy | extensions compose missing policy | plugins/MCP/agents | typed contributors/extensions/MCP | ADK callbacks/plugins/tools/services |

## What is genuinely common

### 1. The host owns effects

All four put a host runtime between model intent and side effects. The model never becomes the filesystem, process table, secret store, or approval authority. Even programmatic tool calling in OpenCode/Codex routes nested calls through the same broker. Codex's nested delegate explicitly returns to `ToolCallRuntime` at [`delegate.rs`](/Users/mathiasl/src/codex/codex-rs/core/src/tools/code_mode/delegate.rs:101); Horizon fixes its ordered guard chain in [`agent.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/agent.py:273).

This gives one non-negotiable invariant for an ADK design:

> Every effect—direct tool, PTC nested call, verifier command, child call, or scheduled run—must traverse one policy/receipt/sandbox broker.

### 2. The active prompt is a projection

Pi projects a branch of append-only JSONL; OpenCode projects database/session state; Codex projects rollout/world-state records; Horizon projects ADK events plus service-backed context. Compaction changes what the next model sees, not what effects occurred.

The common pattern is:

~~~text
durable facts/events/artifacts
          │
          ├─ deterministic reducer
          ├─ bounded retrieval
          └─ lossy model projection (summary + recent exact tail)
~~~

The summary is not the ledger. Treating it as one loses provenance, approvals, failure evidence, and replay correctness.

### 3. Prompt-cache stability is architectural

The harness decides which bytes change each turn. Horizon makes this explicit with three tiers; Codex separates persisted base instructions from typed world state and tool schemas; Pi keeps a compact prompt and append-oriented session; OpenCode uses provider-specific prompt families plus dynamic environment/instructions.

Cache quality depends on:

- stable ordering and serialization;
- keeping dates, counters, errors, and ephemeral auth out of the static prefix;
- not injecting every skill/tool body eagerly;
- treating compaction/model switch as intentional prefix breaks;
- preventing tool declarations from changing accidentally.

### 4. Output must be recoverably bounded

Every mature harness bounds shell/file/tool output. The better implementations preserve a handle, path, structured block, or omission metadata so the model can retrieve details later. Silent truncation is not enough.

### 5. Interruption is cooperative at owned boundaries

Steering and cancellation happen between model/tool steps or through cancellable runtime handles. Codex drains queued input at turn boundaries; Pi has follow-up/steering queues; OpenCode processors observe abort/session state; Horizon uses ADK resumability, child/task/process APIs, and routine cancellation.

No implementation can safely “edit the model's thoughts” in the middle of an arbitrary external side effect. A good harness records the steering event durably, then applies it at the next explicit boundary.

### 6. Long-running work requires multiple lifetimes

All four reveal that “long running” is not one feature. It decomposes into:

- long conversation: transcript + compaction;
- long command: process manager;
- long task: goal/progress/checkpoints/verification;
- parallel work: isolated child contexts/workspaces;
- crash recovery: durable events + receipts + workspace fingerprint;
- unattended recurrence: scheduler + fresh identity/authority/sandbox.

Pi intentionally covers mostly the first. OpenCode and Codex cover more of the middle. Horizon is the only one here with an explicit recurring-work primitive, but its background children are less durable than the label suggests.

## Important differences

### Core size versus product surface

Pi's default four tools minimize schema cost and policy ambiguity. OpenCode, Codex, and Horizon expose broader product capabilities. The broad approach improves discoverability but makes every turn pay in prompt tokens, tool-choice entropy, and guard complexity.

The right lesson is not “four forever.” It is:

- four high-frequency coding tools direct;
- one progressive-disclosure path for everything else;
- optional PTC as a different calling syntax over the same broker;
- host/UI commands outside the model-visible tool list when possible.

### Prompt philosophy

Pi generates one compact coding-centric prompt from active tools and resources ([`system-prompt.ts`](/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts:27)). OpenCode selects model/provider prompt text and appends dynamic environment and instructions. Codex persists or resolves model base instructions, then builds typed world state each turn ([`world_state.rs`](/Users/mathiasl/src/codex/codex-rs/core/src/session/world_state.rs:33)). Horizon freezes stable identity/mechanics at App build, keeps one project file cacheable, and appends volatile reminders outside the prefix ([`system_prompt.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/conversation/system_prompt.py:15)).

For ADK, Horizon's tiering is the strongest cache contract; Pi's brevity is the strongest default content contract; Codex's typed world-state sections are the strongest change/replay contract.

### Compaction loss functions

- Pi prioritizes an incremental continuation summary plus an exact structural tail; original JSONL remains branchable. Its schema and deterministic file appendix are at [`compaction.ts`](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:732).
- OpenCode prunes old tool bodies first, then stores a summary with retained messages and durable session state.
- Codex supports local and provider remote compaction, preserves selected recent user messages, and keeps review-oriented evidence separate from active history ([`compact.rs`](/Users/mathiasl/src/codex/codex-rs/core/src/compact.rs:645)).
- Horizon adds interval and percentage-of-window triggers, structured merge, deterministic touched-file extraction, and a best-effort pre-compaction memory fork ([`summarizer.py`](/Users/mathiasl/src/adk-samples/core/python/long-horizon-harness/horizon/context/summarizer.py:217)).

The optimal synthesis is: deterministic cheap pruning → structured summary over authoritative ledger facts → exact recent tail → durable compaction event containing source watermark and summary hash. Memory extraction should be separate and should not block active compaction unless losing a fact would violate an explicit contract.

### PTC and tool offloading

There are four distinct forms of “offloading”:

| Form | Best use | Main risk |
|---|---|---|
| Shell script | OS-native deterministic composition | broad shell authority and opaque sub-effects |
| Host-side PTC | many structured tool calls, filtering, aggregation | second authority path if broker is bypassed |
| Child agent | semantic research/work with its own context | drift, cost, duplicate effects, vague ownership |
| Scheduled routine | recurring unattended goal | stale intent and excessive long-lived authority |

OpenCode and Codex implement the strongest PTC pattern: a short program sees an eligible catalog but nested calls re-enter normal policy. Horizon's optional Python executor does **not** do this; it is a separate stateful filesystem. Pi core has neither.

PTC should remain optional until an ablation proves that it reduces tokens/latency or improves quality. When enabled, it must produce nested receipts, cap calls/output/time, disallow direct file/process/network APIs, and persist enough program/cell history to explain unknown effects after a crash.

### Subagents

Pi's child support is an optional no-session subprocess example. OpenCode has task-based child agents. Codex has explicit multi-agent thread orchestration and app worktrees. Horizon has the most explicit child-HITL contract: one blocking child can pause, bubble one approval/answer to the parent, and resume the same child session.

None makes child output automatically trustworthy. A parent must verify a child's diff/evidence before reporting completion. Child lifecycle and workspace ownership are more important than a large agent taxonomy.

### Memory

Pi largely uses session/context files; OpenCode leans on storage/plugins; Codex separates memory into extensions/state; Horizon integrates Memory Bank preload, capture, profile generation, and review forks.

The best default for a coding harness is conservative:

- code/repository facts should be re-derived from the workspace;
- in-flight progress belongs in task state, not cross-session memory;
- only durable user preferences, non-derivable constraints, and explicit decisions belong in learned memory;
- every injected memory should carry provenance, scope, and freshness.

Horizon supplies useful patterns, but its automatic review stack is not required for a minimal coding harness.

## Dimensions a long-running-agent analysis must cover

The earlier three-harness report covered prompts, tools, PTC, compaction, sessions, long-running work, safety, and evaluation. Inspecting Horizon exposes additional dimensions that deserve first-class treatment:

1. **State lifetime matrix.** “Persistent” must be decomposed into process, turn, session, task, workspace, user, and scheduled-job lifetime.
2. **Identity propagation.** Parent, child, routine, sandbox, memory, artifact, and secret operations must agree on user/task identity.
3. **Resume granularity.** Transcript resume, model-turn resume, tool-confirmation resume, child resume, process reattachment, and scheduled retry are different guarantees.
4. **Unknown-effect recovery.** After a crash between side effect and receipt commit, the harness must mark unknown rather than retry blindly.
5. **Environment migration.** Workspace revision, runtime image, dependencies, `$HOME`, and `/workspace` have separate upgrade/restore contracts.
6. **Secret lifecycle.** Storage, name-only prompt exposure, just-in-time injection, child/routine scoping, rotation, and redaction must be specified together.
7. **Headless policy.** Unattended work cannot reuse an interactive “ask” outcome; it needs explicit allow/deny semantics and smaller authority.
8. **Prompt-cache placement.** Stable, contextual, and volatile state need an explicit ordering and fingerprint contract.
9. **Compactor side effects.** A summarizer that starts memory agents introduces cost, races, and shutdown behavior outside the nominal compaction call.
10. **Task-tree cancellation.** Cancelling a parent should define what happens to model streams, nested PTC calls, children, processes, verifiers, and routines.
11. **Economic budget composition.** Root, compactor, child, reviewer, retrieval, and scheduled calls must roll up to one budget if budgets are promised.
12. **Deletion and retention.** Transcript, ledger, memory, artifacts, snapshots, child state, and derived indexes need coherent erasure semantics.
13. **Deployment trust profile.** Local policy enforcement, container isolation, and managed sandbox containment must not share one “sandboxed” label.
14. **Control-plane availability.** A live child registry and a durable task store are different operational guarantees.
15. **Instruction trust after compaction.** Summaries and retrieved memories must be explicitly background data, not silently promoted instructions.
16. **Model-switch compatibility.** Window size is only one axis; tokenizer, tool grammar, system-prompt semantics, and compaction format can change.
17. **Verification ownership.** “Agent finished” is not “acceptance passed”; the host needs deterministic evidence or an explicit unverified outcome.
18. **Concurrency conflict policy.** Parallel reads are cheap; concurrent writes need workspace isolation, locks, or merge ownership.

These dimensions are now included in each standalone dossier where the implementation has a real answer; absences are listed rather than inferred away.

## Reconciliation with this repository's earlier ADK design

The original design correctly identified a small prompt, four tools, progressive disclosure, bounded outputs, append-oriented history, structured compaction, sessions, deterministic state, receipts, and verification as the competitive core. Those remain sound in [`pi-inspired-adk-coding-harness.md`](/Users/mathiasl/src/adk_coding_agent/docs/design/pi-inspired-adk-coding-harness.md:1).

The current implementation has since closed several gaps the earlier report identified. Its supported boundary now includes:

- one four-tool ADK worker with stable prompt hashes;
- durable task events, checkpoints, steering, metrics, run registry, and tool receipts;
- exact task-scoped approvals and deterministic completion verification;
- bounded public streaming/replay and server-owned follow-up queues;
- disabled-by-default notebook PTC whose nested calls traverse the existing broker;
- optional canonical JSONL/DuckDB memory with deterministic views and provenance;
- explicit task erasure across operational/canonical stores and artifacts.

These claims and remaining limitations are enumerated in [`IMPLEMENTATION_STATUS.md`](/Users/mathiasl/src/adk_coding_agent/docs/IMPLEMENTATION_STATUS.md:6). The actual effect receipt key and at-least-once recovery boundary are implemented in [`receipts.py`](/Users/mathiasl/src/adk_coding_agent/harness/state/receipts.py:14); deterministic compaction is derived from the typed ledger in [`compaction.py`](/Users/mathiasl/src/adk_coding_agent/harness/context/compaction.py:332); durable steering uses lease/ack semantics in [`steering.py`](/Users/mathiasl/src/adk_coding_agent/harness/state/steering.py:44).

### What the earlier design still under-specified

Horizon adds concrete pressure to refine these contracts:

- **Environment lifecycle:** the current repository has local/Docker command sandboxes, but not Horizon's per-user reattach/version/snapshot/migration contract.
- **Managed process reattachment:** the current design should state whether a process handle is live-only, rediscoverable, or durable; “background” alone is insufficient.
- **Nested HITL:** receipts/approvals are strong at the root, but a future child-agent feature would need Horizon's resume-same-child-or-halt invariant.
- **Headless jobs:** there is no need to add routines now, but any future automation must define fresh workspace/sandbox, secret allowlist, and fail-closed non-shell permission behavior.
- **Secret scoping:** existing redaction/known-secret handling should be specified as storage → name exposure → injection → child/job scope, not only output sanitization.
- **Prompt tiers:** volatile task/session state must remain out of `static_instruction`; the existing repository instruction already requires this, but the architecture should name the stable/context/volatile placement contract.
- **Global budget:** the repository accepts/records cost concepts in places but still lacks one enforced roll-up for root, verifier, PTC, retrieval, and any future child.
- **Task tree:** current root workflow and PTC cells have lifecycle events, but there is no general parent-child-process cancellation tree—and none should be added until a real child feature exists.

### What should remain deliberately absent

The earlier report was right not to require every mature-system feature. For this substrate, keep these out of the default:

- automatic multi-agent spawning;
- broad Memory Bank-style self-review on every task;
- scheduled routines before there is a concrete unattended use case;
- a second semantic index as authority;
- remote/distributed execution without deployment demand;
- automatic skill promotion into trusted instructions;
- PTC enabled by default before the pending four-tool-versus-PTC ablation in [`TODO.md`](/Users/mathiasl/src/adk_coding_agent/docs/TODO.md:3).

## Simplified best-of-breed on ADK

### Design thesis

Use ADK for the model/event/callback/service substrate. Keep a small harness-owned deterministic control plane for task state, effects, and verification. Do not recreate an agent framework beside ADK.

~~~text
client
  │  user turn / steer / approve / cancel / replay
  ▼
ADK Runner + resumable App
  │
  ├─ one coding Agent
  │    ├─ stable static instruction
  │    ├─ bounded project/task context callback
  │    └─ volatile reminder tail
  │
  ├─ four direct tools: read, bash, edit, write
  │                    │
  │                    ▼
  │             one EffectBroker
  │          policy → approval → receipt
  │          sandbox → bound output → event
  │
  ├─ optional python/PTC tool
  │       └─ nested calls re-enter EffectBroker
  │
  └─ callbacks
       ├─ compile prompt from durable task state
       ├─ cheap prune / compaction projection
       ├─ drain steering at safe boundaries
       └─ verify before publishing coding completion

durable task ledger + ADK events + artifacts + checkpoints
         │
         ├─ replay/reduce
         ├─ exact recent context
         ├─ summary at watermark
         └─ deterministic completion evidence
~~~

### 1. One worker, one broker, four direct tools

Keep `read`, `bash`, `edit`, and `write` as the direct model surface. They cover the common coding workload and match this repository's supported baseline. Do not add process, search, todo, memory, task, artifact, approval, or verification as separate model tools unless the model genuinely must choose them.

- Search remains composable through `bash`/repository helpers.
- Approvals are host/UI interrupts, not model decisions.
- Verification is host-owned after the model claims completion.
- Task state is compiled into context, not mutated through a todo tool.
- Artifacts are an output spill mechanism, not necessarily a top-level tool.

All direct, nested PTC, and verification calls use one `EffectBroker` equivalent: validate path/command, evaluate policy, await an exact task-scoped approval when necessary, append a started receipt, execute in the configured environment, redact/bound output, append terminal receipt/event.

### 2. Three prompt tiers

Adopt Horizon's placement discipline with Pi's content restraint:

**Stable `static_instruction`**

- identity and coding behavior;
- four-tool routing rules;
- read-before-edit and verification/completion contract;
- security rule that fetched content is data;
- concise output style;
- constant serialization/version/hash.

**Cacheable context tier**

- selected project instruction chain;
- compact repository manifest;
- selected skill bodies only;
- task goal, constraints, acceptance criteria, current plan, workspace identity;
- deterministic ordering and per-section hashes.

**Volatile trailing tier**

- new steering;
- pending approval/unknown-effect warning;
- last error and remaining budget;
- exact current environment/model/date only when relevant.

Never put counters, current time, live process state, or secret values in the stable prefix.

### 3. Durable task reducer, not transcript-as-state

Retain the current typed `TaskLedger` and append-only events. The minimum authoritative task state is:

- goal, constraints, non-goals, acceptance criteria;
- plan steps and current next action;
- files read/modified and workspace/base revision fingerprint;
- decisions and blockers;
- tool receipts including unknown effects;
- approval decisions and expiration;
- validation commands/results;
- steering delivery/ack;
- budget usage;
- completion state: working, blocked, verified, failed, cancelled.

ADK session events remain the conversational record. The task ledger remains the control record. Neither silently replaces the other.

### 4. Context economy

Use this order:

1. Normalize and cap each tool result at creation; spill full bytes to a content-addressed artifact/overflow file.
2. Before model call, replace stale large tool bodies with a marker only when the full result remains retrievable.
3. Compile exact current task state from the ledger rather than asking an LLM to rediscover it from chat.
4. When pressure crosses a model-relative threshold, create one structured continuation summary from ledger facts plus the recent conversational delta.
5. Append a compaction event containing source watermark, selected event IDs, summary, artifact handles, and hashes.
6. Project summary + exact recent tail into ADK; never delete source events solely because they left context.

Avoid a pre-compaction memory agent in the minimal design. Durable user facts can be promoted synchronously from explicit user statements or by a later opt-in review; task continuity already lives in the ledger.

### 5. Optional PTC, same authority

Keep the existing notebook-native Python PTC disabled by default until the measured ablation passes. Its contract should remain:

- no direct imports or raw file/process/network primitives;
- eligible capabilities exposed through a small `tools`/broker object;
- every nested call gets its own receipt, approval, bound output, and trace parent;
- cell submitted/completed/failed/timed-out recorded durably;
- safe completed cells may restore interpreter state after restart;
- unsafe or unknown effects are never auto-replayed;
- explicit time, call-count, output, and token budgets;
- PTC is a calling syntax, not a new privilege tier.

This keeps the useful part of OpenCode/Codex Code Mode without importing a second runtime architecture.

### 6. Process lifetime

Add no new process system unless the current managed shell path needs it. If background processes are required, specify the smallest correct contract:

~~~text
spawn -> running -> exited | failed | killed | unknown
~~~

Persist command identity, environment/workspace fingerprint, backend process ID, timestamps, bounded log artifact, and last observed state. On restart:

- reattach only when the backend can prove identity;
- otherwise mark `unknown`, do not rerun;
- success claims require an explicit health/readiness check;
- cancellation is idempotent.

Do not call a process durable merely because its metadata is in SQLite.

### 7. Child agents only when isolation pays

Do not add a general swarm. If a concrete workload needs child contexts, add one `delegate` capability with:

- a self-contained goal/context/success brief;
- default read-only profile;
- explicit tool allowlist, workspace ownership, time/call/cost budget;
- no recursive delegation initially;
- one parent-visible status/result envelope;
- parent verification before accepting mutations;
- resume the same child across HITL or halt; never silently rerun;
- background mode only after durable task handles exist.

Horizon's one-approval resurfacing is a good bounded first version. Codex's richer thread/worktree tree is justified only when concurrent repository mutation is a product requirement.

### 8. Headless work as a separate profile

Do not implement routines yet. Define the future contract now so interactive authority cannot leak into unattended work:

- fresh task/run identity;
- fresh isolated workspace or explicit immutable starting revision;
- declared secret allowlist;
- no interactive `ask`; undecidable operations fail closed;
- hard time/cost/tool budgets;
- idempotency key and retry policy;
- deterministic verification and explicit delivery destination;
- durable scheduler row separate from chat/session state.

Add it only when there is a real scheduled coding workflow.

### 9. Verification is the completion boundary

The model may propose `done`; the host decides whether a coding task can be published as complete.

1. Compile required checks from acceptance criteria and repository configuration.
2. Run them through the same sandbox/policy/approval path.
3. Attach exact command, exit status, bounded logs/artifacts, and workspace fingerprint.
4. Mark verified only if deterministic predicates pass.
5. If checks cannot run, return blocked/unverified—not success.

This repository already implements the essential gate; preserve it rather than adding a judge agent.

### 10. Budget and cancellation

One task budget should roll up:

- model input/output/cache tokens and cost;
- compaction calls;
- PTC cells and nested calls;
- verification commands;
- future child calls.

Budget exhaustion should produce a structured handoff from deterministic ledger state, not another LLM call if no budget remains.

Cancellation should propagate through one task tree: stop new model sampling, cancel active broker call where safe, terminate PTC cell, signal managed process/child, preserve terminal receipts, and leave scheduled routines separate unless explicitly targeted.

### 11. Minimal memory

Use canonical ledger views for task recall. Add cross-session learned memory only for durable non-code user facts and explicit decisions. Every injected item needs:

- source event ID;
- user/project/task scope;
- observed and recorded timestamps;
- freshness/expiry or version;
- redaction classification.

Derived search indexes remain rebuildable projections. The current JSONL/DuckDB authority and optional Lance projection already follow this rule; no Memory Bank fork is required for the minimal local harness.

## ADK mapping

| Required primitive | ADK substrate | Harness-owned piece |
|---|---|---|
| model loop/streaming | `Agent`, `Runner`, model adapters | provider registry and public-output policy |
| resumable conversation/HITL | `App(ResumabilityConfig)`, session service, confirmations | receipt/idempotency and UI decision protocol |
| stable prompt | `static_instruction` | versioned builder/hash |
| dynamic context | before-model callback | deterministic compiler/reducer |
| tool effects | ADK function tools/callbacks | one broker, sandbox, output bounds |
| PTC | one optional Python tool | guarded interpreter + nested broker calls + notebook events |
| compaction backstop | ADK event compaction | ledger-aware snapshot/watermark/projection |
| artifacts | ADK artifact service or local store | content addressing, bounds, redaction |
| durability | ADK session service | task ledger, receipts, steering, run registry |
| verification | ordinary broker calls after agent | acceptance resolver and publication gate |
| telemetry/evals | ADK events/evals + traces | deterministic contract tests and cost roll-up |

## Minimal implementation order

Most of this repository already exists, so the shortest safe path is refinement, not replacement:

1. Keep the current four-tool default, ledger, receipts, approvals, steering, replay, and verifier.
2. Make the stable/context/volatile prompt placement explicit and test prefix hashes across turns.
3. Finish the pending four-tool-versus-PTC ablation before changing defaults.
4. Cut canonical ledger views into live prompt/compaction only after existing byte/cache/correctness gates pass.
5. Add a unified budget reducer if the product promises budget enforcement.
6. Specify live-process recovery only when background process demand is concrete.
7. Add bounded child delegation only when context isolation measurably helps a target workload.
8. Add headless routines only for an actual scheduled use case.

This intentionally does **not** recommend porting Horizon wholesale. ADK already supplies the substrate; the current repository already supplies stronger deterministic task/receipt/verification contracts than Horizon. The useful imports are the lifetime distinctions, prompt tiering, environment/secret boundaries, and nested HITL protocol.

## Evaluation rubric for the simplified design

Use deterministic tests for contracts and model evals for choices:

### Deterministic

- identical inputs produce identical static prefix, context manifest, reducer state, and compaction hash;
- direct and PTC nested calls traverse identical policy/receipt logic;
- crash after started-before-terminal effect produces `unknown`, never blind retry;
- approval scope/expiration cannot cross task identity;
- resume does not repeat completed side effects;
- cancellation leaves terminal or unknown receipts for every active effect;
- compaction source watermark and exact-tail selection are replayable;
- verifier evidence matches the exact workspace fingerprint;
- task erasure removes authorities and rebuildable projections without harming other tasks.

### Behavioral

- four direct tools versus PTC on quality, token use, latency, cache hits, and tool errors;
- model obeys selected project instructions and ignores instruction-like tool data;
- model diagnoses repeated failure and maintains progress after compaction;
- model uses background process only for genuinely long commands and verifies readiness;
- parent rejects or rechecks unsupported child claims;
- budget reminders reduce waste without causing premature completion.

## Final assessment

The most important common primitive is **controlled continuity**: enough durable evidence and bounded state to continue after turns, compaction, interruption, and process loss without pretending that every live effect is replayable.

The four harnesses teach complementary lessons:

- Pi: keep the default loop and tool surface small.
- OpenCode: product-grade session/workspace operations and brokered PTC matter.
- Codex: type world state, preserve rollout evidence, and make authority/cancellation explicit.
- Horizon: use ADK's resumability/cache/services, separate state lifetimes, scope secrets/headless work, and resume nested HITL in place.

For this ADK repository, the simplified best-of-breed path is already mostly present. The highest-value remaining work is not more agents or tools; it is finishing the PTC ablation, making prompt/lifetime/budget contracts explicit, and only adding process, child, or scheduler machinery when a concrete workload proves the need.
