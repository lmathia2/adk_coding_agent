# Pi coding-harness design

> **Status:** source-grounded implementation reference
> **Repository:** `/Users/mathiasl/src/pi`
> **Revision:** `853a80d26c90a14c1886f0ebb8ffaae133ca2185`
> **Related:** [OpenCode](coding-harness-opencode.md), [Codex](coding-harness-codex.md), [ADK Long Horizon](coding-harness-adk-long-horizon.md), [comparison](coding-harness-comparison.md)

> **Package version:** 0.84.4
> **Inspection date:** 2026-09-01
> **Scope:** the shipping Pi coding-agent CLI and the libraries it actually composes at this revision. This page describes Pi on its own terms; it is intentionally not a cross-product comparison.

## Reading guide and scope boundary

This repository contains two materially different layers that are easy to conflate:

- **Default/core** below means behavior on the shipping CLI path: pi-ai provider normalization → pi-agent-core Agent/direct loop → pi-coding-agent AgentSession, resource loader, tools, session manager, and UI/JSON/RPC modes. The default active model-visible tools are selected in the CLI SDK at [sdk.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/sdk.ts:256).
- **Optional extension/example** means executable extension machinery or an example shipped in the repository, but not enabled as a core workflow.
- **Developmental API** means code exported from packages/agent/src/harness that sketches a more durable harness. It is not the current CLI runtime: creation refuses to restore any existing record, and prompt, resume, compaction, abort, queues, watching, lanes, and drive-to-completion all reject HarnessNotImplemented at [agent-harness.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/agent-harness.ts:347). Only configuration getters/setters are operational at [agent-harness.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/agent-harness.ts:422).
- **Inference** is explicitly labeled. Everything else is a direct implementation fact grounded in linked source.

That boundary matters most for crash recovery. The developmental session schema has operation, step-attempt, tool-started, queue, and usage records suitable for durable replay at [types.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/session/types.ts:80), but the shipping coding agent does not currently drive that implementation.

## Architecture map

The default request path is:

~~~text
CLI / SDK / interactive, print, JSON, or RPC mode
  │
  ├─ DefaultResourceLoader
  │    ├─ global + project settings/packages
  │    ├─ extensions / prompt templates / themes
  │    ├─ context files
  │    └─ skill metadata
  │
  ├─ AgentSession
  │    ├─ assembles system prompt and active tools
  │    ├─ persists finalized events through SessionManager
  │    ├─ compacts, retries, switches model, branches
  │    └─ bridges extension events/hooks
  │
  ├─ Agent / agent-loop
  │    ├─ normalized transcript
  │    ├─ streaming assistant turn
  │    ├─ direct tool-call execution
  │    └─ steering/follow-up queues
  │
  ├─ ModelRuntime / Models / Provider
  │    └─ protocol adapter (Anthropic, OpenAI Responses, etc.)
  │
  └─ SessionManager
       └─ append-only JSONL conversation tree
~~~

The low-level Agent is deliberately stateful but small: it owns transcript state, lifecycle events, tool execution, and steering/follow-up queues at [agent.ts](/Users/mathiasl/src/pi/packages/agent/src/agent.ts:167). AgentSession adds the coding-agent policies that the raw loop does not have: resource-derived prompts, persistence, extensions, automatic compaction, and retry handling; finalized messages are persisted in its event bridge at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:667).

The session store is a separate append-only tree. Every entry has an id and parentId, the leaf identifies the current path, and branch navigation moves that pointer without deleting history at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:845).

## Model-facing contract

### Exact system-prompt assembly

AgentSession rebuilds the prompt from only the currently active registered tools. For each active name it gathers a one-line prompt snippet and all prompt guidelines, then adds the resource loader's replacement prompt, appended prompt fragments, skills, context files, and cwd at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:1066). Extension-discovered resources cause another rebuild at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2463), and the next-turn hook refreshes the current prompt, tools, model, and thinking state after potentially long compaction work at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:562).

The default template begins exactly:

~~~text
You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.

Available tools:
[one line per active tool that provides a prompt snippet]

In addition to the tools above, you may have access to other custom tools depending on the project.

Guidelines:
[dynamically assembled bullets]
~~~

It then embeds resolved paths for Pi's README, docs, and examples, followed by optional appended prompt text, project context, the skill manifest, and:

~~~text
Current working directory: [normalized cwd]
~~~

The canonical template and ordering are at [system-prompt.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts:128), with project context, skills, and cwd appended at [system-prompt.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts:147).

For a default four-tool session with no custom/project additions, the tool and guideline portion is representatively:

~~~text
Available tools:
- read: Read file contents
- bash: Execute bash commands (ls, grep, find, etc.)
- edit: Make precise file edits with exact text replacement, including multiple disjoint edits in one call
- write: Create or overwrite files

In addition to the tools above, you may have access to other custom tools depending on the project.

Guidelines:
- Use bash for file operations like ls, rg, find
- Use read to examine files instead of cat or sed.
- You can inspect PI_* environment variables for current model and session details.
- Use edit for precise changes (edits[].oldText must match exactly)
- When changing multiple separate locations in one file, use one edit call with multiple entries in edits[] instead of multiple edit calls
- Each edits[].oldText is matched against the original file, not after earlier edits are applied. Do not emit overlapping or nested edits. Merge nearby changes into one edit.
- Keep edits[].oldText as small as possible while still being unique in the file. Do not pad with large unchanged regions.
- Use write only for new files or complete rewrites.
- Be concise in your responses
- Show file paths clearly when working with files
~~~

Those strings come from the prompt's dynamic guideline logic at [system-prompt.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts:79), read at [read.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/read.ts:27), bash at [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:47), edit at [edit.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/edit.ts:56), and write at [write.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/write.ts:20).

