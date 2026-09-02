# Minimal SOTA coding-agent extensions on ADK

> **Status:** source-grounded proposal
> **Current repository:** `/Users/mathiasl/src/adk_coding_agent`
> **Compared designs:** [Pi](coding-harness-pi.md), [OpenCode](coding-harness-opencode.md), [Codex](coding-harness-codex.md), [ADK Long Horizon](coding-harness-adk-long-horizon.md)
> **Comparison:** [cross-harness synthesis](coding-harness-comparison.md)

## Decision and core thesis

The target architecture is:

> **One programmable code-mode tool plus one trace-native database is the sufficient model-facing substrate for a state-of-the-art coding agent.**

The code tool gives the model a scratchpad in which it can inspect, compose, filter, loop, call APIs, invoke shell commands, edit files, coordinate children, and retain warm session state. The trace database records inputs, decisions, effects, receipts, workspace observations, human messages, child activity, verification, and derived computations. Versioned programs over that trace produce the bounded projections needed by the model, UI, verifier, resume path, and operator.

This does **not** mean the host has only two functions. The host still owns policy, approvals, sandboxing, cancellation, verification, durability, and scheduling. It means those mechanisms do not become competing model-visible tools or parallel memory systems.

~~~text
model
  │
  │ one model-visible `python` / code-mode tool
  ▼
persistent scratchpad + `agent.*` capability object
  │
  ├─ fs / shell / process / APIs
  ├─ trace queries / stored computations / durable state
  └─ bounded delegation / wait / message / interrupt
        │
        ▼
one host capability broker
policy → approval → execute → bound/redact → receipt
        │
        ▼
one canonical trace store
raw events + program catalog + materialized view receipts
        │
        ├─ next-model context
        ├─ task/goal/progress view
        ├─ human and child inboxes
        ├─ UI/status/replay/audit
        └─ deterministic verification evidence
~~~

The default should eventually expose only the code tool. The existing four-tool path remains a compatibility/control arm until the required quality, latency, cost, and cache ablation justifies changing the default.

## What already exists

This is a consolidation proposal, not a rewrite. Most difficult primitives are already implemented.

| Target primitive | Current implementation | Remaining gap |
|---|---|---|
| One code-mode tool | Enabling notebook PTC makes `python` the only worker tool in [`factory.py`](/Users/mathiasl/src/adk_coding_agent/app/agent/factory.py:209) | Disabled by default; ablation pending |
| Warm scratchpad | Persistent CPython namespace in [`worker.py`](/Users/mathiasl/src/adk_coding_agent/harness/repl/worker.py:286) | Warm state is process-live; durable named state is absent |
| Brokered filesystem/shell/API calls | `agent.fs`, `agent.shell`, and registered MCP routing in [`worker.py`](/Users/mathiasl/src/adk_coding_agent/harness/repl/worker.py:196) | No capability discovery, trace/state, process, or delegation namespace |
| Effect authority | Nested calls return through the existing tools, approvals, receipts, redaction, and bounds in [`builders.py`](/Users/mathiasl/src/adk_coding_agent/app/agent/builders.py:235) | Preserve this invariant as capabilities grow |
| Notebook trace | Cells and nested capability lifecycle events plus deterministic `.ipynb` materialization in [`builders.py`](/Users/mathiasl/src/adk_coding_agent/app/agent/builders.py:438) | Notebook is still a projection beside other operational stores |
| Canonical events | Immutable `LedgerEvent` with sequence, provenance, time, effect, correlation, payload hash, and idempotency in [`models.py`](/Users/mathiasl/src/adk_coding_agent/harness/ledger/models.py:22) | Optional/shadow operation and multiple live stores remain |
| Programmable views | Versioned `ViewRequest`/`ViewResult` with watermarks, hashes, evidence IDs, and byte bounds in [`models.py`](/Users/mathiasl/src/adk_coding_agent/harness/memory/models.py:12) | Not used by the live prompt or exposed in code mode |
| Stored relational programs | Candidate → shadow → active → retired SQL programs in [`catalog.py`](/Users/mathiasl/src/adk_coding_agent/harness/memory/catalog.py:17) | Separate from the seeded view runtime; no single computation catalog |
| Prompt manifests | Receipt-bearing P0–P3 compiled projections in [`prompt.py`](/Users/mathiasl/src/adk_coding_agent/harness/memory/prompt.py:46) | Test-only rather than the live context source |
| Goal/progress control | Typed `TaskLedger`, observed action fingerprints, deterministic replan, and verifier in [`task.py`](/Users/mathiasl/src/adk_coding_agent/harness/models/task.py:85), [`progress.py`](/Users/mathiasl/src/adk_coding_agent/harness/state/progress.py:24), and [`workflow.py`](/Users/mathiasl/src/adk_coding_agent/app/agent/workflow.py:1091) | Plan lifecycle lacks dropped/superseded states; control projection is not trace-program-native |
| Human steering | Durable SQLite lease/ack queue and safe-point injection in [`steering.py`](/Users/mathiasl/src/adk_coding_agent/harness/state/steering.py:44) | Every message targets current work; no delivery intent or unrelated inbox |
| Completion control | Deterministic validation and completion fence in [`workflow.py`](/Users/mathiasl/src/adk_coding_agent/app/agent/workflow.py:762) | Keep; expose its evidence through trace views |

