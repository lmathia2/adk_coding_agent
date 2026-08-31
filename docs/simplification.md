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

## Migration

Use the new default YAML. Removed fields and provider/backend IDs fail closed.
Existing credentials, downloaded models, and run data are not deleted. Old runs
and experiments should not be resumed under a different behavior hash.

## Verification

Final measurements and verification results are recorded after the retained
execution paths pass tests. Live-model behavior is not implied by unit-test success.