This prompt is notably a capability-and-style contract, not a workflow state machine. It does not mandate planning, test execution, todo maintenance, proof before completion, repository cleanliness, or a fixed step budget. Those policies must come from user instructions, context files, skills, or extensions.

### Replacement and append semantics

A custom system prompt fully replaces the built-in identity, tool list, guidelines, and Pi-doc section. Pi still appends the configured append fragments, project context, visible skills when read is active, and cwd at [system-prompt.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts:46). Therefore replacement changes the behavioral core without silently losing repository instructions or cwd.

An extension's before_agent_start handler may replace the system prompt for one turn, with multiple extension returns chained by the runner contract at [types.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/extensions/types.ts:1156).

One provider exception is protocol-specific: when Anthropic OAuth credentials are used, the adapter prepends the required Claude Code identity before Pi's assembled system prompt at [anthropic-messages.ts](/Users/mathiasl/src/pi/packages/ai/src/api/anthropic-messages.ts:1009). That is transport adaptation, not a separate Pi coding prompt selected by model family.

## Project instructions, resource discovery, and skills

### Context-file inheritance

In each directory Pi selects the first existing regular file in this priority order:

1. AGENTS.override.md
2. AGENTS.md
3. AGENTS.MD
4. CLAUDE.md
5. CLAUDE.MD

The first-match rule is implemented at [resource-loader.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/resource-loader.ts:71). Pi loads one global context file from the agent directory, then walks from cwd all the way to the filesystem root and prepends ancestor hits so broader instructions precede narrower ones at [resource-loader.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/resource-loader.ts:119). It does not stop at a Git root. A linked worktree special case avoids loading a logically duplicated main-worktree context file at [resource-loader.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/resource-loader.ts:92).

Each file is injected verbatim inside:

~~~xml
<project_context>
Project-specific instructions and guidelines:

<project_instructions path="/absolute/path">
...
</project_instructions>
</project_context>
~~~

Assembly is at [system-prompt.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts:151). No context-file length or token cap is applied in the loader shown above; a large hierarchy therefore consumes ordinary prompt context. **Inference:** because these bytes are part of the system prefix, changing any inherited file also changes the cacheable prompt prefix on the next rebuilt session.

Context files are deliberately outside the protected project-resource trust gate: they load even when project trust is declined unless context loading is disabled, as stated and implemented through the final context load at [security.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/security.md:27) and [resource-loader.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/resource-loader.ts:515).

### Skills are indexed, then loaded on demand

The model receives skill name, description, and absolute file location—not every skill body. The exact instruction is “Use the read tool to load a skill's file when the task matches its description,” followed by an XML available_skills manifest at [skills.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/skills.ts:347). This is Pi's progressive-disclosure primitive: selection is semantic and model-driven, while loading is an ordinary read tool call rather than a special skill executor.

Discovery behavior:

- A directory containing SKILL.md is a skill root and recursion stops there.
- Otherwise Pi can load direct Markdown children of the starting root and recurse through subdirectories for SKILL.md.
- Dot directories and node_modules are skipped; .gitignore, .ignore, and .fdignore rules are applied.

These rules are implemented at [skills.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/skills.ts:160). Frontmatter supplies name, description, and disable-model-invocation. Names are limited by validation to 64 characters and descriptions to 1024 at [skills.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/skills.ts:10). Invalid metadata produces diagnostics but the skill still loads unless its description is missing/empty at [skills.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/skills.ts:277).

Default roots are the global agentDir/skills and cwd/.pi/skills, followed by explicit paths. The first skill name wins and later collisions are diagnosed at [skills.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/skills.ts:407). A skill with disable-model-invocation is omitted from the model manifest but remains explicitly invocable through its slash command at [skills.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/skills.ts:347).

Packages and extensions may contribute additional skill paths. Resource resolution first evaluates project trust, reloads settings for that trust state, resolves enabled package resources, merges CLI paths, and then loads skills/prompts/themes at [resource-loader.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/resource-loader.ts:390). The resource loader can be disabled or supplemented per resource class.

## Model and provider abstraction

Pi normalizes ten named API protocols—OpenAI Completions/Responses/Codex Responses, Azure Responses, Anthropic Messages, Bedrock Converse, Google Generative AI/Vertex, Mistral Conversations, and Pi Messages—while allowing arbitrary API strings at [types.ts](/Users/mathiasl/src/pi/packages/ai/src/types.ts:17). Provider ids are similarly open-ended, with a broad built-in catalog at [types.ts](/Users/mathiasl/src/pi/packages/ai/src/types.ts:35).

A Provider owns its identity, base metadata, authentication, synchronous model list, optional dynamic refresh/filter policy, and stream functions at [models.ts](/Users/mathiasl/src/pi/packages/ai/src/models.ts:88). Models/ModelRuntime resolve auth and dispatch to the provider that owns a model; the coding-agent wrapper exposes streamSimple and completeSimple at [model-runtime.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/model-runtime.ts:636).

The provider-neutral request envelope includes:

- abort signal and optional telemetry parent;
- auth, custom fetch, environment, payload and response callbacks, transformed headers;
- timeout and retry controls;
- sampling, maximum tokens, SSE/WebSocket/auto transport;
- cache retention and session id;
- reasoning level/budgets;
- optional deferred response request.

The common contract is defined at [types.ts](/Users/mathiasl/src/pi/packages/ai/src/types.ts:107) and [types.ts](/Users/mathiasl/src/pi/packages/ai/src/types.ts:313). AgentSession supplies dynamic provider retry limits, HTTP idle timeout, WebSocket connect timeout, attribution headers, and extension header hooks on every stream at [sdk.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/sdk.ts:306).

### Streaming contract

