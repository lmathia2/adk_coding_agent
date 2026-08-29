# Implementation Status

This file is the commit-driven status view for the implementation. Each checked item
was implemented with deterministic tests before or alongside its repository commit.
`docs/TODO.md` tracks the original MVP plan, whose local implementation items are now
complete.

## Complete

### Foundation

- [x] Pi-inspired ADK harness design brief.
- [x] Google Agents CLI-compatible application scaffold.
- [x] Typed task, ledger, context, tool, verification, checkpoint, and agent-step contracts.
- [x] Strict, versioned YAML composition and separately typed volatile runtime bindings.
- [x] Closed-registry composition contract for selecting a harness without arbitrary imports.
- [x] ADK `BaseLlm` provider-adapter and `App` assembly interfaces without a second model runtime.
- [x] Closed native-Gemini and OpenAI-compatible ADK model adapters, including a
  credential-free tested Magnitude configuration example with environment-only secrets.
- [x] Provider-scoped, deterministic tool-call ID normalization for OpenAI-compatible
  models that reuse call IDs across turns, including repair of outbound ADK history.
- [x] Repeatable lockfile-based checkout installer with full macOS prerequisite
  bootstrap, fresh uv environment creation, Bubble Tea TUI build, Magnitude
  service/model discovery, generated local-model composition, and an installed
  two-command server/TUI launcher plus a managed single-command handoff with exact
  model selection, shared state, secret-safe token transfer, and child cleanup.
- [x] Registered Pi harness factory with implementation-owned strict configuration,
  explicit runtime bindings, isolated builds, and a swappable test harness behind the
  same public contract.
- [x] Parse-time rejection of unsupported Pi workflow/agent shapes, executable agent
  model bindings, and bounded configuration-root prompt files covered by the resolved
  behavior hash.
- [x] Versioned WebSocket control and AG-UI event-envelope contracts.

### Context economy

- [x] Cache-stable static instruction and deterministic dynamic work packets.
- [x] Stable-prefix hashing and dynamic context token estimates.
- [x] Token-bounded repository map and recent-event tail.
- [x] Per-section and whole-work-packet caps, redundant ledger-event filtering, and a
  configurable aggregate task input-token budget.
- [x] Structured coding-aware compaction checkpoints.
- [x] ADK context caching and event-compaction configuration.
- [x] Recoverable, bounded artifact identifiers carried across compaction snapshots.
- [x] Replay-safe coding-tool artifact events bridged into the compaction stream.

### Coding tools and safety

- [x] Four model-visible tools: `read`, `bash`, `edit`, and `write`.
- [x] Workspace path confinement.
- [x] Atomic and idempotent file mutations.
- [x] Bounded tool output and artifact spill support.
- [x] Static shell risk classification.
- [x] Approval requirement for network, dependency, Git-history, publishing, and unknown operations.
- [x] Denial of destructive operations by default.
- [x] Recursive secret redaction.
- [x] Content-addressed mutation receipts for replay safety.
- [x] Immediate Python/JSON syntax diagnostics after successful writes and edits.
- [x] Recoverable structured results for invalid or denied model tool inputs; expected
  confinement and argument errors cannot crash the enclosing ADK run.
- [x] Model-facing tools execute off the async server loop so bounded commands cannot
  starve WebSocket heartbeat, replay, cancellation, or steering traffic.
- [x] Obvious host-root traversal commands such as `find /` are never automatic in
  the local adapter and require explicit approval.
- [x] Explicit server production mode that fails before startup when the selected
  command adapter is not an enforceable Docker, Kubernetes, or remote boundary.
- [x] Per-launch project trust binding that gates repository instructions and
  project-local skills and is announced by both launcher and server configuration.

### Repository understanding

- [x] Git revision, branch, dirty-state, language, and build-manifest discovery.
- [x] Layered `AGENTS.md`, `CLAUDE.md`, and `AGENTS.override.md` loading.
- [x] Locked in-process FFF grep/fuzzy-find with strict workspace confinement, grouped harness-owned pagination, durable opaque cursors, and post-mutation refresh.
- [x] Incremental Python and TypeScript/JavaScript structural symbol index.
- [x] Import/call relationships and task-ranked repository map.
- [x] Adjacent-test inference.
- [x] Disabled-by-default read-only contracts for operator-managed LSP/Moderne-style semantic providers.

### Durable long-running execution

- [x] Append-only task event stream and deterministic ledger replay.
- [x] SQLite checkpoint persistence.
- [x] Durable, bounded lease/ack user-steering queue with CLI ingress, ADK
  model/tool safe-point delivery, and completion fencing.
- [x] ADK-callback-derived tool/result fingerprints, objective no-progress detection,
  and replan-to-human escalation that preserves stagnation across replans.
- [x] Resumable ADK workflow.
- [x] Isolated Git worktree creation, reattachment, fingerprinting, and guarded cleanup.
- [x] Database/in-memory/Vertex session service configuration.
- [x] In-memory/GCS artifact service configuration.
- [x] In-memory/Vertex memory service configuration.
- [x] Durable SQLite public-run registry with atomic terminal events, cursor replay,
  idempotent starts, ownership checks, and bounded live fan-out.
