# Implementation status

The supported capability boundary is the simplified local harness, not the
historical feature checklist or book-rubric score.

## Retained and verified

- Strict YAML behavior, explicit workspace/state/trust identity, closed harness
  and ADK model-provider registries.
- One ADK coding worker with four tools; bounded context, stable prefix hashes,
  structural repository maps, and native FFF discovery.
- Trusted directory skills and redacted interaction traces.
- Atomic confined file mutations, replay receipts, command approvals, and
  local/Docker command execution.
- Deterministic completion verification sharing the configured sandbox and
  task-scoped approvals.
- Local task events, SQLite checkpoints/steering/metrics/run registry,
  SQLite or in-memory ADK sessions, and local or in-memory artifacts.
- WebSocket/AG-UI transport, replay, steering, cancellation, and Bubble Tea TUI.
- Pi harness public-output opt-in: structured worker text stays internal; the workflow
  publishes prose and a compact outcome explicitly. Coding completion replies are
  withheld until deterministic verification passes. Other ADK factories keep their
  ordinary text streaming by default.
- Standalone Pi terminal toolkit prototype (not yet the default installed client).
- Gemini and Codex subscription adapters.
- Fresh uv checkout installation and default TUI build; no Magnitude requirement.

## Removed

Magnitude/LiteLLM, remote/Kubernetes execution, cloud/distributed state, duplicate
bootstrap/model/context contracts, fake graph configuration, advisory reviewer,
automatic skill learning/trials/promotion, project-memory injection, disconnected
semantic-intelligence scaffolding, and comparison-only report CLIs.

These removals are intentional simplification, not claims that the features were
successfully integrated. Old source and historical reports remain available in Git.

## Validation

See [the measured cleanup report](simplification.md) for test results and exact
source/complexity measurements. Tests exercise fake model streams and real local
state/tools; this change does not claim fresh live-provider or model-quality results.

Remaining limitations include the host-local trust boundary, single-process state
ownership, experimental ADK APIs, and the still-complex server run controller.
