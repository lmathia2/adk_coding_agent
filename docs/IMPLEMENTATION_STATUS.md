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

### Context economy

- [x] Cache-stable static instruction and deterministic dynamic work packets.
- [x] Stable-prefix hashing and dynamic context token estimates.
- [x] Token-bounded repository map and recent-event tail.
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
- [x] Durable lease/ack user-steering queue.
- [x] No-progress fingerprints and replan/human-input routing.
- [x] Resumable ADK workflow.
- [x] Isolated Git worktree creation, reattachment, fingerprinting, and guarded cleanup.
- [x] Database/in-memory/Vertex session service configuration.
- [x] In-memory/GCS artifact service configuration.
- [x] In-memory/Vertex memory service configuration.

### Verification and evaluation

- [x] Cheap-to-broad validation ladder.
- [x] Criterion-level evidence requirements.
- [x] Scope and forbidden-path checks.
- [x] `git diff --check` completion gate.
- [x] Deterministic evaluation case and grader contracts.
- [x] Cost, cache, context, tool-output, replay, and task-outcome metrics.
- [x] Fail-to-pass starter evaluation fixture.
- [x] Interruption/replay integration scenario.
- [x] GitHub Actions unit and integration workflows.

### Trace-driven skills and improvement

- [x] Redacted, bounded, append-only ADK lifecycle tracing for users, runs, agents, models, tools, events, and errors.
- [x] Trusted multi-root Agent Skills discovery with path confinement, validation, hashing, and progressive disclosure.
- [x] Deterministic explicit and lexical skill selection in the volatile work packet.
- [x] Verified-only workflow episodes, repeated-pattern discovery, and privacy-safe heuristic synthesis.
- [x] Candidate/baseline assignment, multi-metric promotion gates, provenance, and automatic rollback.
- [x] Runtime integration that cannot broaden the four-tool surface or override deterministic verification.
- [x] Programmatic high-fanout routing skill with a controlled paired-ablation definition.
- [x] Deterministic FFF-versus-rg search ablation for ranking, context bytes, pagination completeness, and safety.

## Remaining hardening

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
