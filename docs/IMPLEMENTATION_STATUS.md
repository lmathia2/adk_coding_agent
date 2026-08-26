# Implementation Status

This file is the commit-driven status view for the implementation. Each checked item was implemented with deterministic tests before or alongside its repository commit. `docs/TODO.md` remains the original planning baseline.

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
- [x] Incremental Python and TypeScript/JavaScript structural symbol index.
- [x] Import/call relationships and task-ranked repository map.
- [x] Adjacent-test inference.

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

## Remaining hardening

- [ ] Exercise the live Gemini/ADK workflow in a credentialed environment and pin the exact tested ADK 2.x minor version.
- [ ] Add large, real-repository evaluation tasks derived from human pull requests.
- [ ] Add container runtime adapters for Docker, Kubernetes, and enterprise remote sandboxes.
- [ ] Add a human approval transport for interactive CLI, API, and managed-queue deployments.
- [ ] Add provider usage callbacks that write live ADK token/cost events into `MetricsStore`.
- [x] Add curated project-memory extraction after verified task completion.
- [ ] Add optional narrow final-diff reviewer and benchmark it as an ablation.
- [ ] Add distributed locking/database implementations for multi-worker production deployments.

## Release gate

The first production candidate should not be tagged until all of the following hold:

1. Unit, integration, lint, type-check, and ADK import jobs pass in CI.
2. The live workflow completes the core fail-to-pass suite without editing held-out tests.
3. A killed invocation resumes against the same workspace fingerprint and does not repeat a mutation.
4. Every completed task has a passing `VerificationReport` and criterion evidence.
5. Destructive, publishing, and unapproved external commands remain blocked in adversarial tests.
6. Cache-read ratio, uncached input, cost per passed task, and prefix-version count are visible for every evaluation run.