Provider streams normalize text, thinking, and tool-call start/delta/end events into a mutable partial assistant message. The loop places that partial in context, emits updates, and replaces it with the final message at [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:279). Request/runtime failures are contractually encoded in the stream and terminal AssistantMessage with stopReason error or aborted rather than thrown at [types.ts](/Users/mathiasl/src/pi/packages/agent/src/types.ts:18).

Assistant messages preserve requested provider/model, concrete response model/id when exposed, normalized stop reason, usage and cost, diagnostics, and raw stop reason at [types.ts](/Users/mathiasl/src/pi/packages/ai/src/types.ts:405).

### Deferred provider responses

The provider abstraction can represent a server-side asynchronous response as stopReason deferred plus a durable DeferredHandle, and capable providers can fetch or cancel it at [types.ts](/Users/mathiasl/src/pi/packages/ai/src/types.ts:405) and [types.ts](/Users/mathiasl/src/pi/packages/ai/src/types.ts:264). ModelRuntime exposes fetchDeferred/cancelDeferred at [model-runtime.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/model-runtime.ts:647).

However, **default shipping coding-agent limitation:** AgentSession does not implement a user-facing suspended-run/resume lifecycle around those handles at this revision. The durable operation schema and resume API belong to the developmental AgentHarness, whose resume and run methods are unimplemented at [agent-harness.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/agent-harness.ts:363). Provider capability should therefore not be read as end-to-end CLI durability.

## Model-visible tools

### Complete built-in registry

The repository's built-in tool-name union is read, bash, powershell, edit, write, grep, find, and ls at [tools/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/index.ts:93). The **default active coding surface is only read, bash, edit, write** at [tools/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/index.ts:164). Users/settings/SDK callers may select any registry subset; the ready-made read-only set is read, grep, find, ls at [tools/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/index.ts:173).

| Tool | Default | Model contract and execution semantics |
|---|---:|---|
| read | yes | Relative or absolute path, optional 1-based offset/limit; reads text or supported images. Text keeps the head and is limited to 2,000 lines or 50 KiB; callers page with offset. [read.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/read.ts:21), [read.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/read.ts:209) |
| bash | yes | Command plus optional finite positive timeout; there is no default timeout. Runs the configured shell in cwd and combines stdout/stderr. [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:29), [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:338) |
| edit | yes | One file and one or more exact replacements. Every oldText must be unique against the original file; edits cannot overlap/nest. [edit.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/edit.ts:34), [edit.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/edit.ts:316) |
| write | yes | Creates/overwrites a complete file and creates parents. [write.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/write.ts:15), [write.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/write.ts:187) |
| powershell | no | Alternative shell tool built from the shared shell-tool factory; registered but not in the default coding set. [tools/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/index.ts:118) |
| grep | no | Dedicated content search; up to 100 matches, 50 KiB output, and 500 characters per match line. [grep.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/grep.ts:24), [grep.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/grep.ts:134) |
| find | no | Dedicated glob/path search; up to 1,000 results or 50 KiB. [find.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/find.ts:29), [find.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/find.ts:129) |
| ls | no | Dedicated directory listing; up to 500 entries or 50 KiB. [ls.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/ls.ts:14), [ls.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/ls.ts:106) |

Tool schemas live in the provider request, while the system prompt contains only the registered prompt snippets/guidelines. A custom extension tool without a snippet is still callable through its schema but is absent from the “Available tools” prose; this distinction follows the visible-tools filter at [system-prompt.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts:79).

### Filesystem mutation semantics

Edit and write operations targeting the same canonical path are serialized; different files may mutate concurrently at [file-mutation-queue.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/file-mutation-queue.ts:28). Edit holds that queue across access, read, replacement calculation, and write at [edit.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/edit.ts:332). Write holds it across mkdir and write at [write.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/write.ts:201).

This is race control, not transactional storage. The default implementations call ordinary writeFile rather than a temp-file/fsync/atomic-rename protocol. Abort checks deliberately remain inside the queue until awaited filesystem operations settle, but an abort after write cannot roll back the mutation.

Paths are not workspace-confined: resolver semantics explicitly support absolute paths and tilde expansion at [path-utils.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/path-utils.ts:44).

### Extension hooks and custom tools

Extensions can register model-visible tools, commands, shortcuts, flags, renderers, and providers at [types.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/extensions/types.ts:1252). They can dynamically inspect and replace the active tool set, send steering/follow-up user messages, append state-only entries, execute host commands, switch model/thinking, and register/unregister providers at [types.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/extensions/types.ts:1370).

The event surface spans project trust/resource discovery; session start/switch/fork/compact/tree/shutdown; context and provider payload/headers/response; agent/turn/message/tool; model/thinking; input and user bash at [types.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/extensions/types.ts:1257). Hooks may block a call, mutate its input, replace result content/details/error/usage, override a turn's system prompt, cancel/replace compaction, or replace branch summaries at [types.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/extensions/types.ts:1125).

Extensions are in-process TypeScript with the host user's privileges. Their hooks are awaited and thus can add latency or fail behavior; they are not isolation boundaries.

## Tool calling, schema offloading, and PTC

### Direct tool loop

Pi uses a conventional direct loop: send normalized context plus tool schemas to the selected provider, stream one assistant message, validate and execute its tool calls locally, append tool results, then ask the model again. There is no hidden planner/executor split in the default core.

Before execution the loop looks up the tool, applies any argument preparer, validates against the schema, and invokes the beforeToolCall hook. Unknown tools, invalid arguments, blocked calls, aborts, and thrown tool errors become ordinary error tool-result messages so the model can react at [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:598). Partial updates are streamed; afterToolCall may replace result fields before final events at [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:668).

If the assistant response stops for output length, Pi executes **none** of that message's tool calls, because arguments may be syntactically salvageable but truncated. It returns one explicit error result per call at [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:372).

