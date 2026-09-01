# Implementation status

The supported capability boundary is the simplified local harness, not the
historical feature checklist or book-rubric score.

## Retained and verified

- Versioned YAML worker prompt with content-sensitive behavior hashes; fixed
  agent schema, tool surface, safety outcomes, Docker network isolation, and memory
  implementation remain code-owned invariants rather than decorative YAML.
- Strict YAML behavior, explicit workspace/state/trust identity, closed harness
  and ADK model-provider registries.
- One ADK coding worker with four tools; bounded context, stable prefix hashes,
  compact repository manifests, and native FFF discovery.
- Trusted directory skills and redacted interaction traces.
- Atomic confined file mutations, replay receipts, command approvals, and
  local/Docker command execution.
- Deterministic completion verification sharing the configured sandbox and
  task-scoped approvals.
- Local task events, SQLite checkpoints/steering/metrics/run registry,
  SQLite or in-memory ADK sessions, and local or in-memory artifacts.
- WebSocket/AG-UI transport, replay, steering, cancellation, and Pi-toolkit terminal.
- Pi harness public-output opt-in: structured worker text stays internal; the workflow
  publishes prose and a compact outcome explicitly. Coding completion replies are
  withheld until deterministic verification passes. Other ADK factories keep their
  ordinary text streaming by default.
- Eligible conversational replies stream through ADK callbacks after validation of
  a complete control header. Partial words and potentially sensitive spans are held
  for redaction; raw workflow JSON never reaches the terminal. Coding replies still
  wait for verification. Legacy JSON responses remain supported without streaming.
  A live ADK Runner/production WebSocket/scripted-model test reconnects a fresh Pi
  client mid-reply without duplicating text or model execution.
- Standalone Pi terminal toolkit client with authenticated transport, multi-turn
  conversations and replay, tested against the real server/ADK stack with a scripted
  model and now installed by default.
- Server-owned durable follow-ups, bounded conversation queries, queue continuation
  and removal controls; the terminal catches up through successor runs in order.
- Read-only public transcript pages with a stable snapshot cursor, event/byte
  limits, redaction and ownership checks; reads never attach or execute a run.
- Pi-style `/resume` selector, `/history` paging and `/session` identity view.
  Restoring history reuses the live reducer and catches up an active run without
  starting it again. Stopped work and pending queues require explicit continuation.
- Authenticated server-owned provider login/status/cancel/logout controls. OAuth
  workers never block the server event loop; credential tokens remain server-side.
- Pi-style terminal `/login`, `/auth` and confirmed `/logout` dialogs use those
  controls, preserve editor drafts and disclose server credential storage paths.
- Searchable `/model` with background catalog refresh, per-conversation selection
  and explicit saved defaults. The server freezes each run's choice; selection
  changes the next ADK turn without altering active work or replaying mutations.
- Server-owned, bounded resource metadata: `/resources` discloses workspace/state/
  configuration paths and trust; `/skills` and `/skill:NAME` use the runtime's trusted
  directory loader. Ctrl+O shows metadata, not file bodies. Actual selected skill
  names arrive before model execution and are distinct from available resources.
- Pi terminal command approvals: deterministic policy auto-executes recognized local
  read, build, test, and workspace mutations. Dependency, network, Git-history,
  publish/deploy, and unknown operations require exact task-scoped decisions;
  destructive commands remain denied. Worker and verification checks wait asynchronously
  for those decisions. Deny is the dialog default; deferred requests
  remain visible through `/approvals`. Cancellation, expiration, reconnect and
  uncertain decision retries are tested against the real server/ADK/tool stack.
  Approval dialogs accept explicit A/Y approve and D/N deny shortcuts while keeping
  denial as the Enter default. The Pi terminal is the installed client; the superseded
  Go client was removed.
- Plain-language turns support a distinct `answered` outcome without inventing a
  coding task. Direct answers permit no managed shell/write/edit action and no
  explicit verification obligation; coding results still route through verification.
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

See [the measured cleanup report](simplification.md) for exact source/complexity
measurements and [the Pi terminal migration record](design/pi-terminal-migration.md)
for deterministic and live-provider evidence. The live sample is an acceptance set,
not a model-quality benchmark.

Remaining limitations include the host-local trust boundary, single-process state
ownership, experimental ADK APIs, and the still-complex server run controller.
