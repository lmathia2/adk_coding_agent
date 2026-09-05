# Long-running context memory programs

> Status: proposed
>
> Date: 2026-09-05
>
> Related: [ADK long-horizon harness](coding-harness-adk-long-horizon.md), [minimal SOTA extensions](coding-harness-minimal-sota-extensions.md), [Pi-inspired harness design](../design/pi-inspired-adk-coding-harness.md)

## Context

Skein needs long-running agents that can survive process restarts, compact their
working context, resume safely, and recover exact historical evidence when a
summary is insufficient. The desired queries include keyword, semantic, range,
aggregation, summarization, temporally conditioned, and composed queries.

Posthorse's useful idea is not a new compaction algorithm. It is the separation
of:

- a small, model-visible working context;
- durable source history that remains available after compaction; and
- programs that select and transform only the history needed for the current
  step.

Skein already has many of the required pieces: an append-only canonical ledger,
TaskLedger checkpoints, artifacts, a worktree fingerprint, notebook PTC, and
Google ADK session and workflow persistence. The smallest reliable design is to
connect and harden those pieces before adding another database or retrieval
stack.

The main gap is safe recovery. Today, server startup marks interrupted active
runs as failed. A Skein checkpoint records consistency metadata but is not a
workspace or process snapshot. ADK resumability is intentionally best-effort and
at-least-once, so it cannot by itself guarantee that a tool effect is not
repeated.

## Decision

Build recoverable context in dependency-gated stages, in this order:

1. safe execution resume;
2. stable conversation identity;
3. trustworthy consistency checkpoints;
4. exact historical paging;
5. programmatic query composition;
6. relational SQL when JSONL scans stop being adequate;
7. semantic retrieval when lexical retrieval is measurably inadequate; and
8. reusable summaries when repeated summarization cost justifies caching.

The minimal valuable release ends after exact historical paging. It uses the
existing ADK SQLite session service, file artifact service, canonical JSONL
ledger, Python standard library, and the current `read`, `bash`, `edit`, and
`write` tool surface. It adds no database or vector dependency.

ADK owns session persistence and resumable workflow control. Skein remains the
authority for task state, tool-effect safety, workspace consistency, exact
history, verification, and completion.

## Terms and identity

The implementation must distinguish four recovery cases:

- **Transport reconnect**: a client reconnects to an invocation that is still
  running.
- **Invocation resume**: ADK resumes the same interrupted invocation.
- **Task recovery**: a new process reconstructs enough Skein state to continue
  the task safely.
- **Portable recovery**: another machine reconstructs the task, workspace, and
  artifacts. This is not part of the minimal release.

Identity is fixed before cross-session memory is exposed:

- `(app_name, user_id, session_id)` identifies the durable ADK conversation;
- `run_id` identifies one invocation within that conversation;
- `workspace_id` and branch or lane identify the mutable code state;
- `notebook_id` and `kernel_epoch` identify disposable PTC state.

Queries default to the current ADK session. Cross-session retrieval requires an
explicit user plus workspace or project scope and must enforce the same
authorization and erasure boundaries as the source records.

## Authority boundaries

| Concern | Authority | Notes |
| --- | --- | --- |
| Conversation events and small state handles | ADK session service | Use SQLite locally; use `DatabaseSessionService` only for multi-process deployment. |
| Workflow/node resume | ADK resumability | Resume the same invocation; nodes and tools remain at-least-once. |
| Task execution and checkpoints | Skein TaskLedger | Deterministic source of task progress and verification state. |
| Exact historical context | Skein canonical ledger | Append-only evidence authority; summaries and indexes are derived views. |
| Tool effects | Skein receipts and effect ledger | Required for idempotency and unknown-effect reconciliation. |
| Workspace state | Git/worktree plus Skein fingerprint | A fingerprint detects divergence; it does not restore files. |
| Large artifacts | Existing ADK/Skein artifact storage | Do not add a third blob store. |
| Notebook heap | Notebook PTC | A disposable workbench, never the durable source of truth. |
| Semantic recall | Optional derived index | May rank candidates but never replace canonical evidence. |