### Ordering and concurrency

Parallel is the default execution mode at [agent.ts](/Users/mathiasl/src/pi/packages/agent/src/agent.ts:231). A global sequential setting or any tool in the batch marked executionMode sequential makes the whole batch sequential at [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:409).

In parallel mode:

1. Tool calls are preflighted sequentially in assistant source order, including policy hooks.
2. Allowed calls execute concurrently.
3. tool_execution_end is emitted as each finishes.
4. Tool-result message artifacts are emitted and appended in original assistant source order.

The implementation is at [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:487), and the ordering contract is documented at [types.ts](/Users/mathiasl/src/pi/packages/agent/src/types.ts:35). Early termination only stops another model turn when **every** finalized result in the batch has terminate=true; a single terminating result cannot discard siblings.

### Deferred tool-schema loading

Pi supports provider-native late tool-definition loading. When an extension tool activation adds tools, the wrapper annotates that tool result with addedToolNames at [wrapper.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/extensions/wrapper.ts:17). The AI layer reconstructs which definitions were initially visible versus loaded at points in the transcript at [deferred-tools.ts](/Users/mathiasl/src/pi/packages/ai/src/utils/deferred-tools.ts:7).

Capable OpenAI Responses adapters emit additional_tools or synthetic client tool-search call/output items at the activation point at [openai-responses-shared.ts](/Users/mathiasl/src/pi/packages/ai/src/api/openai-responses-shared.ts:314). Capable Anthropic adapters send deferred definitions and later tool_reference blocks at [anthropic-messages.ts](/Users/mathiasl/src/pi/packages/ai/src/api/anthropic-messages.ts:973) and [anthropic-messages.ts](/Users/mathiasl/src/pi/packages/ai/src/api/anthropic-messages.ts:1120). Other providers ignore addedToolNames and receive the ordinary current Context.tools set.

This is **tool-schema offloading**, reducing eager schema/prefix cost where the provider supports it. It is not programmatic tool calling.

### Programmatic tool calling (PTC)

**Default/core fact:** Pi has no PTC/Code Mode primitive in which the model writes a program that invokes a tool API repeatedly inside a sandbox and returns only selected results. The closest primitive is bash: the model can compose many shell operations into one command, but the command and its final output remain an ordinary model-mediated tool call/result. Dedicated tools and extension tools are likewise invoked directly one call at a time by the agent loop.

**Inference:** bash gives Pi some of PTC's mechanical fan-out benefit when Unix tools are available, but without typed nested tool calls, in-sandbox result filtering, or a separate tool-call budget.

## Steering, follow-up, abort, and run boundaries

The loop has an inner cycle for tool calls plus steering messages and an outer cycle for follow-ups at [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:156):

- Steering is polled after the current assistant message's complete tool batch and injected before the next provider request. It does not preempt already-issued calls.
- Follow-up is polled only when the agent would otherwise stop and begins another turn.
- prepareNextTurn runs after a completed turn and can change context/model/thinking; AgentSession uses it for threshold compaction and fresh prompt/tool/model state.
- shouldStopAfterTurn supports graceful stop after the completed turn.

Both queues default to one-at-a-time but may be configured to drain all. Their deterministic FIFO behavior is implemented at [agent.ts](/Users/mathiasl/src/pi/packages/agent/src/agent.ts:125). A second direct prompt while a run is active is rejected; callers use steer/followUp instead at [agent.ts](/Users/mathiasl/src/pi/packages/agent/src/agent.ts:347).

Abort is cooperative through one active AbortController. It cancels provider streaming and is passed into tools/hooks; shell abort kills the process tree. waitForIdle resolves only after the run and awaited event listeners settle at [agent.ts](/Users/mathiasl/src/pi/packages/agent/src/agent.ts:313). AgentSession separately exposes cancellation for compaction, branch summary, and retry sleep at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2090) and [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2939).

There is no fixed default maximum turn/tool count. The run ends when the model returns no tool calls and no queued messages, an error/abort occurs, or an optional stop hook says to stop.

## Shell and process lifecycle

The model-visible bash tool spawns a new shell process for each call. On POSIX it uses a detached process group, inherits the shell environment (plus Pi session metadata unless disabled), merges stdout and stderr in arrival order, and waits for the child while avoiding hangs caused by detached descendants retaining stdio at [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:83). Abort or timeout kills the entire process tree. There is no persistent PTY, terminal session id, stdin continuation channel, or background-process manager in the core tool.

Timeout is opt-in per call; omitted means no default limit at [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:29). Nonzero exit, abort, and timeout are tool errors, but accumulated output is retained in the error text at [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:448).

### Output truncation and artifact indirection

Shared defaults are 2,000 lines and 50 KiB at [truncate.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/truncate.ts:1):

- read keeps the **head**, never partial lines, and directs the model to page with offset/limit at [truncate.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/truncate.ts:71);
- bash keeps the **tail**, where errors and final test summaries usually appear, and may retain a partial oversized final line at [truncate.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/truncate.ts:162);
- grep/find/ls have their own result-count caps listed above.

Bash output is accumulated incrementally and partial snapshots are throttled to the UI. When truncated, the full byte stream is persisted to a temporary file and its path is included in the result, so the model can explicitly read/search the artifact later at [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:338). This is local artifact indirection, not an object store; lifetime follows host temp-file behavior.

## Context accounting and compaction

### Trigger and token accounting

Compaction defaults are enabled=true, reserveTokens=16,384, and keepRecentTokens=20,000 at [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:126). Settings expose the same defaults at [settings-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/settings-manager.ts:830).

The threshold is strictly:

~~~text
estimated context tokens > model context window - reserve tokens
~~~

