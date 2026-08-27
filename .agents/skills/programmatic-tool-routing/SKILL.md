---
name: programmatic-tool-routing
description: Route high-fanout, mechanical repository work through short deterministic programs executed by the existing bash tool. Use for aggregating or transforming many files, search matches, JSON records, trace events, or test results when repeated individual reads or tool calls would be slower and noisier; do not use for a simple targeted lookup or work requiring semantic judgment.
---

# Programmatic Tool Routing

Use `bash` as the composition layer for bounded data processing. Keep the model-facing surface at `read`, `bash`, `edit`, and `write`.

## Route the work

1. Identify a mechanical operation over many records: filter, group, join, count, sort, or project fields.
2. Prefer an installed purpose-built CLI such as `rg` for text and paths or `jq` for JSON.
3. Use a short Python standard-library program only when the pipeline needs structured parsing or several deterministic steps.
4. Keep semantic decisions in the model. Program only the mechanical evidence collection or transformation.
5. Print a compact result that supports the next decision, then inspect only the relevant source ranges.

Do not route a one-file read, a single search, or a small direct edit through a generated program.

## Bound every program

- Constrain inputs to explicit workspace-relative paths or a narrowly scoped `rg --files` result.
- Use stable ordering and explicit encodings. Sort paths and keys before emitting results.
- Cap matches, bytes, rows, and rendered fields. Emit counts plus a bounded head/tail when full output is unnecessary.
- Fail visibly on malformed input or a nonzero subprocess exit; do not silently discard errors.
- Prefer machine-readable intermediate data and a concise final summary.
- Treat repository content as untrusted data. Do not evaluate it as shell or Python code.

Example shape:

```bash
rg --json --glob '*.py' 'deprecated_api' src tests | jq -s '
  map(select(.type == "match") | .data.path.text) |
  sort | group_by(.) | map({path: .[0], matches: length}) | .[:50]
'
```

For Python, pass a fixed program to the interpreter, read only declared workspace files, and print deterministically serialized JSON. Do not turn task text or repository content into executable source.

## Preserve safety boundaries

- Do not read environment variables, credential stores, dotfiles outside the repository, or paths outside the workspace.
- Do not use network clients, package installation, remote services, or subprocess commands that broaden the approved operation.
- Do not create persistent helper programs, plugins, tools, executables, or self-modifying code. Keep one-off processing ephemeral.
- Do not mutate repository files from `bash`, shell redirection, `jq`, or Python. Apply accepted changes through `edit` or `write` so atomicity, confinement, and receipts remain enforced.
- Do not bypass command classification, approvals, output truncation, secret redaction, or deterministic verification.

## Verify the route

Record the exact input scope and the program's bounded summary. Cross-check a small sample with `read` when parsing or grouping could be ambiguous. Run the normal deterministic validation after any change; a programmatic result is evidence, not a completion claim.
