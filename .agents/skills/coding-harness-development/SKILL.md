---
name: coding-harness-development
description: Implements and reviews changes to this Pi-inspired Google ADK coding harness. Use for harness orchestration, context management, coding tools, repository indexing, durable state, verification, safety, evaluation, and documentation changes.
license: Apache-2.0
compatibility: Python 3.12+, Google ADK 2.x, Google Agents CLI, Git, uv
metadata:
  upstream-workflow: google/agents-cli skills
  project: adk_coding_agent
---

# Coding Harness Development

Use this workflow for every non-trivial change to the harness.

## 1. Establish the contract

Read, in order:

1. `docs/IMPLEMENTATION_STATUS.md`
2. `docs/TODO.md`
3. `docs/architecture.md`
4. the typed model associated with the requested capability
5. the focused unit and integration tests

State the smallest independently testable todo. Do not combine a prompt change, tool change, model change, and evaluation change in one experiment.

## 2. Preserve the architecture

The model-visible core remains:

- one cache-stable coding worker
- `read`
- `bash`
- `edit`
- `write`

Keep mutable task state in the dynamic node input, not `static_instruction`. Put deterministic control flow in ADK `Workflow` and `@node` functions. Use `ctx.run_node` for bounded model work and verification nodes.

Do not add a model-visible tool when a CLI, skill, deterministic node, or artifact reference is sufficient.

## 3. Implement outside the model first

Prefer deterministic code for:

- token budgeting and context ordering
- event reduction
- workspace selection
- command authorization
- path confinement
- output truncation
- secret redaction
- test selection
- acceptance-criterion evidence
- completion decisions

Use an LLM only when semantic judgment is necessary.

## 4. Make long-running behavior replayable

For a state-changing operation:

1. define its event or receipt contract;
2. make the operation idempotent;
3. persist enough information to resume;
4. couple conversational state to a workspace fingerprint;
5. add an interruption or replay test.

Never assume an ADK tool executes exactly once.

## 5. Keep context bounded

When adding model context:

- identify whether it belongs in the stable prefix or dynamic suffix;
- set a token or byte budget;
- define deterministic ordering;
- store full data as an artifact when only a summary is needed;
- record the reason for any stable-prefix mutation;
- add telemetry for cached and uncached input.

Skills use progressive disclosure: keep the description specific and load detailed references only for matching work.

## 6. Verify completion independently

A model `done` claim routes to verification. Every completed task must have:

- passing required commands;
- no scope violation;
- `git diff --check` success;
- explicit evidence for each acceptance criterion.

Do not weaken held-out tests or treat a final response as proof.

## 7. Test in layers

Run the focused test first, followed by:

```bash
uv run python -m compileall -q app harness tests
uv run pytest -q tests/unit
uv run pytest -q tests/integration
uv run ruff check app harness tests
uv run pyright app harness
```

For ADK API changes, ensure `tests/unit/test_adk_app.py` imports the live entrypoint with the pinned ADK version.

## 8. Evaluate the change

When behavior or context changes, add or run an evaluation ablation. Report at least:

- pass rate
- cost per passed task
- uncached input tokens
- cache-read ratio
- prefix versions
- tool calls
- wall time

Do not claim an optimization from token count alone.

## 9. Commit the completed todo

After the focused checks pass:

1. update `docs/IMPLEMENTATION_STATUS.md` when the capability boundary changed;
2. commit the todo independently with a descriptive message;
3. keep unrelated work out of that commit;
4. continue to the next todo only after the previous one is durable and testable.

## Reference documents

- Architecture: `docs/architecture.md`
- Security: `docs/security.md`
- Evaluation: `docs/evaluation.md`
- Development: `docs/development.md`
- Design brief: `docs/design/pi-inspired-adk-coding-harness.md`