The shortest path is to connect these pieces and retire duplicate authorities after equality gates pass.

## Design tenets

1. **One model-visible tool.** New capability appears under `agent.*` inside code mode, not as another ADK tool schema.
2. **One effect broker.** Direct, nested, child, verifier, and process effects share policy, approval, sandbox, receipt, cancellation, and output rules.
3. **One canonical trace.** The trace database is the harness authority. Summaries, notebooks, status pages, prompt packets, and indexes are projections.
4. **Programs, not memory products.** Context is a versioned computation over trace facts. Standard SQL/Python reducers replace bespoke memory-agent pipelines.
5. **Declared state is not observed state.** The model may propose progress and plans; the host separately records files, commands, receipts, tests, and workspace identity.
6. **Simple tasks pay almost nothing for complex-task machinery.** Capability schemas are discovered from code, delegation is demand-driven, and the active context remains small.
7. **Model intelligence owns tactics.** The harness supplies authority, continuity, evidence, and bounded composition—not a hand-authored workflow for every task shape.
8. **No silent privilege expansion.** Code mode changes calling syntax, never the authority available to the worker.

## Target code-mode contract

Keep Python first because the repository already has the worker, notebook reducer, safety checks, and tests. A later JavaScript or shell-native implementation should be an evaluated runtime replacement, not a second simultaneous architecture.

The model sees one tool and a short stable instruction:

~~~text
Use python as a persistent scratchpad. Access the outside world only through
the `agent` object. Compose calls in code, keep bulky intermediate data in the
scratchpad or trace store, and return only the evidence needed for the next step.
All effects are brokered and recorded. Warm Python globals are not durable proof.
~~~

The capability object should converge on this small shape:

~~~python
agent.capabilities.list(query=None)
agent.capabilities.describe(name)

agent.fs.read(path, ...)
agent.fs.write(path, content, ...)
agent.fs.edit(path, old, new, ...)

agent.shell.run(command, ...)
agent.process.poll(handle, ...)
agent.process.write(handle, data)
agent.process.stop(handle)

agent.api.call(name, arguments)

agent.trace.view(program, *, version=1, as_of=None, query=None, max_bytes=16000)
agent.trace.events(*, kinds=None, after=None, limit=100)
agent.state.get(key, default=None)
agent.state.put(key, json_value)
agent.state.delete(key)

agent.delegate.start(brief, *, context="none", mode="async", write_scope=None)
agent.delegate.status(handle)
agent.delegate.wait(handles, timeout=None)
agent.delegate.message(handle, message, *, trigger=False)
agent.delegate.interrupt(handle)
~~~

This is one tool, not a large top-level registry. `agent.capabilities` is progressive disclosure: the system prompt names the namespaces and invariants, while detailed schemas are queried only when needed. Existing `agent.mcp.call` can remain as a compatibility alias while registered integrations move behind the generic `agent.api.call` catalog.

### Scratch state versus durable state

Three state classes must stay distinct:

| State | Example | Lifetime | Recovery rule |
|---|---|---|---|
| Warm scratchpad | parsed AST, helper function, cached search rows | CPython worker | safe pure cells may rebuild it; never proof of an effect |
| Durable named state | chosen modules, experiment result, parent/child handoff | trace store | append `state.put`/`state.deleted`; reduce latest value as of a watermark |
| Durable evidence | file receipt, command result, test, approval, child result | trace store/artifact | immutable event plus content-addressed bytes |

`agent.state` is not a second key-value database. It appends typed events to the trace, and `state.latest` is an ordinary stored view. JSON-sized values remain inline; large values become content-addressed artifacts referenced by the event.

### Simple and complex task behavior

For a simple change, the model may use one cell:

~~~python
text = agent.fs.read("src/parser.py")["model_text"]
agent.fs.edit("src/parser.py", "old", "new")
agent.shell.run("pytest -q tests/test_parser.py")
~~~

For a large task, the same surface supports map/filter/fan-out without returning every intermediate result to the model:

~~~python
files = agent.shell.run("rg -l 'LegacyClient' src tests")["model_text"].splitlines()
analyses = [analyze(path) for path in files]
agent.state.put("migration.inventory", analyses)
children = [
    agent.delegate.start(make_brief(group), context="none")
    for group in partition(analyses, 3)
]
results = agent.delegate.wait(children)
~~~

The complexity belongs in model-written code and trace data, not in more harness tools.

## One trace-native store

### End-state authority

Use one logical `TraceStore` backed by DuckDB for the supported single-process local profile. DuckDB is already optional and tested in this repository; it supports the append log, relational programs, temporal reads, and analytical projections the thesis requires. Serialize writers in the existing single-process runtime. Keep JSONL as deterministic export/recovery interchange, not a simultaneous live authority. Keep rebuildable search indexes disposable.

This recommendation is deliberately narrower than “put all ADK internals in DuckDB.” ADK may retain its own session-service implementation. The harness must have one authority for harness facts and must not read competing SQLite/JSONL shadow stores after cutover.

The minimum tables are conceptually:

~~~text
ledger_events          immutable facts, receipts, messages, lifecycle events
programs               name, version, source, state, hash, author/owner
materializations       program + args + watermark -> result/artifact/hash/evidence
~~~

Operational queues, task status, goal progress, process lists, and child lists should be projections or leases over those facts. Add a mutable table only where a lease/compare-and-swap operation cannot be made correct as an append; emit every mutation back into `ledger_events`.

### Stored computations

Unify the current seeded Python view registry and relational `ProgramCatalog` behind one computation catalog. Do not create another memory service.

Each computation identity includes:

- program name and version;
- source/content hash;
- task/thread scope;
- canonical arguments hash;
- input watermark;
- optional retrieval/index version;
- result hash and evidence event IDs.

Execution emits:

~~~text
computation.requested
computation.completed | computation.failed | computation.timed_out
view.materialized
~~~

An identical request at the same watermark can reuse its materialization. A later watermark is a different result even if the bytes happen to match. Context compilation records the exact view IDs it used.

Operator/repository programs may reach `active` through the existing candidate → shadow → active lifecycle. Model-authored one-off analysis should normally stay in the Python scratchpad. Promote it to a stored candidate only when repeated use or UI/context latency justifies owning it.

### Prompt and compaction as views

The live model packet should become a deterministic projection over the trace:

| Priority | View | Purpose |
|---|---|---|
| P0 | `task.control` | original/revised goal, acceptance, constraints, active plan, remaining work |
| P0 | `execution.open` | active/unknown effects, approvals, processes, children |
| P1 | `task.progress` | observed work, declared progress, dropped/superseded work, blockers |
| P1 | `history.model` | compaction summary plus exact recent model-visible tail |
| P2 | `workspace.state` | base/current fingerprint, changed paths, validations |
| P2 | `human.pending` | only messages whose delivery mode permits current injection |
| P3 | `task.memory` | query-relevant older decisions/evidence |

The current `compile_prompt()` already carries view IDs and hashes. Cut it into the live workflow only after byte equality, cache, and correctness ablations pass. Compaction then becomes another stored computation over trace evidence, not a separate memory authority.

## Goal control: staying on the original objective

Goal control is a control-plane projection, not a free-form todo list.

