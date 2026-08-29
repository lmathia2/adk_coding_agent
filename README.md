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

On macOS, install [Homebrew](https://brew.sh) once. The installer detects macOS and,
in one run, installs any missing uv, Git, Node.js/npm, and Go prerequisites through
Homebrew; installs the compatible Magnitude CLI; creates the locked Python environment;
builds the Bubble Tea TUI; and exposes the CLI, TUI, and launcher on your user PATH:

```bash
./install.sh
```

If this is the first Magnitude installation, select and install a local model once:

```bash
magnitude setup
```

At Magnitude's **Select harness** step, choose **Magnitude** to complete setup. The
model selection is stored in Magnitude's shared state and this coding harness discovers
it from there. Magnitude 0.0.8 exposes a closed list of built-in harness connectors, so
`ADK Coding Agent` does not yet appear as its own row; that requires an upstream
Magnitude connector and does not affect local-model compatibility here.

If setup prints an error, check whether it completed before rerunning it:

```bash
magnitude models list
```

A selected primary model means the setup state was saved. An empty result means setup
did not finish; rerun `magnitude setup` in an interactive terminal.

### 2. Start the coding-agent server in Terminal 1

```bash
adk-agent-start server --workspace "$HOME/src/coding_tools"
```

Leave Terminal 1 running. The server listens locally and exposes the agent at
`ws://127.0.0.1:8765/v1/agent`; opening `http://127.0.0.1:8765/` in a browser is not
the TUI and may return `404 Not Found`.

### 3. Start the TUI in Terminal 2

```bash
adk-agent-start tui
```

For a single foreground lifecycle—used by Magnitude harness handoff—start the
server and TUI together. The command passes an exact optional Magnitude model to
the server and stops its managed server child when the TUI exits:

```bash
adk-agent-start run --workspace "$HOME/src/coding_tools" \
  --model 'qwen3.8-27b:gguf:q8'
```

Server output for this mode is appended to
`~/.local/state/adk-coding-agent/server/foreground.log`, leaving the terminal
renderer exclusively owned by the TUI.

Type a request in the TUI and press Enter. You can send additional guidance while
the agent is running; the server delivers it at the next safe steering point.

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
adk-agent-start server

# Terminal 2, from any directory
adk-agent-start tui
```

Use `--` to forward additional arguments to the underlying command, for example
`adk-agent-start tui -- --input "Inspect this repository and run its tests"`.

### Installation behavior and options

The installer is safe to rerun. It removes only this checkout's `.venv`, recreates it
with uv, syncs every selected Python dependency from `uv.lock`, verifies
the required imports and commands, and links the CLI and launcher into `~/.local/bin`
(or `UV_TOOL_BIN_DIR`). It never replaces an existing non-symlink. On other operating
systems, install Python 3.11+, uv, Git, npm, and Go 1.24+ before requesting their
corresponding features. Options:

```bash
./install.sh --plan                   # show the detected platform and planned work
./install.sh --dev                    # include test, lint, and type-check dependencies
./install.sh --minimal                # Python CLI only; skip local models and TUI
./install.sh --tui                    # request the Bubble Tea client outside macOS
./install.sh --magnitude              # request Magnitude outside macOS
./install.sh --no-local-models        # smaller Gemini-only environment
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

### Magnitude on macOS

The harness can use a model selected and served by
[Magnitude](https://github.com/magnitudedev/magnitude), or another
OpenAI-compatible service, without adding a second model runtime. The provider
adapter builds ADK's `LiteLlm`; requests, streaming, tools, and lifecycle events
remain owned by Google ADK.

The default macOS installation installs Magnitude 0.0.8 or newer and the TUI. Complete
Magnitude's interactive hardware/model setup once. Then one harness command discovers
Magnitude's selected model, writes a
validated generated composition beneath the harness state directory, supplies the
conventional local placeholder token in process memory, and starts the WebSocket
server:

```bash
./install.sh
magnitude setup

adk-agent-start server --workspace /absolute/path/to/repository
```

`serve-magnitude` runs `magnitude server start` when the endpoint is not already
available. Magnitude 0.0.6 and earlier do not expose the fixed external-harness
service and are rejected with an exact update command. The launcher prefers the
primary model in `~/.magnitude/state/models.json`, falls back to the first model
reported by Magnitude, and accepts `--model MODEL_ID` as an explicit override. Use
`--print-config` to inspect the resolved harness endpoint without starting its
listener. After an install or upgrade, initial hardware and model assessment can take
a few minutes; the launcher keeps probing the service through that startup window.

The installer prints the complete launch sequence. Keep the harness in the first
terminal and connect the TUI from a second terminal:

```bash
adk-agent-start tui -- --input "Inspect this repository and run its tests"
```

The generated YAML stores only an environment-variable reference, never the token.
The checked-in [`examples/magnitude.yaml`](examples/magnitude.yaml) remains available
for manual or non-default endpoint configuration. This repository intentionally
reuses Magnitude's machine scan, model ranking, download, and inference lifecycle
rather than duplicating them.

### Other OpenAI-compatible endpoints

Install the local-model adapter and point a copied composition at the endpoint:

```bash
./install.sh
cp examples/magnitude.yaml local-model.yaml
# Edit the model id, base_url, and API-key environment reference.
adk-coding-agent serve --config local-model.yaml --workspace /absolute/path/to/repository
```

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