at [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:232). Accounting uses the latest valid non-error/non-aborted assistant usage, then estimates messages after that response. Without valid usage it estimates all messages. Estimation is approximately characters/4, with image content charged as 4,800 synthetic characters at [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:142) and [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:244).

AgentSession checks before the next assistant request, not only after a run, so steering or queued messages that push the transcript over threshold can trigger compaction before another provider call at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:543).

### Cut point

The cutter walks backward from newest entries until it accumulates keepRecentTokens. It may cut at a user or assistant message but never at a tool result, preserving provider-valid call/result structure. If the cut falls inside a turn, it records that turn's user start and creates a separate prefix summary at [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:387).

On repeated compaction, only material since the last compaction boundary is newly summarized; the prior summary is fed back for incremental update. prepareCompaction records the first kept entry id, history messages, optional split-turn prefix, token estimate, previous summary, and extracted file operations at [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:732).

### Summary schema and generation

The summary model is told not to continue or answer the conversation, only to output a structured summary at [utils.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/utils.ts:152). The requested exact schema is:

~~~text
### Goal
### Constraints & Preferences
### Progress
#### Done
#### In Progress
#### Blocked
### Key Decisions
### Next Steps
### Critical Context
~~~

It is instructed to preserve exact file paths, function names, and error messages. Incremental updates must preserve prior facts, move completed progress, and refresh next steps at [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:467).

When a turn is split, the prefix gets a second schema—Original Request, Early Progress, Context for Suffix—and the two summaries are merged. Read and modified file lists extracted from tool history are appended deterministically at [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:835) and [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:883).

Summary generation uses the current model and thinking level, with output bounded by reserve/model capacity. It has its own transient retry policy. The request explicitly sets cacheRetention=none; it reuses the routing session id if supplied or creates a fresh id otherwise at [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:572).

### Replay/projection after compaction

Compaction appends a compaction entry rather than rewriting old messages. Model context becomes:

1. latest compaction-summary message;
2. retained entries beginning at firstKeptEntryId that precede the compaction entry;
3. all later entries on the active path.

The projection is at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:410). Full original history remains in JSONL and is reachable through the tree; compaction is lossy only for the model's active view, as documented at [README.md](/Users/mathiasl/src/pi/packages/coding-agent/README.md:273).

### Overflow recovery and cache consequences

Automatic compaction has three distinct paths at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2105):

- context-overflow error or recoverable length stop: remove the failed assistant from in-memory retry context, compact, retry once; the failed message remains persisted;
- a successful response that nevertheless exceeds context: compact without retry;
- proactive threshold crossing: compact without retrying the completed response.

Stale usage from an old model or a pre-compaction response is ignored, preventing immediate repeated compaction at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2133).

**Cache fact:** the summary call itself requests no cache retention. The cache-waste analyzer resets continuity at compaction and branch-summary entries because the next prompt legitimately changes; model switches are intentionally not exempt at [cache-stats.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/cache-stats.ts:104). **Inference:** the first main request after compaction necessarily has a structurally new history prefix, so provider prefix caches cannot simply reuse the entire pre-compaction prompt even if the system prompt and retained tail are unchanged.

## Session persistence, branching, and resume

The shipping format is versioned JSONL. A header contains session id, timestamp, cwd, and optional parent session; entries include id, parentId, timestamp and variants for messages, model/thinking changes, compaction, branch summary, custom state/messages, labels, and session metadata at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:30).

Persistence behavior is append-oriented:

- entries are added in memory and persisted as they are finalized;
- the file is lazily created once an assistant response exists, then subsequent entries append;
- the current leaf advances with each append.

The implementation is at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:1016), while AgentSession persists user, assistant, tool-result, and model-visible custom messages on message_end at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:673).

Branching moves the leaf to an earlier entry; the next append creates another child without modifying/deleting the abandoned branch at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:1297). branchWithSummary both moves the leaf and appends a model-visible summary of the abandoned path at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:1377). A new branched/clone session can materialize a selected root-to-leaf path, and forkFrom copies a source session to a new file with parentSession provenance at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:1410) and [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:1574).

Open and continue-recent restore persisted entries and reconstruct the active model context. Ephemeral in-memory sessions use the same manager without a file at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:1521). The default directory encodes cwd under the agent sessions directory at [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:472).

### Crash semantics

Completed finalized messages/tool results already appended to JSONL survive process death. An in-flight provider stream, active child process, partial tool result, queued in-memory steering/follow-up, retry timer, and exact operation program counter do not have a shipping replay protocol.

**Inference grounded in the write/event boundary:** a crash can leave durable evidence through the last message_end but cannot safely infer whether an external side effect happened after the last persisted boundary. Restart resumes the conversation tree, not the exact interrupted instruction pointer. The developmental operation schema distinguishes replay safe/never for tool starts at [types.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/session/types.ts:150), but the harness that would consume it is explicitly unimplemented at [agent-harness.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/agent-harness.ts:347).

## Long-horizon control, goals, plans, progress, and verification

### What core provides

The default long-horizon primitive is continuity, not supervision:

- an unbounded direct tool loop;
- user steering and queued follow-ups;
- append-only recoverable conversation history;
- compaction checkpoints with structured Goal/Progress/Next Steps;
- model switching and thinking control;
- extension lifecycle hooks.

The compaction summary can carry goals and progress across a context boundary, but it is generated only when compacting. It is not a live authoritative task database, scheduler, or acceptance test.

### What core does not provide

The shipping core has no built-in:

- plan mode;
- todo/goal tool or durable task graph;
- subagent/delegation manager;
- background shell/process manager;
- maximum-step or time budget;
- doom-loop/repetition detector;
- independent completion verifier or mandatory test gate;
- checkpoint heartbeat that re-reads the user goal on a cadence.

