# AI coding harness rubric and implementation audit

Date: 2026-08-29

Harness revision: `843007c`

Primary source: *AI Agents in Depth: Design Principles and Engineering Practice*,
version 2.0 (2026-08-26), especially Chapters 1–7, 9, and 10.

## Executive assessment

The repository has unusually broad architectural coverage for a young coding
harness: a small model-facing tool surface, deterministic context assembly,
indexed retrieval, durable execution, a WebSocket protocol, steering, sandbox
adapters, trace capture, project memory, and guarded skill trials all exist as
real code with substantial deterministic tests.

It is not yet a state-of-the-art *proven* coding harness. The evidence-weighted
score is **63/100 (strong research beta)**. The central weakness is the gap
between control-plane completeness and end-to-end task reliability. The recent
local-model evaluation passed 6 of 10 tasks, exposed false-positive completion,
four silent inference stalls, extreme repair-context growth, missing tool metrics,
and cancellation cleanup noise. Those findings carry more weight than checked
items in an implementation-status document.

The shortest path to a production-capable score is not another major feature.
It is to close the verification, liveness, telemetry, and recovery loops already
present in the design.

## Method

The audit uses three evidence layers:

1. **Contract evidence**: source code implements the behavior outside prompts.
2. **Deterministic evidence**: focused tests prove the contract and failure path.
3. **Operational evidence**: representative coding tasks demonstrate quality,
   latency, token cost, recovery, and safety under the actual model/provider.

Documentation alone can explain intent but cannot score above maturity level 1.
A unit-tested component without representative task evidence cannot score above
level 3.

### Maturity scale

| Level | Meaning | Minimum evidence |
|---:|---|---|
| 0 | Absent or contradicted | No implementation, or the live path bypasses it |
| 1 | Specified | Documentation, prompt rule, interface stub, or unused schema |
| 2 | Implemented | Happy-path production code is connected to the live path |
| 3 | Deterministically verified | Boundary, error, replay, and security tests pass |
| 4 | Operationally validated | Representative repeated tasks meet declared SLOs with auditable traces |

Each category earns `weight × maturity / 4`. Fractional maturity is allowed when
the evidence sits between levels.

### Grade bands

| Score | Interpretation |
|---:|---|
| 90–100 | State-of-the-art evidence: broad, efficient, reliable, and production-proven |
| 80–89 | Production-capable with bounded, documented limitations |
| 70–79 | Strong beta; core loops work but one or two release-critical gaps remain |
| 55–69 | Research beta; broad features, uneven end-to-end reliability |
| 40–54 | Integrated prototype |
| 0–39 | Early prototype or thin model wrapper |

## Book-derived principles for a coding agent

### 1. Treat the agent as model plus harness

The useful unit is not the LLM alone. A production harness expands context and
tools into five responsibilities: context management, tool interfaces,
constraints, verification, and correction. The system should remain simple,
transparent, and composable; complexity needs measured benefit.

### 2. Make context an explicit, cache-aware working set

Keep a stable prefix and append volatile state late. Use a compact status view to
surface the goal, current plan, environment, budgets, progress, and failures.
Prefer progressive disclosure, bounded reads, pagination, artifacts, and isolated
exploration over dumping raw search and tool output into the main trajectory.
Compression must preserve decisions, constraints, failed paths, sources, and
recoverable artifacts.

### 3. Design tools from the model's perspective

Tools should make likely mistakes impossible or obvious. Names, parameters,
defaults, execution arguments, and model-visible declarations must agree exactly.
General executors are valuable, but dangerous operations need code-enforced
boundaries. Perception tools should be bounded and paginated; mutation tools should
be atomic, conflict-aware, minimal, and reversible; every action should return fast,
structured feedback.

### 4. Use the repository as the durable coordination surface

Load scoped project instructions, infer build and test commands, map structure,
locate relevant code from coarse to fine, and keep code, tests, and documentation in
sync. Exact search, path search, structural relationships, and optional semantic
retrieval are complementary. Retrieval sophistication matters only if it improves
passed-task cost, files read, or turns to completion.