ADK state may store small handles such as checkpoint IDs, ledger watermarks,
and notebook IDs. It must not contain large histories. ADK `MemoryService` may
be used later as a semantic backend, but its query-string interface is not the
authority for exact paging, range, aggregation, or historical `as_of` queries.

## Required invariants

1. Every model-visible claim can resolve to canonical event or artifact IDs.
2. Compaction never deletes canonical history required by retention policy.
3. Resume is blocked when an external effect is unknown or the workspace has
   diverged beyond the selected recovery policy.
4. Replaying a completed effect is prevented by a stable idempotency key and
   receipt, not by model memory.
5. Checkpoints are accepted only when their referenced ledgers, workspace, and
   open-effect view agree.
6. Historical queries apply the knowledge boundary before event-domain time:
   `sequence <= as_of_sequence`, then `observed_at` predicates.
7. Indexes, summaries, notebook heaps, and cached query results are rebuildable.
8. Serialization and hashes remain deterministic.

## Staged implementation

Each stage must pass its acceptance gate before the next stage begins.

### Stage 0: prove the ADK durability substrate

Dependencies: none.

Implement:

- exercise the real ADK SQLite session service and file artifact service across
  an actual subprocess restart;
- verify the pinned ADK version's resumability behavior rather than relying on
  in-memory doubles; and
- test transport reconnect separately from invocation resume.

Acceptance:

- a persisted session and artifact are readable after process restart;
- the same invocation can be resumed using ADK's supported resume path; and
- the test records duplicate-delivery behavior at crash boundaries.

Limitation addressed by Stage 1: the server still treats interrupted Skein runs
as failed and has no effect-aware recovery policy.

### Stage 1: resume interrupted invocations safely

Dependencies: none.

Implement:

- replace unconditional `server_restarted` failure in
  `recover_interrupted_runs()` with classification into resumable, reconcilable,
  and blocked runs;
- resume the same ADK invocation with no new user message;
- inspect the latest checkpoint, workspace fingerprint, `execution.open`
  records, and effect receipts before resuming;
- suppress completed effects by receipt and block unknown effects; and
- add deterministic crash tests before and after intent, effect, receipt, ADK
  event, verification, checkpoint, and compaction writes.

Acceptance:

- a process crash cannot silently repeat a completed mutating tool call;
- a known-safe run resumes from the same invocation; and
- an unknown effect produces a blocked state with a concrete reconciliation
  reason.

Limitation addressed by Stage 2: recovery is still run-scoped and checkpoint
identity can drift across sessions, ledgers, and workspaces.

### Stage 2: bind stable identity and consistency checkpoints

Dependencies: none.

Implement:

- make the ADK conversation tuple stable and make each invocation a child run;
- pass one canonical memory root and conversation identity through runtime
  bindings, removing the current ambiguity between server-level and per-run
  ledger roots;
- stamp canonical events with conversation, run, workspace, and branch or lane;
- allow only one active execution per conversation unless lanes are explicitly
  introduced; and
- extend checkpoint validation to cover ADK IDs, canonical-ledger watermark and
  hash, TaskLedger hash, workspace fingerprint, open-effect-view hash,
  compaction boundary, and notebook watermark when notebooks are enabled.

Resume algorithm:

1. Load the ADK session and invocation.
2. Find the newest checkpoint whose references still validate.
3. Replay canonical and task ledgers after its watermarks.
4. Compare the current workspace fingerprint.
5. Reconcile or block unknown effects.
6. Resume the invocation or report the exact blocking condition.

Acceptance:

- a restart deterministically selects the same latest valid checkpoint;
- corrupted or mismatched hashes fail closed; and
- workspace or branch divergence cannot be mistaken for normal continuation.

Limitation addressed by Stage 3: the agent can resume but cannot page exact
pre-compaction context into the current turn.

### Stage 3: add exact history programs over JSONL

Dependencies: none. This stage completes the minimal valuable release.

Implement two built-in, versioned programs:

- `history.page@1`: deterministic keyword, sequence, recorded-time,
  observed-time, kind, actor, and scope filtering with stable cursor or byte
  paging and `as_of_sequence`;
- `event.read@1`: exact event and artifact retrieval by canonical ID, with
  bounded byte ranges.

Expose them through reserved `bash` virtual commands so the model-facing tool
surface remains unchanged:

