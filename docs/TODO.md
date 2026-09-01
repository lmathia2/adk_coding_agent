# Implementation TODO

## Trace-native notebook PTC agent proposal

The proposed successor architecture is specified in
`docs/design/trace-native-repl-agent.md`. It is not part of the supported runtime
until the migration and ablation gates in that document pass.

- [ ] Freeze contracts and record the current four-tool baseline.
- [ ] Add the canonical ledger behind existing behavior and prove replay equality.
- [ ] Implement canonical notebook reduction/materialization plus seeded deterministic
  views and retrieval receipts.
- [ ] Add cache-epoch prompt construction, bounded notebook context, and structured
  compaction/resume views.
- [ ] Add notebook-native PTC, the persistent CPython worker, and capability broker
  behind a feature flag.
- [ ] Run a same-model four-tool versus notebook-native one-tool ablation.
- [ ] Add validated/shadowed agent-authored memory programs.
- [ ] Add DuckLake retention and analytics only when scale measurements justify it.
- [ ] Cut over and remove superseded stores only after all hard gates pass.

## Pi terminal experience

- [x] Prove standalone Pi toolkit reuse with deterministic rendering fixtures.
- [x] Separate public replies from workflow control and support non-coding turns.
- [x] Preserve conversations and wire queues, model/auth controls and resources.
  - [x] Add cancellable server-owned provider authentication controls.
  - [x] Connect Pi-style login/status/logout dialogs to the authenticated server.
  - [x] Wire searchable model selection and saved defaults to actual next-turn ADK configuration.
  - [x] Expose bounded read-only transcript pages from durable public events.
  - [x] Connect `/resume` and historical transcript navigation to those pages.
  - [x] Expose trusted resource metadata and actual skill selection through the server.
- [x] Complete Pi-style UI and migrate installation/launching.
  - [x] Prevent stored approvals from leaking across tasks or surviving expiration in a shared adapter.
  - [x] Wire command approval decisions to waiting worker/verification execution and terminal controls.
  - [x] Stream eligible public replies with immutable control headers, verification gates and reconnect tests.
  - [x] Finish quiet activity presentation and live visual/latency comparisons.
  - [x] Migrate installation/launching after the new client passes the delivery gates.
- [x] Verify conversational/coding examples, replay and harness swap; remove Go TUI.

See `docs/design/pi-terminal-migration.md` for delivery gates.

## Minimal-harness simplification

- [x] Remove Magnitude, LiteLLM integration, and installer/launcher branches; retain Codex and native ADK provider seams.
- [x] Replace shadowed legacy tools with the tested atomic file primitives and remove unwired adapters.
- [x] Reduce fixed-graph configuration and optional orchestration layers without weakening verification or approvals.
- [x] Verify retained paths and report source-line and McCabe-complexity changes.

Each checked item is committed independently.

## Historical delivery record

The entries below describe earlier deliveries, not the supported feature inventory.
See `IMPLEMENTATION_STATUS.md` and `simplification.md` for current capabilities and
intentional removals.

- [x] Commit the Pi-inspired ADK coding-harness design brief.
- [x] Add an Agents CLI-compatible prototype scaffold and pin upstream Google skills.
- [x] Implement typed task, ledger, tool, context, checkpoint, and verification models.
- [x] Implement deterministic context compilation, prefix hashing, and coding-aware compaction.
- [x] Implement the environment abstraction, command policy, bounded output, and four coding tools.
- [x] Implement repository discovery, lexical/structural indexing, and compact repository maps.
- [x] Pin native FFF search and expose bounded, cursor-paginated discovery through the existing `bash` tool.
- [x] Implement event reduction, SQLite persistence, tool receipts, steering, and checkpoints.
- [x] Implement deterministic verification and acceptance-criterion evidence.
- [x] Wire the ADK 2.x coding agent, dynamic workflow, caching, compaction backstop, and resumability.
- [x] Add unit, integration, resume, security, and Agents CLI evaluation fixtures.
- [x] Add CI and operational documentation after the MVP contracts are stable.

## Declarative runtime and interactive client

- [x] Define strict YAML composition, volatile runtime bindings, ADK model/App assembly seams, and versioned AG-UI/control protocol contracts.
- [x] Replace singleton application wiring with a closed registry of harness factories that assemble and reuse ADK primitives.
- [x] Adapt the current coding harness to the common runtime and prove a second registered test harness can be selected without server or client changes.
- [x] Make safe Pi prompt/model choices executable configuration and reject unsupported topology or agent changes during parsing.
- [x] Implement the durable run registry and bidirectional WebSocket/AG-UI server with replay and backpressure.
- [x] Implement a Bubble Tea TUI that depends only on the public protocol.
- [x] Add deterministic harness-swap, reconnect, replay, steering, cancellation, and client compatibility tests.