### 5. Plan in proportion to task complexity

Clarify ambiguous requirements. For broad changes, create a reviewable design and
plan before editing; for a tiny fix, keep the ceremony small. The invariant is that
the agent understands the goal, acceptance criteria, constraints, and non-goals
before it acts.

### 6. Let environmental verification decide when to stop

The completion claim is not evidence. A coding agent should iterate through
implement, check, diagnose, and repair until independent tests, types, lint, scope,
and acceptance checks pass. Hidden or held-out tests are essential against shallow
checks and reward hacking. A separate reviewer should judge artifacts and test
results, not inherit the producer's private reasoning.

### 7. Engineer every failure class as a closed loop

Classify API, tool, context, and control-flow failures. Decide whether retrying can
help, apply bounded exponential retry only to retryable faults, detect repeated-call
fingerprints, enforce idle watchdogs on streams, degrade or switch models when safe,
and surface failure only after recovery options are exhausted. Every failure class
needs detection, recovery, handoff, and termination.

### 8. Constrain both outcome and process

Passing tests does not justify deleting tests, escaping the workspace, exposing
credentials, or using destructive shortcuts. Separate data, input-trust, output-
impact, and cross-session boundaries. Default-disable network and destructive
operations, isolate execution, redact before storage, require precise approvals,
and retain reliable rollback.

### 9. Build for asynchronous human collaboration

Long-running agents need event streams, wake-ups, safe points, cancellation,
preemption, replay, and explicit state. Users should be able to steer without
waiting for a turn to finish. Slow work must not make the interface look dead.

### 10. Evaluate the system, not anecdotes

Measure outcome, process, and expression separately. Use reproducible task
environments, held-out verifiers, failure attribution at the first bad decision,
trajectory-prefix boundary tests, retention tests, repeated runs, confidence
intervals, and cost per passed task. Every major feature needs an independent
ablation. Observability must connect traces, tokens, tools, latency, cost, errors,
and final environmental state.

### 11. Learn only from verified, privacy-safe evidence

Operational traces become learning signals only after outcome and process
verification. Improvements can be encoded as knowledge, instructions/skills,
programs, or model parameters, in that order of reversibility. Every candidate needs
provenance, a boundary set, a retention set, an A/B gate, rollback, and privacy
sanitization. Never let self-reflection alone write durable policy.

### 12. Add multiple agents only for information gain

Parallel agents help when they contribute independent information, tools,
permissions, or execution—not merely more copies of the same reasoning. Isolate
contexts, use structured handoffs, independent validation, worktree isolation,
optimistic locking, explicit budgets, cancellation, and a deterministic merge gate.

## Weighted scorecard

| Category | Weight | Maturity | Points | Current judgment |
|---|---:|---:|---:|---|
| Architecture and composability | 8 | 3.25 | 6.5 | Strong ADK-first seams and swappable factory; Pi topology remains fixed despite generic graph schema |
| Task understanding and workflow governance | 8 | 2.25 | 4.5 | Typed goal/criteria and durable ledger; weak explicit planning and broken no-progress escalation |
| Context and token economy | 10 | 2.5 | 6.3 | Stable prefix, bounded packet, compaction; operational repair token blow-up and no live ablation proof |
| Repository understanding and retrieval | 8 | 3.25 | 6.5 | Excellent bounded FFF path plus incremental structure; optional semantics and representative performance unproven |
| Tool interface, editing, and feedback | 10 | 2.75 | 6.9 | Four tools, atomic edits, artifacts; no persistent terminal or automatic post-write syntax feedback |
| Verification, correction, and completion integrity | 14 | 1.75 | 6.1 | Solid mechanism, but model-supplied evidence plus syntax/diff can falsely pass behavioral work |
| Safety, isolation, and trust boundaries | 12 | 2.75 | 8.3 | Strong policy, receipts, redaction, remote/container adapters; local default is not an OS boundary |
| Durability, recovery, and asynchronous control | 10 | 2.5 | 6.3 | Durable replay and steering are real; no model idle deadline/failover and cancellation cleanup has leaked |
| Observability, evaluation, and economics | 8 | 2.5 | 5.0 | Rich schemas and tests; tool metrics are unwired and live evaluation is small/single-run |
| Memory, skills, and continual evolution | 7 | 2.5 | 4.4 | Guarded verified-only pipeline exists; learned skills encode shallow action labels and depend on a permissive verifier |
| Model/provider readiness | 3 | 2.0 | 1.5 | Gemini and OpenAI-compatible adapters plus model identity; no capability negotiation, effective-setting report, or fallback |
| Multi-agent and parallel execution | 2 | 1.0 | 0.5 | Reviewer seam and schema exist; Pi rejects parallel topology and shared-workspace runs are serialized |
| **Total** | **100** |  | **63** | **Strong research beta; not SOTA-proven** |

