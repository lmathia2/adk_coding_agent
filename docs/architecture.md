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

## Declarative composition and stable client boundary

The target runtime separates configuration, harness construction, transport, and
presentation so either harness behavior or the complete harness implementation can
change without rebuilding the client:

```text
Bubble Tea TUI
      │  versioned control messages + AG-UI event envelopes
      ▼
WebSocket server / durable run registry
      │  shared ADK Runner → AG-UI runtime adapter
      ▼
closed harness-factory registry ← strict versioned YAML + runtime bindings
      │
      ▼
ADK App assembly / workflows / control hooks
      │
      ▼
shared ADK Runner / sessions / artifacts / memory
```

YAML selects a closed registry key, never an arbitrary import path. The selected
factory receives validated behavior plus separately supplied volatile runtime
bindings and assembles the corresponding ADK application. Editing the composition
can tune the current coding harness; registering another factory can replace its
workflow topology. Both implementations are driven through the same ADK runner and
expose the same run/event/control contract.

The server maps normalized ADK output to standard AG-UI lifecycle, text, tool, state,
and error events, with namespaced custom events for coding-specific checkpoints,
verification, approvals, compaction, and learning. A small WebSocket control envelope
adds start, attach/replay, steering, pause, cancel, acknowledgement, and heartbeat.
The TUI speaks only this public protocol: it does not import ADK, parse the YAML, or
know which harness factory is active.

The declarative models, implementation-specific strict schemas, closed factory
registry, and configuration-driven ADK `App` assembly are implemented. Application
imports are lazy, and each factory build receives explicit runtime bindings and
constructs isolated workflow, tool, state, sandbox, plugin, and model dependencies.
The Pi implementation fails closed when YAML changes a topology edge, route, prompt,
or agent contract that its compiled workflow cannot honor; materially different
topologies are registered as separate harnesses.
The WebSocket server and Bubble Tea client remain pending. See
[`design/declarative-runtime-and-clients.md`](design/declarative-runtime-and-clients.md)
for the full boundary and delivery plan.

## ADK ownership

The new boundary does not recreate an agent runtime. Harness factories reuse ADK
`App`, workflow/agent composition, `Runner.run_async()`, streamed events, plugins and
callbacks, `SessionService`, `ArtifactService`, `MemoryService`, `RunConfig`, and ADK
invocation/resume semantics. The coding harness continues to own only the
coding-specific deterministic context, tools, policy, ledger, workspace, routing,
and verification contracts.

The provider seam builds ADK `BaseLlm` instances, and the shared server runtime wraps
ADK `Runner`. The built-in adapters support native ADK Gemini models and explicitly
configured OpenAI-compatible endpoints through ADK `LiteLlm`. Endpoint credentials
remain environment-variable references in YAML. These are intentionally thin
interfaces around ADK rather than a second model or agent runtime; provider-specific
request and streaming stacks are not implemented by the harness.

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
  bounded skill catalog + selected skill bodies
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

User steering can arrive while a coding batch is active. The ADK steering adapter
checks the durable inbox before every coding-model turn and before an unstarted tool
call. Newly arrived messages are appended only to the dynamic request suffix and are
repeated until the enclosing `AgentStep` is durably reduced. The workflow then
acknowledges the lease. A final completion fence sends the task back through another
work batch if guidance arrived during verification or review. This is cooperative
safe-point steering; it does not terminate a model request or subprocess already in
flight.

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
2. **Lexical evidence:** in-process FFF indexed grep/fuzzy path discovery plus bounded `rg`, Git history, compiler, and test-runner commands through `bash`.
3. **Structural map:** content-hash incremental signatures and relationships for Python and TypeScript/JavaScript.
4. **Semantic fallback:** intentionally not part of the default implementation; it should be added only after an ablation demonstrates value.

The structural map stores signatures and relationships, never complete source bodies. The model uses it to choose what to search and read.

## Four tools

The model-visible surface is fixed:

- `read(path, offset, limit)`
- `bash(command, timeout_seconds)`
- `edit(path, old_text, new_text, expected_sha256)`
- `write(path, content, expected_sha256, expected_absent)`

Search, Git, compilers, formatters, linters, and test runners are composed through
`bash`. The strict `search grep|find|health` grammar is intercepted in-process before
shell policy and execution; malformed reserved commands fail closed. FFF owns lazy
workspace indexing while the harness owns exact limits, grouped pagination,
content-addressed cursors, confinement, redaction, and output artifacts. Rich
operational details, full logs, and artifacts stay outside the model transcript.

## Safety boundary

Tool calls pass through a managed adapter:

```text
model call
  → reserved search parser ── valid search → confined FFF index
  → ordinary command classification
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
| FFF cursor snapshots and optional frecency | SQLite/FFF state beneath the harness state root |
| Workspace checkpoints | SQLite checkpoint store + Git worktree |
| User steering | SQLite lease/ack queue |
| Large outputs | artifact service |
| Cross-session knowledge | ADK MemoryService |
| Cache/cost/quality metrics | SQLite MetricsStore |
| Redacted ADK lifecycle traces | append-only SQLite TraceStore |
| Workflow observations and trials | SQLite LearningStore |
| Learned skill revisions | lifecycle directories under the state root |

Local JSONL and SQLite implementations remain the single-process default. Multi-worker
deployments can select the transactional PostgreSQL event adapter, which serializes
sequence allocation per task, and database-clock task leases that enforce one active
worker owner with token-checked renewal and release.

## Package map

```text
app/agent/                 Pi harness factory, ADK App, worker, and workflow nodes
harness/config/            strict YAML composition and volatile runtime bindings
harness/ai/                provider adapters that build ADK BaseLlm instances
harness/agent/             factory registry, ADK assembly, and run/control contracts
harness/server/            AG-UI and WebSocket protocol contracts (server pending)
harness/context/           stable-prefix and bounded-context compiler
harness/models/            typed contracts
harness/orchestration/     pure reducers and route decisions
harness/repo/              discovery, FFF lexical search, and structural index
harness/tools/             four tools and managed ADK adapter
harness/safety/            approval and redaction policy
harness/state/             events, receipts, checkpoints, steering
harness/workspace/         Git worktree lifecycle
harness/verification/      validation planning and completion gate
harness/persistence/       ADK service factories
harness/telemetry/         cache/cost/tool/task metrics
harness/tracing/           redacted ADK lifecycle spans and export
harness/skills/            trusted Agent Skills discovery and selection
harness/learning/          verified episodes, trials, promotion, and rollback
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

The decisions for Pi-style compaction, programmatic routing, and optional
LSP/Moderne-style repository intelligence are recorded in
[`design/pi-extension-adoption.md`](design/pi-extension-adoption.md).

## Trace-driven improvement

Tracing observes user messages, runs, agents, models, tools, events, successes, and
errors through ADK callbacks. Stored projections are always metadata-only or redacted
and byte-bounded; raw trace storage is not supported. Traces never mutate provider
objects.

Skill discovery is progressive: the dynamic packet gets a compact catalog and only
the enabled bodies matched by an explicit `$skill-name` or deterministic lexical
ranking. Candidate bodies can enter only through an exact-name trial assignment.

Verified tasks are reduced to normalized action sequences and quality metrics. A
repeated pattern may create a candidate, but it is promoted only after paired baseline
and candidate evidence meets non-regression gates. Repeated failures move candidate or
active revisions to the disabled lifecycle directory. See
[`design/trace-driven-skill-learning.md`](design/trace-driven-skill-learning.md) for
the complete contract.
