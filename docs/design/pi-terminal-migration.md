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

## Output boundary

The assembly opts into explicit public messages. The generic ADK mapper still
streams tools and errors, but accepts prose/results only from events tagged by
the workflow. The worker's optional `message` field is published only after its
typed result is reduced; completion replies wait for passing verification.
Private partial/final JSON and child-node outputs remain in ADK history, not the
public transcript. A live ADK Runner with a scripted model verifies this contract.

## Conversation gate evidence

The real ADK Runner, real worker and real tools pass scripted greeting, read-only
explanation and adversarial write-then-answer fixtures. Greeting takes one model
call, no tools and no verification; a read can finish as `answered`, explicitly
`verified: false`. A write followed by a model `answer` is withheld and invokes
verification. Explicit coding contracts, acceptance criteria, verification strength,
completion claims and workspace changes also disallow the direct-answer path.
All shell calls conservatively require verification: command classification alone
is not proof that a shell expression has no side effects.

The full Python unit/integration suite, Ruff, Pyright and compilation pass, as do
the four terminal prototype tests. These are deterministic contract checks; fresh
live-model quality/latency comparisons remain required before migration completion.

## Conversation continuity

Sequential completed turns can reuse one authenticated WebSocket. Admission rejects
overlapping runs in the same ADK session and prevents changing its workspace/harness
binding. Task budgets, skill selection and effect markers reset for a new task but
not a same-task resume. The reset is flushed before child nodes run: pending ADK
parent-state deltas otherwise mask a child's newly written effect markers.

ADK's workflow isolation does not itself carry prior turns into the coding worker.
The harness retains that isolation and supplies up to 24 prior user/public-message
events through the existing bounded context compiler (`context.conversation_tokens`,
default 2000). It excludes private node inputs, thought content and the current
invocation. A two-turn real ADK test verifies prior conversation reaches the next
request without stale budgets/skills; no alternate session store or agent loop is added.

## Remote terminal transport

The new terminal now uses authenticated loopback WebSockets with a deterministic
view reducer, contiguous replay cursors, bounded display buffers, reconnection,
heartbeat and acknowledged steering/cancellation. The public control ID echoes
the caller's idempotency key even when a harness returns a separate internal receipt.
Internal state and workflow JSON are not transcript entries.

The cross-language integration gate builds the terminal and runs its real Node
client against Uvicorn, the production WebSocket coordinator and ADK Runner with
a scripted model. Two turns retain conversation context; a real read tool is shown
as a compact card. Rendering at 40/80/120 columns remains bounded and excludes
workflow diagnostics. This is stronger than separate fake-socket tests but is not
live-model quality or end-to-end visual parity evidence. Model/auth selectors,
durable follow-ups and launcher migration are still pending.

## Durable follow-ups

The server now advertises `sessions` controls. Conversation summaries/history are
queried from the existing run registry; follow-up and continuation receipts live
in that same SQLite database. Alt+Enter queues a new turn; Enter remains steering.
The terminal shows up to three queue previews and a count, with `/queue`,
`/queue continue` and `/queue clear` controls while full selectors are completed.

Success drains the queue in order. Blocked, failed and cancelled turns retain
pending messages. Shutdown does not silently run them; an explicit continuation
can restart the pending queue. A continuation retry remains tied to the original
item, and a crash after run creation cannot invoke that run again. User/workspace/
harness identity is checked before session access or queue dispatch.

The cross-language test now exercises three real ADK turns, with two follow-ups
queued while the first model response is gated. The terminal follows each successor
run, not merely the newest run, so fast completions cannot skip transcript entries.
Unit tests cover queue ordering, retries, cancellation, blocked/error results,
restart persistence, ownership and redacted previews. Session/model/login selectors,
full historical transcript loading and live visual/model comparisons remain pending.

## Recovery refinements

Server startup terminalizes interrupted queued/running records with a replayable
`server_restarted` error, without reinvoking models or draining pending follow-ups.
Cancellation before an asyncio task first enters also closes its allocated Runner
and terminalizes its run. Completed attachments can be released from the durable
run status even when a reconnect cursor is already past the final event. Focused
tests cover all three cases; these changes do not claim automatic task resumption
after an unknown-effect process crash.

