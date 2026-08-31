# ADK Coding Agent

A Pi-inspired, token-efficient coding harness built on Google ADK 2.x.

The model-facing surface is intentionally small: a stable instruction prefix and four coding tools (`read`, `bash`, `edit`, and `write`). ADK owns durable workflow execution, resumability, state, compaction, caching, and verification gates.

> Status: local implementation complete; live credentialed validation remains. See
> [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) and the
> [design brief](docs/design/pi-inspired-adk-coding-harness.md).

## Design goals

- Keep the stable prompt and tool declarations small enough to cache effectively.
- Keep volatile task state in a deterministic context packet, not the system prefix.
- Use bounded, indexed repository discovery and targeted file reads.
- Maintain a durable Task Ledger rather than relying on the raw transcript.
- Bound tool output and spill full logs to artifacts.
- Require deterministic verification before accepting a completion claim.
- Isolate the workspace behind an environment interface so a managed sandbox can replace the local backend.
- Trace redacted ADK interactions and turn repeated verified workflows into quality-gated Agent Skills.
- Configure harness behavior in strict YAML or select another registered harness without changing the protocol client.

## Quick start

### 1. Install on macOS

On macOS, install [Homebrew](https://brew.sh) once. The installer installs missing
uv, Git, and Go prerequisites; recreates this checkout's locked Python environment;
and builds the Bubble Tea TUI. No Node/npm or local-model service is needed.

```bash
./install.sh
```

### 2. Run the coding agent

Start the server and TUI together:

```bash
adk-agent-start run --workspace /absolute/path/to/repository
```

Or use two terminals:

```bash
# Terminal 1
adk-agent-start server --workspace /absolute/path/to/repository

# Terminal 2
adk-agent-start tui
```

Codex subscription is the launcher default. Enter `/login` if prompted and
`/model` to choose a model. Add `--trust-project` only after reviewing the
workspace's instructions and skills. Gemini remains available through
`adk-coding-agent serve --config FILE`.

The server listens at `ws://127.0.0.1:8765/v1/agent`. In two-terminal mode,
keep Terminal 1 running. In combined mode, exiting the TUI stops its server child;
server output goes to `~/.local/state/adk-coding-agent/server/foreground.log`.

Type a request in the TUI and press Enter. You can send additional guidance while
the agent is running; the server delivers it at the next safe steering point.
With the Codex provider, the model line appears before the first task. If it reports
`authentication_required`, enter `/login`; an inline device-login panel opens the
browser, displays the short-lived code, remains cancellable with Escape, and never
renders the stored credential. Enter `/model` for the inline searchable model picker,
or use `/models`, `/model MODEL_ID`, `/benchmark`, `/auth`, and `/logout` directly.
A saved model or benchmark winner applies on the next server start.

The composer supports cursor editing, `Ctrl-J` for a newline, `Ctrl-W` to delete the
previous word, and an inline command palette after `/`; use the arrow keys and Tab to
complete a command. `Ctrl-C` clears a draft before it interrupts a run or exits. User
prompts, tool activity, errors, and model/run readiness have distinct visual states,
and the bottom status line remains visible while dialogs replace the composer.

Both commands announce the resolved configuration before starting. By default the
shared state is saved under `~/.local/state/adk-coding-agent`; the server writes the
secret to `server/auth-token` beneath that directory, and the TUI launcher reads it
from there. The launcher exports `ADK_CODING_AGENT_TOKEN` only to the TUI child
process and never prints it or puts it on the command line. It reads the optional
`ADK_CODING_AGENT_STATE_ROOT` override in both terminals and reads
`ADK_CODING_AGENT_SERVER_URL` when starting the TUI. Equivalent flag overrides are
`--state-root` for either command and `--server` for the TUI.

The server workspace defaults to the current directory, so this also works:

```bash
# Terminal 1, from the repository to edit
adk-agent-start server --trust-project

# Terminal 2, from any directory
adk-agent-start tui
```

Use `--` to forward additional arguments to the underlying command, for example
`adk-agent-start tui -- --input "Inspect this repository and run its tests"`.

For the smallest ChatGPT-subscription workflow, run both processes together:

```bash
adk-agent-start run --provider codex \
  --workspace "$HOME/src/coding_tools" --trust-project
```

Then enter `/login` in the TUI if prompted. The OAuth credential and selected model
are stored under `~/.local/state/adk-coding-agent/auth/`; generated provider YAML,
server state, and logs remain under sibling directories in the same state root.
Neither the browser token nor the WebSocket bearer token is printed.

### Installation behavior and options

The installer is safe to rerun. It removes only this checkout's `.venv`, recreates it
with uv, syncs every selected Python dependency from `uv.lock`, verifies
the required imports and commands, and links the CLI and launcher into `~/.local/bin`
(or `UV_TOOL_BIN_DIR`). It never replaces an existing non-symlink. On other operating
systems, install uv, Git, and Go 1.24+ first. Options:

```bash
./install.sh --plan                   # show the detected platform and planned work
./install.sh --dev                    # include test, lint, and type-check dependencies
./install.sh --minimal                # Python CLI only; skip TUI
./install.sh --tui                    # Bubble Tea client (already the default)
./install.sh --bin-dir /custom/bin
```

Use `source .venv/bin/activate` when you want the repository's Python, pytest,
Ruff, and Pyright commands directly in the current shell. The installed
`adk-coding-agent` link already targets that environment and does not require activation.

If `~/.local/bin` is not already on `PATH`, add it in `~/.zshrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

For development and verification:

```bash
./install.sh --dev
cp .env.example .env
uv run pytest
```

`uv sync` installs the pinned native `fff-search==0.10.5` wheel with the rest of the
project. No separate Pi extension, npm package, `rg`, or `fd` installation is needed
for indexed discovery. This is a project dependency rather than copied third-party
source, so a first sync still needs access to the configured Python package source.

Install the Google Agents CLI for the intended development workflow:

```bash
uv tool install "google-agents-cli~=1.4.1"
agents-cli info
```

Point the harness at a repository workspace and run through Agents CLI:

```bash
export ADK_CODING_WORKSPACE=/absolute/path/to/repository
agents-cli run '{"goal":"Fix the failing parser test","acceptance_criteria":["Relevant tests pass"]}'
```

Executable-code changes require a passing behavioral check by default. Supply trusted
task-level checks with `verification_requirements`; use `verification_level: "syntax"`
only when syntax validity is itself the full acceptance contract:

```bash
agents-cli run '{"goal":"Fix the parser","acceptance_criteria":["Malformed input is rejected"],"verification_requirements":["pytest -q tests/test_parser.py"]}'
```

The local environment backend is a development adapter, not a security boundary.
Production command execution can select Docker, a pre-provisioned Kubernetes task
pod, or a pluggable enterprise remote sandbox with fail-closed configuration.
Start `serve --production` after selecting one of those adapters; production mode
refuses the host-local sandbox before creating server state. `--print-config` reports
both the effective sandbox kind and whether the production gate is active.

The repository lockfile pins the tested Google ADK 2.7 minor. Live credentialed
Gemini runs remain an explicit deployment validation step; deterministic development
and CI checks do not require cloud credentials.

The YAML `context` section caps each model work packet and the aggregate estimated
input for a task (`work_packet_tokens` and `max_task_input_tokens`). A JSON task
request can set a lower `max_input_tokens` for a specific run. Exceeding the limit
produces a durable blocked result rather than silently continuing an expensive loop.

Agent model keys and prompts are also executable YAML configuration. To replace the
worker instruction, set `harness.config.agents.coding_worker.prompt` to
`{source: file, path: prompts/coding.md}`. The path is resolved relative to the YAML
file's directory; absolute paths, escapes, symlink escapes, oversized files, and
invalid UTF-8 fail before a run. The resolved prompt content is included in the
behavior hash. The built-in Pi graph and agent contracts are deliberately fixed—use
a registered `HarnessFactory` implementation to introduce another topology. Because
the WebSocket/AG-UI contract is shared, the Bubble Tea TUI does not change when the
harness implementation changes.

Repository `AGENTS.md` files and `.agents/skills` are untrusted data by default.
Pass `--trust-project` to `adk-agent-start server`, `adk-agent-start run`,
`adk-coding-agent serve`, or `serve-codex` only after reviewing that workspace.
The server's `--print-config` output announces the effective decision. Legacy
Agents CLI startup uses the equivalent `ADK_CODING_TRUST_PROJECT=1` opt-in. External
skill roots explicitly configured by the harness and guarded learned-skill roots do
not inherit project trust.

### Removed local-model integration

Magnitude, `serve-magnitude`, the `local-models` extra, and the LiteLLM
`openai_compatible` adapter have been removed. Existing installed Magnitude
binaries, model downloads, and credentials are not touched. Old local-model YAML
must be replaced with a retained provider configuration.

### ChatGPT subscription through Codex OAuth

The `openai_codex` adapter follows Pi's provider pattern while keeping Google ADK as
the agent runtime. The harness performs browser/device OAuth, refreshes and stores the
credential with owner-only permissions, maps ADK requests and tools to streaming Codex
Responses calls, and maps the stream back into ADK `LlmResponse` events. It deliberately
rejects API-key configuration so subscription and metered API routes cannot be mixed.

The model catalog is discovered from the authenticated account rather than hard-coded.
To select for latency, run identical short coding probes and save the lowest median
time-to-first-token result:

```bash
adk-coding-agent codex login
adk-coding-agent codex models
adk-coding-agent codex benchmark --runs 3
adk-agent-start run --provider codex --workspace /absolute/path/to/repository
```

The benchmark result is machine/account/network specific. Its saved winner and client
version are written to `auth/openai-codex-selection.json`; `serve-codex` renders a
validated private composition at `server/openai-codex.yaml`. The first authenticated
task remains the definitive end-to-end compatibility check because the subscription
transport is provider-controlled and may evolve independently of the public ADK API.

Launcher runs can be steered from another terminal without waiting for task
completion. `adk-coding-agent steer --repository PATH --task-id ID "new guidance"`
queues a bounded durable message that is injected at the next ADK model/tool safe
point; `steering-status` reports its delivery state.

### WebSocket server and Bubble Tea TUI

The same configured harness can run behind the durable bidirectional server. The
built-in authentication policy intentionally permits loopback listeners only:

```bash
uv run adk-coding-agent serve \
  --workspace /absolute/path/to/repository \
  --state-root /absolute/path/to/state \
  --print-config

# Remove --print-config to listen using server.host, port, and websocket_path from YAML.
uv run adk-coding-agent serve \
  --workspace /absolute/path/to/repository \
  --state-root /absolute/path/to/state
```

The standalone TUI needs Go 1.24+ only when building the client. It speaks the public
protocol and does not import ADK or harness implementation code:

```bash
cd clients/tui
go build -o adk-agent-tui .
export ADK_CODING_AGENT_TOKEN="$(cat /absolute/path/to/state/server/auth-token)"
./adk-agent-tui --server ws://127.0.0.1:8765/v1/agent \
  --input "Fix the parser and run its tests"
```

The server creates that mode-`0600` token file on first startup, or accepts a
32-byte-or-longer token supplied through `ADK_CODING_AGENT_TOKEN`. Browser origins
are rejected by default to prevent webpages from driving the local coding agent.

During execution, ordinary input steers the active run at the next safe point.
`/pause`, `/cancel`, `/reconnect`, and cursor-based `/attach` are also available.
Disconnecting the TUI does not cancel the run; reconnect replays durable events after
the highest sequence already applied.

Model stalls do not block indefinitely. The checked-in YAML defaults to a 120-second
first-event deadline, 180-second idle deadline, 30-minute total deadline, and one
startup retry. These are deployment settings under `server` and can be tuned without
changing the harness or TUI; timeout and cleanup outcomes are persisted for replay.

## Google Agents CLI skills

This project follows the upstream Google skills and `long-horizon-harness` recipe:

- `google-agents-cli-workflow`
- `google-agents-cli-scaffold`
- `google-agents-cli-adk-code`
- `google-agents-cli-eval`
- `google-agents-cli-observability`

Install them with the official setup command or sync the pinned copies into `.agents/skills`:

```bash
uvx google-agents-cli setup
# or
uv run python scripts/sync_agents_cli_skills.py
```

The pinned upstream revision is recorded in `.agents/skills/upstream-lock.json`.

## Development

```bash
make test
make lint
make typecheck
make sync-skills
```

Behavioral assertions about model output belong in Agents CLI eval datasets, not deterministic unit tests. Unit tests cover context compilation, state reduction, policy, repository processing, tool behavior, verification, and resume/idempotency contracts.

## Architecture

```text
Bubble Tea or another protocol client
  -> WebSocket control + AG-UI events
  -> registered harness selected by YAML
  -> harness-specific ADK App assembly
  -> shared ADK Runner / sessions / artifacts / memory
  -> deterministic context compiler
  -> trusted skill catalog + selected skill bodies
  -> one coding Agent
  -> read | bash | edit | write
  -> Task Ledger reducer
  -> verification gate
  -> verified trace learner + candidate trials
  -> continue | compact | block | finish
```

Large logs, repository indexes, checkpoints, and receipts stay outside the model transcript. The model sees bounded summaries and artifact references.

The declarative composition, registered harness factory, durable WebSocket runtime,
and protocol-only Bubble Tea client are implemented. Google ADK remains the execution
engine, including workflow/agent composition, sessions, artifacts, memory, plugins,
streamed events, and resume. See the
[declarative runtime design](docs/design/declarative-runtime-and-clients.md).

Indexed `grep`, fuzzy path discovery, and health checks are virtual commands behind
`bash`, so the model-visible surface remains exactly four tools. See the
[development guide](docs/development.md#indexed-repository-search) for the grammar and
platform notes.

## License

Apache-2.0. Upstream Google skills remain licensed by their respective source repository; see `THIRD_PARTY_NOTICES.md`.
