# Pi terminal migration

Goal: match Pi's CLI/TUI interaction for conversational and coding tasks while
retaining the configurable ADK harness. The terminal must not run Pi's agent loop,
execute tools itself, or own provider credentials.

## Delivery gates

1. Prove the pinned standalone Pi terminal toolkit can render a small remote-session
   interface: editor, Markdown, compact tools, expansion, Unicode, resize and history.
2. Separate human replies, activity, and internal workflow control. Support ordinary
   conversation without forcing code changes; preserve deterministic coding verification.
3. Preserve threads across turns and reconnects. Wire steering, queued follow-ups,
   cancellation, model selection and authentication through authenticated controls.
4. Complete the Pi-style client, capability-aware commands, resource discovery and
   launcher/installer integration. No dependence on a developer's Pi checkout.
5. Compare representative prompts and terminal behavior, exercise replay and a second
   harness, run regressions, remove the superseded Go client and document state/log paths.

Commit each verified coherent unit separately. Keep the old client until the new one
passes the migration gates; do not claim full parity from rendering fixtures alone.

The initial reference is local Pi 0.84.4 (commit 853a80d26). Runtime reuse is through
the MIT-licensed `@earendil-works/pi-tui` package pinned in the client lockfile, not
copied Pi AgentSession or InteractiveMode implementation.

## Prototype evidence

`npm test --prefix clients/terminal` passes four focused tests using the actual Pi
editor and renderers: read collapse/expansion, bash previews/errors, Markdown with
terminal-cell width checks at 20/40/80/120 columns, input history and bracketed paste.
The rendering-only demo was also run in a real PTY: `/tools` showed a one-line read,
Ctrl+O exposed its 30 lines, and Ctrl+D restored the terminal and exited successfully.
This passes the toolkit-reuse gate, not the server integration or full UX gates.