## Detailed checklist rubric

The checkboxes below describe the target. `x` means demonstrated, `~` means partial
or contradicted by operational evidence, and a blank box means absent or not yet
demonstrated.

### A. Architecture and composability — 8 points

- [x] A1. Core models, context, tools, verification, and state remain importable
  without cloud credentials.
- [x] A2. ADK owns model execution, sessions, events, caching, compaction, and resume;
  the harness does not create a competing model runtime.
- [x] A3. Strict, versioned configuration rejects unknown fields, arbitrary imports,
  invalid references, unreachable nodes, and completion paths without verification.
- [x] A4. A stable transport contract lets the TUI survive a harness factory swap.
- [~] A5. YAML can alter the complete behavior of the selected harness. The schema
  describes nodes including `parallel`, but `pi_coding_v1` compares the graph to one
  fixed literal and rejects changes.
- [x] A6. A materially different harness can be registered behind the same server,
  event, and control interfaces.
- [~] A7. There is one authoritative implementation per concern. Legacy and managed
  tool/policy paths coexist, increasing audit and divergence risk.

Evidence: `app/agent/factory.py`, `harness/config/models.py`,
`harness/agent/contracts.py`, `tests/unit/test_harness_factory.py`, and
`tests/unit/test_harness_config.py`.

### B. Task understanding and workflow governance — 8 points

- [x] B1. The request carries goal, acceptance criteria, constraints, non-goals,
  path scope, verification requirements, and budgets.
- [x] B2. The durable ledger retains the goal, criteria, progress, decisions,
  blockers, paths, validations, and next action across rounds.
- [~] B3. Ambiguity produces targeted clarification before consequential edits.
  A blocked response is supported, but there is no deterministic ambiguity gate.
- [ ] B4. Complex tasks produce a reviewable design/plan and optional approval;
  trivial tasks automatically use a smaller workflow.
- [~] B5. The workflow tracks plan steps and evidence. The models exist, but the live
  initializer does not populate a plan.
- [~] B6. Repeated action fingerprints drive replan and human handoff. The helper is
  tested but not called by the live workflow; generic no-progress resets on replan,
  making the higher human threshold effectively unreachable.
- [x] B7. Iteration limits give the workflow a hard termination bound.
- [x] B8. New user steering crosses a completion fence before a run can finish.

Required level-4 evidence: a task set sliced by ambiguity and complexity showing
clarification precision, plan usefulness, iteration count, and no over-planning on
small changes.

### C. Context and token economy — 10 points

- [x] C1. Static instructions and tool declarations are deterministic and hashed.
- [x] C2. Volatile ledger, repository state, recent events, skills, compaction, and
  steering are rendered in a bounded suffix.
- [x] C3. Reads have line ranges and line numbers; outputs preserve head/tail context
  and spill full redacted content to recoverable artifacts.
- [x] C4. Search results are grouped, ranked, bounded, and cursor-paginated.
- [x] C5. Compaction preserves goal, constraints, progress, failures, verification,
  file focus, and artifact identifiers.
- [~] C6. Token accounting uses provider tokenization. Current planning relies in
  part on a four-characters-per-token estimate.