```text
memory search ...
memory history ...
memory read EVENT_ID ...
```

Every result returns evidence IDs, source hashes, the applied scope, the
knowledge boundary, and truncation or paging metadata.

Acceptance:

- after multiple compactions, the agent can retrieve exact source bytes;
- the same query over the same ledger watermark is byte-for-byte stable;
- cross-scope retrieval is denied; and
- restart does not change cursor order or result identity.

Limitation addressed by Stage 4: exact retrieval works, but notebook analysis is
run-scoped and its heap is not recoverable across invocations.

### Stage 4: make notebook PTC conversation-scoped

Dependencies: existing notebook dependencies only.

Implement:

- scope notebook metadata to conversation plus branch or lane, not `run_id`;
- record notebook ID, completed cells, dependencies, output handles, and kernel
  epoch in durable metadata;
- rebuild a dead kernel by replaying only completed, self-contained,
  replay-safe cells; and
- add notebook helpers that call the same Stage 3 query service. Do not create a
  second history implementation.

The notebook remains the authoring and composition environment. Selected output
is copied into the model context; unselected intermediate data remains in the
heap and may be discarded.

Acceptance:

- a new invocation can reconstruct the notebook's useful definitions without
  treating heap state as authoritative; and
- failed or side-effectful cells are never replayed automatically.

Limitation addressed by Stage 5: JSONL scans are linear and fixed Python
reducers become awkward for repeated joins and aggregations.

### Stage 5: add relational SQL only when measured

Dependency: optional `memory-duckdb` extra.

Entry gate: Stage 3 has measured unacceptable latency or repeated query logic
that is materially clearer as relational SQL.

Implement:

- immutable, parameterized DuckDB programs for range, temporal, correlation,
  and aggregation queries;
- structured parameter schemas, output contracts, allowed sources, row and byte
  limits, and a hard runtime deadline;
- program identity derived from normalized SQL, parameter schema, output schema,
  limits, allowed sources, and dependency versions; and
- parity tests against the canonical JSONL implementation.

Built-in correctness-critical queries remain available in Python. Notebook
cells may author and test candidate SQL, but the notebook is not the registry.
A candidate becomes reusable only after promotion to the immutable catalog and
deterministic tests. Agent-authored SQL remains disabled until execution can be
isolated and forcibly timed out.

Acceptance:

- SQL and Python produce equivalent evidence IDs at the same watermark; and
- a query cannot exceed its source, time, row, or byte budget.

Limitation addressed by Stage 6: relational and lexical queries do not recover
semantically related wording.

### Stage 6: add semantic retrieval only when measured

Dependencies: optional `memory-search` extra and an explicit embedding provider.

Entry gate: evaluation shows important evidence is missed by Stage 3 lexical
retrieval and cannot be fixed with a small deterministic query improvement.

Implement:

- structured scope and temporal prefiltering before semantic ranking;
- one immutable index snapshot per conversation or task watermark;
- hybrid lexical/vector ranking;
- hydration of selected IDs from the canonical ledger; and
- index identity containing embedding model version, serializer version,
  projection hash, filters, and source watermark.

Acceptance:

- retrieval quality improves on a checked-in evaluation set;
- every returned item resolves to canonical evidence; and
- stale or incompatible index snapshots are rejected or rebuilt.

Limitation addressed by Stage 7: repeated high-level synthesis still pays the
same model cost.

### Stage 7: cache evidence-bound summaries only when measured

Dependencies: model calls already available to the harness.

Entry gate: traces show the same source view is summarized repeatedly at
meaningful cost.

Implement:

- summaries as advisory derived records with source IDs and source watermark;
- identity containing the query/program version, prompt version, and model
  version; and
- invalidation when source, scope, or version changes.

Do not add triggers, a dependency DAG, automatic program synthesis, or automatic
materialization in this stage.

Acceptance:

- a summary can always disclose and retrieve its exact evidence; and
- cache hits do not change deterministic execution or verification decisions.

Limitation addressed by Stage 8: local persistence is not a portable restore
point.

### Stage 8: add portable or distributed recovery only for deployment

Dependencies: deployment-specific database and artifact infrastructure.

Entry gate: Skein must resume across machines or coordinate multiple server
processes.

Implement:

- ADK `DatabaseSessionService` backed by Postgres;
- a remote artifact store;
- a Git or object-store workspace checkpoint bundle; and
- leases plus fencing tokens for single-writer execution.

DuckLake or another lakehouse layer is out of scope until data volume and
concurrent analytical workloads prove a need.

Acceptance:

- a different worker can reconstruct the ADK invocation, Skein ledgers,
  workspace, artifacts, and effect status without relying on local disk.

## SQL, Python, and notebook policy

Use each representation for a different job:

- **Python** implements the initial built-in programs. It needs no new
  dependency, can scan JSONL deterministically, and is easiest to test beside
  `LedgerStore`.
- **Pure SQL** is the stored representation for stable relational programs once
  Stage 5's gate is met. SQL is reviewable, hashable, declarative, and naturally
  parameterized. Store metadata beside it; a SQL string alone is not a complete
  program contract.
- **Notebook cells** are the workbench for composing queries and combining
  results. They are versioned as execution records and recoverable definitions,
  but they are not the canonical program registry because cell order, hidden
  heap state, and partial execution make them an unsafe deployment boundary.

A context program version therefore includes:

```text
name + version + source/SQL + parameter schema + output contract
+ source allowlist + resource budgets + dependency versions
```

The program output is a bounded derived view with provenance, not a replacement
for the source events.

## Deliberate simplifications

The following are explicitly deferred:

- no database before JSONL scan latency or query complexity requires one;
- no vector index before lexical retrieval fails an evaluation;
- no reusable summaries before repeated cost is observed;
- no cross-session recall until identity and authorization are stable;
- no portable workspace recovery until deployment needs cross-machine resume;
- no new model-facing tools; and
- no second live prompt compiler or second canonical event store.

These are gates, not placeholders. If the condition is never observed, the
corresponding stage should not be built.

## Implementation sequence

Completed items should be committed independently in this order:

1. `test(resume): define process-crash recovery boundaries`
2. `feat(server): resume interrupted ADK invocations safely`
3. `feat(memory): bind canonical events to conversation identity`
4. `feat(checkpoint): validate ledger workspace and open effects on resume`
5. `feat(memory): add exact history paging and temporal lexical search`
6. `feat(tools): expose memory paging through reserved bash commands`
7. `eval(memory): compare compacted history with exact retrieval`
8. `feat(notebook): recover PTC workbench across conversation runs`
9. `feat(memory): add parameterized DuckDB programs`
10. `feat(memory): wire versioned semantic retrieval`
11. `feat(memory): cache evidence-bound summaries`

Stop after item 7 unless a later stage's entry gate has been demonstrated.

## Minimal release definition of done

The minimal release is complete when a subprocess can crash after compaction,
restart, validate a consistent checkpoint, reconcile tool effects, resume the
same ADK invocation, and retrieve exact pre-compaction evidence through the
existing tool surface. The proof must use deterministic tests for persistence,
identity, scope, ordering, idempotency, and crash boundaries, plus an Agents CLI
evaluation for the model's decision to retrieve history.

## Consequences

Positive:

- compaction becomes lossy only for the working prompt, not for recoverability;
- ADK capabilities are reused without making ADK memory the exact-history
  authority;
- the first useful release adds no dependency;
- each later dependency has a measurable entry gate; and
- query programs can evolve from Python to SQL and semantic retrieval without
  changing canonical evidence.

Negative:

- the minimal release scans history in linear time;
- local checkpoints detect workspace divergence but cannot restore a lost
  workspace;
- notebook heap values may be lost and recomputed; and
- ADK upgrades require resumability compatibility tests because the behavior is
  best-effort and version-sensitive.

## References

- [Posthorse repository](https://github.com/fitchmultz/pi-posthorse)
- [Posthorse context-management thread](https://x.com/fitchmultz/status/2095857889135247602)
- [ADK resumability configuration](https://github.com/google/adk-python/blob/main/src/google/adk/apps/_configs.py)
- [ADK session documentation](https://github.com/google/adk-docs/blob/main/docs/sessions/session/index.md)
- [ADK context documentation](https://google.github.io/adk-docs/context/)
- [ADK memory service interface](https://github.com/google/adk-python/blob/main/src/google/adk/memory/base_memory_service.py)