Pi states that these workflow-specific mechanisms are intentionally pushed to extensions/packages and lists the absent facilities at [usage.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/usage.md:305). Therefore a model's “done” claim is not independently validated by core. Verification happens only if the user, prompt/context, skill, or extension induces the model to run deterministic checks.

### Optional examples, not defaults

- The plan-mode example toggles a read-only tool set, allowlists shell commands, extracts numbered plan steps, and uses DONE markers/progress UI at [plan-mode/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/plan-mode/index.ts:1). Its state is process-local extension logic.
- The todo example registers a model-visible todo tool and reconstructs state from tool-result details on the active session branch at [todo.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/todo.ts:105). This demonstrates branch-correct durable extension state, but is not enabled by core.
- The permission-gate example blocks a few dangerous bash regexes pending UI confirmation at [permission-gate.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/permission-gate.ts:1).
- The sandbox example overrides bash through an external OS sandbox runtime at [sandbox/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/sandbox/index.ts:1).

## Long-running agents, background work, and subagents

The main loop can run for a long time because there is no built-in step cap and each shell call may have no timeout. “Long-running” is nevertheless tied to one live Pi process and one active run. Steering is only observed at turn boundaries, and a blocking shell call must finish or be aborted before the model sees steering.

Core intentionally has no background bash or native subagent primitive at [usage.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/usage.md:305). Users can use an external terminal multiplexer/container or implement an extension.

The shipped **subagent example** is informative but optional. It launches a separate pi --mode json -p --no-session process per task, thereby giving each child its own context window at [subagent/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/subagent/index.ts:1). It supports single, chain, and parallel dispatch; parallel is capped at eight tasks with concurrency four and 50 KiB per-task output at [subagent/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/subagent/index.ts:33). The parent parses child JSON events, aggregates usage/output, and on abort sends SIGTERM then SIGKILL after five seconds at [subagent/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/subagent/index.ts:300). Parallel scheduling is a bounded concurrency map at [subagent/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/subagent/index.ts:645).

It does not persist child operation state because children run with --no-session. **Inference:** a parent crash loses in-flight orchestration and child result aggregation; this is process delegation, not a durable multi-agent supervisor. The example also provides no automatic judge/replan loop to ensure delegated work remains aligned; quality is governed by the parent prompt, named agent prompt, and returned output.

## Permissions, sandboxing, and trust

Pi is a local privileged-by-user tool. Core bash, read, edit, and write run with the permissions and environment of the Pi process, paths can escape cwd, and extensions are same-process TypeScript. There is no built-in sandbox at [security.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/security.md:31).

Project trust has a narrower purpose: it decides whether project-local settings, resources, missing packages, and extensions may load before work begins. The prompt explains that consequence at [project-trust.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/project-trust.ts:24). Resolution order is explicit CLI override, absence of protected resources, extension-owned trust decision, stored closest decision, configured always/never/ask, then false for non-UI ask at [project-trust.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/project-trust.ts:46).

Trust is **not** per-tool approval and does not constrain model-requested actions after startup. Context files load regardless, so repository prompt injection remains inside the expected local-agent risk model at [security.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/security.md:3). A before-tool extension can implement policy, but the repository's permission gate is only an example.

Network access is also not denied by core: shell commands, extensions, provider clients, and ordinary developer tools can use host networking. Strong isolation must wrap the entire process or route tools into a real OS/container/VM boundary, as the security guide recommends at [security.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/security.md:39).

## Retry, error, overflow, and model-switch behavior

There are two retry layers:

1. Provider-request retry mirrors OpenAI/Anthropic SDK semantics for transport/status failures. It honors x-should-retry, retries missing status, 408, 409, 429 and 5xx, parses Retry-After, caps server-requested delay at 60 seconds by default, jitters exponential delay, and makes sleep abortable at [provider-retry.ts](/Users/mathiasl/src/pi/packages/ai/src/utils/provider-retry.ts:22).
2. Agent-turn retry handles a completed normalized assistant error. It is enabled by default with three retries and 2,000 ms base delay at [settings-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/settings-manager.ts:869). It removes the error assistant from in-memory retry context but retains it in session history, then waits 2s, 4s, 8s abortably at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2883).

Context overflow is excluded from generic retry and routed through compact-and-retry once. Length-truncated tool calls are never executed. Repeated overflow after the one recovery attempt stops with an explicit failure, rather than entering an infinite compact loop at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2152).

Tool failures are generally data, not fatal control flow: they become isError tool-result messages and the model gets another turn. Provider stream error/abort ends the low-level run, after which AgentSession may retry. Extension hook failures are surfaced through extension error handling; hooks do not provide transaction rollback.

Model switching validates auth, appends a model-change entry, optionally persists the global default, and clamps/restores the thinking level appropriate to the new model at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:1651). Cycling uses either the scoped --models set or all available authenticated models at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:1693). An overflow from a previously selected smaller model cannot trigger compaction against the newly selected model because provider/id must match.

## Observability, usage, cost, and telemetry

The runtime's primary observability surfaces are:

- streaming agent/turn/message/tool events;
- extension hooks at the same lifecycle boundaries;
- JSON mode event output and RPC mode;
- append-only JSONL transcript with provider/model/stop reason/usage;
- session HTML export;
- aggregate session and context statistics.

Session statistics scan **all** entries, including compacted-away history and summary usage, to report user/assistant/tool counts, token buckets, and cost at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:3318). Context usage is treated as unknown immediately after compaction until a valid post-compaction assistant usage exists at [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:3375).

Cache observability estimates wasted cache tokens/cost relative to the previous request, uses a five-minute TTL explanation heuristic, ignores misses at or below 1,024 tokens, and records whether a model switch caused rebilling at [cache-stats.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/cache-stats.ts:4).