- [~] C7. Duplicate full-file reads and repeated tool results are deduplicated before
  model input. The local evaluation recorded 469,838 aggregate input tokens in one
  repair sequence.
- [ ] C8. Context budgets include enforced per-call, per-task, and repair-loop limits
  with a graceful handoff when exhausted.
- [~] C9. Cache, compaction, programmatic routing, reviewer, skills, and retrieval
  ablations show reduced cost per passed task on representative work.
- [~] C10. High-volume exploration can be context-isolated. The swappable harness
  interface could support it, but the active Pi loop has one coding context.

Level-4 gate: no pass-rate regression, stable prefix on at least 95% of non-boundary
calls, at least 2× less context than baseline, and bounded p95 tokens per passed task.

### D. Repository understanding and retrieval — 8 points

- [x] D1. Manifest discovery reports revision, branch, dirty state, languages,
  tracked-file count, and canonical build/test commands.
- [x] D2. Scoped `AGENTS.md` and override files load deterministically.
- [x] D3. FFF is vendored/locked, workspace-confined, git-aware, refreshed after
  mutation, and exposes durable cursors.
- [x] D4. The FFF-versus-rg fixture proves complete safe pagination and better first-
  window relevance/context bytes on the 33-hit noise case.
- [x] D5. The incremental Python/TypeScript index records symbols, imports, calls,
  parser provenance, stale generations, and bounded signatures rather than bodies.
- [x] D6. Repository ranking incorporates lexical match, changed/recent paths,
  tests, and centrality.
- [~] D7. Semantic/LSP/Moderne providers are read-only, fingerprinted contracts but
  are disabled and have no live quality ablation.
- [~] D8. Representative large-repository latency and cost are published across
  cold/warm index states and developer machines.

Level-4 gate: improve pass rate, cost per pass, files read, or time to first correct
edit on PR-derived large-repository tasks; index size alone earns no credit.

### E. Tool interface, editing, and feedback — 10 points

- [x] E1. The model sees exactly `read`, `bash`, `edit`, and `write`.
- [x] E2. Tool declarations are short, typed, bounded, and match runtime arguments.
- [x] E3. `read` returns canonical paths, line numbers, hashes, pagination hints,
  binary rejection, and bounded content.
- [x] E4. `edit` requires one exact unique preimage and fails atomically on zero or
  multiple matches.
- [x] E5. `write` and `edit` support optimistic hashes, idempotency, diff feedback,
  and replay receipts.
- [x] E6. Shell results return status, exit code, duration, redacted bounded output,
  and an artifact handle when truncated.
- [~] E7. Blocked commands provide a mechanically useful safer rewrite. They explain
  risk and approval but do not reliably steer local models away from repeated
  compound-command attempts.
- [ ] E8. The default terminal is persistent across commands while isolated terminals
  remain available for parallel work.
- [ ] E9. Successful writes trigger immediate language-appropriate syntax/lint
  feedback in the same tool result.
- [ ] E10. Large edits can use an efficient boundary-matching or patch primitive
  without expanding the visible tool surface.

Level-4 gate: low failed-edit rate across model families, no tool-schema/argument
drift, and lower tool calls and bytes per passed task than a shell-only baseline.

### F. Verification, correction, and completion integrity — 14 points

- [x] F1. Every graph path to `finish` is statically required to pass through a
  verification node.
- [x] F2. Validation discovers syntax, adjacent tests, lint, type checking, broader
  tests, and `git diff --check` from repository evidence.
- [x] F3. Task-supplied deterministic verification commands are supported.
- [x] F4. Allowed and forbidden path checks run independently of the model.
- [x] F5. Failed checks return structured diagnostics and route back to repair.
- [~] F6. Acceptance evidence is independently validated. Today any non-empty
  model-supplied evidence string satisfies a criterion when the discovered commands
  pass; evidence is not linked to an executed command, artifact, path hash, or result.
- [ ] F7. Behavioral tasks cannot pass on syntax plus diff checks alone.
- [x] F8. Eval cases can protect held-out tests, scope, revisions, and explicit
  verification commands.