### Required contract

1. **Original objective is immutable.** `task.created` records the user's initial goal, acceptance criteria, constraints, non-goals, permitted scope, and workspace identity.
2. **Revision is explicit.** Only a user/host steering event may revise scope. Store `task.goal_revised` with the prior goal hash, new goal hash, author, reason, and source message ID.
3. **Plans are replaceable but auditable.** A replan creates a new plan revision; it does not erase the old one.
4. **Progress separates claims from observations.** Model claims are stored, but file changes, tool receipts, tests, and child results are independently reduced.
5. **Dropped work is typed.** A plan item can be `pending`, `active`, `complete`, `blocked`, `dropped`, or `superseded`, with a reason and replacement item when applicable.
6. **Remaining work is computed.** `task.control` derives it from the active plan revision, acceptance coverage, blockers, and verification evidence.
7. **Completion is host-owned.** A model `done` claim starts verification; it never directly changes task status to complete.

The existing `TaskLedger` is the right seed. Extend its plan-step lifecycle minimally and derive it from trace events rather than maintaining an unrelated plan tool.

### Drift detection

Use the cheapest checks that work:

- every work packet includes the compact `task.control` view;
- path-scope and workspace-diff checks are deterministic;
- repeated action fingerprints trigger replan/human escalation independently of model prose;
- any plan revision must state how each remaining acceptance criterion is covered;
- before completion, compare changed paths and verification evidence with the current goal revision;
- after user steering, recompute the control view before another model step.

Do not add a periodic “goal guardian” model call by default. The primary model can perform semantic reconciliation from the compact control view, and the host already owns objective facts and deterministic scope/evidence checks. Add a separate semantic reviewer only if an eval demonstrates material drift that the primary model and verifier miss.

### Minimal control events

~~~text
task.created
task.goal_revised
plan.revised
plan.item_status_changed
progress.claimed
workspace.changed
validation.completed
task.blocked
task.verification_requested
task.completed
~~~

This is enough to answer “what was requested, what changed, what happened, what was abandoned, and what remains” without rereading the full conversation.

## Human steering and message disposition

The current lease/ack queue is a sound mechanism but has an underspecified routing contract. Every message currently belongs to the active task and eventually enters its work packet. Codex demonstrates the missing distinction: safe-boundary pending input is separate from explicit interruption, and child mail can queue with or without triggering a turn ([Codex input queue](/Users/mathiasl/src/codex/codex-rs/core/src/session/input_queue.rs:76), [queue-only child message](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/send_message.rs:28), [triggering child follow-up](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/followup_task.rs:29)).

Add one explicit user-selected delivery field:

| Delivery | Effect |
|---|---|
| `current_now` | request cooperative cancellation; deliver after the active model/tool boundary settles |
| `current_next_boundary` | do not cancel; inject before the next model request |
| `after_current` | retain as a follow-up; start only after verified/blocked/answered outcome |
| `inbox` | unrelated to active work; never inject into its context; user may assign/start it later |

Do not infer this field from message text. The UI can default to `current_next_boundary` while offering “interrupt and steer,” “after this,” and “new task/inbox.”

`current_now` must not kill an arbitrary filesystem mutation halfway through. It cancels the active model sample or waits for the broker's effect boundary, records terminal/unknown effect state, then delivers the steering. Existing message ID, idempotency, priority, lease, ack, and expiry semantics remain.

All message state becomes trace-visible:

~~~text
message.queued -> message.leased -> message.delivered -> message.acked
               -> message.deferred | message.cancelled
~~~

The `human.pending` view decides which messages may enter the current packet. Unrelated inbox content therefore cannot accidentally change the active goal or destroy cache locality.

## Clean-context and asynchronous delegation

### Importance for SOTA complex problem solving

Delegation is high value but not foundational. The foundational capabilities are code composition, authoritative trace, goal control, and verification. Once those work, clean-context delegation is one of the few additions that can materially extend complex-task performance because it buys:

- a fresh context window for a noisy subproblem;
- independent hypotheses rather than one anchoring chain;
- parallel repository discovery or review;
- containment of large intermediate outputs;
- specialization through a compact role/brief without bloating the parent prompt.

