# Minimal harness simplification

The user's current priority is a small, correctly wired harness, not maximal features.
The original design and historical scores are not a release contract for this version.

## Baseline

Before this cleanup (including the user's uncommitted hello command):

- 155 production Python/Go files in app/, harness/, clients/tui/ (excluding Go tests).
- 31,720 source lines; 28,876 Python lines.
- 768 lines in install.sh and start.sh.
- Ruff C901 McCabe complexity sum: 3,074 across 1,026 Python functions; maximum 27.

Measure the same paths before and after. Count physical lines, including comments
and blank lines; exclude tests, fixtures, generated files, and lockfiles from
production metrics. Obtain each function's McCabe value using Ruff with
`--select C901 --config 'lint.mccabe.max-complexity=0' --output-format=json`.
Do not equate fewer tests or a smaller lockfile with less production complexity.

## Decisions

- Keep ADK as the model/tool runtime, one worker, four tools, bounded context,
  trusted directory skills, traces, deterministic verification, resumable state,
  approvals, and the common WebSocket/TUI interface.
- Remove Magnitude/LiteLLM and its function-ID translation workaround. Keep the
  provider registry; the native Gemini and Codex adapters are the built-ins.
- Replace dynamic shadow imports and fallback tools with the real atomic file
  primitives. Failed mutations must not be cached as successful receipts.
- Remove remote/Kubernetes command backends: their workspaces were disconnected
  from host file tools. Local and Docker share the authoritative workspace.
- Remove semantic-intelligence scaffolding: no runtime invoked its plans.
- Remove automatic skill synthesis/trials/promotion and project-memory injection.
  Skills now change only when their trusted directory content is edited.
- Remove the advisory reviewer and duplicated graph DSL. The YAML graph was
  validated against fixed Python code, not executed as a graph configuration.
- Remove experiment-only comparison report engines. Keep deterministic graders,
  core search/skill tests, and historical evaluation data without relabeling it.
- Remove cloud/distributed persistence and ADK service signature guessing. Use
  pinned ADK constructors with local SQLite/files or in-memory services.
- Use one YAML factory for the server and Agents CLI entrypoint. Remove the
  separate environment-configured worker that the root app did not execute.
- Remove unused alternate task/event/repository/context schemas and the secondary
  context compiler. Keep the contracts consumed by the live workflow.
- Supply verification explicitly with the tools' sandbox, policy, secret inputs,
  state root, and actual task ID. Remove the unmanaged shell-verifier fallback and
  environment-selected verification backend. Do not synchronize dependencies in
  verification or share approved fingerprints across tasks.

## Migration

Use the new default YAML. Removed fields and provider/backend IDs fail closed.
Existing credentials, downloaded models, and run data are not deleted. Old runs
and experiments should not be resumed under a different behavior hash.

The deployment contract is now one local server process per state directory.
Old distributed/cloud configurations and behavior environment overrides must move
to the supported YAML fields. Directory skills are manual operator-maintained
inputs; the harness no longer claims to improve itself automatically.

## Measured result

Measured after the production-code cleanup on 2026-08-30, using the same scope as
the baseline. The user's pre-existing hello command is present in both measurements
and remains uncommitted.

| Measure | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Production Python/Go files | 155 | 122 | 33 files |
| Production source lines | 31,720 | 23,784 | 7,936 (25.0%) |
| Python source lines | 28,876 | 20,940 | 7,936 (27.5%) |
| Installer + launcher lines | 768 | 259 | 509 (66.3%) |
| Python McCabe sum | 3,074 | 2,275 | 799 (26.0%) |
| Python functions measured | 1,026 | 760 | 266 |
| Highest function complexity | 27 | 27 | unchanged |
| Locked packages, including dev/platform entries | 140 | 71 | 69 (49.3%) |

The largest remaining function is the server run controller, `_drive` (27).
The coding orchestration loop fell from 25 to 19. Aggregate McCabe is reported
separately from peak complexity; removal is not evidence of lower runtime latency,
token usage, or improved model success rate. This is a substantially smaller harness,
not a claim that a 23.8k-line server/client stack is the mathematical minimum.

## Focused commits

- `9b038fd`: remove Magnitude and simplify installation.
- `41e548c`: wire atomic file tools and remove disconnected adapters.
- `bd3e1ba`: reduce orchestration to one worker and directory skills.
- `4db3771`: use one YAML bootstrap and local persistence.
- `0f0f077`: wire verification to the configured sandbox and task approvals.

## Verification

- 414 deterministic Python unit/integration tests pass, including the existing
  user-owned hello test, real ADK Runner/fake-model coverage, replay, confined atomic
  mutations, failure receipts, configuration isolation, and installer guards.
- Python compilation, Ruff, and Pyright pass.
- All five Go packages pass `go test -race ./...`; the TUI builds successfully.
- `uv lock --check --offline` and `uv pip check` pass after removing the unused
  installed dependencies. The macOS development environment has 66 installed packages;
  the cross-platform lockfile contains 71 entries.
- Real full installation passed twice in a temporary macOS checkout, offline from
  cached dependencies. Both builds produced working CLI/TUI/launcher entrypoints.
  The second run removed an environment sentinel and preserved the sentinel outside
  `.venv`. No global command links, model downloads, or credential state were removed.
- Installer regression tests reject a symlinked `.venv` and a user-owned command
  file before dependency installation or deletion.

No live-provider model request or new model-quality benchmark was run. ADK's
experimental-feature/deprecation warnings remain visible. Historical book-rubric
scores and earlier live runs are not re-certified by this cleanup.
