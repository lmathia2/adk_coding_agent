# Evaluation Guide

## North-star metric

The primary metric is:

```text
cost per passed task
```

This prevents token savings from being reported as progress when completion quality falls. A task passes only through deterministic verification; an LLM judge may provide supplemental analysis but is not the source of truth.

## Case construction

Prefer tasks derived from recent human pull requests:

1. select a base revision before the human change;
2. preserve the original issue or request as the task;
3. hide the solution diff from the agent;
4. add fail-to-pass tests that fail at the base revision and pass after the intended change;
5. add regression and scope checks;
6. record task metadata without exposing the answer.

The starter suite in `tests/eval/suites/core.json` demonstrates the schema with a localized Python bug. It is a smoke case, not a representative benchmark.

`tests/eval/suites/real_repositories.json` contains the source-backed large-repository
suite. Its cases are derived from merged human pull requests in Django and Ruff. Each
case records the upstream task URL, immutable base/solution/merge revisions, repository
size at observation time, expected scope, forbidden held-out paths, exact held-out blob
hashes, and validation commands. `load_real_repository_suite()` rejects provenance,
revision, path, or case-alignment drift before a runner fetches a repository.

## Portable case schema

Each `EvaluationCase` contains:

- task ID and description
- repository fixture
- typed `TaskRequest`
- expected and forbidden changed-file globs
- required validation-command fragments
- cost, context, iteration, and latency budgets
- tags for slicing results

The grader combines the case with:

- final task status
- changed paths
- `VerificationReport`
- telemetry summary

A case fails when any required check fails.

## Required metrics

### Quality

```text
pass rate
acceptance-criterion satisfaction
regression count
scope violations
held-out test modifications
human review severity
```

### Context economy

```text
input tokens
output tokens
reasoning tokens
cache-read tokens
cache-write tokens
uncached input tokens
cache-read ratio
static prefix versions
static prefix tokens
dynamic suffix tokens
compactions
```

### Agent behavior

```text
tool calls
duplicate/replayed calls
model-visible tool bytes
omitted tool bytes
files read before first correct edit
failed edits
no-progress cycles
replans
user interventions
```

### Reliability

```text
resume success
workspace/checkpoint mismatch
duplicate side effects after resume
post-compaction goal consistency
branch reconstruction success
```

### Latency and cost

```text
time to first useful edit
model latency
tool latency
verification latency
wall time
cost per task
cost per passed task
```

`MetricsStore` persists these values in provider-neutral tables and computes cached versus uncached input explicitly.

## Mandatory ablations

Use the same model, reasoning settings, task set, and concurrency for each variant:

| Variant | Capability added |
|---|---|
| A | Stock ADK coding loop |
| B | Stable prompt and four tools |
| C | Bounded outputs and artifacts |
| D | Task Ledger and deterministic routing |
| E | Evidence-based verification |
| F | Stable-prefix cache discipline |
| G | Coding-aware compaction |
| H | Structural repository map |
| I | Optional semantic retrieval |
| J | Optional final-diff reviewer |

Do not compare a prompt change bundled with a model change. Do not compare a tool change bundled with a new test-selection policy.

## Run deterministic tests

```bash
uv run pytest -q tests/unit
uv run pytest -q tests/integration
```

The interruption scenario proves that:

- the event stream replays;
- the same worktree is reattached;
- the checkpoint matches;
- an exact mutation is not repeated;
- deterministic verification passes after resume.

## Validate an evaluation suite

```python
from pathlib import Path
from harness.evals import load_evaluation_suite

suite = load_evaluation_suite(Path("tests/eval/suites/core.json"))
print([case.case_id for case in suite.cases])
```

Grade a completed run:

```python
from harness.evals import grade_case

result = grade_case(
    case,
    status="complete",
    changed_paths=changed_paths,
    verification=verification_report,
    metric_summary=metrics_store.task_summary(case.case_id),
)
assert result.passed, result.model_dump_json(indent=2)
```

## Long-horizon stress suite

A release candidate should include tasks with:

- at least 50 tool calls;
- two or more compactions;
- multiple test-repair loops;
- process termination before and after mutation;
- process termination during verification;
- user steering after partial implementation;
- two alternative branches;
- model switch at a checkpoint;
- external workspace modification.

## Cache acceptance criteria

Treat these as starting engineering targets, not guaranteed benchmark outcomes:

```text
stable prefix unchanged on >95% of non-boundary calls
context per work batch at least 2× lower than stock baseline
no statistically meaningful pass-rate regression
prefix mutations have an explicit recorded reason
full tool logs absent from normal model context
```

Report confidence intervals over tasks and repeated runs. Cost and latency distributions are usually skewed, so include median, p90, and p95 rather than averages alone.

## Large-repository evaluation

The structural map should remain disabled for small repositories and evaluated separately on larger codebases. Compare:

```text
shell search only
shell + static map
shell + task-ranked structural map
structural map + semantic fallback
```

Retain an indexing layer only when it improves pass rate, cost per passed task, files read, or turns to completion. Index size and retrieval sophistication are not success metrics.

## Result publication

Every published result should include:

- exact repository revisions and task construction method;
- harness commit;
- model and reasoning configuration;
- provider caching configuration;
- concurrency and timeout settings;
- tool and network policy;
- pass/fail evidence;
- token and cost accounting method;
- failed-task traces or categorized failure reasons.

Without those details, a harness comparison is not reproducible.
