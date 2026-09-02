# OpenCode coding-harness design

> **Status:** source-grounded implementation reference
> **Repository:** `/Users/mathiasl/src/opencode`
> **Revision:** `8e0f1c253b6b7292b419505af849d06747c0e049`
> **Related:** [Pi](coding-harness-pi.md), [Codex](coding-harness-codex.md), [ADK Long Horizon](coding-harness-adk-long-horizon.md), [comparison](coding-harness-comparison.md), [minimal ADK proposal](coding-harness-minimal-sota-extensions.md)

> **Scope:** the local coding-agent harness at this revision, with the currently wired `packages/opencode` session path treated as the production/default path and the in-progress `packages/core` V2 runner called out separately.
> **Method:** static, end-to-end source inspection at the stated revision. Test suites are mapped as contracts, but this document does not claim that every test was executed.
> **Notation:** **Fact** means directly implemented by the linked code. **Inference** means a design or security consequence inferred from that implementation.

## 1. Executive model

OpenCode is a stateful, local coding harness rather than merely a prompt plus a shell. Its default execution path is:

```text
user input
  -> durable session/message records
  -> agent + model + permission resolution
  -> provider-family base prompt
     + dynamic environment
     + project instructions
     + skill/MCP manifests
     + projected prior history
  -> provider-normalized streaming request
  -> streamed assistant/tool-call persistence
  -> host-owned permission checks and tool execution
  -> snapshots, usage/cost accounting, retry/overflow handling
  -> another provider turn until a terminal assistant turn
```

The currently complete loop lives in [`packages/opencode/src/session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1081), with stream settlement in [`processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:87), request/provider normalization in [`llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:85), and model-visible tool construction in [`tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:41). Durable storage and projections already use shared `packages/core` schema/services.

There is also a newer `packages/core` V2 runner. Its own source calls it a slice under construction and lists missing durable ownership, retry/doom-loop parity, fully policy-filtered MCP/plugin tools, cancellation settlement, snapshots/patches, and maintenance work. It should therefore not be mistaken for the default feature-complete harness described below. See the implementation-status comment in [`packages/core/src/session/runner/llm.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/runner/llm.ts:39). Where this document says **V1**, it means the `packages/opencode/src/session` path; **V2** means the newer `packages/core/src/session` architecture.

## 2. Architecture and ownership map

| Layer | Concrete owner | Responsibility and state class |
|---|---|---|
| Session orchestration (V1/default) | [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1081), [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:87) | Multi-step provider/tool loop; process-local active runner plus durable events/messages |
| LLM request | [`session/llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:85), [`session/llm/request.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/request.ts:56) | Provider resolution, prompt/options, streaming adapter, tool dispatch |
| Prompt/context | [`session/system.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/system.ts:27), [`session/instruction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/instruction.ts:110) | Provider-family static prompt plus volatile environment, instructions, skills, MCP guidance |
| Tool host | [`tool/registry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/registry.ts:101), [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:41) | Built-ins, plugin tools, MCP tools, permission/hook wrappers, schemas |
| Policy | [`permission/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/permission/index.ts:28), [`agent/agent.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/agent/agent.ts:98) | Ordered wildcard rules; deny/ask/allow; agent modes |
| Persistence | [`core/src/session/sql.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/sql.ts:22), [`core/src/session/projector.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/projector.ts:88) | SQLite sessions/messages/parts/todos/usage and event projection |
| Context pressure | [`session/overflow.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/overflow.ts:8), [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:28) | Token accounting, pruning, incremental summary, retained-tail replay |
| Workspace rollback | [`snapshot/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/snapshot/index.ts:23), [`session/revert.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/revert.ts:38) | Shadow-Git snapshots, per-step patches, session revert/unrevert |
| Delegation | [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:81), [`core/src/background-job.ts`](/Users/mathiasl/src/opencode/packages/core/src/background-job.ts:113) | Durable child sessions, process-local live job fibers, resume by child session ID |
| Programmatic tool calling | [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:188), [`packages/codemode/src/codemode.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/codemode.ts:9) | Experimental host-interpreted TypeScript orchestration over MCP tools |
| Extensions | [`plugin/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/plugin/index.ts:37), [`mcp/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/mcp/index.ts:38), [`skill/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/skill/index.ts:173) | Trusted in-process plugins, MCP clients, progressive-disclosure skills |

**State boundary.** Session/message/todo/snapshot metadata is durable; active runners, permission approvals, session status, instruction-claim de-duplication, and live background-job fibers are process-local. The active-runner map is explicit in [`run-state.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/run-state.ts:35), permission approvals live in instance state in [`permission/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/permission/index.ts:109), status is an in-memory map in [`session/status.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/status.ts:24), and background jobs describe themselves as process-local in [`background-job.ts`](/Users/mathiasl/src/opencode/packages/core/src/background-job.ts:113).

## 3. System prompt and context construction

### 3.1 Exact provider/model prompt routing

The V1 selector is a deterministic ordered cascade over the API model ID (and, for Kimi, provider ID):

| Match, in order | Prompt file |
|---|---|
| ID contains `muse` | Meta prompt with `{{MODEL_NAME}}` replaced by Muse Glimmer or Muse Spark |
| ID contains `gpt-4`, `o1`, or `o3` | `beast.txt` |
| ID contains `gpt` and `codex` | `codex.txt` |
| Other ID containing `gpt` | `gpt.txt` |
| ID contains `gemini-` | `gemini.txt` |
| ID contains `claude` | `anthropic.txt` |
| Lowercased ID contains `trinity` | `trinity.txt` |
| Lowercased ID contains `kimi`, or provider is `kimi-for-coding`, `moonshotai`, or `moonshotai-cn` | `kimi.txt` |
| Otherwise | `default.txt` |

This order and mapping are the implementation in [`session/system.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/system.ts:27). It matters that `gpt-4` is caught before the generic GPT branch, and that a Codex model receives the Codex-specific prompt only when its API ID contains both substrings.

An agent-configured `prompt` **replaces**, rather than appends to, this provider-family base prompt. The request then appends dynamic system material and an optional per-user system string. A plugin may transform the resulting system array. OpenAI OAuth sends the joined value as provider `instructions`; ordinary providers receive system-role messages. This exact order is in [`session/llm/request.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/request.ts:56).

