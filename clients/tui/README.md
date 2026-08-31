# ADK Coding Agent TUI

This Bubble Tea client speaks only the versioned WebSocket control protocol and
normalized AG-UI JSON events. It does not import Google ADK, load harness YAML, or
know which registered harness is serving a run.

Building requires Go 1.24 or newer (the minimum required by the pinned Bubble Tea
release). Dependency checksums are committed in `go.sum`.

## Build and run

```bash
cd clients/tui
go build -o adk-agent-tui .
export STATE_ROOT=/absolute/path/to/state
export ADK_CODING_AGENT_TOKEN="$(cat "$STATE_ROOT/server/auth-token")"
./adk-agent-tui --server ws://127.0.0.1:8765/v1/agent
```

The local server requires a bearer token of at least 32 UTF-8 bytes. The client
reads it from `ADK_CODING_AGENT_TOKEN` by default or from `--token`; it never sends
an `Origin` header. The default token file is beneath the configured state root at
`server/auth-token`.

Start immediately with a prompt or attach to a durable run cursor:

```bash
./adk-agent-tui --input "Fix the parser and run its tests"
./adk-agent-tui --run RUN_ID --after 42
```

While a run is active, ordinary input is sent as steering at the next safe point.
Commands are `/start PROMPT`, `/attach RUN [CURSOR]`, `/pause`, `/cancel`,
`/reconnect`, `/help`, and `/quit`. When the server uses `openai_codex`, Pi-style
provider commands are also available: `/login`, `/logout`, `/auth`, `/models`,
`/model MODEL_ID`, and `/benchmark [OPTIONS]`. Login and benchmarking suspend the
alternate screen while their interactive process runs, then restore the TUI. Model
selection and benchmark winners apply after restarting the server. `Ctrl-C` requests
cancellation for a running task; `Ctrl-D` exits the client.

The TUI receives an allowlisted provider, model name, and readiness value in the
server hello, so the coding-model line is useful before a task is submitted. An
`authentication_required` state includes an explicit `/login` prompt. Tokens,
endpoints, and provider response payloads are not part of this public projection.

The client reconnects with exponential backoff, negotiates protocol version 1 on
each connection, and attaches with the highest event sequence it has applied.
Replayed events at or below that cursor are ignored. Buffers, frame size, retained
history, acknowledgement frequency, heartbeat, and reconnect bounds are all
configurable through CLI flags; run with `--help` for the complete list.