- [~] F9. A representative end-to-end suite proves completion integrity. The local
  10-task suite passed 60%, and held-out checks caught semantic defects after the
  harness had accepted completion.
- [~] F10. A separate reviewer assesses the final artifact without producer context.
  The optional reviewer is isolated but advisory and disabled by default.
- [ ] F11. Verification has adversarial process checks for deleting/weakening tests,
  replacing implementations with constants, or otherwise gaming acceptance.
- [~] F12. Each failure has a first-error attribution and becomes a trajectory-prefix
  boundary regression, not only an end-to-end anecdote.

Level-4 gate: at least 90% held-out pass rate on the target task distribution, zero
false-positive completions, zero held-out modifications, and stable consecutive-pass
reliability over repeated runs.

### G. Safety, isolation, and trust boundaries — 12 points

- [x] G1. File tools reject traversal, symlink escape, unsupported URI schemes, and
  cross-task artifact access.
- [x] G2. Destructive actions deny by default; network, dependency, Git history,
  publishing, deployment, and unknown commands require exact approval or opt-in.
- [x] G3. Shell control operators and pipelines are segmented before classification.
- [x] G4. Approval fingerprints and mutation receipts constrain authorization and
  replay to exact operations.
- [x] G5. Secrets are redacted before bounding, persistence, artifacts, traces, and
  model-visible output.
- [x] G6. Docker, Kubernetes, and remote adapters fail closed and can enforce network
  and resource isolation.
- [~] G7. The default local adapter is an enforceable OS sandbox. It intentionally is
  not; shell commands may access host paths permitted by the user account.
- [~] G8. Shell policy uses semantic effects rather than executable/argument pattern
  classification. Current parsing is substantially better than a keyword blacklist
  but remains static and cannot infer arbitrary script behavior.
- [~] G9. Repository instructions and skills are treated as untrusted data until an
  explicit project-trust decision. Roots are validated and hashed, but project-local
  loading is enabled by default.
- [x] G10. Resume validates workspace identity and content fingerprints, and cleanup
  refuses dirty worktrees without explicit force.
- [x] G11. Production guidance separates workspace, artifacts, credentials, network,
  and external side effects.
- [~] G12. The full required adversarial suite runs in CI, including prompt injection
  and exfiltration through package scripts. Many primitives are tested, but the
  documented full matrix is not represented end to end.

Level-4 gate: production deployments default to an enforceable container/remote
boundary and pass the complete adversarial matrix with no ambient credentials.

### H. Durability, recovery, and asynchronous control — 10 points

- [x] H1. Task events are append-only, replayable, and project into a deterministic
  ledger.
- [x] H2. Checkpoints bind task/session, base revision, workspace fingerprint, ledger
  hash/version, compaction, and parent checkpoint.
- [x] H3. Exact file mutations do not repeat after interruption.
- [x] H4. WebSocket clients can start, attach, replay from a cursor, acknowledge,
  steer, pause, cancel, heartbeat, and reconnect after backpressure.
- [x] H5. Steering is leased, acknowledged, bounded, and delivered at safe points.
- [~] H6. Cancellation awaits all ADK children, provider sessions, and subprocesses.
  The local evaluation observed leftover-task and unclosed-session warnings.
- [ ] H7. Every model stream has time-to-first-event, idle, per-call, and total-run
  deadlines with structured timeout events.
- [ ] H8. Retryable API failures use bounded backoff/jitter and can safely degrade or
  switch models using a neutral trajectory representation.
- [~] H9. A server restart resumes in-flight work automatically. Current public-run
  recovery terminalizes a previously running record rather than rerunning it.
- [~] H10. Multiple workspaces/runs execute concurrently with fault isolation. The
  coordinator holds one shared-workspace lock across each complete run.

Level-4 gate: kill/restart and cancellation fault injection at model, tool, mutation,
verification, compaction, and transport boundaries with no duplicate side effects,
leaked tasks, or silent stalls.

### I. Observability, evaluation, and economics — 8 points

- [x] I1. Model usage records tokens, cache reads/writes, reasoning, prefix identity,
  latency, and optional price.