### 3.2 Representative exact prompt: Codex family

The following is a verbatim, representative excerpt from the prompt actually selected for model IDs containing `gpt` and `codex`:

```text
You are OpenCode, the best coding agent on the planet.

You are an interactive CLI tool that helps users with software engineering tasks. Use the instructions below and the tools available to you to assist the user.

## Editing constraints
- Default to ASCII when editing or creating files. Only introduce non-ASCII or other Unicode characters when there is a clear justification and the file already uses them.
- Only add comments if they are necessary to make a non-obvious block easier to understand.
- Try to use apply_patch for single file edits, but it is fine to explore other options to make the edit if it does not work well. Do not use apply_patch for changes that are auto-generated (i.e. generating package.json or running a lint or format command like gofmt) or when scripting is more efficient (such as search and replacing a string across a codebase).

## Tool usage
- Prefer specialized tools over shell for file operations:
  - Use Read to view files, Edit to modify files, and Write only when needed.
  - Use Glob to find files by name and Grep to search file contents.
- Use Bash for terminal operations (git, bun, builds, tests, running scripts).
- Run tool calls in parallel when neither call needs the other’s output; otherwise run sequentially.
```

Later sections of the same prompt tell the model to preserve dirty-tree changes, never use destructive Git operations without request/approval, avoid generic frontend aesthetics, default to doing work without questions, ask only one targeted question after exhausting non-blocked work, keep final answers concise, and emit navigable file references. The authoritative complete 79-line prompt is [`prompt/codex.txt`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt/codex.txt:1).

The fallback prompt is materially different: it insists on very short CLI answers, searches extensively, verifies with tests/lint/typecheck, never commits unless explicitly asked, prefers `Task` for search to conserve parent context, and batches independent calls. Its exact text is [`prompt/default.txt`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt/default.txt:1). This is not a single universal OpenCode persona; provider-family prompt adaptation is a first-class primitive.

### 3.3 Dynamic environment, references, instructions, skills, and MCP context

Every turn’s system assembly adds the exact model/provider ID, working directory, workspace root, Git presence, platform, current date, and sorted named project references. These are volatile values, generated in [`session/system.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/system.ts:67). The main loop then combines environment, project instructions, MCP instructions, and skill inventory before converting history and optionally adding structured-output guidance in [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1257).

Project instruction discovery is layered but intentionally bounded:

- Global candidates include the OpenCode config `AGENTS.md` and, when enabled, `~/.claude/CLAUDE.md`; project candidates are `AGENTS.md`, `CLAUDE.md`, and `CONTEXT.md`. [`instruction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/instruction.ts:60)
- The project scan takes the first matching candidate class rather than blindly concatenating all formats, then adds configured local paths and URLs. Local reads use concurrency 8, remote reads concurrency 4, and URL fetches time out after five seconds. [`instruction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/instruction.ts:110)
- For a file being worked on, OpenCode walks from that file’s directory toward the session root and injects nearby nested instruction files. A per-assistant-message in-memory claim map prevents duplicate attachment; claims are cleared when the message settles. [`instruction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/instruction.ts:179), [`prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1330)

**Fact:** local and configured remote instruction text is placed into model system context. **Inference:** because this path contains no signature, provenance classification, or content sanitizer, configured URLs and repository instruction files are trusted prompt inputs; they are not a security boundary.

Skills use progressive disclosure. Discovery scans user Claude/agent skill roots, project ancestors to the worktree, OpenCode config `skill`/`skills` directories, configured paths, and configured remote skill locations; feature flags can disable external or Claude-compatible discovery. Duplicate names warn and the later discovered definition wins, allowing disk skills to override the built-in customization skill. [`skill/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/skill/index.ts:173), [`skill/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/skill/index.ts:235). The system prompt includes only a permission-visible XML manifest of names, descriptions, and locations; the `skill` tool asks permission and loads the selected full body, plus a sample of up to ten sibling files. [`session/system.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/system.ts:105), [`tool/skill.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/skill.ts:12).

MCP is likewise scoped. The client advertises roots, while sampling, elicitation, and MCP Tasks are not enabled in this client capability block. Default call timeout is 30 seconds. [`mcp/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/mcp/index.ts:38). The session directory is exposed as an MCP root. [`mcp/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/mcp/index.ts:75). Server instructions are injected only if at least one associated server tool remains visible under merged agent/session permissions. [`session/system.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/system.ts:119).

There is no explicit aggregate token/byte budget around environment, repository instructions, or the skill manifest in this V1 assembly path. That is a limitation of the inspected path, distinct from later history compaction.

## 4. Model/provider normalization and streaming

OpenCode resolves the language provider, provider configuration, and auth concurrently, then prepares one normalized request. [`session/llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:85). Provider options are merged in this precedence chain: provider/model-family defaults, model options, agent options, then selected variant; plugins can transform parameters and headers. [`session/llm/request.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/request.ts:80).

The transform layer carries many provider-specific compatibility rules: reasoning/thinking fields, output-token limits, strict-schema behavior, encrypted reasoning, and cache controls. For providers that support it, the session ID becomes `prompt_cache_key`/`promptCacheKey`; the AI Gateway requests automatic caching. [`provider/transform.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/provider/transform.ts:1286). OpenAI uses the Responses API in its provider loader. [`provider/provider.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/provider/provider.ts:208).

The default runtime is AI SDK `streamText`; internal SDK retries are deliberately set to zero because retry policy is owned by the outer session processor. The call receives the active tool set, abort signal, provider middleware, and optional OpenTelemetry configuration. [`session/llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:276). A feature-flagged native runtime exists only for supported OpenAI/OpenCode/Anthropic combinations and falls back to AI SDK otherwise. [`native-runtime.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/native-runtime.ts:46).

