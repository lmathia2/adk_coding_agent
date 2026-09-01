#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Start the ADK Coding Agent with shared local state (Codex subscription provider).

Usage:
  adk-agent-start server [--workspace DIR] [--state-root DIR] [--model ID] [--notebook-ptc] [--trust-project] [-- SERVER_ARGS...]
  adk-agent-start tui [--state-root DIR] [--server URL] [-- TUI_ARGS...]
  adk-agent-start run [--workspace DIR] [--state-root DIR] [--model ID] [--trust-project] [-- TUI_ARGS...]

--server and --tui are aliases. --provider codex is accepted for older commands.
The workspace defaults to the current directory. /login authenticates in the TUI.
ADK_CODING_AGENT_STATE_ROOT defaults to ~/.local/state/adk-coding-agent.
ADK_CODING_AGENT_SERVER_URL configures only the TUI; default ws://127.0.0.1:8765/v1/agent.
For a custom YAML/Gemini provider use adk-coding-agent serve --config FILE directly.
EOF
}
die() { printf 'error: %s\n' "$1" >&2; exit "${2:-1}"; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "required command is not on PATH: $1"; }
script_path=$0
links=0
while [ -L "$script_path" ]; do
  links=$((links + 1)); [ "$links" -le 20 ] || die 'launcher symlink chain is too deep'
  target=$(readlink "$script_path")
  case "$target" in
    /*) script_path=$target ;;
    *) script_path=$(dirname -- "$script_path")/$target ;;
  esac
done
project_root=$(CDPATH= cd -- "$(dirname -- "$script_path")" && pwd -P)

mode=${1:-}
case "$mode" in
  server|tui|run) shift ;;
  --server) mode=server; shift ;;
  --tui) mode=tui; shift ;;
  -h|--help|'') usage; exit 0 ;;
  *) die "unknown mode: $mode" 2 ;;
esac
workspace=$(pwd -P)
state_root=${ADK_CODING_AGENT_STATE_ROOT:-${HOME}/.local/state/adk-coding-agent}
server_url=${ADK_CODING_AGENT_SERVER_URL:-ws://127.0.0.1:8765/v1/agent}
model=
trust_project=0
notebook_ptc=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --state-root|--workspace|--server|--model|--provider)
      [ "$#" -ge 2 ] && [ -n "$2" ] || die "$1 requires a value" 2
      case "$1" in
        --state-root) state_root=$2 ;;
        --server) [ "$mode" = tui ] || die '--server URL is only valid in tui mode' 2; server_url=$2 ;;
        --workspace) [ "$mode" != tui ] || die '--workspace is selected by the server' 2; workspace=$2 ;;
        --model) [ "$mode" != tui ] || die '--model is selected by the server' 2; model=$2 ;;
        --provider) [ "$mode" != tui ] && [ "$2" = codex ] || die 'the launcher supports only --provider codex' 2 ;;
      esac
      shift 2 ;;
    --trust-project) [ "$mode" != tui ] || die '--trust-project is selected by the server' 2; trust_project=1; shift ;;
    --notebook-ptc) [ "$mode" != tui ] || die '--notebook-ptc is selected by the server' 2; notebook_ptc=1; shift ;;
    --) shift; break ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown launcher option: $1 (use -- before forwarded options)" 2 ;;
  esac
done

if [ "$mode" != tui ]; then
  require_command adk-coding-agent
  [ -d "$workspace" ] || die "workspace is not a directory: $workspace"
  workspace=$(CDPATH= cd -- "$workspace" && pwd -P)
  mkdir -p "$state_root/server"
fi
if [ -d "$state_root" ]; then
  state_root=$(CDPATH= cd -- "$state_root" && pwd -P)
fi
token_file=$state_root/server/auth-token
printf '%s\n' 'ADK Coding Agent launch configuration:' \
  "  State root: $state_root" "  Auth token file: $token_file" \
  "  TUI WebSocket URL: $server_url" \
  '  Environment read: ADK_CODING_AGENT_STATE_ROOT, ADK_CODING_AGENT_SERVER_URL'

start_server() {
  [ -z "$model" ] || set -- --model "$model" "$@"
  [ "$trust_project" -eq 0 ] || set -- --trust-project "$@"
  [ "$notebook_ptc" -eq 0 ] || set -- --notebook-ptc "$@"
  exec adk-coding-agent serve-codex --workspace "$workspace" --state-root "$state_root" "$@"
}
if [ "$mode" != tui ]; then
  printf '%s\n' "  Workspace: $workspace" '  Model provider: codex' \
    "  Model: ${model:-saved/provider default}" \
    "  Notebook PTC: $notebook_ptc" \
    "  Project instructions/skills trusted: $trust_project" \
    '  Environment set: none (workspace and state root are passed as server flags)'
fi
[ "$mode" != server ] || start_server "$@"
if [ -n "${ADK_CODING_AGENT_TUI_COMMAND:-}" ]; then
  tui_command=$ADK_CODING_AGENT_TUI_COMMAND
elif [ -x "$project_root/clients/terminal/adk-agent-tui" ]; then
  tui_command=$project_root/clients/terminal/adk-agent-tui
elif command -v adk-agent-tui >/dev/null 2>&1; then
  tui_command=adk-agent-tui
else
  die 'Pi-style terminal is not installed; run ./install.sh or npm run build --prefix clients/terminal'
fi
[ -x "$tui_command" ] || die "terminal command is not executable: $tui_command"
if [ "$mode" = run ]; then
  server_log=$state_root/server/foreground.log
  printf '%s\n' "  Server log: $server_log" \
    '  Lifecycle: this command owns and stops its harness server child'
  start_server >>"$server_log" 2>&1 &
  server_pid=$!
  cleanup() {
    trap - EXIT HUP INT TERM
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' HUP TERM
  attempts=0
  while [ ! -s "$token_file" ]; do
    kill -0 "$server_pid" 2>/dev/null || die "managed server exited; inspect $server_log"
    attempts=$((attempts + 1))
    [ "$attempts" -lt 300 ] || die "managed server did not become ready; inspect $server_log"
    sleep 0.1
  done
  sleep 0.2
  kill -0 "$server_pid" 2>/dev/null || die "managed server exited during startup; inspect $server_log"
fi

[ -r "$token_file" ] || die "auth token not found at $token_file; start the server first"
token=$(tr -d '\r\n' <"$token_file")
[ -n "$token" ] || die "auth token file is empty: $token_file"
printf '%s\n' \
  '  Environment set for TUI: ADK_CODING_AGENT_TOKEN (read from the auth token file)' \
  '  Environment set for TUI: ADK_CODING_AGENT_STATE_ROOT (resolved state directory)' \
  '  The token value is never printed or passed on the command line.'
ADK_CODING_AGENT_TOKEN=$token
ADK_CODING_AGENT_STATE_ROOT=$state_root
export ADK_CODING_AGENT_TOKEN ADK_CODING_AGENT_STATE_ROOT
if [ "$mode" = tui ]; then
  exec "$tui_command" --server "$server_url" --state-root "$state_root" "$@"
fi
"$tui_command" --server "$server_url" --state-root "$state_root" "$@"
