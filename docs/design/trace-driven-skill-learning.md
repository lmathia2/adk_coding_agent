# Trace-driven skill learning

## Objective

The harness records how a task moves through users, agents, models, and tools, then
uses repeated successful traces to propose reusable Agent Skills. Learning is a
guarded control loop: it may improve the volatile work packet, but it cannot change
the coding worker, its four-tool surface, verification policy, permissions, or the
cache-stable instruction prefix.

## Trace contract

Each interaction is an immutable span with a task, invocation, parent, component,
operation, outcome, timestamps, content hashes, and a bounded JSON projection. The
SQLite sequence is the canonical ordering. Writes accept idempotency keys so ADK
resume and callback retries cannot duplicate observations.

Trace modes are `off`, `metadata`, and `redacted`, with `metadata` as the default.
There is deliberately no raw or unredacted mode. Secret-like values and sensitive keys are redacted before storage;
oversized values are truncated with their omitted byte count retained. The store can
be queried by task and exported as JSONL without weakening those protections.

ADK lifecycle callbacks cover run, user message, agent, model, and tool boundaries,
including success and error outcomes. Harness workflow events provide the durable
task and verification facts used by learning.

## Skill contract

Skills use the Agent Skills directory shape:

```text
<skill-root>/<skill-name>/SKILL.md
```

`SKILL.md` starts with YAML frontmatter containing at least `name` and
`description`. Harness lifecycle metadata adds `status`, `version`, and optional
`source_trace_ids`. Status is one of `enabled`, `candidate`, or `disabled`.

Discovery is deterministic and confined to configured roots. Symlinks that escape a
root are rejected, duplicate names are rejected, root precedence orders distinct
skills, file and content sizes are bounded, and hashes identify the exact selected revision. Only the compact
catalog is indexed eagerly. Explicit `$skill-name` references win; otherwise a
bounded lexical matcher selects the top enabled skills. Full skill bodies enter only
the volatile work packet, and referenced resources are not loaded implicitly.

## Learning and quality gates

A workflow episode is eligible only when deterministic verification passed. The
learner stores a privacy-safe fingerprint, normalized action sequence, outcome and
cost/latency/context metrics. It never treats model completion claims as success.

Repeated eligible patterns may synthesize a candidate skill. Synthesis is pluggable,
but the baseline implementation is conservative and local: it writes only procedural
steps inferred from normalized action kinds and provenance trace IDs. It does not
copy prompts, tool payload bodies, model responses, source code, secrets, or commands
that broaden permissions.

Candidate assignment is deterministic for a task ID, and selected bodies and hashes
are pinned in resumable task state. Candidate and baseline trials
are compared on verification pass rate first, then bounded efficiency metrics such
as model/tool calls, input context, wall time, and cost when available. Promotion
requires minimum support and no configured regression. A promoted skill is disabled
automatically when subsequent evidence crosses the rollback threshold. Blocked
terminal tasks count as failed trials, and policy-blocked tool attempts make an
episode ineligible for synthesis. Every
observation, trial, lifecycle transition, and generated revision is idempotent and
auditable.

## Runtime flow

1. The trace plugin observes ADK callbacks without mutating prompts or responses.
2. At task initialization, the registry matches enabled skills and any assigned
   candidate, pins that selection for resume, then renders a bounded dynamic `SKILLS`
   section for each iteration.
3. Deterministic verification remains the sole completion authority.
4. After a verified task, the learner records an episode and trial outcome, evaluates
   promotion or rollback, and may atomically create a new candidate.
5. Learning failures are recorded but do not turn a correctly verified coding task
   into a failure.

## Operational controls

- Tracing can be disabled or reduced to metadata without changing the worker prompt.
- Additional trusted skill roots can be configured explicitly.
- Learning and candidate trials can be disabled independently.
- Candidate/enabled/disabled status is visible on disk and in the learning ledger,
  allowing operators to inspect or roll back a skill without deleting its history.
- Trace retention and database backup are deployment concerns; exports remain
  redacted and bounded by construction.