It is most useful for separable, evidence-heavy work: map several subsystems, diagnose independent failures, review a diff, compare migration strategies, or investigate modules that can be summarized independently. It is weak for tightly coupled edits, rapidly changing shared state, underspecified tasks, or work where the parent cannot cheaply verify the result.

The main risk is not only cost. Delegation introduces lossy briefing, duplicated exploration, stale world views, merge conflicts, unverified child claims, and orchestration overhead. A swarm is not a substitute for reasoning.

### Minimal primitive

Expose delegation only inside code mode. A child gets:

- a self-contained brief with goal, scope, expected result, and success evidence;
- `context="none"` by default for clean memory;
- optionally the last N completed parent turns or explicit trace view/artifact references;
- the same one-tool code surface and broker policy;
- a task/trace scope, time/token/cost/effect budget, and cancellation token;
- read-only shared workspace by default;
- an isolated worktree or explicit path ownership before mutation;
- no recursive delegation in the first version.

Codex's `fork_turns=none|all|N` is the clearest implementation of selectable context inheritance ([spawn](/Users/mathiasl/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs:280)). Its separate queue-only message, triggering follow-up, interrupt, and event-driven wait operations are also the right lifecycle primitives. OpenCode proves durable child session identity and foreground/background execution; Pi's optional subagent extension proves that fresh processes and bounded concurrency can deliver the isolation benefit without making a swarm core; Horizon proves that nested HITL must resume the same child rather than silently rerun it.

### Async semantics

`agent.delegate.start(..., mode="async")` returns immediately with a durable child ID. The live executor may still be process-bound, but its identity and terminal result are trace-durable.

The parent should continue only with independent work. It waits on events rather than polling aggressively. New human steering wakes a parent wait. Parent cancellation propagates to children unless the child was explicitly detached by the human. A process crash may resume a child from its durable thread/task record, but must not pretend to recover an in-flight model sample or side effect.

The child returns a bounded envelope:

~~~json
{
  "status": "complete|blocked|failed|cancelled",
  "summary": "bounded finding",
  "claims": [{"claim": "...", "evidence_event_ids": ["..."]}],
  "artifacts": ["artifact://..."],
  "workspace": {"base": "...", "changed_paths": []},
  "remaining_questions": []
}
~~~

The parent prompt receives this envelope, not the child transcript. The full child trace remains queryable. Parent verification or integration decides whether a child claim affects task progress.

### When to delegate

Keep the policy model-driven and simple:

- delegate when the subproblem is independent and its evidence would materially pollute the parent context;
- use asynchronous mode only when the parent has useful independent work;
- do not delegate a one-file change, a serial dependency, or work cheaper to express as a local Python loop;
- prefer code-mode fan-out over child agents for mechanical transformations;
- prefer one reviewer child over multiple redundant implementers unless an eval shows ensemble value.

This makes delegation available for SOTA complex work while keeping simple-task overhead at zero.

## Long-running commands and process handles

Large coding tasks need builds, servers, watchers, and tests that outlive one tool yield. Keep this behind the one code tool:

~~~text
spawn -> running -> exited | failed | stopped | unknown
~~~

`agent.shell.run` may return a process handle when the initial yield expires. `agent.process.poll/write/stop` operate on it. Persist command identity, workspace/environment fingerprint, backend PID/handle, timestamps, bounded log artifact, readiness evidence, and last observed status in the trace.

After restart, reattach only when the backend proves identity. Otherwise record `unknown`; never rerun automatically. A process being `running` is not readiness, and a zero exit code is not task completion.

## Budget, cancellation, and verification

One task tree rolls up:

- root model tokens/cost;
- code cells and nested effects;
- trace computations;
- compaction;
- child models and effects;
- verification.

Cancellation propagates root → active code cell → nested capability → processes/children where safe. Every started effect ends as completed, failed, blocked, cancelled, timed out, or unknown. Budget exhaustion produces a deterministic handoff from `task.control` and trace evidence; it does not imply success.

Preserve the current deterministic verifier as the completion boundary. Do not replace it with a reviewer agent. A reviewer child may add semantic evidence, but required commands, scope checks, `git diff --check`, acceptance coverage, and exact workspace fingerprint remain host predicates.

