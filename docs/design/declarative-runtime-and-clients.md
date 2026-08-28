# Declarative Runtime and Stable Clients

## Objective

Changing coding-harness behavior should normally mean editing one validated YAML file.
Implementing a materially different coding harness should mean registering another
harness factory and selecting its registry key in YAML. Neither operation should
require changes to the interactive client.

The durable boundary is:

```text
Bubble Tea TUI (replaceable client)
        │
        │ versioned control messages + AG-UI events
        ▼
WebSocket server and run registry
        │
        │ one shared ADK Runner → AG-UI runtime adapter
        ▼
registered harness factory selected by YAML
        │
        │ builds an ADK App assembly + optional control hooks
        ▼
Google ADK
Runner · workflows · agents · events · plugins
sessions · memory · artifacts · resume
```

This is an interface boundary, not a second agent framework. Google ADK remains the
execution engine. The harness adds coding-specific composition, deterministic
context, tools, verification, safety, and durable control state.

## Two kinds of change

### Configure an existing harness

A strict, versioned YAML document describes behavior that is safe and useful to
change without writing Python:

- the registered harness/workflow key;
- model references and retry behavior;
- agent prompts, output contracts, and the fixed four-tool assignment;
- workflow implementation, iteration bounds, review, and verification policy;
- context, cache, compaction, repository search, skill, and learning budgets;
- safety, sandbox, persistence, tracing, steering, and server settings.

The loader rejects unknown fields and invalid references. The normalized document
has deterministic serialization and a stable content hash. Secrets are references
such as environment-variable names, never values embedded in YAML or hashes.

Runtime identity does not belong in the behavior document. Workspace paths, state
roots, task and worker identifiers, base revisions, and ADK invocation identifiers
are supplied as typed runtime bindings. This keeps volatile state out of the stable
prompt and makes the same composition reusable across workspaces.

The current `pi_coding_v1` factory deliberately rejects changes to its hard-coded
node edges, routes, agent bindings, and prompt contracts instead of silently ignoring
them. Its budgets, models, tools, safety, sandbox, steering, tracing, learning, and
review behavior remain configurable. A different workflow topology is a different
registered harness until a topology compiler is implemented and tested.

### Swap the harness implementation

The YAML `harness.implementation` value is a key in a closed Python registry. It is
not an import path and cannot cause arbitrary module loading. A registered factory
accepts its implementation-specific typed configuration and runtime bindings, then
returns an ADK `App` assembly plus optional control hooks. The shared server runtime
owns `Runner` construction and event translation.

For example, `coding_harness` can select the current iterative
compile/code/reduce/verify graph. A future `planner_executor_harness` could build a
different ADK agent and workflow topology. Both are driven through the same shared
ADK runner adapter and public run, event, steering, cancellation, snapshot, and
resume interface. The WebSocket server and TUI would not know which implementation
was selected.

Registration is code review: adding executable behavior requires a Python change and
tests. Selecting already registered behavior is configuration. This separation keeps
YAML expressive without turning it into an unsafe plugin loader.

## Reuse ADK instead of rebuilding it

Each harness factory should assemble existing ADK primitives rather than duplicate
their behavior:

- `App`, workflow agents, and `Runner.run_async()` for execution and streamed events;
- `SessionService` for conversation state and event history;
- `ArtifactService` for large outputs and durable references;
- `MemoryService` for cross-session knowledge;
- ADK plugins and callbacks for tracing, metrics, steering safe points, and policy;
- `RunConfig` for streaming, bounded tool concurrency, and model-call limits;
- ADK invocation identifiers and resumability for interrupted runs;
- ADK event objects as the source normalized into public protocol events.

The harness-specific layer remains responsible for deterministic workflow routing,
context compilation, the four coding tools, safety policy, task-ledger projection,
workspace coupling, and completion verification. Those contracts are deliberately
independent of transport and UI concerns.

## Stable AG-UI and control boundary

AG-UI supplies the public event vocabulary. Standard lifecycle, text-message,
tool-call, state, and error events are emitted wherever they fit. Coding-specific
information such as checkpoints, verification evidence, approvals, compaction, and
skill-learning observations is carried in namespaced AG-UI custom events.

The bidirectional WebSocket adds a small versioned control envelope around those
events. Client messages cover run start, attach/replay, steering, pause, cancel,
acknowledgement, and heartbeat. Server envelopes add monotonic sequence numbers and
task, session, and invocation identity so a client can reconnect and resume from a
cursor. Raw provider or ADK event JSON is not part of the public contract; the server
normalizes and redacts it first.

AG-UI is therefore the compatibility boundary, not the harness's internal state
model. Internal ledger and ADK types may evolve without forcing a TUI rewrite as long
as the versioned public mapping is preserved.

## Bubble Tea client invariant

The Bubble Tea application is a protocol client only. It should:

- open the WebSocket and negotiate the supported protocol version;
- start or attach to a task and replay from the last acknowledged sequence;
- render text, tools, progress, verification, approval, and terminal events;
- submit steering, pause, cancellation, and approval decisions;
- keep local presentation state, keyboard handling, and reconnect state.

It should not import ADK, parse the harness YAML, instantiate agents, understand the
workflow graph, or call model providers. Consequently, configuration edits and
registered harness swaps leave the TUI unchanged. A web client or another terminal
client can replace Bubble Tea by implementing the same protocol.

The same boundary also supports richer clients without backend changes: a native
SwiftUI macOS application, a cross-platform Tauri desktop application, or a web/PWA
client can consume the identical handshake, controls, replay cursor, and AG-UI event
stream. The server does not select or identify a preferred client. Bubble Tea is the
first implementation because it is small and fast to distribute, not because the
protocol is terminal-specific.

## Provider seams without a second model runtime

The model-provider interface is intentionally only an adapter that builds an ADK
`BaseLlm` from validated configuration and secret references. ADK continues to own
model calls and streaming; the harness does not define a second model-request/event
stack. Likewise, harness factories return ADK `App` assemblies rather than custom
execution engines. The shared server-side `AgentRuntime` is implemented once around
ADK `Runner`, not once per harness.

## Delivery status

Implemented:

- strict, versioned composition and runtime-binding models;
- side-effect-free YAML loading with deterministic normalization and hashing;
- implementation-owned strict configuration schemas and a closed factory registry;
- isolated configuration-driven assembly of the current Pi harness using ADK `App`,
  workflow, agents, plugins, tools, state, sandbox, and resumability primitives;
- a swappable test harness proving the common server/client boundary is independent
  of the selected implementation;
- ADK `BaseLlm` provider-adapter and ADK `App` assembly interfaces;
- versioned WebSocket control and AG-UI event-envelope models.

Pending executable integration:

- implement the long-lived run registry and WebSocket/AG-UI server;
- build the Bubble Tea protocol client;
- add reconnect, replay, backpressure, cancellation, and end-to-end tests.

Until the transport integrations land, the Agents CLI-compatible bootstrap remains
the executable path and delegates to the same registered factory.