## Shared terminal dialogs

The terminal uses Pi's Input, SelectList and fuzzy filtering for a shared inline
selector. `/help` discovers currently supported commands, `/queue` manages pending
turns, and the real Pi editor completes slash commands without scanning client
files. Selection and saving a default are separate callbacks for the upcoming
model picker. Escape closes a dialog and restores the draft before it can reach
the run-interrupt handler.

Deterministic tests cover filtering by labels rather than opaque IDs, arrow keys,
Enter/Ctrl+S, empty results, Unicode width bounds, command availability and draft
preservation. A real PTY fixture exercised `/help`, search/selection, Tab completion,
Escape and clean Ctrl+D exit. Login/model/session pickers still need their backend
actions; the selector component alone is not evidence those features work.

## Provider authentication controls

The server advertises `provider_controls` only when its local provider service is
wired. `provider.request` / `provider.result` provide status, login, cancel-login
and logout outside the run transcript. The existing Codex device flow runs in a
worker thread with cooperative cancellation. Only instructions, masked account
status and the credential path reach clients, never token values or provider
error bodies. Credentials continue to use the private atomic server-side store.

One pending login is shared across local clients. Login receipts and attempts are
bounded and process-local; clients must reconcile `/auth`, not automatically replay
authentication mutations after a disconnect or restart. Logout cancels pending
login and removes the local credential file; it neither revokes the remote account
session nor recalls an already-authorized model request. Invalid saved credentials
do not prevent inspecting status or removing the invalid cache.

Ten focused tests exercise a real credential store and synthetic HTTP transport,
including success, cancellation before token exchange, shutdown, logout retry,
ownership, secret-safe failures and an authenticated production-server socket that
still answers ping during login. The full Python unit/integration suite passes
460 tests. This does not constitute fresh live-provider authentication evidence.

## Terminal login experience

The new terminal advertises `/login`, `/auth` and `/logout` when the server supports
provider controls. A searchable selector opens an inline login dialog showing the
verification URL, code and status; Escape cancels, including when the initial reply
has not arrived. Confirmed login shows the server credential path. Logout requires
explicit confirmation. Delayed status replies cannot revive a dismissed dialog.

Authentication requests are correlated but deliberately not replayed on reconnect;
unconfirmed operations direct the user to `/auth`. A server-owned pending login can
be reattached via `/login`. The terminal never reads or writes provider credentials.
Closing the terminal requests cancellation of its active login dialog before closing
the socket, but only an acknowledged cancellation is reported as confirmed.

Twenty terminal tests pass. A cross-language test drives the actual Pi dialogs and
remote session against the production authenticated Python server, with only the
provider HTTP transport mocked. It verifies login, cancellation, logout, preserved
drafts, width bounds and zero auth messages in the conversation transcript. The
rendering-only demo's `/login` was exercised in a real PTY through selector, code
display, Escape and clean exit. Model/session selectors and installation migration
remain outstanding; these tests are not a live-account sign-in or full-parity claim.

## Model selection

The optional `model_selection` capability exposes `model.request` status/catalog/
select controls. The searchable Pi picker refreshes the catalog without blocking
typing or the server receive loop. Enter selects for the current conversation's
next turn; Ctrl+S additionally saves the local server owner's default. Active turns
retain their original model. Selection receipts persist alongside the run registry,
and retrying a prior selection cannot revert a newer acknowledged choice.

The server records model identity and effective behavior hashes at run admission,
then applies that immutable configuration through an optional harness-factory seam.
Configured models remain selectable when catalog discovery is unavailable. Discovery
currently uses the existing Codex adapter; other providers retain configured choices.
Listing/configuring a model is not evidence that it has loaded or responded.

Conversation preferences are scoped to user, workspace and harness. Shared defaults
live at `STATE_ROOT/auth/model-selection.json`, atomically replaced with mode 0600;
the CLI reads legacy `openai-codex-selection.json` only if the new file is absent.
Explicit YAML/launch model choices win unless `server.use_saved_model_default` is
enabled; an ordinary generated Codex launch enables it. Ctrl+S enables the saved
default for new conversations in the current process. Existing conversations retain
their model. No client-side provider credentials or YAML mutation is introduced.

