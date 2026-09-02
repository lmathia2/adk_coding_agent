# Security Model

## Scope

The harness executes model-selected commands and edits source code. The primary security boundary is therefore the workspace and execution policy, not the prompt. Prompt instructions improve behavior but are not authorization.

## Trust boundaries

```text
Untrusted
  user task text
  repository content
  project-local skills and instructions
  model output
  shell output
  package scripts

Trusted control plane
  workspace manager
  path confinement
  command classifier
  approval policy
  secret redactor
  receipt/checkpoint stores
  deterministic verifier
```

Repository files can contain prompt injection. They are treated as data unless the control plane intentionally loads an instruction or skill file from a trusted project.

## Filesystem isolation

The file tools resolve paths against the configured workspace. Paths that resolve outside that root are rejected. Mutations use temporary files and atomic replacement. Expected hashes provide optimistic concurrency when the agent edits a file it previously read.
Rejected model tool inputs return bounded structured errors so the model can recover;
they do not weaken confinement and do not escape as fatal ADK workflow exceptions.
Read-only shell classification is intentionally conservative for host-root traversal:
commands such as `find /` require approval even in the local adapter. Model tool work
runs outside the async server loop so a bounded command cannot prevent cancellation,
steering, or WebSocket keepalives while it executes.

The host-local command adapter is not an OS security boundary. Docker command
execution mounts the same workspace used by file tools, with network disabled by
default. File and native-search code still execute in the harness process; Docker
alone does not isolate that process. Remote and Kubernetes adapters were removed
because their command filesystem was not the file tools' authoritative workspace.

The server's `--production` gate rejects the host-local adapter before it creates
state or opens a listener. Configuration inspection reports the effective sandbox,
so an operator does not have to infer the isolation level from YAML or process logs.

Recommended production defaults:

```text
filesystem: task worktree + artifact directory only
network: disabled, then allowlisted per operation
processes: CPU, memory, PID, and wall-time limits
credentials: short-lived and operation-scoped
Git: local reads allowed; push and history mutation gated
cloud: no ambient deploy or administration credentials
```

## Command policy

Commands are split at shell control operators and pipelines. The highest-risk segment determines the decision.

| Risk | Default |
|---|---|
| Read-only search and inspection | Allow |
| Build and test | Allow |
| Workspace-local mutation | Allow |
| Dependency installation | Require approval |
| Network access | Require approval |
| Git-history mutation | Require approval |
| Publish or deploy | Require approval |
| Unknown executable | Require approval |
| Destructive operation | Deny |

Approval is represented by a fingerprint over the normalized operation. This allows the control plane to approve one exact command rather than enabling a broad category for the process.

Persisted approvals are checked for the current task on every command attempt.
An approved fingerprint is never copied into the shared policy: switching tasks
requires a separate approval, and an expired approval stops authorizing execution
even if the same adapter used it successfully earlier.

The new Pi terminal opts into asynchronous approval waits for worker commands and
deterministic verification. The authenticated server checks run owner, workspace,
harness, request ID and exact fingerprint; clients cannot supply the decision actor.
Decisions are stored in `STATE_ROOT/runs/RUN_ID/approvals.db`. The receipt authorizes
that exact operation in that task, not one guaranteed execution; repeated model
attempts within the task may reuse approval. Tool results report actual execution.

Pending waits are bounded to 32 per run and expire after the smaller of
`server.approval_wait_timeout_seconds` (default 120 seconds) and half the server's
idle timeout (90 seconds with defaults). Cancellation and expiry close pending
requests without executing them. Disconnecting a terminal does not cancel its run;
another authenticated terminal can resume it and inspect `/approvals`. Restarted
runs are failed closed and are never automatically reinvoked. Uncertain decisions
are not automatically replayed. Once a command has launched, cancellation remains
best-effort: closing a dialog does not revoke an approval or undo effects.

The approval UI defaults to Deny and does not offer approval for a command too large
to display in full. Commands can contain redacted secrets. Existing non-interactive
clients retain immediate blocked results instead of entering invisible human waits.
No additional network, destructive, publication or Git mutation permission is enabled.

`nb-cli` is an operator/inspection surface, not an alternate execution authority.
Local `nb read --no-output`, `nb search`, and `nb status` are classified as inspection.
Remote server/token flags, `nb execute`, notebook mutation, and unrecognized `nb`
subcommands remain approval-gated. The supported `adk-coding-agent notebook` command
passes an argument vector without a shell and never executes notebook cells.

### Indexed-search branch

Commands whose first token is the reserved word `search` are parsed before shell
classification. Only the documented `grep`, `find`, and `health` grammar is accepted;
unknown options, duplicate options, newlines, shell operators, and mixed cursor/query
requests fail closed and never reach a shell. Every returned path is independently
resolved beneath the workspace, `.git` and `.artifacts` are excluded, filesystem-root
and home scans are refused, and symlink following is disabled.

