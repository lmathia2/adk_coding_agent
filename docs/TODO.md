# Implementation TODO

Each checked item is committed independently.

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
- [ ] Replace singleton application wiring with a closed registry of harness factories that assemble and reuse ADK primitives.
- [ ] Adapt the current coding harness to the common runtime and prove a second registered test harness can be selected without server or client changes.
- [ ] Implement the durable run registry and bidirectional WebSocket/AG-UI server with replay and backpressure.
- [ ] Implement a Bubble Tea TUI that depends only on the public protocol.
- [ ] Add deterministic harness-swap, reconnect, replay, steering, cancellation, and client compatibility tests.
