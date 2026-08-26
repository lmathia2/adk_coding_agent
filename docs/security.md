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

The four tools resolve paths against the task worktree. Absolute paths and traversal outside that root are rejected. Mutations use temporary files and atomic replacement. Expected hashes provide optimistic concurrency when the agent edits a file it previously read.

Every managed task should run in a container or remote sandbox in addition to the Git worktree. The worktree prevents task-to-task code interference; the sandbox must enforce operating-system, process, network, and secret boundaries.

The production adapters fail closed: Kubernetes requires a pre-provisioned pod and an
explicit assertion that a deny-by-default NetworkPolicy is enforced, while the remote
adapter accepts only HTTPS or an injected enterprise transport. Neither backend falls
back to local execution after a configuration or transport failure. Output is redacted
before it is bounded or persisted as an artifact.

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
- explicit evidence for every acceptance criterion

A final reviewer model, when enabled, is advisory unless its findings are translated into deterministic checks or an explicit human decision.

## Project-local extensions and skills

Project instructions, skills, scripts, and extensions are executable supply-chain inputs. Production deployments should:

1. require project trust before loading them;
2. pin package and Git references;
3. record content hashes in the task event stream;
4. scan scripts before execution;
5. prevent a project skill from silently broadening tool or network permissions.

## Logging and privacy

Audit logs should contain operation hashes, classifications, approval decisions, paths, exit status, byte counts, and redacted diagnostics. Avoid storing source bodies, complete prompts, secrets, or customer data in centralized telemetry unless retention and access controls explicitly allow it.

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