- [x] Loopback-safe bidirectional WebSocket server with AG-UI normalization,
  protocol negotiation, steering, cancellation, acknowledgements, heartbeat, and
  reconnect-after-backpressure semantics.
- [x] Protocol-only Bubble Tea TUI with cursor resume, replay deduplication, bounded
  buffers/history, streaming tool/text rendering, and mid-run steering.
- [x] Public, replayable coding-model identity and honest adapter/responding status
  without exposing endpoints, credential references, or provider secrets.
- [x] Explicit Magnitude reasoning-effort override in the CLI and generated,
  validated composition for controllable local-model speed/quality tradeoffs.
- [x] Magnitude startup performs a real completion probe before announcing model
  responsiveness, with an explicit discovery-only opt-out.
- [x] Configurable first-event, idle, total-run, and cleanup deadlines with one
  bounded startup retry, classified durable failures, and same-task ADK generator
  shutdown.
- [x] Hidden model-reasoning events renew liveness without exposing chain-of-thought
  or persisting token-level heartbeat noise; one bounded reasoning state is public.

### Verification and evaluation

- [x] Cheap-to-broad validation ladder.
- [x] Criterion-level completion claims bound to typed, harness-produced validation
  references rather than model-authored evidence prose.
- [x] Local-model structured-output normalization for scalar claim evidence and
  completion decisions based on typed environmental evidence rather than model prose.
- [x] Automatic behavioral-verifier requirement for executable-code changes, with
  explicit syntax/static opt-down for tasks whose acceptance contract is narrower.
- [x] Scope and forbidden-path checks.
- [x] `git diff --check` completion gate.
- [x] Deterministic evaluation case and grader contracts.
- [x] Cost, cache, context, live ADK tool-call/output/replay, verification-tool, and
  task-outcome metrics.
- [x] Fail-to-pass starter evaluation fixture.
- [x] Interruption/replay integration scenario.
- [x] GitHub Actions unit and integration workflows.

### Trace-driven skills and improvement

- [x] Redacted, bounded, append-only ADK lifecycle tracing for users, runs, agents, models, tools, events, and errors.
- [x] Trusted multi-root Agent Skills discovery with path confinement, validation, hashing, and progressive disclosure.
- [x] Deterministic explicit and lexical skill selection in the volatile work packet.
- [x] Verified-only workflow episodes, repeated-pattern discovery, and privacy-safe heuristic synthesis.
- [x] Learning eligibility requires typed environmental evidence for every criterion
  and records privacy-safe requirement, scope, plan, decision, steering, tool, and
  verification phases rather than raw interaction content.
- [x] Candidate/baseline assignment, multi-metric promotion gates, provenance, and automatic rollback.
- [x] Runtime integration that cannot broaden the four-tool surface or override deterministic verification.
- [x] Programmatic high-fanout routing skill with a controlled paired-ablation definition.
- [x] Deterministic FFF-versus-rg search ablation for ranking, context bytes, pagination completeness, and safety.

## Remaining hardening

- [x] Replace import-time singleton wiring with a registered factory that builds the selected harness from YAML and reuses ADK apps, events, plugins, tools, and resume semantics. Runner and service construction remains transport-owned.
- [x] Implement the durable run registry and bidirectional WebSocket server behind the versioned AG-UI/control contract.
- [x] Implement the Bubble Tea protocol client without ADK or harness-implementation dependencies.
- [x] Add deterministic reconnect, replay, backpressure, steering, cancellation, and harness-swap integration tests.
- [ ] Exercise the live Gemini/ADK workflow in a credentialed environment.
- [ ] Run the programmatic-routing and optional semantic-provider ablations with live model credentials.
- [ ] Run the FFF-versus-rg performance sample on representative developer machines; latency is reported, not a deterministic CI gate.
- [x] Pin the unit- and import-tested Google ADK 2.7 minor and commit the resolved lockfile.
- [x] Add large, real-repository evaluation tasks derived from human pull requests.
- [x] Add container runtime adapters for Docker, Kubernetes, and enterprise remote sandboxes.
- [x] Add human approval transports for interactive CLI, API, and managed-queue deployments.
- [x] Add provider usage callbacks that write live ADK token/cost events into `MetricsStore`.
- [x] Add curated project-memory extraction after verified task completion.
- [x] Add an optional narrow final-diff reviewer and a paired ablation comparator for required quality, cost, cache, context, tool, and latency metrics.
- [x] Add transactional PostgreSQL events and distributed task leases for multi-worker production deployments.

## Release gate

The first production candidate should not be tagged until all of the following hold:

1. Unit, integration, lint, type-check, and ADK import jobs pass in CI.
2. The live workflow completes the core fail-to-pass suite without editing held-out tests.
3. A killed invocation resumes against the same workspace fingerprint and does not repeat a mutation.
4. Every completed task has a passing `VerificationReport` and criterion evidence.
5. Destructive, publishing, and unapproved external commands remain blocked in adversarial tests.
6. Cache-read ratio, uncached input, cost per passed task, and prefix-version count are visible for every evaluation run.
