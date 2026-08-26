# Development Guide

## Prerequisites

- Git
- Python 3.12 or newer
- `uv`
- Google Agents CLI
- Google ADK credentials for live Gemini runs

Install Agents CLI from the upstream repository when a published version is not already available in your environment:

```bash
uv tool install git+https://github.com/google/agents-cli.git
agents-cli --help
```

## Local setup

```bash
git clone https://github.com/lmathia2/adk_coding_agent.git
cd adk_coding_agent
uv sync --all-groups
```

The lockfile resolves the tested Google ADK 2.7 minor. Install production-only
PostgreSQL support when a worker uses the distributed control-state backend:

```bash
uv sync --all-groups --extra production
```

Run deterministic checks:

```bash
uv run python -m compileall -q app harness tests
uv run pytest -q tests/unit
uv run pytest -q tests/integration
uv run ruff check app harness tests
uv run pyright app harness
```

The unit suite does not require cloud credentials. The ADK import smoke test skips only when the Google ADK dependency is absent.

## Run against a repository

The launcher creates or reattaches a dedicated Git worktree and exports its identity to the ADK process:

```bash
uv run python -m harness.cli prepare \
  --repository /path/to/target-repository \
  --task-id issue-1842 \
  "Implement issue 1842 and run the affected tests"
```

Inspect the machine-readable launch contract, then run:

```bash
uv run python -m harness.cli run \
  --repository /path/to/target-repository \
  --task-id issue-1842 \
  "Implement issue 1842 and run the affected tests"
```

A named branch is optional:

```bash
uv run python -m harness.cli run \
  --repository /path/to/target-repository \
  --task-id issue-1842 \
  --branch agent/issue-1842 \
  "Implement issue 1842"
```

Cleanup is guarded:

```bash
uv run python -m harness.cli cleanup \
  --repository /path/to/target-repository \
  --task-id issue-1842
```

A dirty worktree is not removed. `--force` is deliberately explicit.

## Direct Agents CLI run

When the workspace has already been provisioned:

```bash
export ADK_CODING_WORKSPACE=/path/to/task-worktree
export ADK_CODING_STATE_DIR=/path/to/durable-state
agents-cli run "Fix the failing authentication tests"
```

## Core environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ADK_CODING_MODEL` | `gemini-3.7-flash` | Coding worker model |
| `ADK_CODING_WORKSPACE` | current directory | Confined code workspace |
| `ADK_CODING_STATE_DIR` | per-workspace cache directory | Events, receipts, index, metrics |
| `ADK_CODING_MAX_ITERATIONS` | `40` | Hard work-batch limit |
| `ADK_CODING_COMPACT_AT_TOKENS` | `80000` | Custom compaction trigger estimate |
| `ADK_CODING_CACHE_MIN_TOKENS` | `4096` | ADK context-cache threshold |
| `ADK_CODING_CACHE_TTL_SECONDS` | `1800` | Context-cache TTL |
| `ADK_CODING_CACHE_INTERVALS` | `10` | ADK cache interval setting |
| `ADK_CODING_ADK_COMPACT_TOKENS` | `96000` | ADK overflow compaction threshold |
| `ADK_CODING_SANDBOX` | `local` | Command backend: `local`, `docker`, `kubernetes`, or `remote` |
| `ADK_CODING_CONTROL_DATABASE_URL` | unset | PostgreSQL control events and distributed task leases |
| `ADK_CODING_WORKER_ID` | process-derived | Stable owner identity for distributed task leases |
| `ADK_CODING_TASK_LEASE_SECONDS` | `900` | Distributed task-lease duration, renewed around long operations |
| `ADK_CODING_FINAL_REVIEWER` | `0` | Enable the advisory no-tool final-diff reviewer |
| `ADK_CODING_REVIEW_MODEL` | coding model | Optional reviewer model override |
| `ADK_CODING_REVIEW_MAX_CHARS` | `60000` | Maximum redacted diff characters sent to the reviewer |

## Approval policy

Safe local commands are allowed by default. Explicit opt-ins are available for controlled development environments:

| Variable | Meaning |
|---|---|
| `ADK_CODING_ALLOW_DEPENDENCY_INSTALL=1` | Allow package-manager mutations |
| `ADK_CODING_ALLOW_NETWORK=1` | Allow network-classified commands |
| `ADK_CODING_ALLOW_GIT_MUTATION=1` | Allow Git-history mutations |
| `ADK_CODING_ALLOW_UNKNOWN_COMMANDS=1` | Allow unclassified commands |
| `ADK_CODING_APPROVED_COMMAND_FINGERPRINTS` | Comma-separated one-operation approvals |

