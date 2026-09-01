# Architecture

The supported runtime is local, single-process, and built around one ADK coding
worker. This document supersedes the larger historical designs.

## Boundaries

```text
Bubble Tea TUI
  │ versioned WebSocket control + AG-UI event envelopes
Server / run registry / ADK Runner
  │
HarnessFactory ← strict YAML behavior + explicit runtime identity
  │
ADK App → bounded coding loop → one worker
  │                             │
ledger / verify / checkpoint     read · bash · edit · write (default)
                                python → guarded broker (experimental)
```

- `harness/agent` defines the factory, assembly, control, and provider-neutral
  descriptor contracts. A registered factory can replace the harness without
  changing the TUI.
- `harness/config` validates YAML and separates stable behavior from workspace,
  task, state-directory, and project-trust bindings.
- `app/agent/factory.py` assembles ADK agents, plugins, sandbox, tools, and workflow.
  The server and Agents CLI application entrypoint use this same factory.
- `harness/ai` builds ADK models. Built-ins are native ADK/Gemini and the Codex
  subscription adapter; there is no second agent runtime or LiteLLM route.
- Factories can opt into `ModelConfigurableHarness` to expose their coding model
  and validate a replacement configuration. The server owns catalog/preferences
  and freezes model identity and behavior hashes when admitting each run. The
  terminal only sends control requests; it does not rewrite YAML or build models.
- An optional resource-discovery hook uses the same trusted loaders as execution.
  Server metadata includes paths, availability and trust, never instruction/skill
  bodies. Selected skill names are flushed through ADK state before the worker
  starts; the terminal cannot decide project trust or scan server directories.
- `harness/server` owns authenticated local transport, run ownership, replay,
  cancellation, deadlines, and ADK event translation. The TUI imports none of the
  Python harness implementation.

The Pi implementation's loop is Python code, not a configurable graph interpreter.
YAML references a versioned worker prompt and tunes its model, budgets, skills, tool limits, safety allowances, tracing, persistence,
steering, and iteration limits. Change topology by registering another factory.

## Minimal coding loop

The workflow initializes/replays a task ledger, builds a bounded work packet,
runs the coding worker, and reduces its typed result. Deterministic routes continue,
compact, replan, verify, block, or finish. Model prose cannot mark a task verified.

The static instruction excludes volatile task/session data. Dynamic packets hold the
ledger, repository manifest, selected skills, recent events, compaction snapshot, and
steering. Serialization and prefix hashes are deterministic. Even tiny context
budgets are hard bounds.

Directory skills are trusted operator/project inputs. Project instructions and
skills require explicit project trust; selected bodies are budgeted and hashed.
There is no automatic synthesis, trial assignment, promotion, or project-memory
injection. Traces remain useful for manual investigation and future measured work.

## Execution boundary

The experimental `notebook_ptc.enabled` path exposes one `python` tool backed by a
persistent CPython worker and parent-owned capability broker. Registered MCP, file,
and shell capabilities traverse that broker and its lifecycle trace. This path
supports trusted local workspaces. Production or adversarial execution is outside
the supported boundary; the source guard is defense in depth, not a sandbox. An
OS-isolated profile may be added when a concrete deployment requires it, but it is
not a notebook-PTC activation or completion gate.

Managed file tools call the same atomic, confined primitives exercised by tests.
Successful mutations have replay receipts; failed operations are never persisted
as successful receipts. Shell output is redacted and bounded.

FFF provides grouped, cursor-paginated search through reserved `bash` commands,
not another model-visible tool. The repository manifest supplies only compact build and
verification metadata.
No LSP/Moderne planning-only bridge remains.

Local and Docker command adapters use the authoritative host/mounted workspace.
Verification is supplied explicitly with that same sandbox, YAML approval policy,
redactor inputs, state directory, and actual task ID. Approval fingerprints do not
leak across tasks. Validation disables uv dependency synchronization and online
resolution; project dependencies must already be available.

The local adapter is not a security sandbox. Docker isolates commands, not the
entire Python harness or its host-side file/search primitives. See
[security](security.md).

## State and interaction

The canonical DuckDB ledger captures task events, receipts, checkpoints,
approvals, steering, metrics, public/run events, redacted ADK session lifecycle, and
ADK traces. Task-state replay, recent context, and compaction now read the canonical
task-event projection after byte-level equality tests; the JSONL compatibility store is
still dual-written and idempotently read-repaired for pre-migration tasks. Other SQLite
stores remain operational projections during measured migration. Artifacts use local
files or memory.

When installed through the optional `memory-search` extra, LanceDB provides immutable
hybrid-search projections over ledger events. Projection identity includes the exact
event rows, vectors, and embedding version; results return canonical event IDs for view
provenance. DuckDB remains the sole authority and all Lance data is disposable. LanceDB
and its embedding implementation are imported only when this projection is configured,
so the default harness pays no startup or dependency cost. Projections live beneath a
SHA-256 task directory and explicit task erasure removes that directory with the
canonical evidence.

`ledger-backfill` idempotently imports recognized legacy stores and reports source-count
equality. Explicit task watermarks can be sealed atomically to deterministic Parquet;
DuckLake is not installed or claimed because current scale measurements do not justify
another catalog tier.

Physical erasure is an explicit operator-authorized exception to append-only retention.
It removes an exact task from canonical and recognized operational stores plus its
notebook, unshared artifacts, JSONL stream, and manifested sealed segments. Shared
content-addressed artifacts are retained while another ledger task references them.
PostgreSQL, GCS, Vertex, and distributed-worker leases are not supported.

Steering is durably queued and consumed at safe model/tool/work-batch boundaries.
The server owns cancellation and replay. Run a single server process per state
directory; SQLite persistence does not imply multi-worker coordination.

ADK still owns Runner invocation, agent execution, streaming, session services,
caching, and resumability. The harness adds coding-specific contracts, not a
replacement framework.

See [simplification results](simplification.md), [development](development.md),
and [current status](IMPLEMENTATION_STATUS.md).