The default product telemetry described in the CLI is an anonymous install/update version ping—not a hosted trace of coding turns—and can be disabled/offlined. The actual request is a best-effort five-second fetch at [interactive-mode.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts:1290); behavior and opt-outs are documented at [README.md](/Users/mathiasl/src/pi/packages/coding-agent/README.md:310).

The repository also exports typed AI/harness telemetry schemas with provider, model, stop reason, HTTP, token, cost, latency, lifecycle, hook, and fault vocabulary at [telemetry.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/telemetry.ts:42) and [telemetry.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/telemetry.ts:147). The AI request schema can be used by provider code. The richer pi.harness lifecycle vocabulary belongs to the developmental harness surface; it should not be treated as evidence that the shipping CLI emits every defined span.

There is no core eval runner, acceptance-test ledger, or automatic quality score attached to a session.

## Extensibility model

Pi's primary workflow abstraction is “small core, rich extension boundary.” Extension authors can:

- add/replace tools and their model prompt contributions;
- intercept call/result and provider payload/headers/response;
- contribute project trust decisions and discover resources;
- add session entries for branch-aware state;
- override compaction/branch summaries;
- add UI, commands, keybindings, flags, and renderers;
- switch active tools/model/thinking and register providers.

The complete registration and event contract is centralized in [types.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/extensions/types.ts:1241). Tool implementations themselves accept pluggable operation backends; for example write explicitly allows remote-delegated operations at [write.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/write.ts:27), and the shell factory accepts alternate BashOperations at [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:83). This lets an integrator keep the model schema while routing execution into SSH, a VM, or another substrate.

Prompt templates and skills are lighter-weight extension points: templates create user prompts, while skills add discoverable task instructions that are loaded through read. Packages bundle resources and are governed by the same project-trust/resource-resolution path.

## Deterministic contracts and tests

The repository has targeted tests for the important mechanical invariants rather than only end-to-end natural-language behavior:

- direct loop ordering, parallel/sequential execution, steering, abort, and termination: [agent-loop.test.ts](/Users/mathiasl/src/pi/packages/agent/test/agent-loop.test.ts:274);
- compaction threshold, cut points, incremental and split-turn summaries: [compaction.test.ts](/Users/mathiasl/src/pi/packages/agent/test/harness/compaction.test.ts:162) plus shipping coding-agent coverage in [agent-session-compaction.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/agent-session-compaction.test.ts:1);
- prompt assembly and tool contributions: [system-prompt.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/system-prompt.test.ts:1) and [tool-system-prompt-contributions.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/tool-system-prompt-contributions.test.ts:1);
- compaction queue and retry behavior: [agent-session-auto-compaction-queue.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/agent-session-auto-compaction-queue.test.ts:1) and [agent-session-retry.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/agent-session-retry.test.ts:1);
- tree traversal/session projection: [tree-traversal.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/session-manager/tree-traversal.test.ts:1);
- project trust: [trust-manager.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/trust-manager.test.ts:1);
- regressions for bash truncation, next-turn active tools, and truncated compaction summaries: [5303-bash-output-truncation.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/suite/regressions/5303-bash-output-truncation.test.ts:1), [6162-extension-active-tools-next-turn.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/suite/regressions/6162-extension-active-tools-next-turn.test.ts:1), [7048-compaction-truncated-summary.test.ts](/Users/mathiasl/src/pi/packages/coding-agent/test/suite/regressions/7048-compaction-truncated-summary.test.ts:1).

These tests establish execution/data-shape contracts. They do not establish that arbitrary model output follows project intent or that a completion claim is correct.

## Limitations and non-features

At this revision, the important negative-space facts are:

- No built-in sandbox, cwd confinement, network deny, secret broker, or per-command approval.
- No native MCP, subagent supervisor, plan/todo/goal manager, or background terminal.
- No PTC/code-mode nested tool runtime.
- No deterministic final-answer verifier or required test pass.
- No default step/time/cost budget or repetition detector.
- No crash-resumable in-flight operation/tool protocol in the shipping CLI.
- No persistent PTY or stdin continuation for model bash.
- No automatic skill-body loading; the model must notice the manifest and call read.
- No guarantee that extension state is durable unless the extension records it in session entries.
- No atomic rollback across tool calls or across an edit/write followed by abort.
- Compaction is a lossy model-generated checkpoint and can omit or distort facts despite its structured prompt; full transcript survives for human/model revisit.
- Project trust protects resource loading, not tool actions or context-file prompt injection.
- Provider-deferred handles exist at the library layer, but the shipping coding-agent does not expose a complete durable suspend/resume experience.

These are intentional in several cases: Pi explicitly positions workflow-specific behavior in extensions and recommends external OS isolation for security at [usage.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/usage.md:305) and [security.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/security.md:31).

## Primitive inventory

