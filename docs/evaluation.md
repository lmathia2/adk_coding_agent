# Evaluation

The primary outcome is cost and latency per deterministically passed task.
Smaller prompts and fewer source lines are useful engineering measurements, not
evidence of coding quality.

## Current deterministic checks

```bash
.venv/bin/pytest tests/unit tests/integration
npm test --prefix clients/terminal
```

These cover file confinement/atomicity, failed-operation receipts, repository
search, context bounds, skill selection, verification strength, replay, steering,
protocol mapping, and factory swapping without live credentials.

The integration replay case reconstructs the worktree, event stream, checkpoint,
and tools after interruption, repeats an exact mutation without duplicating it,
and verifies the resulting code through the managed execution boundary.

## Model evaluations

The case schema and deterministic grader remain in `harness/evals`.
`tests/eval/suites/core.json` is a smoke case, not a representative benchmark.
Source-backed cases in `real_repositories.json` include provenance and held-out
checks. Inspect and authorize external downloads and provider usage separately.

```python
from pathlib import Path
from harness.evals import load_evaluation_suite

suite = load_evaluation_suite(Path("tests/eval/suites/core.json"))
print([case.case_id for case in suite.cases])
```

For each real task record the harness revision, model/reasoning configuration,
task source and base revision, verification commands, allowed scope, provider and
network policy, token/cache accounting, tool calls, latency, and failure traces.
Never grade success from a model's natural-language completion claim.

For Harbor/Pier benchmark execution, use the pinned host-side external-agent
adapter and commands in [evaluation-harbor.md](evaluation-harbor.md).

For one host-side trial, use the noninteractive runner. It reuses the server's
production coordinator and writes a second copy of the single stdout result to
`STATE/evaluation/result.json`:

```bash
skein eval-run \
  --workspace /path/to/clean/task-repository \
  --state-root /path/to/fresh/trial-state \
  --task-id smoke-001 \
  --model gpt-5.6-luna \
  --reasoning max \
  --wall-time-seconds 900 \
  "Fix the task and verify the result."
```

Keep `--auth-state-root` on the trusted host and outside the fresh trial state.
The command never copies credentials into the workspace or its result artifacts.

For a fixed-model OpenRouter trial, keep the API key on the host and reference only
its environment-variable name:

```bash
# Load OPENROUTER_API_KEY through your shell's secret manager first.
export OPENROUTER_API_KEY
skein eval-run \
  --workspace /path/to/clean/task-repository \
  --state-root /path/to/fresh/trial-state \
  --task-id smoke-001 \
  --provider openrouter \
  --model meta/muse-spark-1.2-contributor \
  --reasoning xhigh \
  --wall-time-seconds 900 \
  "Fix the task and verify the result."
```

The contributor endpoint permits Meta to use prompts and outputs to improve its
products. Use it only for public benchmark repositories and non-sensitive fixtures.
The adapter requests OpenRouter routing metadata and persists only allowlisted model,
provider, region, attempt, token, cache, and cost fields. Dynamic routers such as
`openrouter/pareto-code` are a separate system-level treatment, not a fixed-intelligence
harness comparison.

### Subscription automation authorization gate

Status on 2026-09-01: **blocked pending an authorized authentication path**.

Official OpenAI documentation supports access tokens for trusted noninteractive
Codex workflows only in ChatGPT Business and Enterprise workspaces. It describes
API keys as the authentication method for programmatic Codex CLI workflows. It
also limits non-human service accounts to pay-as-you-go plans:

- [Codex access tokens](https://learn.chatgpt.com/docs/enterprise/access-tokens)
- [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Codex service accounts](https://learn.chatgpt.com/docs/enterprise/service-accounts)

Those sources do not authorize using a personal ChatGPT subscription's browser
device credential through this custom ADK provider for a batch benchmark. Do not
start the live smoke queue with that credential. Unblock it only with written
OpenAI confirmation, a supported Business/Enterprise Codex access token, a
pay-as-you-go service account, or Platform API-key billing. This is a product
authorization decision, not legal advice. The deterministic engineering gate and
fixture preparation do not require provider access and may proceed.

Compare one change at a time on the same tasks and model: context caps, search,
directory skills, or compaction. Include failures and repeated runs. Measure
behavioral correctness before claiming speed or token-efficiency improvements.

## Historical material

Reports under `docs/audits` and `tests/eval/results`, and old ablation specifications,
describe earlier implementations. Their automatic-learning, semantic-retrieval,
reviewer, or Magnitude results do not validate this simplified runtime. Removed
comparison CLIs and learning-promotion code are recoverable from Git, not supported
execution paths.

The simplification report records source/complexity reductions and deterministic
verification only. A new live multi-task run is still required before publishing
a new model-quality or latency claim.

## Notebook PTC promotion gate

The notebook-native branch includes deterministic ledger, view, broker, restart,
timeout, rich-output, and prompt-manifest tests. These validate contracts, not model
quality. Before changing the default, run the same model, reasoning setting, workspace
revisions, verification commands, and task suite in four-tool and one-REPL modes and
record pass rate, cost per passed task, uncached input, cache-read ratio, model/tool
calls, and wall time. No provider-backed paired run has been recorded yet.