- [~] I2. Every live tool call records arguments hash, result hash, status, duration,
  visible/omitted bytes, and replay state in `MetricsStore`. The table and API exist,
  but the live metrics plugin only handles model callbacks; the 10-task run reported
  zero tools despite visible tool events.
- [x] I3. Redacted traces cover model/tool/run lifecycle and preserve correlation and
  provenance without raw prompt/source storage.
- [x] I4. Eval schemas cover quality, context, tools, reliability, latency, and cost,
  with cost per passed task as the north-star metric.
- [x] I5. Search has a deterministic paired micro-ablation.
- [~] I6. Every major feature has completed paired ablations on representative tasks.
  Several definitions exist, but live programmatic, semantic, and full feature
  ablations remain undone.
- [~] I7. Results report repeated runs, percentiles, confidence intervals, timeout
  policy, and categorized failures. The current local run is informative but only ten
  single attempts with model changes mid-suite.
- [~] I8. Integration testing exercises the complete ADK/server/TUI/provider path.
  The deterministic integration directory currently contains one synthetic replay
  and verification scenario; most coverage is unit-level.

### J. Memory, skills, and continual evolution — 7 points

- [x] J1. Skill discovery uses trusted roots, path confinement, validation, hashes,
  progressive disclosure, and explicit-mention precedence.
- [x] J2. Candidate skills cannot silently broaden the four-tool surface, safety
  policy, or deterministic verification.
- [x] J3. Only deterministically completed tasks become learning episodes.
- [x] J4. Candidate/baseline assignment, support thresholds, non-regression metrics,
  provenance, promotion, disable, and rollback are durable.
- [x] J5. Project memory stores curated commands, decisions, and conventions only
  after verification rather than raw conversations.
- [~] J6. Learned skills encode the user's implicit workflow semantics. The current
  heuristic synthesizer converts normalized labels such as inspect/mutate/ok into
  generic procedural steps; it does not yet learn requirement framing, preferred
  sequencing, steering corrections, rationale, or scope decisions.
- [~] J7. Memory resolves contradiction, staleness, supersession, and cross-session
  retrieval with boundary/retention tests. The schema has provenance and
  `supersedes`, but the extraction/search path is deliberately narrow.
- [ ] J8. A false-positive harness completion cannot contaminate memory or skill
  promotion. This depends on fixing category F first.
- [~] J9. Operational A/B evidence demonstrates at least one learned skill improves
  cost per passed task without quality, safety, or latency regression.

### K. Model/provider readiness — 3 points

- [x] K1. Native Gemini and OpenAI-compatible models use ADK adapters behind a
  closed provider registry.
- [x] K2. Credentials are environment references, not serialized configuration.
- [x] K3. The TUI receives allowlisted coding-model identity and distinguishes
  adapter initialization from a real model response.
- [~] K4. Requested reasoning, tool-calling, context, streaming, cache, and usage
  capabilities are negotiated and the effective settings are reported. The adapter
  forwards `reasoning_effort`, but the local run showed reasoning tokens even when
  `none` was requested.
- [ ] K5. The live Gemini credentialed workflow passes the core fail-to-pass suite.
- [ ] K6. Provider fallback and cross-provider handover are tested on an unfinished
  tool trajectory.

### L. Multi-agent and parallel execution — 2 points

- [~] L1. A separate final reviewer can run with isolated context and no mutation
  tools.
- [ ] L2. The selected Pi harness can compose parallel ADK agents from YAML. A
  `ParallelNode` schema exists, but the Pi factory rejects that topology.
- [ ] L3. Parallel workers receive isolated worktrees, namespaces, budgets, and
  structured acceptance contracts, followed by deterministic merge validation.
- [ ] L4. Faults remain local to a parallel batch; one failed child does not erase
  successful independent results.
- [ ] L5. Diverse reviewers or deterministic evidence address homogeneous common-
  cause errors.

Multi-agent support is intentionally low-weight. It should be added only after an
ablation shows independent information gain greater than its token and coordination
cost.