Both runtimes are normalized into the same `LLMEvent` stream. The AI SDK adapter maps start/finish steps, text and reasoning deltas, tool-call lifecycle, provider errors/metadata, token usage, reasoning tokens, and provider cache read/write counts. It treats a `network_error` finish as failure. [`ai-sdk.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/ai-sdk.ts:77). The experimental native adapter explicitly uses a fiber set to start non-provider-executed tool calls concurrently, but still invokes the same OpenCode-owned tool `execute` functions. [`native-runtime.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/native-runtime.ts:74), [`native-runtime.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/native-runtime.ts:169).

Historical tool-call names can be repaired to lowercase or mapped to the synthetic `invalid` tool, and GitHub Copilot can receive compatibility no-op definitions for historical calls. Current tools are alphabetically sorted and filtered against permissions and user tool toggles before sending. [`session/llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:280), [`session/llm/request.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/request.ts:166).

## 5. Model-visible tool surface

### 5.1 Built-ins and conditional tools

The complete built-in registry at this revision is:

| Model-visible ID | Purpose | Availability |
|---|---|---|
| `invalid` | absorb/diagnose invalid repaired tool calls | Always registered |
| `bash` | run a shell command | Default |
| `read` | bounded file/directory read | Default |
| `glob` | find files | Default |
| `grep` | search text | Default |
| `edit` | exact-string file edit | Hidden on selected GPT models that use patch |
| `write` | write a file | Hidden with `edit` on selected GPT models |
| `apply_patch` | patch files | Selected GPT models; other models use edit/write |
| `task` | run or resume a child agent | Default, subject to agent permission |
| `webfetch` | fetch a URL | Default |
| `websearch` | web search | Provider/flag gated |
| `todowrite` | replace durable session todo list | Default |
| `skill` | load a discovered skill body | Default if enabled/permitted |
| `question` | structured user question | App/CLI/desktop or flag gated |
| `lsp` | language-server operations | Experimental flag |
| `plan_exit` | request transition from plan to build | Plan-mode/flag path |
| `execute` | CodeMode script over visible MCP tools | Experimental CodeMode |

The construction and feature gates are in [`tool/registry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/registry.ts:206); model-family patch-vs-edit selection and dynamic descriptions are in [`tool/registry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/registry.ts:265). Tool IDs come from their definitions—for example `bash` in [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:338), `task` in [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:81), and `execute` in [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:188).

The final surface can also contain tools loaded from config-directory `{tool,tools}/*.{js,ts}`, plugin-contributed tools, MCP resource helpers (`list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource`), and either direct flattened MCP tools or CodeMode’s single `execute` tool. JSON-schema output requests add a per-request `StructuredOutput` tool. [`tool/registry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/registry.ts:183), [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:136), [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1243).

### 5.2 Filtering and execution wrappers

Tool existence and tool-call authorization are separate. Definitions are wrapped with session ID, message ID, call ID, agent, abort signal, metadata updates, permission callback, and plugin `tool.execute.before/after` hooks. [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:41). The request then removes tools disabled by merged agent/session permission and by user-provided tool toggles. [`session/llm/request.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/request.ts:208).

Permission rules can hide an entire tool only when it is fully denied; fine-grained path/command rules leave the definition visible and are checked at execution. [`permission/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/permission/index.ts:186). Direct MCP tools preserve the same before/after hooks, per-tool permission, abort signal, timeout, output truncation, and media attachment handling. [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:388).

## 6. Native agent loop, ordering, steering, and abort

The V1 runner repeatedly reloads the durable projected history, detects a terminal assistant turn, handles queued subtask/compaction markers, checks context overflow, increments the step count, assembles system/tools/messages, and streams one assistant response. It continues after tool calls or unknown finish states and exits only when the latest assistant turn associated with the latest user turn has no tool calls. [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1081).

Before streaming, the processor captures a filesystem snapshot because the AI SDK may execute tools before emitting its `start-step` event. [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:98). Tool events are persisted through `pending -> running -> completed|error` states keyed by call ID. Multiple calls may be active; the default runtime delegates scheduling to AI SDK, while the experimental native runtime explicitly starts calls concurrently. Persistence follows event arrival, not source-code order. [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:216).

Abort is first-class: each request and tool receives the signal, session cancellation propagates into descendant background jobs, and an interrupted assistant message is finalized with an aborted error. Cleanup gives outstanding calls 250 ms to settle and then records remaining calls as interrupted. [`run-state.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/run-state.ts:111), [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:580), [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1203).

**Steering boundary.** The default V1 core maintains one process-local runner per session; a shell/API start against an already running session joins/reuses or reports busy rather than injecting a new user message into the current model turn. [`run-state.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/run-state.ts:35). **Inference:** the V1 loop has abort and serial follow-up, but no semantic mid-turn user-steer injection at tool boundaries. The newer V2 runner explicitly lists durable user steering during an active provider turn as implemented, but also documents several missing continuation/recovery pieces; that is V2 behavior, not default-loop parity. [`core/session/runner/llm.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/runner/llm.ts:39).

## 7. CodeMode / programmatic tool calling

### 7.1 What it is

CodeMode is an **experimental, default-off** host-side programmatic tool-calling layer enabled by `OPENCODE_EXPERIMENTAL_CODE_MODE` (or the broad experimental switch unless specifically overridden). [`effect/runtime-flags.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/effect/runtime-flags.ts:10), [`effect/runtime-flags.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/effect/runtime-flags.ts:47). Instead of flattening every MCP function into the top-level model schema, OpenCode exposes one `execute({code})` tool and presents a searchable catalog of the MCP functions that the current permission set allows. [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:388), [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:188).

This is PTC-like in the architectural sense—one model-generated program can call, filter, join, and aggregate many tools—but it is not provider-native PTC. The host transpiles and interprets the program and remains responsible for every child call.

### 7.2 Discovery, interpreter, and concurrency

Visible MCP tools are grouped into sanitized server namespaces. Catalog resolution uses longest matching names, deterministic tokenization/scoring, exact path lookup, and paginated search; a synthetic `$codemode.search` function lets programs discover omitted tools. [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:39), [`packages/codemode/src/tool-runtime.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/tool-runtime.ts:365). The generic catalog budget defaults to approximately 2,000 tokens and can emit a partial catalog plus discovery instructions. [`packages/codemode/src/codemode.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/codemode.ts:9), [`tool-runtime.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/tool-runtime.ts:560).

Programs are TypeScript-transpiled, parsed by Acorn, and run by a custom AST interpreter—there is no `eval`, V8 isolate, or spawned JavaScript process. Diagnostics sanitize host paths. [`interpreter/runtime.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/interpreter/runtime.ts:115), [`interpreter/runtime.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/interpreter/runtime.ts:151). The restricted language deliberately omits modules/imports, classes, generators, timers, fetch, eval, prototypes, and promise chaining; it supplies selected data operations and `Promise.all`. [`tool-runtime.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/tool-runtime.ts:604). Tool-call concurrency is bounded by an interpreter semaphore of eight. [`stdlib/promise.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/stdlib/promise.ts:6), [`interpreter/runtime.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/interpreter/runtime.ts:628).

### 7.3 Budgets and authority boundary

The generic `@opencode-ai/codemode` package supports optional wall-clock timeout, maximum child-tool-call count, and maximum returned-output bytes; absent values mean unlimited. It validates and applies those limits around interpreter execution. [`packages/codemode/src/codemode.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/codemode.ts:119), [`packages/codemode/src/interpreter/runtime.ts`](/Users/mathiasl/src/opencode/packages/codemode/src/interpreter/runtime.ts:3340). **Current integration caveat:** OpenCode constructs `CodeMode.make` without passing those generic limits. The integration therefore has no CodeMode-wide timeout/call-count/output-byte budget at this revision. It still has the outer session abort and each MCP child’s own MCP timeout. [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:188).

Every MCP child call still runs plugin before/after hooks, performs its normal permission ask, receives the outer abort signal, and uses a timeout that can reset on progress. [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:134). The interpreter is a language-capability boundary, not an OS sandbox. Only MCP tools are placed behind CodeMode at this revision; core `bash`, file editing, task, and other built-ins remain normal top-level calls. Thus CodeMode reduces schema/context fan-out and enables mechanical aggregation without delegating authority away from the host.

## 8. Tool-output bounding and artifact indirection

The generic truncation service defaults to 2,000 lines and 50 KiB. When exceeded, it writes the complete output to a temporary artifact retained for seven days and returns either a head or tail preview with the artifact path. If the `task` tool is available, the returned guidance tells the model to delegate artifact exploration instead of reading the entire file into the parent context. [`tool/truncate.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/truncate.ts:12), [`tool/truncate.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/truncate.ts:68), [`tool/truncate.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/truncate.ts:85). Cleanup begins after one minute and runs hourly. [`tool/truncate.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/truncate.ts:143).

This is a reusable convention, not a universal postcondition over every tool. Direct plugin/MCP wrappers invoke it; `read` has its own 50 KiB cap and offset continuation; `bash` streams a bounded tail and spills full output to a file after its threshold. [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:388), [`tool/read.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/read.ts:345), [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:438). The current CodeMode adapter does not route its aggregate return through generic truncation or pass CodeMode’s own maximum-output option. Large aggregate CodeMode output is therefore a documented gap in the current integration.

MCP binary resource results have a separate attachment policy: only a bounded supported-media set is accepted and individual resource payloads are capped at 10 MiB. [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:32), [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:440). Temporary full-output files are semi-durable artifacts with retention, not SQLite session records; a resumed session can retain the path in its transcript even after cleanup removes the target.

## 9. Context accounting, pruning, compaction, and replay

### 9.1 Trigger and accounting

V1 computes usable input as model context minus a reserve. The reserve is configured when present; otherwise it is 20,000 tokens or the model maximum output when that is smaller. Accounted total prefers explicit `totalTokens`, otherwise input + output + cache-read + cache-write tokens. Auto-compaction triggers when accounted total reaches usable input, unless auto-compaction is disabled. [`session/overflow.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/overflow.ts:8).

This accounting is provider-reported rather than a local exact tokenizer. Cache reads/writes are included in the fallback aggregate, and output reservation is conservative. A provider can also return an actual context-overflow error; the processor routes that to compaction instead of ordinary retry. [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:621).

### 9.2 Tool-output pruning

Before summary compaction, completed historical tool outputs may be replaced with a compacted marker. V1 protects at least the newest two user turns, never crosses an earlier summary boundary, protects `skill` results, retains roughly the newest 40,000 tokens of tool output, and only performs the rewrite if it can reclaim at least 20,000 tokens. [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:271). This mutation affects model projection while keeping the underlying part record and compaction marker in durable history.

### 9.3 Summary plus retained tail

Compaction is turn-aware. It groups history by user turns and keeps the newest whole turns backward within a tail budget; a too-large turn may be split only at a message boundary. The default tail budget is 25% of usable model input, clamped between 2,000 and 15,000 tokens, with an optional configured turn count. [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:97), [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:122).

The summary serialization includes user text/files, assistant text/reasoning, tool calls, and tool results, but each serialized tool result is capped at 2,000 characters. [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:51). The compaction model receives a structured prompt requiring: Objective; Important Details; Work State with Completed/Active/Blocked; Next Move; Relevant Files; and preservation/merge of prior summary facts. [`core/src/session/compaction.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/compaction.ts:12). A plugin can replace the prompt or append context. Compaction runs as a hidden no-tool agent, using the current model unless a compaction model is configured. [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:372).

The durable history is not destructively replaced. Projection orders a synthetic compaction user marker, summary assistant message, retained tail, and continuation user message for the next provider request. [`session/message-v2.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/message-v2.ts:521). Media excluded from summary is represented by textual markers. Auto-replay otherwise adds “Continue if you have next steps…” unless a plugin disables it. [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:468). If the summary itself still overflows, the loop stops rather than recursively compacting forever. [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:450).

### 9.4 Cache and V1/V2 distinctions

**Fact:** supported providers receive the stable session ID as a prompt-cache key. [`provider/transform.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/provider/transform.ts:1286). **Inference:** compaction changes the actual message prefix by introducing a new summary and retained tail, so old prefix-cache coverage is reduced even though affinity remains on the same cache key. Dynamic date/environment and context/plugin changes also prevent a globally stable static prefix. No explicit V1 prefix hash or cache epoch is visible in the request path.

The newer V2 compactor is not parameter-identical: its core defaults are a 20,000-token buffer, 8,000-token keep region, and 4,096 summary output tokens. [`packages/core/src/session/compaction.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/compaction.ts:12). Those V2 values must not be substituted for the V1 default tail policy above.

## 10. Durable session state, todos, snapshots, revert, and resume

### 10.1 SQLite and event projection

The shared SQLite schema stores sessions with project/workspace/parent/title/version, aggregate cost and tokens, revert state, permission, agent/model selection, and timestamps. Legacy V1 `MessageTable`, `PartTable`, and `TodoTable` coexist with newer ordered V2 `SessionMessageTable`, durable prompt input, and system-context epoch tables. [`core/src/session/sql.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/sql.ts:22), [`core/src/session/sql.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/sql.ts:68), [`core/src/session/sql.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/sql.ts:119). This coexistence is migration architecture, not evidence that every V2 runner feature is active in V1.

V1 creates a durable session with parent/workspace/directory/model/permission/cost/token fields and publishes events on message/part changes. [`session/session.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/session.ts:499), [`session/session.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/session.ts:629). The projector transactionally updates messages/parts and rolls step usage into session cost, input/output/reasoning, and cache-read/write aggregates. [`core/src/session/projector.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/projector.ts:88), [`core/src/session/projector.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/projector.ts:310).

`todowrite` transactionally replaces the session’s ordered todo rows and emits an update event. [`session/todo.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/todo.ts:29). Todos are durable coordination memory; their truth and completion status are model-maintained rather than independently derived from repository state.

### 10.2 Workspace snapshots and logical revert

For Git workspaces with snapshots enabled, OpenCode maintains a shadow Git directory under application data with the actual worktree attached. Snapshot objects are pruned after seven days. [`snapshot/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/snapshot/index.ts:23), [`snapshot/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/snapshot/index.ts:66). Snapshot capture discovers changed and untracked files, respects ignores, and excludes untracked files larger than 2 MiB. [`snapshot/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/snapshot/index.ts:235).

The processor captures before a step and records the resulting patch at step end. [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:98), [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:424). Revert is refused while the session is busy; it identifies a message boundary, saves the original snapshot, applies later patches backward, computes a diff, and stores staged revert state. [`session/revert.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/revert.ts:38). Unrevert restores the original; submitting the next prompt commits the logical revert by deleting later session messages/parts. [`session/revert.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/revert.ts:91).

Forking creates a new durable session and clones message/part history up to a chosen boundary while remapping IDs and compaction-tail references. [`session/session.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/session.ts:691).

### 10.3 What resume means

After restart, OpenCode can reload durable session messages, parts, todos, model/agent selection, and child transcripts, then continue with a new provider turn. It cannot resume the same in-flight network stream, shell process, active permission deferred, or background-job fiber. On projection, formerly pending/running tool calls are converted to interrupted-error results so replay has a settled transcript. [`session/message-v2.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/message-v2.ts:349). This is durable conversational resume, not transparent continuation of arbitrary live side effects.

## 11. Staying on track: agents, plan, todo, step caps, and doom loops

### 11.1 Agent modes and policy profiles

An agent can set mode, model, prompt, options, permissions, and a step limit. [`agent/agent.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/agent/agent.ts:35). Built-ins include primary `build` and `plan`, subagents `general` and `explore`, and hidden no-tool `compaction`, `title`, and `summary` agents. Config may override them or add agents. [`agent/agent.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/agent/agent.ts:140), [`agent/agent.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/agent/agent.ts:267).

The default permission profile broadly allows operations, asks on doom-loop/external-directory/`.env` access, and denies selected interaction/plan transitions until the relevant mode enables them. It also whitelists harness temporary, truncation, skill, and reference locations. [`agent/agent.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/agent/agent.ts:98).

### 11.2 Plan and todo discipline

Plan mode is primarily a prompt-and-permission profile. Its prompt tells the model to research with explore/general agents, avoid non-read-only operations except writing the plan file, resolve ambiguity, include verification, and call `plan_exit`. [`session/prompt/plan-mode.txt`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt/plan-mode.txt:1). The plan agent mechanically denies direct edit/write operations outside its permitted plan path; `plan_exit` asks the user and injects a synthetic switch back to build. [`agent/agent.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/agent/agent.ts:166), [`tool/plan.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/plan.ts:15).

**Limitation:** plan-mode “read only” is not a complete OS-level policy. The plan prompt prohibits mutable tools, and file editing is permission-restricted, but `bash` remains an available host command primitive under the broad default unless a configuration rule narrows it. Therefore strict read-only behavior partly depends on model compliance and deployment policy.

The todo tool prompt prescribes use for three-or-more-step work, exactly one `in_progress` item, and completion only after verification. [`tool/todowrite.txt`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/todowrite.txt:1). The host only validates/persists the supplied list; it does not prove those semantic invariants. [`tool/todo.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/todo.ts:14).

### 11.3 Mechanical loop guards

Agent steps default to infinity. At the configured cap, the loop injects `MAX_STEPS_PROMPT`, which tells the model to stop tool use and provide a concise progress/blocker handoff. [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1178), [`core/src/session/runner/max-steps.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/runner/max-steps.ts:1). **Important caveat:** V1 still passes tool definitions with that final call; “tools are disabled” is prompt-level language rather than mechanically removing the tool registry in this code path. [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1283).

The processor also compares the newest tool call with recent calls by tool name plus JSON input. After three identical calls it requests explicit `doom_loop` permission. [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:331). This is a mechanical escalation, not automatic termination: the user/policy can allow the repetition.

No independent acceptance-criteria engine verifies a model’s final completion claim. Prompts encourage tests/lint/typecheck, snapshots record changes, and the transcript records tool evidence, but “done” remains a model judgment unless a user/project policy adds deterministic checks.

## 12. Delegation and long-running work

### 12.1 Child-agent task model

`task` starts or resumes a child session. Its schema can expose `task_id` for resuming the same context; experimental background execution adds a background flag. [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:24). The host computes nesting depth through parent sessions and defaults to one subagent level unless configured otherwise. [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:92).

Starting a child performs a permission check, derives child permissions, persists a session with `parentID`, inherits or overrides the model, runs the child prompt, and returns the child’s last text or error. [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:119), [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:181). The model-facing task guidance asks for detailed delegation prompts, parallel tasks for independent work, avoidance of duplicate work, and reuse of `task_id`; it says child output is generally trusted. [`tool/task.txt`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.txt:1).

### 12.2 Foreground/background lifecycle

Foreground tasks wait for the child but may be promoted; background tasks start in a process-local job registry. Extensions to a running child are serialized through a per-job tail. Waiting can use a timeout. [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:267), [`background-job.ts`](/Users/mathiasl/src/opencode/packages/core/src/background-job.ts:202), [`background-job.ts`](/Users/mathiasl/src/opencode/packages/core/src/background-job.ts:292). When an experimental background task completes, it injects a synthetic result into the parent and automatically runs the parent prompt so the parent can integrate it. [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:227).

The live background registry explicitly says status is process-local: process restart or owner-scope closure loses the live job and interrupts work. [`background-job.ts`](/Users/mathiasl/src/opencode/packages/core/src/background-job.ts:113). The child session/transcript remains durable, so a later `task_id` can continue from recorded context, but it cannot resurrect the same fiber or in-flight side effect. Parent cancellation recursively cancels descendant background jobs known to the live registry. [`run-state.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/run-state.ts:111).

### 12.3 Shell processes are not durable jobs

`bash` spawns a host child with ignored stdin and detached process-group behavior on Unix. Default timeout is two minutes unless a tool/runtime flag overrides it; a caller may request a larger timeout. [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:293), [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:338), [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:615). Exit, abort, and timeout race; abort/timeout kills the process group and escalates after three seconds. [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:481).

There is no harness-managed PTY handle, session ID, or `write_stdin` primitive in this default shell tool. One invocation owns and waits for one process until completion/abort/timeout. A shell command could daemonize something itself, but OpenCode would not durably track or recover that process; this is outside the agent-job lifecycle.

## 13. Permission, trust, and sandbox reality

Permission evaluation uses ordered wildcard rules where the last matching rule wins; no match defaults to `ask`. [`permission/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/permission/index.ts:28). Execution evaluates all relevant patterns, immediately rejects `deny`, and creates a pending deferred plus event for `ask`. Replies support once, reject, or always. “Always” approvals are stored in process-local instance state, not the session database. [`permission/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/permission/index.ts:67), [`permission/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/permission/index.ts:109).

The shell parser scans command/path patterns, asks for `external_directory` when needed, and checks command permission before spawning. [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:257), [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:620). File tools and MCP/CodeMode child calls likewise ask through the host context.

**Sandbox fact:** the local `bash` tool directly spawns a child process; no OS sandbox wrapper is inserted in this implementation. [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:293). Consequently, approved or allowed commands run with the OpenCode process’s operating-system credentials. Git worktrees and shadow snapshot repositories provide isolation/rollback organization, not process containment. A hosted product may surround OpenCode with an external sandbox, but that is outside this local harness implementation.

**Trust boundaries:**

- Permission/tool wrappers are the application authority boundary; the model cannot directly invoke host operations without emitting a call the host accepts. [`session/tools.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/tools.ts:41)
- CodeMode preserves this boundary per MCP child; its interpreter is not a substitute for tool permission or OS isolation. [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:134)
- Repository/global/remote instructions, skills, tool output, and MCP server instructions are prompt inputs and can steer the model; they are not authenticated by the session assembler.
- Plugins are trusted in-process code. They can add tools and transform system text, request parameters, tool arguments/results, permissions, and shell environment. [`packages/plugin/src/index.ts`](/Users/mathiasl/src/opencode/packages/plugin/src/index.ts:222)

## 14. Retry, overflow, model switching, and failure semantics

Provider retry uses exponential backoff starting at two seconds, factor two, 25% jitter, and a 30-second cap unless provider headers supply delay. The retry schedule has a five-attempt constant. [`session/retry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/retry.ts:26). Context overflow is never treated as a normal retry. Transient network, selected 5xx/status, overload, and known provider messages are retriable; `Retry-After` is honored. [`session/retry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/retry.ts:33), [`session/retry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/retry.ts:85). The processor publishes retry status/action/next time while it wraps the stream; AI SDK’s own retry count remains zero. [`session/retry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/retry.ts:183), [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:674).

Actual context overflow routes to compaction if automatic compaction is allowed; otherwise it becomes terminal. [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:621). Missing models emit a model-not-found event with suggestions, and current model/agent selection is persisted on the session. [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:594), [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:614).

On a model/provider switch, history conversion strips incompatible provider metadata and reasoning signatures; reasoning is replayed as plain text, while settled tool calls/results remain. Pending tools become interrupted errors. [`session/message-v2.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/message-v2.ts:244). This preserves semantic history without pretending provider-specific opaque state is portable. It also means a switch cannot reuse all reasoning/cache artifacts from the old provider.

## 15. Observability, cost, and telemetry

Each LLM request logs provider, model, session, agent, and selected runtime. Optional OpenTelemetry records a generation span and carries user/session metadata. [`session/llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:85), [`session/llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:208). Normalized stream events retain provider metadata and reasoning/text/tool lifecycle, allowing the processor to persist an incremental transcript rather than only a final blob. [`session/llm/ai-sdk.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/ai-sdk.ts:77).

At step finish, OpenCode persists usage and cost, attaches the workspace patch, and schedules summary work. [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:435). Durable session aggregates distinguish input, output, reasoning, cache-read, and cache-write tokens. [`core/src/session/projector.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/projector.ts:88). Busy/retry/idle status is evented but the V1 status map itself is live in memory. [`session/status.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/status.ts:24).

The durable transcript, tool states, usage, cost, and patches provide useful audit evidence. They are not a built-in evaluation system: there is no invariant requiring a passing deterministic verifier before an assistant can finish.

## 16. Plugins, hooks, and extension surfaces

Plugins are loaded from built-in and external sources, then hooks are invoked sequentially in registration order. [`plugin/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/plugin/index.ts:66), [`plugin/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/plugin/index.ts:284). The public hook contract covers configuration/events, custom tools, auth/provider loading, chat messages/parameters/headers, permission, commands, tool before/after, shell environment, message/system transforms, small-model selection, compaction prompt/auto-continue, streamed text transforms, and tool-definition transforms. [`packages/plugin/src/index.ts`](/Users/mathiasl/src/opencode/packages/plugin/src/index.ts:222).

Other extensibility planes are deliberately different:

- **Config/custom tools:** executable TypeScript/JavaScript definitions added directly to the model registry. [`tool/registry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/registry.ts:183)
- **MCP:** external tool/resource/prompt/instruction servers over stdio or remote transports, governed by MCP lifecycle, permissions, and timeouts. [`mcp/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/mcp/index.ts:142)
- **Skills:** declarative instruction bundles advertised cheaply and loaded on demand. [`skill/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/skill/index.ts:321)
- **Agents:** named prompt/model/permission/step profiles, including subagent-only and hidden maintenance roles. [`agent/agent.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/agent/agent.ts:267)

These make OpenCode adaptable but widen the trusted computing base. In-process plugins and custom tools are not sandboxed extensions.

## 17. Tests and implementation contracts

The repository contains deterministic tests for the main harness seams:

| Contract area | Test evidence |
|---|---|
| Provider prompt routing | [`test/session/system.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/system.test.ts:86) |
| Project/nested/system instruction resolution | [`test/session/instruction.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/instruction.test.ts:114) |
| Compaction selection, summary, replay, pruning | [`test/session/compaction.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/compaction.test.ts:1) |
| Message/provider projection and model switch | [`test/session/message-v2.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/message-v2.test.ts:1) |
| Retry classification and delay | [`test/session/retry.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/retry.test.ts:1) |
| Snapshot/tool-start race and revert/compaction | [`test/session/snapshot-tool-race.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/snapshot-tool-race.test.ts:1), [`test/session/revert-compact.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/revert-compact.test.ts:110) |
| Streaming adapter and native-runtime parity | [`test/session/llm.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/llm.test.ts:176), [`test/session/llm-native-recorded.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/session/llm-native-recorded.test.ts:398) |
| Built-in tool schema snapshots | [`test/tool/parameters.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/tool/parameters.test.ts:37) |
| CodeMode integration and generic budgets | [`test/tool/code-mode-integration.test.ts`](/Users/mathiasl/src/opencode/packages/opencode/test/tool/code-mode-integration.test.ts:1), [`packages/codemode/test/codemode.test.ts`](/Users/mathiasl/src/opencode/packages/codemode/test/codemode.test.ts:1115) |
| V2 background jobs, projection, todo, permissions, snapshots | [`packages/core/test/background-job.test.ts`](/Users/mathiasl/src/opencode/packages/core/test/background-job.test.ts:1), [`packages/core/test/session-projector.test.ts`](/Users/mathiasl/src/opencode/packages/core/test/session-projector.test.ts:1) |

These tests define code-level contracts; they do not measure model task success, long-horizon coherence, or security against malicious repository instructions.

## 18. Limitations and explicit non-features

1. **V2 is incomplete.** The core V2 runner source itself lists durable ownership, full tool resolution, retry/doom-loop parity, cancellation settlement, snapshots, and maintenance work as incomplete. [`core/session/runner/llm.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/runner/llm.ts:39)
2. **No local OS sandbox.** Permission prompts and worktrees do not confine an allowed host shell process. [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:293)
3. **No durable live fibers/processes.** Active session runners, approvals, statuses, shell processes, and background jobs do not survive restart; only recorded state does. [`run-state.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/run-state.ts:35), [`background-job.ts`](/Users/mathiasl/src/opencode/packages/core/src/background-job.ts:113)
4. **No V1 mid-turn semantic steering queue.** Abort is supported; normal user follow-up is serialized. V2 has an emerging durable-input design, but is not V1 parity. [`core/src/session/sql.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/sql.ts:145)
5. **Step-cap enforcement is soft at the last model call.** The prompt says tools are disabled, but V1 still supplies definitions. [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1283)
6. **Plan mode is not mechanically read-only for arbitrary shell commands.** It combines prompt guidance and file-tool permissions rather than an OS read-only mount.
7. **CodeMode limits are not wired by the OpenCode adapter.** Generic budget support exists, but current `CodeMode.make` receives no limit object. [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:188)
8. **Output bounds are uneven.** Generic truncation is opt-in by tool wrapper; current CodeMode aggregate output is a notable gap. [`tool/truncate.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/truncate.ts:68)
9. **No deterministic completion gate.** Verification is prompted and evidenced, not required by a host acceptance contract.
10. **Prompt inputs have broad trust.** Repository instructions, configured remote instructions, skills, MCP instructions/results, and plugin transforms can all influence the model.
11. **Provider cache stability is opportunistic.** A stable session cache key exists for supported providers, but dynamic environment/context and compaction mutate the actual prefix. [`provider/transform.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/provider/transform.ts:1286)
12. **No durable interactive shell protocol.** The default tool has no PTY/session handle or stdin continuation.

## 19. Primitive inventory

| Primitive | Default / experimental | Durable / live | Primary artifact | What it buys |
|---|---|---|---|---|
| Provider-family base prompts | Default | Static source | [`session/system.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/system.ts:27) | Model-specific operating behavior |
| Dynamic environment/reference system context | Default | Regenerated | [`session/system.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/system.ts:67) | Workspace/model grounding |
| Project instruction discovery | Default | Files/URLs + per-message claims live | [`session/instruction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/instruction.ts:110) | Repository-local policy/context |
| Skill manifest + explicit load | Default | Source files; inventory regenerated | [`skill/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/skill/index.ts:173) | Progressive disclosure |
| MCP clients/resources/tools/instructions | Configured extension | Client live; results in transcript | [`mcp/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/mcp/index.ts:142) | External capability plane |
| Provider normalization/streaming adapter | Default | Per turn | [`session/llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:224) | One loop across heterogeneous providers |
| Native provider runtime | Experimental | Per turn | [`native-runtime.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm/native-runtime.ts:46) | Alternate lower-level event/tool dispatch |
| Built-in/plugin tool registry | Default/extensible | Rebuilt per request | [`tool/registry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/registry.ts:206) | Stable host-owned action surface |
| Permission rules and deferred asks | Default | Rules durable/configured; approvals live | [`permission/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/permission/index.ts:67) | Human/policy authority boundary |
| Multi-step agent loop | Default V1 | Runner live; transcript durable | [`session/prompt.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/prompt.ts:1081) | Autonomous tool-use continuation |
| Abort propagation | Default | Live | [`run-state.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/run-state.ts:111) | Controlled interruption |
| CodeMode `execute` | Experimental | Per call | [`tool/code-mode.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/code-mode.ts:188) | Low-schema, high-fanout MCP orchestration |
| Tool-output spill/truncation | Default where adopted | Temp artifact, 7-day retention | [`tool/truncate.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/truncate.ts:68) | Protect model context while retaining full output |
| Token overflow accounting | Default | Per step from usage | [`session/overflow.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/overflow.ts:8) | Timely context-pressure detection |
| Tool-result pruning | Default | Compaction markers durable | [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:271) | Cheap reclaim before summary |
| Incremental summary + retained tail | Default | Durable summary/tail metadata | [`session/compaction.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/compaction.ts:319) | Long-horizon conversational continuity |
| SQLite session/message/part/todo store | Default | Durable | [`core/src/session/sql.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/sql.ts:22) | Restartable semantic state and audit trail |
| Usage/cost/event projector | Default | Durable aggregates | [`core/src/session/projector.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/projector.ts:88) | Accounting and replayable state |
| Shadow-Git snapshots/patches | Default for eligible Git workspaces | Retained objects + durable parts | [`snapshot/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/snapshot/index.ts:167) | Diff evidence and rollback |
| Revert/unrevert/fork | Default | Durable session state | [`session/revert.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/revert.ts:38) | Recoverable exploration and branching |
| Todo discipline | Default | Durable rows | [`session/todo.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/todo.ts:29) | Explicit model-maintained progress state |
| Agent modes and step caps | Default/configurable | Config + session choice durable | [`agent/agent.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/agent/agent.ts:140) | Role/policy specialization and bounded turns |
| Doom-loop detector | Default | Live recent-history check; ask event | [`session/processor.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/processor.ts:331) | Escalation on repeated identical action |
| Child-agent task sessions | Default | Transcript durable; execution live | [`tool/task.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/task.ts:119) | Context isolation and specialization |
| Background task jobs | Experimental | Live registry; child transcript durable | [`background-job.ts`](/Users/mathiasl/src/opencode/packages/core/src/background-job.ts:113) | Concurrent long-running delegation |
| Shell timeout/process-group kill | Default | Live process | [`tool/shell.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/tool/shell.ts:481) | Bounded command lifecycle |
| Retry/backoff and overflow routing | Default | Live status + durable error/messages | [`session/retry.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/retry.ts:26) | Resilience without double retry stacks |
| Telemetry/logging/cost | Default/optional OTEL | Logs + durable usage | [`session/llm.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/session/llm.ts:208) | Operational visibility |
| Plugin hook pipeline | Configured extension | In-process | [`plugin/index.ts`](/Users/mathiasl/src/opencode/packages/opencode/src/plugin/index.ts:284) | Deep customization at stable seams |
| Core V2 durable runner | In progress, not V1 parity | Increasingly durable | [`core/session/runner/llm.ts`](/Users/mathiasl/src/opencode/packages/core/src/session/runner/llm.ts:39) | Future durable input/tool continuation architecture |

## 20. Design invariants

1. **The host owns actions.** Model output proposes typed calls; OpenCode resolves schemas, permissions, hooks, abort, and execution.
2. **Provider differences terminate at an event boundary.** Both AI SDK and experimental native paths produce the same normalized `LLMEvent` stream before session processing.
3. **One active V1 runner owns a session locally.** Concurrent work is expressed as tool-call concurrency or child jobs, not two independent parent drains.
4. **A tool call is recorded before/while it causes side effects and is settled to a terminal transcript state.** Interrupted calls are not silently left pending on replay.
5. **Conversation durability and execution durability are distinct.** Sessions/parts/todos survive restart; fibers, streams, approvals, and shell processes do not.
6. **Context loss is explicit and structured.** Pruning marks tool results; compaction creates a summary plus a bounded recent tail and synthetic continuation rather than silently dropping arbitrary history.
7. **Compaction is incremental.** Prior summary state is carried forward, and the newest actionable turns remain verbatim when budget permits.
8. **Permissions are evaluated at execution, even when discovery is filtered.** Hiding a definition is an optimization; host authorization is the actual boundary.
9. **Programmatic tool calling does not bypass policy.** CodeMode child calls still traverse permission and plugin hooks.
10. **Provider-specific opaque state is not portable.** Model switches strip incompatible metadata/reasoning signatures while preserving semantic text and settled tools.
11. **Workspace rollback is evidence-driven.** Step snapshots and patches are captured around tool execution, and revert is prohibited while the session is active.
12. **Long-running delegation has a durable identity but a live executor.** A child session can be resumed by ID; a dead process cannot resurrect its old fiber.
13. **Retries have one owner.** AI SDK internal retry is disabled and the outer session retry classifier/backoff owns repeat attempts.
14. **Extension power is trusted power.** Plugins/custom tools can change prompts and actions in process; they are inside the trusted computing base.
15. **Default autonomy is deliberately bounded but not proof-carrying.** Step caps, todo prompts, plan mode, doom-loop asks, tests, and snapshots improve staying-on-track, yet no host invariant proves the final task is correct.