Opaque cursors are bound to a workspace and operation and to content hashes for
matched files. Missing, tampered, cross-workspace, cross-operation, and stale cursors
are rejected. Snapshot rows retain relative positions and hashes, not raw patterns or
source bodies. Redacted spill artifacts can contain source snippets and therefore need
the same retention and access controls as other tool artifacts.

The native FFF library runs in the trusted host control plane for local and Docker
bind-mounted workspaces. Its compromise blast radius is consequently larger than a
sandboxed `rg` subprocess. Both supported command backends use the authoritative
host workspace.

The following should never be implemented as prompt-only rules:

- filesystem confinement
- network allowlisting
- secret access
- deployment permission
- destructive command denial
- approval state

## Secret handling

The redactor removes:

- configured secret values
- common GitHub, AWS, Google, Slack, JWT, and private-key formats
- authorization headers
- credential assignments
- values under sensitive mapping keys

Redaction occurs after tool execution and before model-visible output or telemetry. Full artifacts must also be redacted or encrypted according to deployment policy; keeping an unredacted artifact and hiding only the model result is not sufficient for a shared service.

Do not expose the complete environment to the model or shell. Pass only variables required for an approved operation.

## Replay and side effects

ADK resumability can repeat a tool call after interruption. The harness therefore uses content-addressed receipts for file mutations. An exact `edit` or `write` replay returns the prior success instead of applying the side effect again.

Read, build, and test operations may safely rerun. External writes, publishing, deployment, and irreversible operations require provider idempotency keys and must not be automatically replayed.

## Workspace integrity

A checkpoint couples:

- task and session IDs
- base revision
- workspace ID
- Git tree/workspace fingerprint
- ledger version and hash
- compaction checkpoint

Resume should fail closed when the worktree fingerprint does not match the checkpoint and the difference cannot be explained by durable tool receipts.

Cleanup refuses a dirty worktree unless a human or higher-level policy explicitly passes `force=True`.

## Completion integrity

The model cannot authorize its own completion. The verifier independently checks:

- required validation commands
- targeted or broader tests
- syntax/lint/type diagnostics
- `git diff --check`
- permitted and forbidden paths
- typed references for validation results the harness actually produced for every
  acceptance criterion; model claims remain diagnostic context and cannot grant or
  prevent completion
- at least one successful behavioral verifier for executable-code changes unless the
  caller explicitly requested a syntax-only or static-only contract

Verification uses the tools' configured sandbox, YAML policy, redactor inputs, and
state root. Approval requests are keyed by the actual task ID, and mutable approved
fingerprint sets are not shared between verification tasks. uv verification commands
run offline and without implicit dependency synchronization. There is no unmanaged
shell verifier fallback or reviewer model.

## Project-local extensions and skills

Project instructions, skills, scripts, and extensions are executable supply-chain inputs. Production deployments should:

1. require project trust before loading them;
2. pin package and Git references;
3. record content hashes in the task event stream;
4. scan scripts before execution;
5. prevent a project skill from silently broadening tool or network permissions.

The built-in server implements the first boundary explicitly: project instructions
and project-local skills are omitted unless that launch supplies `--trust-project`.
The choice is a volatile runtime binding rather than portable YAML, so cloning or
reusing a composition cannot silently trust a different workspace. The server and
launcher announce the effective choice. Explicit external roots remain operator
configuration. There is no automatic skill-learning or promotion lifecycle.

## Logging and privacy

Public conversational streaming validates the complete control header before
publishing Markdown. It cannot authorize coding completion, waive verification,
or dispatch tools after publication starts. Partial replies remain partial when
cancelled; a changed final result or workspace fails closed. Stream state is scoped
to the ADK invocation and discarded on exit, including failure and cancellation.

Words, incomplete known secrets, and potentially sensitive spans are buffered for
redaction before entering the public event log. Pattern-based redaction is not a
guarantee of detecting every secret. Raw model content remains in ADK's local
session history (`STATE_ROOT/adk/sessions.db` with SQLite); protect the whole state
directory, not only exported traces. The terminal receives only the public projection.

Audit logs should contain operation hashes, classifications, approval decisions, paths, exit status, byte counts, and redacted diagnostics. Avoid storing source bodies, complete prompts, secrets, or customer data in centralized telemetry unless retention and access controls explicitly allow it.

The local trace store has only `metadata` and `redacted` content modes (plus `off`).
There is no raw mode. Redaction precedes byte bounding and persistence, exports read
the already-sanitized records. Operators must still apply retention, access-control,
and backup policy to the state directory. State is local and single-process; no
distributed ownership/lease backend is provided.

## Required adversarial tests

Before production release, exercise:

- path traversal and symlink escape attempts
- shell operator and quoting bypasses
- exfiltration through pipes and package scripts
- prompt injection in source and test files
- credentials in stdout, stderr, JSON, and nested objects
- duplicate mutation after process termination
- workspace mismatch on resume
- held-out test modification
- forced Git push and destructive reset
- artifact retrieval across task boundaries
- malformed virtual-search commands and cross-workspace/cross-operation/stale cursors
- binary, ignored, internal-artifact, and external-symlink search decoys
- secret-bearing and oversized search pages before and after artifact spill
