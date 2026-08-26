# Architecture

## Design objective

The harness is a context allocator and deterministic control plane around one strong coding model. Google ADK 2.x owns workflow execution, resumability, state projection, caching, and routing. The model sees a cache-stable instruction, a bounded work packet, and four tools.

```text
Agents CLI / API / managed task queue
                │
                ▼
        Google ADK App
  cache · compaction · resumability
                │
                ▼
       Dynamic Workflow loop
 initialize → compile → code → reduce
      ▲                    │
      │       ┌────────────┼────────────┐
      │       ▼            ▼            ▼
    replan  compact      verify       blocked
      │                     │
      └──────── failure ◄───┘
                            │ pass
                            ▼
                         complete
```

## Stable and dynamic context

The coding worker uses a stable `static_instruction`. Mutable state is serialized into the node input after that prefix.

```text
Stable prefix
  system behavior
  four tool schemas
  stable project policy

Dynamic suffix
  compact Task Ledger
  project instructions
  repository manifest
  ranked repository map
  compaction summary
  recent event tail
  latest user steering
```

The dynamic serializer is deterministic: stable field order, normalized paths, no timestamps, and no random identifiers. Every work batch emits a stable-instruction hash and an estimated dynamic token count.

## Work-batch loop

One model invocation is one bounded work batch. It may inspect, search, edit, and validate, but it must finish with a structured `AgentStep`:

```text
status: continue | verify | blocked | done
progress
next_action
decisions
questions
discovered_constraints
files_in_focus
completion_claims
```

The outer workflow, not the model, decides the next route. A `done` claim always routes to deterministic verification.

## Task state

The event log is authoritative. `TaskLedger` is a replayable projection containing only current operational state:

- goal and acceptance criteria
- constraints and non-goals
- current phase and next action
- plan progress
- decisions and blockers
- files read and modified
- validations
- iteration and no-progress counters

Observational events do not mutate state implicitly. State changes are explicit `ledger.patched` events, which keeps replay deterministic.

## Repository understanding

Repository discovery has four layers:

1. **Manifest:** Git state, languages, build systems, commands, instructions, and top-level layout.
2. **Lexical evidence:** `rg`, `git grep`, history, compiler, and test-runner commands through `bash`.
3. **Structural map:** content-hash incremental signatures and relationships for Python and TypeScript/JavaScript.
4. **Semantic fallback:** intentionally not part of the default implementation; it should be added only after an ablation demonstrates value.

The structural map stores signatures and relationships, never complete source bodies. The model uses it to choose what to search and read.

## Four tools

The model-visible surface is fixed:

- `read(path, offset, limit)`
- `bash(command, timeout_seconds)`
- `edit(path, old_text, new_text, expected_sha256)`
- `write(path, content, expected_sha256, expected_absent)`

Search, Git, compilers, formatters, linters, and test runners are composed through `bash`. Rich operational details, full logs, and artifacts stay outside the model transcript.

## Safety boundary

Tool calls pass through a managed adapter:

```text
model call
  → command classification
  → approval decision
  → confined execution
  → bounded output
  → secret redaction
  → durable receipt/telemetry
  → model-visible result
```

Read, build/test, and workspace-local mutation are allowed. Dependency installation, network access, Git-history mutation, unknown commands, and publish/deploy require explicit policy approval. Destructive commands are denied.

## Workspace coupling

Each task is bound to a Git worktree and a durable workspace record:

```text
task_id
workspace_id
source repository
base revision
worktree path
branch or detached state
HEAD revision
tree hash
workspace fingerprint
```

The fingerprint includes HEAD, tracked changes, and untracked file content. A resumed task reattaches to the same worktree. Cleanup refuses a dirty workspace unless force is explicit.

## Compaction

The custom compaction snapshot is a structured ledger projection plus recent evidence. ADK event compaction remains an overflow backstop. Compaction is a deliberate cache boundary and occurs at work-batch boundaries, never after each tool call.

The original event stream, full tool artifacts, receipts, and checkpoints remain durable. Only the default model view is compacted.

## Verification

The validation ladder is ordered from cheap to broad:

1. changed-file syntax checks
2. formatter/linter
3. type checker
4. adjacent or targeted tests
5. package or repository tests
6. `git diff --check`
7. scope and forbidden-path checks

A task passes only when required commands succeed, no scope violation exists, and every acceptance criterion has explicit evidence.

## Persistence boundaries

| Information | Store |
|---|---|
| ADK conversation/session events | ADK SessionService |
| Harness control events | append-only JSONL or transactional database adapter |
| Current task state | replayed Task Ledger / ADK session state |
| Tool idempotency | SQLite receipt store |
| Workspace checkpoints | SQLite checkpoint store + Git worktree |
| User steering | SQLite lease/ack queue |
| Large outputs | artifact service |
| Cross-session knowledge | ADK MemoryService |
| Cache/cost/quality metrics | SQLite MetricsStore |

Local JSONL and SQLite implementations remain the single-process default. Multi-worker
deployments can select the transactional PostgreSQL event adapter, which serializes
sequence allocation per task, and database-clock task leases that enforce one active
worker owner with token-checked renewal and release.

## Package map

```text
app/agent/                 ADK App, coding worker, workflow nodes
harness/context/           stable-prefix and bounded-context compiler
harness/models/            typed contracts
harness/orchestration/     pure reducers and route decisions
harness/repo/              discovery and structural index
harness/tools/             four tools and managed ADK adapter
harness/safety/            approval and redaction policy
harness/state/             events, receipts, checkpoints, steering
harness/workspace/         Git worktree lifecycle
harness/verification/      validation planning and completion gate
harness/persistence/       ADK service factories
harness/telemetry/         cache/cost/tool/task metrics
harness/evals/             portable cases and deterministic graders
harness/review/            bounded advisory diff review and paired ablation metrics
harness/sandbox/           local, Docker, Kubernetes, and remote command backends
```

## Extension policy

New capabilities should normally be implemented as one of:

- an Agent Skills-compatible workflow loaded on demand
- a CLI invoked through `bash`
- a deterministic workflow node outside the model
- a project-memory entry retrieved at task initialization

A new model-visible tool is justified only when an ablation improves pass rate, cost per passed task, recovery, safety, or cross-model reliability.
