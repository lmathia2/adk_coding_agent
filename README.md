# ADK Coding Agent

A small, configurable Google ADK coding harness with a Pi-style terminal client.
One coding worker uses four tools: `read`, `bash`, `edit`, and `write`.
The server owns task state, steering, approval requests, and deterministic verification.

## 1. Install

On macOS, install [Homebrew](https://brew.sh) and make sure `brew` is on your PATH.
Git is needed to clone the repository; if it is missing, run `brew install git` first.

For a new checkout:

```bash
mkdir -p "$HOME/src"
git clone https://github.com/lmathia2/adk_coding_agent.git "$HOME/src/adk_coding_agent"
cd "$HOME/src/adk_coding_agent"
./install.sh --bin-dir "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"
```

If you already have the checkout, skip cloning. Stop its running server/TUI before
reinstalling:

```bash
cd "$HOME/src/adk_coding_agent"
./install.sh --bin-dir "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"
```

The installer detects macOS, installs missing uv/Git/Node.js through Homebrew, creates
Python 3.11+ in this checkout's `.venv`, syncs the locked dependencies, and builds
the Pi-toolkit terminal **by default**. It links `adk-coding-agent`, `adk-agent-tui`, and
`adk-agent-start` into the specified command directory. You do not need to activate
the virtual environment, install Pi or Magnitude, or pass a workspace to installation.
Indexed search comes from the pinned `fff-search` dependency; no separate rg/fd
installation is needed for that search backend.

**Every installation deletes and recreates this checkout's `.venv`.** Credentials
and task state outside it are preserved. Keep the checkout in place: command links
point into it. The installer refuses a symlinked environment or a non-symlink
command collision.

Add `export PATH="$HOME/.local/bin:$PATH"` to your shell startup file (`~/.zshrc`
for zsh, or the appropriate bash profile) to make the commands available in future
terminals. The startup examples below also set PATH explicitly.

Node.js 22.19+ is required. If an older Homebrew Node.js is already installed, run
`brew upgrade node` and rerun the installer; existing outdated Node.js is not auto-upgraded.
On Linux, install uv, Git, and Node.js 22.19+ before running the same script. The first
installation needs internet access for dependencies.

Optional installer flags (choose only what you need):

| Flag | Effect |
| --- | --- |
| `--plan` | Show paths and the plan without installing |
| `--dev` | Also install pytest, Ruff, and Pyright |
| `--minimal` | CLI only; skip the TUI |
| `--bin-dir DIR` | Choose where the command links are placed |

## 2. Start the coding agent

Choose **one** of the following launch modes. The examples use the existing
`$HOME/src/coding_tools` repository; replace that path with the repository you want
the agent to work on. The server works directly in that workspace, so review its
changes as you would any local edits.

### One terminal: server and TUI together

```bash
export PATH="$HOME/.local/bin:$PATH"
adk-agent-start run --workspace "$HOME/src/coding_tools"
```

This starts a managed server, opens the TUI, and stops that server child when you
exit the TUI with `/quit`. Do not also start a separate server on the same port.

### Two terminals: server and TUI separately

Terminal 1 — start the server and leave it running:

```bash
export PATH="$HOME/.local/bin:$PATH"
adk-agent-start server --workspace "$HOME/src/coding_tools"
```

Terminal 2 — connect the TUI:

```bash
export PATH="$HOME/.local/bin:$PATH"
adk-agent-start tui
```

The launcher reads the token and sets the TUI environment automatically. No token
copy/paste or manual `STATE_ROOT` setup is needed. `/quit` exits only the TUI in this
mode; press Ctrl+C in Terminal 1 to stop the server.

From the harness checkout, `./start.sh run`, `./start.sh server`, and
`./start.sh tui` are equivalent launcher commands (installed CLI/TUI commands must
still be on PATH). `--server` and `--tui` are also accepted as mode aliases.

### First login and model selection

The launcher uses this harness's Codex subscription adapter, not a local model or
an OpenAI API key. Login uses the harness's own credential store; do not assume that
being signed into the Codex app or Pi has authenticated this harness.

In the TUI:

1. Enter `/login` if needed and follow the displayed browser/device-code instructions.
2. Enter `/auth` to check authentication.
3. Enter `/model` to choose a model from your account's catalog.
4. Press Enter to select for the next turn; Ctrl+S also saves the server default.
   The active turn keeps its frozen model and does not need a server restart.
5. Enter your coding request. The TUI reports the
   configured coding model; merely starting the server is not a model-response test.

Use `/help` for commands. Guidance entered during execution is delivered at the next
safe boundary; `/cancel` requests cancellation. `/benchmark` is optional and makes
live model requests—it is not required for installation or startup.

Add `--trust-project` to `run` or `server` only after reviewing the workspace's
`AGENTS.md` and `.agents/skills`. Without it, project instructions and skills are not
loaded. Prepare the target repository's own test/build dependencies separately;
the harness installation does not install them.

### State and environment

The launcher announces the resolved paths and environment handoff without printing
tokens. Defaults:

| Data | Location |
| --- | --- |
| State root | `~/.local/state/adk-coding-agent` |
| Codex credentials | `STATE_ROOT/auth/openai-codex.json` |
| Saved model selection | `STATE_ROOT/auth/model-selection.json` (older `openai-codex-selection.json` is read for migration) |
| Generated Codex configuration | `STATE_ROOT/server/openai-codex.yaml` |
| WebSocket bearer token | `STATE_ROOT/server/auth-token` |
| Combined-launch server log | `STATE_ROOT/server/foreground.log` |
| Per-run state and artifacts | `STATE_ROOT/runs/` |

`ADK_CODING_AGENT_STATE_ROOT` overrides the launcher's default state directory.
`ADK_CODING_AGENT_SERVER_URL` overrides the TUI URL, not the server listener.
`ADK_CODING_AGENT_TUI_COMMAND` can select an alternate compatible terminal executable;
the checkout's pinned Pi-style terminal is used by default.
The TUI process receives `ADK_CODING_AGENT_TOKEN` from the token file and
`ADK_CODING_AGENT_STATE_ROOT` from the resolved directory. Tokens are not command-line
arguments. The default endpoint is `ws://127.0.0.1:8765/v1/agent`; it is not a browser UI.

For a different state directory, pass the **same** `--state-root /absolute/path`
to both `server` and `tui` (or once to `run`). This also selects which saved login
and model selection the TUI uses. Run only one server process per state directory.

### Startup troubleshooting

- **Command not found:** set PATH as shown above; use the directory supplied to
  `--bin-dir` if it differs from `~/.local/bin`.
- **Missing auth token:** start Terminal 1 first and ensure both terminals use the
  same state root. This WebSocket token is separate from your Codex login.
- **Managed server exited / address already in use:** stop your existing harness
  server before using `run`. Inspect `STATE_ROOT/server/foreground.log` for the cause.
- **Browser shows 404 at port 8765:** connect with the TUI; there is no web frontend.
- **Removed Magnitude commands/options:** use the commands above, not
  `serve-magnitude`, `magnitude --setup`, or installer `--workspace`/`--magnitude` flags.

## Configure the harness

Copy [the default YAML](harness/config/default.yaml), then change the model,
instruction file, skill roots, budgets, steering, tracing, or local/Docker command
settings. Start a custom configuration with:

Terminal 1:

```bash
adk-coding-agent serve --config /absolute/path/to/harness.yaml \
  --workspace /absolute/path/to/repository \
  --state-root "$HOME/.local/state/adk-coding-agent"
```

Terminal 2:

```bash
adk-agent-start tui
```

Built-in providers are native ADK/Gemini (`google_adk`) and Codex subscription
(`openai_codex`). Gemini needs its normal ADK credentials.
The closed `HarnessFactory` and model-provider registries permit explicit
Python extensions without changing the server protocol or TUI.

YAML configures executable settings; loop topology belongs to the harness
implementation. Unknown/removed options fail validation.
File prompts are relative to the configuration directory and cannot escape it.
Volatile state stays out of the stable instruction prefix.

Skills are read from trusted directories, selected deterministically, and disclosed
within a byte/token budget. Redacted interaction traces remain available:

```bash
adk-coding-agent trace-export --state-root /path/to/run-state --task-id TASK_ID
```

## Safety and verification

The host-local command adapter is a development tool, **not a security sandbox**.
Network, publishing, destructive, and unknown commands are not enabled by default.
Docker command execution uses the same mounted workspace; `serve --production`
rejects the host-local command adapter. File tools still run in the harness process,
so Docker alone does not isolate the entire harness.

Files use confined paths, atomic replacement, optional hash preconditions, and
mutation receipts. Tool output is bounded and large logs are recoverable as artifacts.
The outer workflow—not model prose—decides whether a coding task is verified.

Structured requests can specify `goal`, `acceptance_criteria`, and
`verification_requirements`. Executable changes require behavioral checks by
default; `verification_level: syntax` is an explicit weaker contract.

## Development

```bash
./install.sh --dev
.venv/bin/python -m compileall -q app harness
.venv/bin/pytest tests/unit tests/integration
.venv/bin/ruff check app harness tests
.venv/bin/pyright --pythonpath .venv/bin/python app harness
npm test --prefix clients/terminal
```

Deterministic tests use fake model streams and temporary repositories. They do not
require cloud credentials. Live model quality/speed must be measured separately;
old evaluation reports are not evidence for the simplified runtime.

## Removed features

The simplification removes Magnitude and LiteLLM, remote/Kubernetes command
adapters with disconnected file workspaces, the unwired semantic/LSP/Moderne bridge,
automatic skill synthesis/promotion/trials, automatic project memory, advisory
reviewer agents, and experiment-only comparison CLIs. Directory skills, traces,
verification, core evaluation graders, and the server/TUI remain.

Persistence is local-only (JSONL/SQLite/files or in-memory ADK services). PostgreSQL,
Vertex/GCS services and multi-worker control leases are removed. Run one server
process per state directory. The server and optional Agents CLI entrypoint now use
the same YAML factory; behavior environment overrides must be migrated to YAML.

Existing global Magnitude binaries/models and runtime credentials are untouched.
Old YAML must drop `learning`, `reviewer`, the reviewer model/agent, and
`workflow.entry/nodes`; retain `workflow.max_iterations`. Regenerate Codex
configuration by starting `serve-codex`. Removed code remains recoverable in Git.

See [architecture](docs/architecture.md) and
[simplification notes](docs/simplification.md). The original
[design brief](docs/design/pi-inspired-adk-coding-harness.md) and old evaluation
reports describe the larger pre-simplification system.