## Prioritized extensions

### P0: connect and consolidate what exists

1. **Run the four-tool versus one-tool ablation.** Measure task pass rate, cost per pass, uncached tokens, cache ratio, latency, and tool errors. Flip the default only if the one-tool path holds quality.
2. **Unify view runtimes.** Put seeded reducers and SQL programs behind one versioned computation catalog and receipt format.
3. **Cut trace views into the live work packet and compactor.** Preserve byte-equality and rollback gates.
4. **Expose `agent.capabilities`, `agent.trace`, and ledger-backed `agent.state` inside Python.** Add no top-level tool.
5. **Make goal/progress control explicit.** Add plan revision plus dropped/superseded semantics; keep original goal revision host-owned and completion verifier-owned.
6. **Add explicit human message disposition.** Reuse the current queue's lease/ack/idempotency mechanics.
7. **Cut over one harness authority.** For the single-process analytical profile, make DuckDB authoritative and demote JSONL/SQLite harness stores to migration/export until removable.

### P1: complex-task capabilities behind the same surface

8. **Add clean-context delegation with bounded async handles.** Default read-only, no recursive spawning, result envelopes only, trace all lifecycle and cost.
9. **Add process handles only if target evals need dev servers or commands longer than the current shell boundary.** Reuse shell broker policy and receipts.
10. **Add one optional semantic reviewer child only if it improves held-out complex-task acceptance.** It never owns completion.

### Defer until evidence exists

- automatic agent swarms or fixed planner/coder/reviewer pipelines;
- a model-driven memory-curation agent;
- LanceDB in the live prompt without a measured retrieval need and explicit embedder;
- a custom database, vector store as authority, or bespoke “memory OS”;
- recursive child delegation;
- concurrent writes in one workspace;
- scheduled routines, distributed workers, or remote sandboxes without a deployment requirement;
- model-authored active SQL programs without bounded execution and promotion evidence;
- a separate todo, plan, memory, search, process, delegate, or verification tool schema.

## Delivery slices and gates

Each slice is independently testable and revertible.

| Slice | Smallest change | Deterministic gate | Behavioral gate |
|---|---|---|---|
| 1 | unify computation identity/catalog | same request/watermark gives same result hash and evidence | none |
| 2 | live trace-derived work packet behind config | byte-equivalent ledger/task/context fixtures; stable prefix unchanged | no pass-rate regression |
| 3 | `agent.trace` + `agent.state` nested APIs | same broker/receipt path; state as-of replay | model uses queries without context inflation |
| 4 | typed goal revision/plan supersession | original goal immutable; revisions attributable; remaining-work view deterministic | fewer drift/forgotten-work failures |
| 5 | message delivery modes | no inbox leakage; safe cancellation; lease/ack replay | users can steer without unnecessary aborts |
| 6 | one-tool default decision | direct/nested authority equivalence; replay tests | quality/cost/cache/latency ablation |
| 7 | clean async delegation | child scope/budget/cancel/result replay; no shared-write races | complex-task pass/cost gain |
| 8 | process handles, if needed | unknown-on-unprovable-reattach; idempotent stop | long build/server tasks improve |

Required goal-control evals should include:

- a late user scope revision;
- an attractive but out-of-scope refactor;
- a failed approach that must be explicitly dropped;
- compaction between implementation phases;
- a child returning an unsupported completion claim;
- an unrelated queued human message while work is running;
- repeated no-progress actions followed by a materially different replan;
- verification failure that reopens the exact remaining criterion.

## Resulting minimal architecture

The end state has very little model-facing machinery:

~~~text
stable prompt
  identity + authority rules + one code-tool schema

dynamic packet
  trace-derived task.control + open effects + recent exact evidence

one code tool
  persistent Python scratchpad + discoverable brokered capabilities

one trace store
  immutable facts + stored computations + materialized projections

thin host control
  safe message delivery + budgets/cancellation + deterministic verification
~~~

That substrate scales down to a one-cell edit and up to a long-running, steered, compacted, delegated task without changing the model's basic interface. Increased model intelligence can absorb more planning, composition, retrieval, and delegation policy over time because those decisions are expressed as code over stable capabilities and trace facts rather than frozen into harness workflows.
