# ADK Coding Agent

A Pi-inspired, token-efficient coding harness built on Google ADK 2.x.

The model-facing surface is intentionally small: a stable instruction prefix and four coding tools (`read`, `bash`, `edit`, and `write`). ADK owns durable workflow execution, resumability, state, compaction, caching, and verification gates.

> Status: active MVP implementation. See [`docs/TODO.md`](docs/TODO.md) and the [design brief](docs/design/pi-inspired-adk-coding-harness.md).

## Design goals

- Keep the stable prompt and tool declarations small enough to cache effectively.
- Keep volatile task state in a deterministic context packet, not the system prefix.
- Use shell-first repository exploration and targeted file reads.
- Maintain a durable Task Ledger rather than relying on the raw transcript.
- Bound tool output and spill full logs to artifacts.
- Require deterministic verification before accepting a completion claim.
- Isolate the workspace behind an environment interface so a managed sandbox can replace the local backend.

## Quick start

Prerequisites: Python 3.11+, [`uv`](https://docs.astral.sh/uv/), Google credentials or a Gemini API key, and `git`.

```bash
uv sync --all-groups
cp .env.example .env
uv run pytest
```

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

The local environment backend is a development adapter, not a security boundary.
Production command execution can select Docker, a pre-provisioned Kubernetes task
pod, or a pluggable enterprise remote sandbox with fail-closed configuration.

The repository lockfile pins the tested Google ADK 2.7 minor. Live credentialed
Gemini runs remain an explicit deployment validation step; deterministic development
and CI checks do not require cloud credentials.

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
ADK Workflow
  -> deterministic context compiler
  -> one coding Agent
  -> read | bash | edit | write
  -> Task Ledger reducer
  -> verification gate
  -> continue | compact | block | finish
```

Large logs, repository indexes, checkpoints, and receipts stay outside the model transcript. The model sees bounded summaries and artifact references.

## License

Apache-2.0. Upstream Google skills remain licensed by their respective source repository; see `THIRD_PARTY_NOTICES.md`.
