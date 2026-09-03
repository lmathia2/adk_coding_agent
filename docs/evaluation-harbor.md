# Harbor evaluation adapter

Skein runs as a host-side Harbor external agent. The ADK/model loop and provider
credential stay in the host process; `read`, `bash`, `edit`, `write`, repository
inspection, and verification execute through Harbor's task environment. Harbor
does not receive the provider credential through agent or task environment variables.
For this adapter only, dependency, network, unknown-command, and Git authority
is delegated to Harbor's disposable task environment; its task policy and
container boundary remain the enforcement layer. Local `skein eval-run` keeps
the standard restrictive approval policy.

## Pinned contract

- Python 3.12
- Skein: the Git revision recorded by the job
- Harbor 0.22.0 (`uv.lock` and the `eval` extra)
- Pier 0.3.1 for DeepSWE v1.1
- concurrency: 1
- retries: 0 for ordinary agent failures
- task state: a fresh `agent/skein-state` directory per Harbor trial

Install Harbor without changing the normal Skein runtime:

```bash
uv sync --extra eval
```

Pier is a separate Harbor-compatible runner and should remain isolated from the
project environment:

```bash
uv tool install datacurve-pier==0.3.1
```

## Resumable suite runner

The checked-in CLI runs the frozen samples sequentially and keeps the complete
Harbor job tree, Skein traces, events, metrics, verifier results, command
artifacts, stdout/stderr, run metadata, and SHA-256 file inventory:

```bash
python scripts/run_harbor_eval.py --suite smoke --plan
python scripts/run_harbor_eval.py --suite smoke --task-id modernize-scientific-stack
python scripts/run_harbor_eval.py --suite smoke
python scripts/run_harbor_eval.py --suite broader
python scripts/run_harbor_eval.py --suite full
```

Rerun the same command after an interruption. Completed task keys are skipped,
an incomplete Harbor job is resumed with `harbor job resume`, and a finished
infrastructure error gets a separate attempt directory. A result written before
a wrapper crash is recovered from disk without rerunning the task.
Ordinary verifier failures are completed results and are not retried. The CLI
rejects a jobs directory whose fixed model, reasoning, sample, configuration,
attempt count, or Git revision differs from its original run contract.

## Local Harbor run

Use the checked-in external-agent import path and pass no provider secret to
`--agent-env`, the task, or the container. For an authorized Codex workspace:

```bash
harbor run \
  --task terminal-bench/TASK_ID@TASK_ARTIFACT_SHA256 \
  --agent harness.evals.harbor:SkeinHarborAgent \
  --model gpt-5.6-luna \
  --agent-kwarg provider=openai_codex \
  --agent-kwarg reasoning=max \
  --n-concurrent 1 \
  --n-attempts 1 \
  --max-retries 0
```

For a fixed-model OpenRouter trial, load `OPENROUTER_API_KEY` into the host shell
and reference its name only:

```bash
harbor run \
  --task terminal-bench/TASK_ID@TASK_ARTIFACT_SHA256 \
  --agent harness.evals.harbor:SkeinHarborAgent \
  --model meta/muse-spark-1.2-contributor \
  --agent-kwarg provider=openrouter \
  --agent-kwarg reasoning=xhigh \
  --agent-kwarg api_key_env=OPENROUTER_API_KEY \
  --n-concurrent 1 \
  --n-attempts 1 \
  --max-retries 0
```

Use the same agent import and kwargs with `pier run -p deep-swe/tasks/<task-id>`.
Do not append benchmark-specific instructions. The adapter forwards Harbor's
instruction unchanged.

Each trial writes `skein-state/evaluation/result.json` plus durable events,
metrics, traces, command artifacts, the model/config identity, and a redacted
error classification under Harbor's agent log directory. Harbor `AgentContext`
receives token/cache/cost totals and the versioned Skein result metadata.

The adapter supports Linux tasks only. It fails rather than copying credentials,
exposing `/tests` or `/solution`, retrying around provider limits, or converting a
missing/unverified Skein result into a successful agent result.