| Primitive | Default/core status | Implementation artifact | Durable across restart? | Primary invariant / caveat |
|---|---|---|---:|---|
| System prompt builder | active | [system-prompt.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts:27) | rebuilt | Replacement prompt still receives context/skills/cwd |
| Project context inheritance | active | [resource-loader.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/resource-loader.ts:119) | source files | One file per directory; root→cwd order; outside trust gate |
| Skill manifest | active when skills/read exist | [skills.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/skills.ts:347) | source files | Metadata in prompt, body loaded through read |
| Provider/model registry | active | [models.ts](/Users/mathiasl/src/pi/packages/ai/src/models.ts:88) | config/auth dependent | Common normalized streaming contract |
| Reasoning/thinking control | active where model supports | [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:1651) | session entry/settings | Clamped to selected model |
| Direct tool loop | active | [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:156) | finalized transcript only | Tool errors return to model |
| Default coding tools | active | [tools/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/index.ts:164) | effects persist | read/bash/edit/write |
| Optional built-in search tools | selectable | [tools/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/index.ts:93) | no state | grep/find/ls/powershell |
| Parallel tool batch | default | [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:409) | results after finalization | Preflight/source-order, execute concurrent, results source-order |
| Per-file mutation queue | active | [file-mutation-queue.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/file-mutation-queue.ts:28) | no | Same-file serialization, no rollback |
| Steering queue | active | [agent.ts](/Users/mathiasl/src/pi/packages/agent/src/agent.ts:282) | no | Delivered after current tool batch |
| Follow-up queue | active | [agent.ts](/Users/mathiasl/src/pi/packages/agent/src/agent.ts:287) | no | Delivered when run would stop |
| Abort | active | [agent.ts](/Users/mathiasl/src/pi/packages/agent/src/agent.ts:313) | no | Cooperative; shell kills process tree |
| Shell output artifact | active on truncation | [bash.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/bash.ts:416) | temp-file lifetime | Tail in context, full output path provided |
| Deferred tool definitions | provider-dependent | [deferred-tools.ts](/Users/mathiasl/src/pi/packages/ai/src/utils/deferred-tools.ts:7) | transcript annotations | Schema loading, not PTC |
| PTC/code mode | absent | direct loop evidence above | no | Bash can compose shell only |
| Automatic compaction | active/default | [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:126) | yes, compaction entry | Structured LLM summary + exact retained tail |
| Overflow compact-and-retry | active | [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2152) | compaction/history | One recovery attempt |
| JSONL session tree | active/default | [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:845) | yes | Append-only parent-linked entries |
| In-place branch navigation | active | [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:1355) | leaf reconstructed | No deletion of abandoned path |
| Crash-resumable active operation | absent in shipping | [agent-harness.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/agent-harness.ts:347) | no | Developmental schema exists, executor does not |
| Generic retry | active/default | [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2883) | failed attempts persist | 3 retries, exponential 2-second base |
| Project trust | active | [project-trust.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/project-trust.ts:46) | trust store | Resource-loading gate only |
| Sandbox/permissions | absent by default | [security.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/security.md:31) | n/a | Host user privileges |
| Extension lifecycle | active | [types.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/extensions/types.ts:1252) | extension-defined | In-process, broad authority |
| Plan/todo/progress tools | examples only | [plan-mode/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/plan-mode/index.ts:1) | extension-defined | Not a core invariant |
| Subagent delegation | example only | [subagent/index.ts](/Users/mathiasl/src/pi/packages/coding-agent/examples/extensions/subagent/index.ts:1) | no child sessions | Separate no-session Pi processes |
| Background process manager | absent | [usage.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/usage.md:305) | no | External tmux/container suggested |
| Completion verification | absent | prompt/core evidence above | no | Model/user/extension initiated only |
| Usage/cost statistics | active | [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:3318) | session history | Includes compacted-away requests |
| Cache-waste accounting | active | [cache-stats.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/cache-stats.ts:104) | derivable | Compaction resets comparison; model switch counts |
| Runtime trace schema | partial/developmental | [telemetry.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/telemetry.ts:42) | sink-defined | Rich harness vocabulary is not shipping execution proof |

## Design invariants

1. **The model sees only the active tool set.** Tool prose and schemas are derived from active registered tools and refreshed before subsequent turns; inactive built-ins are not silently promised. [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:1066)
2. **Provider differences live below one normalized conversation/tool contract.** Agent code consumes normalized streams and stop reasons rather than provider SDK objects. [types.ts](/Users/mathiasl/src/pi/packages/ai/src/types.ts:264)
3. **A truncated assistant tool-call batch never mutates the host.** A length stop causes all calls in that assistant message to fail safely. [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:372)
4. **Parallelism does not reorder transcript artifacts.** Completion events may race, but result messages rejoin assistant source order. [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:487)
5. **Same-file mutations serialize.** Edit/write calls for one canonical path cannot concurrently interleave inside this process. [file-mutation-queue.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/file-mutation-queue.ts:28)
6. **User steering is boundary-safe, not preemptive.** The current assistant's tool batch completes before steering reaches the model. [agent-loop.ts](/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts:243)
7. **Compaction changes model view, not source history.** The latest summary plus exact tail is projected while original JSONL branches remain. [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:410)
8. **Overflow recovery is bounded.** Pi performs at most one compact-and-retry recovery for the interrupted response. [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:2166)
9. **Finalized lifecycle events are the shipping persistence boundary.** message_end entries survive; in-flight operation state is not promised. [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:673)
10. **Branching is append-only.** Moving the leaf creates a new child path and does not rewrite/delete the old branch. [session-manager.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts:1355)
11. **Project trust is not execution security.** It gates protected project resources before load, while tools retain host-user authority. [security.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/security.md:5)
12. **Workflow policy is compositional.** Planning, todos, permissions, sandboxing, delegation, and background execution belong to explicit extensions/packages rather than hidden core behavior. [usage.md](/Users/mathiasl/src/pi/packages/coding-agent/docs/usage.md:305)
13. **Summary calls avoid polluting prompt caches.** One-off compaction/branch requests set cacheRetention=none. [compaction.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts:579)
14. **Model switches are transcript facts.** Auth is checked, the change is appended, and thinking is adapted to the new model. [agent-session.ts](/Users/mathiasl/src/pi/packages/coding-agent/src/core/agent-session.ts:1651)
15. **The shipping CLI must not be confused with the developmental durable AgentHarness.** Its operational methods currently reject HarnessNotImplemented, so durable record types are design surface, not current end-to-end behavior. [agent-harness.ts](/Users/mathiasl/src/pi/packages/agent/src/harness/agent-harness.ts:347)
