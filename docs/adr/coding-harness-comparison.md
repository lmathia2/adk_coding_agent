# Coding-harness comparison

> **Status:** source-grounded cross-harness synthesis
> **Compared revisions:** Pi `853a80d26c90a14c1886f0ebb8ffaae133ca2185`; OpenCode `8e0f1c253b6b7292b419505af849d06747c0e049`; Codex `986ff1cc7ced0081ec5014b700a376333d87f869`; ADK Long Horizon `cf7cadbc537cab9a6105a9b9adf5a2af2da43061`
> **Standalone designs:** [Pi](coding-harness-pi.md), [OpenCode](coding-harness-opencode.md), [Codex](coding-harness-codex.md), [ADK Long Horizon](coding-harness-adk-long-horizon.md)
> **ADK proposal:** [minimal SOTA extensions](coding-harness-minimal-sota-extensions.md)

## Executive result

The four harnesses are popular for different reasons, but their successful core is the same:

> A coding harness is a host-owned effect and context control plane around a model—not primarily a prompt, tool list, or swarm of agents.

The common primitives are a provider-normalized model loop, explicit tool authority, bounded outputs, a reconstructible conversation, cache-conscious prompt assembly, lossy compaction over a durable source, interruption at owned boundaries, and extensibility that cannot silently bypass the effect broker.

Their centers of gravity differ:

- **Pi** optimizes for a small understandable core: four broad tools, progressive disclosure, an append-only branchable JSONL session, and excellent terminal ergonomics.
- **OpenCode** optimizes for product orchestration: database-backed sessions, workspace snapshots/revert, agent modes, todo/plan discipline, child tasks, permissions, plugins, and an explicit CodeMode/PTC path.
- **Codex** optimizes for a typed, deeply defended execution platform: provider/session separation, typed world state, rollout reconstruction, unified process control, dynamic/deferred tools, OS sandbox/approvals, goals, multi-agent threads, and host-side Code Mode.
- **ADK Long Horizon** optimizes for multiple lifetimes on an ADK service substrate: cache-tiered prompts, resumable HITL, per-user environments/sandboxes, scoped secrets, process tools, child approval resurfacing, Memory Bank, review forks, and scheduled routines.

No implementation is best on every axis. This document compares the implementations without turning the comparison into a product plan. The separate [minimal SOTA extensions proposal](coding-harness-minimal-sota-extensions.md) applies these findings to the current ADK coding agent.

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
| Durable task goal | absent in core | plan/todo but no universal acceptance ledger | durable goal extension | absent in Horizon root |
| Progress/control state | compaction summary; examples only | durable model-maintained todos + plan mode | durable goal + UI plan | model-maintained `plan.md` + guardrails |
| Dropped/superseded work | no typed state | todo list replacement loses explicit reason unless preserved in prose | plan replacement; no typed dropped status | plan file rewrite; no typed dropped status |
| Deterministic completion gate | absent | absent as universal primitive | review/verification workflow dependent | absent as universal primitive |
| Foreground process | bash | shell | unified exec | bash |
| Background process | absent core | implemented but live | unified exec, live | local live or sandbox-runtime-backed |
| Blocking child | example only | task/agent | multi-agent thread with selectable history fork | resumable delegate |
| Background child | example only/no session | task lifecycle | event-driven child mailbox/wait | process-live registry |
| Clean-context delegation | example starts a fresh process | child session gets a scoped prompt | `fork_turns=none`; partial/full history also selectable | child instruction + task; no arbitrary history-fork selector |
| Human input disposition | steer vs follow-up queues | serialized follow-up/abort; V2 evolving | steer queue, mailbox trigger/no-trigger, explicit interrupt | queued steering/cancellation, less explicit message routing |
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

The right lesson is not a fixed tool count. It is the smallest compositional surface that preserves authority: one programmable code surface when it can broker every capability, or a few direct primitives while that path is still being validated. Everything uncommon should use progressive disclosure, and host/UI controls should remain outside the model-visible registry.

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

### Human steering is a routing problem, not a single queue

Pi distinguishes steering that enters the active run from follow-up work after the run, but both queues are process-live. Codex goes further: pending user input is drained only at safe sample boundaries, explicit interrupt cancels the active task, and inter-agent mailbox messages carry whether they should trigger a turn ([turn drain](/Users/mathiasl/src/codex/codex-rs/core/src/session/turn.rs:304), [input queue](/Users/mathiasl/src/codex/codex-rs/core/src/session/input_queue.rs:76), [interrupt](/Users/mathiasl/src/codex/codex-rs/core/src/session/mod.rs:4567)). Its V2 child API also makes the distinction concrete: `send_message` queues without triggering work, while `followup_task` queues and triggers an idle child ([send](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/send_message.rs:28), [follow-up](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/followup_task.rs:29)).

The important primitive is explicit delivery intent:

- **steer current work now:** request cooperative cancellation, then deliver at the next safe boundary;
- **steer at the next boundary:** do not cancel the current model/tool step;
- **follow up after the current outcome:** retain the message without polluting active context;
- **unrelated inbox/new task:** never inject it into the current task.

The host or human must choose this disposition. Inferring it from message text risks cancelling useful work or corrupting the active objective.

### Goal control and progress are not ordinary memory