The default file and conversation database are individually atomic, not a distributed
transaction. A process/disk failure between them can leave only the default saved.
Unconfirmed selections are never automatically replayed; reopen `/model` to reconcile
the current and saved choices. Escape during an in-flight save discloses the same
uncertainty instead of claiming that a server mutation was cancelled.

Verification: 468 Python unit/integration tests and 24 terminal tests, plus Ruff,
Pyright, compilation and diff checks. The cross-language fixture drives the actual
Pi picker → authenticated WebSocket → real ADK Runner: alpha stays active, a queued
turn uses beta with prior conversation context, and a new conversation uses saved
gamma. Reconnect reconciles the conversation model without replaying selection.
Width checks cover 20/40/80/120 columns. A rendering-only PTY also exercised `/model`,
filtering, Ctrl+S, Escape and clean exit. No fresh live-provider quality/latency claim.

## Historical transcript contract

`session.request` operation `events` reads one run's public event history. A page
returns its frozen high-water sequence and exclusive continuation cursor, at most
100 events and 512 KB of event JSON. New live events do not extend an in-progress
snapshot; normal attachment catches up afterward. Inputs and events are redacted,
and both the conversation and run must match the authenticated user/workspace/
harness. History reads neither attach nor acknowledge, dispatch queues, or invoke
models/tools. Focused tests cover Unicode byte limits, snapshot retries and growth,
ownership, invalid cursors and zero execution.

## Resume and history UI

The `session_history` capability gates `/resume` and `/history`; `/session` shows
conversation/run identity and queue status. The picker lists server-scoped recent
conversations, restores up to 20 turns through the same reducer used for live
events, and attaches after the last restored sequence. A fresh terminal can reopen
saved conversations without creating a new model invocation. Pending follow-ups
remain server-owned; opening a stopped conversation does not continue them.

`/history` browses older pages in a read-only dialog. Escape restores the live view
and editor draft; Ctrl+O expands tool details. The 400-entry/64-KB-per-entry display
bounds still apply, while full public events remain in server storage. This is not
Pi's session branching, renaming or cross-workspace switching; those are not exposed
as placeholder commands. A run ending without a tool result says so instead of
leaving a historical card marked `running`.

The production-server/ADK bridge drives the real resume picker and history dialog,
then reopens the conversation from a second client. Model invocation counts stay
unchanged. Deterministic terminal tests additionally cover active-run catch-up,
cancelled delayed loads, chronological replay, malformed cursors, earlier-page
navigation and widths of 20/40/80/120 columns. Installation migration and fresh
live-provider visual/quality comparisons remain outstanding.

## Resource discovery and selection

The `resources` capability exposes read-only `resource.request` / `resource.result`
metadata. Discovery runs off the WebSocket receive loop, so slow filesystem reads
do not block ping or cancellation. Each connection permits one discovery at a time;
the terminal coalesces concurrent refreshes and ignores dismissed dialog callbacks.
Inventory is redacted, limited to 128 entries and a 512-KB response, and explicitly
reports truncation. Alternate factories may omit the resource hook without breaking
the protocol; their workspace/state metadata remains available with a warning.

`/resources` shows server workspace, state, configuration and history locations,
project trust, prompts, tools and skill roots. `/skills` is a searchable selector;
`/skill:NAME` expands to the existing explicit skill request without another model
tool. Ctrl+O reveals compact resource metadata. Available resources are not labelled
loaded: actual selected skill names are emitted before the model call, separately
from the directory inventory. Instruction bodies and skill hashes remain private.

The production WebSocket/ADK fixture checks resource paths, sends a skill request,
observes selection while the model is gated, and verifies the body reached the
worker but not the terminal. Tests also cover trust, configured roots, disabled
budgets, invalid manifests, bounded discovery, responsive ping, safe errors,
dialog dismissal and terminal widths. Live-model/visual comparisons and installation
migration still remain; this is not a claim of full Pi parity.
