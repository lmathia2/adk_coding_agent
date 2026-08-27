# Development Guide

## Prerequisites

- Git
- Python 3.11 or newer
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

The lockfile resolves the tested Google ADK 2.7 minor and `fff-search==0.10.5`.
The normal `uv sync` therefore provides indexed search without a separate Pi/npm
extension or separately installed `rg`/`fd` binary. Install production-only
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
| `ADK_CODING_SEARCH_BACKEND` | `auto` | Indexed search: `auto`, `fff`, or `disabled`; host-side FFF is limited to authoritative local/bind-mounted workspaces |
| `ADK_CODING_CONTROL_DATABASE_URL` | unset | PostgreSQL control events and distributed task leases |
| `ADK_CODING_WORKER_ID` | process-derived | Stable owner identity for distributed task leases |
| `ADK_CODING_TASK_LEASE_SECONDS` | `900` | Distributed task-lease duration, renewed around long operations |
| `ADK_CODING_FINAL_REVIEWER` | `0` | Enable the advisory no-tool final-diff reviewer |
| `ADK_CODING_REVIEW_MODEL` | coding model | Optional reviewer model override |
| `ADK_CODING_REVIEW_MAX_CHARS` | `60000` | Maximum redacted diff characters sent to the reviewer |
| `ADK_CODING_TRACE_MODE` | `metadata` | Lifecycle trace mode: `off`, `metadata`, or `redacted`; raw storage is unsupported |
| `ADK_CODING_TRACE_MAX_CONTENT_BYTES` | `8192` | Maximum already-redacted JSON bytes per trace span |
| `ADK_CODING_SKILL_DIRS` | unset | Additional trusted Agent Skills roots, separated by the platform path separator |
| `ADK_CODING_SKILL_MAX_SELECTED` | `3` | Maximum enabled skill bodies in one work packet |
| `ADK_CODING_SKILL_CONTEXT_BYTES` | `24000` | Total catalog and selected-skill byte budget |
| `ADK_CODING_LEARNING_ENABLED` | `1` | Learn candidates from verified traces and run guarded trials |
| `ADK_CODING_LEARNING_MIN_SUPPORT` | `3` | Minimum observations per arm before promotion is possible |
| `ADK_CODING_LEARNING_TRIAL_PERCENT` | `20` | Deterministic percentage of matching tasks assigned the candidate |

## Indexed repository search

FFF is exposed as a reserved virtual command through the existing `bash` tool; it is
never passed to a shell and does not add a fifth model-visible tool:

```text
search grep --pattern TEXT [--path REL] [--mode literal|regex]
            [--case-sensitive] [--context 0..2] [--limit 1..50]
search grep --cursor TOKEN
search find --pattern TEXT [--path REL] [--limit 1..50]
search find --cursor TOKEN
search health
```

The default page limit is 20. Results are grouped and scheduled across files so one
high-frequency file cannot consume the first page. Continue only with the returned
opaque cursor; cursor requests cannot change the original query. Use bounded
`rg --json` through ordinary `bash` when a deterministic program must enumerate and
transform an entire result set.

The harness lazily starts FFF, disables filesystem-root/home scanning and symlink
following, and post-confines every result to the workspace. Cursor snapshots contain
paths, positions, hashes, and query hashes—not source bodies or raw queries—and live
under `ADK_CODING_STATE_DIR/fff`. Model-visible pages and any spill artifact are
redacted and byte-bounded. Normal `bash` tracing policy still applies to the command
arguments.

Successful, non-replayed `edit` and `write` operations synchronously refresh the
index. Changes produced by ordinary shell commands are observed by FFF's watcher and
can briefly lag. Health output is sanitized and does not expose the absolute workspace.

Host-side FFF is enabled only for the local backend and Docker's authoritative
bind-mounted workspace. Kubernetes and remote sandboxes return a typed unavailable
result rather than searching a potentially different host tree. Set
`ADK_CODING_SEARCH_BACKEND=disabled` to turn the virtual backend off explicitly.

Version 0.10.5 publishes wheels for macOS arm64/x86_64, Windows x86_64, and Linux
arm64/x86_64 with glibc 2.38 or newer. Other architectures, musl distributions, or
older Linux systems may require a working Rust toolchain to build the locked source
distribution. That is the only case where the normal clone-and-`uv sync` path needs
additional native build tooling.

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

The project root `${ADK_CODING_WORKSPACE}/.agents/skills` is discovered by default.
Additional roots must be named explicitly with `ADK_CODING_SKILL_DIRS`. Explicit
`$skill-name` mentions take precedence over lexical matches and are also required to
disclose linked reference files. Learned revisions live under
`${ADK_CODING_STATE_DIR}/learned-skills/{candidates,active,disabled}`; do not edit or
move them while a task is running.

Inspect the sanitized trace and learned lifecycle records with:

```bash
uv run adk-coding-agent trace-export \
  --state-root /path/to/durable-state --task-id issue-1842
uv run adk-coding-agent learned-skills \
  --state-root /path/to/durable-state
uv run adk-coding-agent disable-skill \
  --state-root /path/to/durable-state learned-skill-name
```

Launcher runs trace under the supplied task ID. A direct Agents CLI/API run has no
task ID before its first callbacks, so its trace is consistently keyed by the ADK
session ID; use that ID for `trace-export`.

## Change discipline

For every implementation todo:

1. Add or update the typed contract.
2. Implement deterministic behavior outside the model where possible.
3. Add focused unit tests.
4. Run the affected suite.
5. Commit that completed todo independently.
6. Update `docs/IMPLEMENTATION_STATUS.md` when the capability boundary changes.

Avoid combining prompt changes, tool changes, and model changes in one evaluation. They need separate ablations to identify the source of a gain or regression.