A long-running coding harness needs a control projection that answers five different questions:

1. What is the immutable or explicitly revised objective and acceptance contract?
2. What approach is currently active?
3. What environment-observed work and evidence are complete?
4. What work was dropped or superseded, by whom, and why?
5. What remains before the host may publish completion?

The implementations cover this unevenly:

| Harness | Original goal | Work done/in progress/remaining | Drop/supersede semantics | Completion authority |
|---|---|---|---|---|
| Pi | transcript + compaction `Goal` field | compaction `Done/In Progress/Blocked/Next Steps`; optional examples | prose or branch history only | model/user/extension |
| OpenCode | user/session history | durable model-written todos and plan mode | list replacement or plan rewrite; no typed reason | model unless external checks |
| Codex | durable goal extension | goal status plus model/UI `update_plan` | replacement; plan statuses omit dropped/superseded | goal policy/model; no universal test gate |
| Horizon | user turn + project/plan files | model-maintained `plan.md`, counters, no-progress guardrails | plan-file rewrite | model unless task-specific check |
| This ADK repository | typed `TaskLedger.goal`, acceptance criteria, constraints/non-goals | compact ledger projection, observed files/actions/validations, deterministic replan routing | no explicit dropped/superseded plan-step status yet | deterministic verifier |

The crucial separation is **declared progress versus observed progress**. OpenCode todos, Codex plans, Horizon's plan file, and Pi summaries are useful working memory, but the model can mark them incorrectly. This ADK repository already resists that failure: repeated-action fingerprints drive no-progress routing independently of model prose ([progress reducer](/Users/mathiasl/src/adk_coding_agent/harness/state/progress.py:24)), workspace changes are observed before ledger reduction, and a `done` claim routes to verification ([workflow](/Users/mathiasl/src/adk_coding_agent/app/agent/workflow.py:1091)). Its remaining gap is lifecycle precision for replanning: `PlanStepStatus` has pending/active/complete/blocked but no dropped or superseded state ([task model](/Users/mathiasl/src/adk_coding_agent/harness/models/task.py:31)).

### Delegation and context isolation

Delegation has two independent benefits:

- **context isolation:** a child reads noisy evidence or explores an alternative without filling the parent window;
- **wall-clock parallelism:** independent work proceeds concurrently.

Codex makes context inheritance explicit: V2 `fork_turns` accepts `none`, `all`, or the last N completed turns, so a parent can choose a clean worker, a narrow handoff, or a full-history fork ([spawn parser](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs:280)). Children receive their own thread identity and mailbox; waiting is event-driven and wakes on child mail or new root steering ([wait](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs:39)). OpenCode creates durable child sessions and supports foreground/background execution. Pi's shipped example starts separate no-session processes, proving context isolation and parallel scheduling without making them a core durability promise. Horizon has blocking resumable delegates and process-live background children.

For complex problem solving, clean-context delegation is high value when a subproblem has a crisp boundary and a large evidence footprint: repository mapping, independent failure diagnosis, reviewing a proposed diff, or analyzing several modules. It is not inherently better reasoning. It adds briefing loss, duplicated reads, cost, coordination, merge conflicts, and child-claim verification. Asynchronous children add value only when there is useful parent work that truly does not depend on their answers.

A minimal SOTA harness should therefore provide bounded clean-context delegation, but invoke it selectively. The return path should be a small typed result plus trace/artifact references, not the child's transcript. Mutation should default to parent-owned or isolated workspaces. The parent remains responsible for integration and verification.

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
9. **Message disposition.** Active steering, next-boundary steering, after-current follow-up, and unrelated inbox work must not share an implicit delivery policy.
10. **Compactor side effects.** A summarizer that starts memory agents introduces cost, races, and shutdown behavior outside the nominal compaction call.
11. **Task-tree cancellation.** Cancelling a parent should define what happens to model streams, nested PTC calls, children, processes, verifiers, and routines.
12. **Economic budget composition.** Root, compactor, child, reviewer, retrieval, and scheduled calls must roll up to one budget if budgets are promised.
13. **Deletion and retention.** Transcript, ledger, memory, artifacts, snapshots, child state, and derived indexes need coherent erasure semantics.
14. **Deployment trust profile.** Local policy enforcement, container isolation, and managed sandbox containment must not share one “sandboxed” label.
15. **Control-plane availability.** A live child registry and a durable task store are different operational guarantees.
16. **Instruction trust after compaction.** Summaries and retrieved memories must be explicitly background data, not silently promoted instructions.
17. **Model-switch compatibility.** Window size is only one axis; tokenizer, tool grammar, system-prompt semantics, and compaction format can change.
18. **Verification ownership.** “Agent finished” is not “acceptance passed”; the host needs deterministic evidence or an explicit unverified outcome.
19. **Concurrency conflict policy.** Parallel reads are cheap; concurrent writes need workspace isolation, locks, or merge ownership.

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

## Separate ADK implementation proposal

The source-grounded extension plan for `/Users/mathiasl/src/adk_coding_agent`—including one-tool Code Mode, one trace-native store, human message disposition, explicit goal control, and bounded clean-context delegation—is in [Minimal SOTA coding-agent extensions on ADK](coding-harness-minimal-sota-extensions.md).
