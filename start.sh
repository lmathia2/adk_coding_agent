#!/bin/sh

set -eu

default_state_root() {
  printf '%s\n' "${ADK_CODING_AGENT_STATE_ROOT:-${HOME}/.local/state/adk-coding-agent}"
}

default_server_url() {
  printf '%s\n' "${ADK_CODING_AGENT_SERVER_URL:-ws://127.0.0.1:8765/v1/agent}"
}

usage() {
  cat <<'EOF'
Start the ADK Coding Agent server or TUI with shared local configuration.

Usage:
  adk-agent-start server [--workspace DIR] [--state-root DIR] [-- SERVER_ARGS...]
  adk-agent-start tui [--state-root DIR] [--server URL] [-- TUI_ARGS...]

Aliases:
  adk-agent-start --server ...
  adk-agent-start --tui ...

Defaults:
  server workspace  current directory
  state root        $ADK_CODING_AGENT_STATE_ROOT or ~/.local/state/adk-coding-agent
  WebSocket URL     $ADK_CODING_AGENT_SERVER_URL or ws://127.0.0.1:8765/v1/agent

Examples:
  # Terminal 1
  adk-agent-start server --workspace "$HOME/src/coding_tools"

  # Terminal 2
  adk-agent-start tui

  # Forward an initial prompt to the TUI
  adk-agent-start tui -- --input "Inspect this repository and run its tests"
EOF
}

server_usage() {
  cat <<'EOF'
Usage: adk-agent-start server [--workspace DIR] [--state-root DIR] [-- SERVER_ARGS...]

Starts the Magnitude-backed harness server. The workspace defaults to the
current directory. The server writes its auth token beneath the shared state
root for `adk-agent-start tui` to read.
EOF
}

tui_usage() {
  cat <<'EOF'
Usage: adk-agent-start tui [--state-root DIR] [--server URL] [-- TUI_ARGS...]

Reads the server auth token from STATE_ROOT/server/auth-token, exports it only
to the TUI process as ADK_CODING_AGENT_TOKEN, and connects to the WebSocket URL.
EOF
}

die() {
  printf 'error: %s\n' "$1" >&2
  exit "${2:-1}"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    die "required command is not on PATH: $1"
  fi
}

print_common_configuration() {
  resolved_state_root=$1
  resolved_server_url=$2
  printf '%s\n' \
    '' \
    'ADK Coding Agent launch configuration:' \
    "  State root: $resolved_state_root" \
    "  Auth token file: $resolved_state_root/server/auth-token" \
    "  TUI WebSocket URL: $resolved_server_url"
}

mode=${1:-}
case "$mode" in
  server|--server)
    shift
    ;;
  tui|--tui)
    shift
    ;;
  -h|--help|'')
    usage
    exit 0
    ;;
  *)
    printf 'error: unknown mode: %s\n\n' "$mode" >&2
    usage >&2
    exit 2
    ;;
esac

state_root=$(default_state_root)
server_url=$(default_server_url)

case "$mode" in
  server|--server)
    workspace=$(pwd -P)
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --workspace)
          [ "$#" -ge 2 ] || die '--workspace requires a directory' 2
          workspace=$2
          shift 2
          ;;
        --state-root)
          [ "$#" -ge 2 ] || die '--state-root requires a directory' 2
          state_root=$2
          shift 2
          ;;
        -h|--help)
          server_usage
          exit 0
          ;;
        --)
          shift
          break
          ;;
        *)
          die "unknown server launcher option: $1 (use -- before serve-magnitude options)" 2
          ;;
      esac
    done

    [ -d "$workspace" ] || die "workspace is not a directory: $workspace"
    workspace=$(CDPATH= cd -- "$workspace" && pwd -P)
    mkdir -p "$state_root"
    state_root=$(CDPATH= cd -- "$state_root" && pwd -P)
    require_command adk-coding-agent

    print_common_configuration "$state_root" "$server_url"
    printf '%s\n' \
      "  Workspace: $workspace" \
      '  Environment read: ADK_CODING_AGENT_STATE_ROOT (unless --state-root is supplied)' \
      '  Environment set: none (workspace and state root are passed as server flags)' \
      '  ADK_CODING_AGENT_SERVER_URL configures the companion TUI, not this listener.' \
      '  Server writes the token file; its value is never printed.' \
      '' \
      'Starting server. Keep this terminal running.'

    exec adk-coding-agent serve-magnitude \
      --workspace "$workspace" \
      --state-root "$state_root" \
      "$@"
    ;;

  tui|--tui)
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --state-root)
          [ "$#" -ge 2 ] || die '--state-root requires a directory' 2
          state_root=$2
          shift 2
          ;;
        --server)
          [ "$#" -ge 2 ] || die '--server requires a WebSocket URL' 2
          server_url=$2
          shift 2
          ;;
        -h|--help)
          tui_usage
          exit 0
          ;;
        --)
          shift
          break
          ;;
        *)
          die "unknown TUI launcher option: $1 (use -- before TUI options)" 2
          ;;
      esac
    done

    if [ -d "$state_root" ]; then
      state_root=$(CDPATH= cd -- "$state_root" && pwd -P)
    fi
    token_file=$state_root/server/auth-token
    [ -r "$token_file" ] || die "auth token not found at $token_file; start the server first"
    token=$(tr -d '\r\n' <"$token_file")
    [ -n "$token" ] || die "auth token file is empty: $token_file"
    require_command adk-agent-tui

    print_common_configuration "$state_root" "$server_url"
    printf '%s\n' \
      '  Environment read: ADK_CODING_AGENT_STATE_ROOT, ADK_CODING_AGENT_SERVER_URL' \
      '  Environment set for TUI: ADK_CODING_AGENT_TOKEN (read from the auth token file)' \
      '  The token value is never printed or passed on the command line.' \
      '' \
      'Connecting TUI.'

    ADK_CODING_AGENT_TOKEN=$token
    export ADK_CODING_AGENT_TOKEN
    exec adk-agent-tui --server "$server_url" "$@"
    ;;
esac
