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