Publishing and deployment still require an explicit approval fingerprint. Destructive commands remain denied.

Never set broad opt-ins in a shared production worker. Approve the smallest operation possible through the outer control plane.

Approval requests share one durable store across three transports. An operator can
decide one interactively:

```bash
uv run python -m harness.approvals review REQUEST_ID --actor operator@example.com
```

API servers can adapt `ApprovalHTTPTransport` to their framework, and managed workers
can lease pending requests with `ManagedApprovalQueue`. Request submission and terminal
decisions are idempotent; queue delivery uses exclusive expiring leases. The adapter
does not provide HTTP authentication or authorization—deployments must enforce both at
the server boundary before calling it.

## Command sandboxes

Docker requires `ADK_CODING_SANDBOX_IMAGE`. Kubernetes executes only in an existing
task pod and requires `ADK_CODING_K8S_NAMESPACE`, `ADK_CODING_K8S_POD`,
`ADK_CODING_K8S_WORKSPACE`, and an explicit
`ADK_CODING_K8S_NETWORK_ISOLATED=1` assertion. The adapter does not create pods or
silently fall back to the host.

The enterprise remote backend requires `ADK_CODING_REMOTE_ENDPOINT` (HTTPS),
`ADK_CODING_REMOTE_TOKEN`, and `ADK_CODING_REMOTE_WORKSPACE`. Deployments needing
mTLS, workload identity, or a proprietary protocol can inject the `RemoteTransport`
contract instead of the built-in bearer-token HTTP transport. All backends apply the
same timeout, output-bound, artifact, and secret-redaction contracts.

## Distributed control state

Set `ADK_CODING_CONTROL_DATABASE_URL` to a `postgresql://` or
`postgresql+psycopg://` URL in a multi-worker deployment. Event sequence allocation is
serialized per task inside a transaction, and a database-clock lease prevents two
workers from executing the same task concurrently. The local JSONL path remains the
default for single-process development.

## Final-reviewer ablation

The reviewer is disabled by default and never overrides deterministic verification.
Run the same evaluation cases once with `ADK_CODING_FINAL_REVIEWER=0` and once with it
set to `1`, then compare paired sample metrics:

```bash
uv run python -m harness.review reviewer-ablation-samples.json
```

The comparator rejects missing pairs and changes to the harness revision, model, or
reasoning setting. It reports pass rate, cost per passed task, uncached input, cache
ratio, prefix versions, tool calls, and wall time; do not infer quality gains from the
reviewer's additional tokens alone.

## Secret redaction

The redactor automatically recognizes common credential formats and sensitive mapping keys. Add environment variable names containing secrets when their format is project-specific:

```bash
export INTERNAL_SERVICE_CREDENTIAL='...'
export ADK_CODING_REDACT_ENV_VARS=INTERNAL_SERVICE_CREDENTIAL
```

Only the value is retained in process memory; it is replaced before model-visible output and telemetry are returned.

## ADK persistence

Runtime services are selected through environment settings:

```bash
export ADK_SESSION_BACKEND=database
export ADK_DATABASE_URL='postgresql+asyncpg://user:pass@host/database'

export ADK_ARTIFACT_BACKEND=gcs
export ADK_ARTIFACT_BUCKET=my-agent-artifacts

export ADK_MEMORY_BACKEND=vertex
export GOOGLE_CLOUD_PROJECT=my-project
export GOOGLE_CLOUD_LOCATION=us-central1
export ADK_AGENT_ENGINE_ID=...
```

Local development can use in-memory services:

```bash
export ADK_SESSION_BACKEND=in_memory
export ADK_ARTIFACT_BACKEND=in_memory
export ADK_MEMORY_BACKEND=in_memory
```

## Add a coding skill

Follow the Agent Skills structure rather than adding another built-in tool:

```text
skills/my-workflow/
├── SKILL.md
├── scripts/
└── references/
```

`SKILL.md` should contain a precise description that tells the model when to load it. Keep detailed references and scripts outside the always-visible description.

## Change discipline

For every implementation todo:

1. Add or update the typed contract.
2. Implement deterministic behavior outside the model where possible.
3. Add focused unit tests.
4. Run the affected suite.
5. Commit that completed todo independently.
6. Update `docs/IMPLEMENTATION_STATUS.md` when the capability boundary changes.

Avoid combining prompt changes, tool changes, and model changes in one evaluation. They need separate ablations to identify the source of a gain or regression.