## Highest-priority findings

### P0 — Completion evidence is not bound to environmental facts

`build_report()` considers a criterion satisfied when its model-supplied evidence
list is non-empty and every discovered command passes. For a new Python file in a
repository without a discovered test command, that can mean only `py_compile` plus
`git diff --check`. This exactly matches the false-positive completions observed in
the held-out local evaluation.

Required change:

- represent evidence as typed references to executed validation results, file/diff
  hashes, or trusted artifacts;
- require at least one behavioral verifier for behavioral acceptance criteria;
- reject syntax-only completion unless the task is explicitly syntax-only;
- run hidden fail-to-pass and retention checks outside the model's writable scope;
- add a regression built from each false-positive task in the local evaluation.

### P0 — Model liveness has no deadline or recovery policy

The server iterates ADK events directly. It limits the number of LLM calls but does
not enforce time to first event, idle time, or total call duration. Four local-model
tasks stalled for five to nine minutes without producing an artifact.

Required change:

- add configured first-event, idle, per-call, and total-run deadlines;
- emit durable progress and timeout events;
- classify timeout versus provider error versus cancellation;
- retry only retryable failures with bounded jitter;
- optionally route to a declared fallback model after a circuit breaker;
- prove no provider client/session leak after timeout or cancellation.

### P1 — Tool telemetry is a schema without live wiring

`MetricsStore` can record tools, but `HarnessMetricsPlugin` implements model callbacks
only. Trace callbacks observe tools, yet they do not project those samples into the
metrics store. Consequently, the operational report showed `tool_calls: 0` despite
many tool events.

Required change: record one idempotent `ToolUsageSample` from the managed tool or ADK
callbacks and reconcile it against trace/event counts in a deterministic test.

### P1 — No-progress detection is disconnected

`register_action()` computes repeated tool fingerprints, but no live caller invokes
it. The workflow instead increments `no_progress_count` when an `AgentStep` contains
no progress text. At the replan threshold, `replan_ledger()` resets the counter, so
the later human-handoff threshold cannot be reached through repeated no-progress
cycles.

Required change: project actual tool fingerprints/results into the ledger, maintain
separate counters per recovery path, and test `repeat → replan → repeat → handoff`.

### P1 — YAML topology is descriptive but not executable for Pi

The generic schema validates route and parallel nodes, but `PiCodingHarnessFactory`
requires exact equality with one literal graph. This is safe and makes new harness
registration possible, but it does not satisfy “change the harness behavior entirely
through YAML.”

Required decision: either narrow the public schema to honest Pi-tunable behavior, or
implement a closed node-builder registry that compiles validated YAML into ADK
workflow nodes without arbitrary imports.

### P1 — Context efficiency is not bounded operationally

The packet is individually bounded, but aggregate task input can still grow without
a cost/token circuit breaker. Compaction and prompt-cache configuration did not
prevent a 469,838-input-token repair episode on a small file.

Required change: add per-call and per-task token budgets, content-hash deduplication,
delta file context, repeated-read suppression, and task-level fallback/handoff.

### P1 — Cancellation and provider cleanup need fault-injection tests

The deterministic coordinator test closes a fake execution correctly, but the real
local run logged leftover workflow tasks and an unclosed client session. Cancellation
is documented as best effort for synchronous subprocesses.

Required change: test real ADK generator closure, LiteLLM client closure, cancellation
during inference/tool/verification, and process-group cleanup.

### P2 — Local developer execution is not a security boundary

The local adapter narrows environment variables and applies resource limits, but it
runs a host shell under the user's account. Production adapters exist; the installer
and server should make the active isolation level unmistakable and production mode
should refuse the local backend.

### P2 — Learned workflow semantics are too shallow

The learning control plane is conservative and well structured, but the synthesizer
learns sequences of normalized tool categories, not the user's implicit decisions.
It should incorporate verified steering corrections, requirement clarification,
plan structure, scope choices, command preferences, and failure/recovery patterns—
still in redacted form and still behind boundary/retention trials.

## Recommended execution order

