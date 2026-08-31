# ADK Coding Agent

A small, configurable Google ADK coding harness with a Bubble Tea terminal client.
One coding worker uses four tools: `read`, `bash`, `edit`, and `write`.
The server owns task state, steering, approval requests, and deterministic verification.

## Install

On macOS, install [Homebrew](https://brew.sh) once. Then:

```bash
git clone https://github.com/lmathia2/adk_coding_agent.git ~/src/adk_coding_agent
cd ~/src/adk_coding_agent
./install.sh
```

The installer installs missing uv, Git, and Go on macOS, recreates **only this
checkout's `.venv`**, syncs `uv.lock`, builds the TUI, and links commands into
`~/.local/bin`. It refuses to overwrite non-symlink commands or a symlinked
environment. No workspace is selected during installation.

On Linux, install uv, Git, and Go 1.24+ first. uv can provision Python 3.11+.
No Node/npm, Magnitude, local-model runtime, or separate rg/fd install is required.
Indexed search uses the pinned native `fff-search` Python wheel.

```bash
./install.sh --plan             # inspect without installing
./install.sh --dev              # also install pytest, Ruff, and Pyright
./install.sh --minimal          # CLI only, no TUI
./install.sh --bin-dir /custom/bin
export PATH="$HOME/.local/bin:$PATH"  # add to ~/.zshrc if needed
```

The first install requires package downloads. Rerunning recreates the environment;
model credentials and task state are outside it and are not deleted.

## Run in the TUI

Choose a workspace at launch:

```bash
adk-agent-start run --workspace /absolute/path/to/repository
```

Enter `/login` if prompted, then `/model` to select a Codex subscription model.
The launcher defaults to Codex; it does not use an OpenAI API key. Exiting the TUI
stops the server child that this command started.

Alternatively, run in two terminals:

```bash
# Terminal 1: leave running
adk-agent-start server --workspace /absolute/path/to/repository

# Terminal 2
adk-agent-start tui
```

Add `--trust-project` only after reviewing the workspace's `AGENTS.md` and
`.agents/skills`. Without it, project instructions and skills are not loaded.
Use `--state-root DIR` in both terminals if you override the default.

The TUI supports `/help`, `/login`, `/auth`, `/logout`, `/model`, `/models`,
`/benchmark`, `/start`, `/attach`, `/cancel`, and `/reconnect`.
Guidance sent during execution is delivered at the next safe boundary.
Model/default changes apply on the next server start.

### State and environment

The launcher announces the resolved paths and environment handoff without printing
tokens. Defaults:

| Data | Location |
| --- | --- |
| State root | `~/.local/state/adk-coding-agent` |
| Codex credentials and model selection | `STATE_ROOT/auth/` |
| WebSocket bearer token | `STATE_ROOT/server/auth-token` |
| Combined-launch server log | `STATE_ROOT/server/foreground.log` |
| Per-run state and artifacts | `STATE_ROOT/runs/` |

`ADK_CODING_AGENT_STATE_ROOT` overrides the launcher's default state directory.
`ADK_CODING_AGENT_SERVER_URL` overrides the TUI URL, not the server listener.
The TUI process receives `ADK_CODING_AGENT_TOKEN` from the token file and
`ADK_CODING_AGENT_STATE_ROOT` from the resolved directory. Tokens are not command-line
arguments. The default endpoint is `ws://127.0.0.1:8765/v1/agent`; it is not a browser UI.

## Configure the harness

Copy [the default YAML](harness/config/default.yaml), then change the model,
instruction file, skill roots, budgets, steering, tracing, or local/Docker command
settings. Start a custom configuration with:

```bash
adk-coding-agent serve --config /absolute/path/to/harness.yaml \
  --workspace /absolute/path/to/repository \
  --state-root "$HOME/.local/state/adk-coding-agent"
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
(cd clients/tui && go test ./...)
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
