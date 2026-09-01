# Development

## Pi terminal client

The default installer builds the protocol-only Pi-toolkit client. For client work,
use Node.js 22.19+ and build from this checkout:

```bash
npm ci --prefix clients/terminal
npm run build --prefix clients/terminal
```

Start the usual server in terminal 1:

```bash
adk-agent-start server --workspace /absolute/path/to/project
```

From this checkout in terminal 2:

```bash
npm start --prefix clients/terminal -- --state-root "$HOME/.local/state/adk-coding-agent"
```

For a custom server, also pass `--server ws://127.0.0.1:PORT/v1/agent` and use its
state root. The client reads `ADK_CODING_AGENT_TOKEN` when set, otherwise
`STATE_ROOT/server/auth-token`; provider credentials stay on the server. `/resources`
shows server paths, `/login` and `/model` control the provider, `/resume` restores
history, and `/approvals` reviews waiting commands. Escape defers an approval dialog;
Escape from the editor requests cancellation. See the migration design for remaining
live comparison gates; installation alone is not a claim of full Pi parity.

The default prompt streams eligible conversational Markdown using a typed control
header. Custom prompts returning the older JSON `message` format still work but
remain buffered. Code-completion replies appear only after verification. Sensitive
spans may wait until the reply ends so credentials split across chunks are redacted.
Public partial replies are replayable from the server run database; cancelling a
reply does not label it complete. No additional provider call is used for display.

## Setup and checks

Follow [installation and TUI startup](../README.md). Development setup:

```bash
./install.sh --dev
.venv/bin/python -m compileall -q app harness
.venv/bin/pytest tests/unit tests/integration
.venv/bin/ruff check app harness tests
.venv/bin/pyright --pythonpath .venv/bin/python app harness
npm test --prefix clients/terminal
```

Python 3.11+, uv, Git, and Node.js 22.19+ are required for the full developer stack.
Tests do not need cloud credentials. Live behavior requires separately authorized
provider access. Do not interpret deterministic smoke tests as model benchmarks.

## Configuration and entrypoints

Copy `harness/config/default.yaml` and pass `serve --config FILE` for custom
behavior. The launcher `adk-agent-start run` defaults to the Codex subscription
configuration and handles server/TUI state handoff.

The optional Google Agents CLI is not installed by the minimal installer.
If already installed, it can load `app.agent.application`; this entrypoint uses
the same YAML factory as the server. See `.env.example` for its identity bindings:
`ADK_CODING_CONFIG`, `ADK_CODING_WORKSPACE`, `ADK_CODING_STATE_DIR`, task/workspace
IDs, and `ADK_CODING_TRUST_PROJECT`. Former model/workflow/learning environment
settings are not the behavior API: migrate to YAML.

Do not run multiple server workers against one state directory. Back up local state
before upgrading a behavior configuration; do not resume old runs across a behavior
hash change.

## Search and skills

The four tools remain `read`, `bash`, `edit`, and `write`. Search is a reserved
virtual `bash` command, never passed to a shell:

```text
search grep --pattern TEXT [--path REL] [--mode literal|regex]
            [--case-sensitive] [--context 0..2] [--limit 1..50]
search grep --cursor TOKEN
search find --pattern TEXT [--path REL] [--limit 1..50]
search find --cursor TOKEN
search health
```

Pages default to 20 results, grouped across files and bounded in bytes. Cursors are
workspace/query/content-bound and reject stale or cross-workspace use. File
mutations refresh the index; external shell changes can briefly lag its watcher.

Skills live in trusted directories with `SKILL.md` and optional `scripts/` and
`references/`. Project `.agents/skills` requires `--trust-project`; additional
trusted roots belong in YAML. Explicit `$skill-name` selection takes precedence,
and selected content is bounded and hashed. Editing a skill is an operator action;
there is no automatic promotion system.

## Approvals, verification, and traces

YAML safety settings apply to tools and deterministic verification. Network,
dependency installation, history mutation, publishing, and unknown commands are
gated; destructive commands are denied by default. Verification receives an
explicit executor and cannot silently choose a different backend from the environment.
Prepare the target project's dependencies before running its offline checks.

Inspect and decide pending approvals in the connected TUI with `/approvals`. Do not broadly allow unknown
commands to make a test pass.

```bash
adk-coding-agent trace-export --state-root /path/to/run-state --task-id TASK_ID
```

Exports are already-redacted records. Metadata/off/redacted modes are configured
in YAML. Treat trace content and artifacts as sensitive even after redaction.

## Change discipline

Keep core code importable without credentials; place ADK wiring in `app/` or
`harness/adk/`. Keep volatile data out of static instructions. Test contracts
deterministically and commit coherent, verified changes independently.

Avoid adding a public configuration field until a running path consumes it.
Prefer a direct primitive over a compatibility loader, parallel model schema,
unused provider bridge, or speculative orchestration layer.