1. **Completion integrity:** typed evidence, behavioral-verifier requirement,
   false-positive regression cases.
2. **Liveness and cleanup:** model deadlines, timeout taxonomy, provider fallback,
   cancellation fault injection.
3. **Telemetry correctness:** wire tool samples and reconcile traces, events, and
   metrics.
4. **Progress control:** connect tool fingerprints, recovery counters, circuit
   breakers, and human handoff.
5. **Token economy:** task budgets, deduplication, delta context, live paired
   ablations.
6. **Configuration honesty:** execute the generic graph safely or expose only what Pi
   actually supports.
7. **Learning quality:** richer privacy-safe workflow signals after verification is
   trustworthy.
8. **Optional parallelism:** only after a controlled task slice proves information
   gain and safe merge behavior.

## Release rubric

The harness may call itself production-capable only when all boxes below are checked:

- [ ] Zero false-positive completion across the core and held-out semantic suites.
- [ ] At least 90% pass rate on a representative PR-derived task distribution.
- [ ] Pass-consecutive reliability is reported over repeated runs, not only Pass@1.
- [ ] Every model call has first-event, idle, and total deadlines.
- [ ] Kill/cancel/restart fault injection produces no leaked tasks, sessions, or
  duplicate side effects.
- [ ] Live tool/event/trace counts reconcile exactly or explain intentional sampling.
- [ ] Cost per passed task, p50/p90/p95 latency, cache ratio, uncached tokens, and
  failed-edit rate are published for every supported provider configuration.
- [ ] Stable prefix remains unchanged on at least 95% of non-boundary calls.
- [ ] Context is at least 2× lower than the stock baseline without meaningful pass-
  rate regression.
- [ ] Every enabled major feature has an independent ablation and rollback switch.
- [ ] The complete adversarial safety matrix passes in an enforceable production
  sandbox with network denied and no ambient credentials.
- [ ] Learned skills have provenance, boundary and retention evidence, paired trial
  support, promotion criteria, and automatic rollback.
- [ ] A live Gemini/ADK fail-to-pass run succeeds without modifying held-out tests.
- [ ] Documentation states the effective model, reasoning mode, provider capability,
  sandbox level, state root, and all control-plane paths.

## Validation performed for this audit

At revision `843007c`:

- Python compilation: passed.
- Ruff over `app`, `harness`, and `tests`: passed.
- Pyright over `app` and `harness`: passed with zero errors and warnings.
- Unit suite: passed; 481 unit/integration test cases were collected across the two
  directories.
- Integration suite: passed; the integration directory currently contains one
  synthetic interruption/replay/verification scenario.
- Operational evidence: the 2026-08-28/29 local-model TUI report recorded 6/10
  held-out tasks passing.

The unit suite emitted warnings for experimental ADK agent configuration, event
compaction, resumability, and JSON-schema function declarations. These are not test
failures, but release qualification should pin the tested ADK minor and rerun the
live workflow before upgrades.

## Evidence index

- Architecture and fixed topology: `app/agent/factory.py:155`,
  `harness/config/models.py:133`
- Context compiler: `harness/context/compiler.py:113`
- Managed four-tool adapter: `harness/tools/adk_adapter/__init__.py:241`
- Atomic file mutations: `harness/environment/local.py:80`
- Verification discovery and evidence: `harness/verification/discovery.py:57`,
  `harness/verification/runner.py:106`
- No-progress logic: `harness/state/progress.py:25`,
  `harness/orchestration/core.py:78`
- ADK event streaming and cancellation: `harness/server/runtime.py:135`,
  `harness/server/runtime.py:605`
- Model-only metrics callbacks: `harness/telemetry/adk_plugin.py:172`
- Tool metrics schema: `harness/telemetry/metrics.py:43`
- Search and structural repository map: `harness/repo/fff_search.py:268`,
  `harness/repo/index.py:831`
- Trace-derived skill synthesis: `harness/learning/skills.py:29`
- Local operational evaluation:
  `.artifacts/local-model-eval-20260828/results/FINAL_REPORT.md`
