#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Install ADK Coding Agent and the Bubble Tea TUI from this checkout.

Usage: ./install.sh [options]
  --bin-dir DIR  Command links (default: ~/.local/bin)
  --dev          Include tests, lint, and type-check tools
  --minimal      Python CLI only; skip the TUI
  --tui          Build the TUI (default; requires Go 1.24+)
  --plan         Print the installation plan without making changes
  -h, --help     Show help

macOS: missing uv, Git, and Go are installed with Homebrew.
Other platforms: install uv, Git, and Go 1.24+ first.
Each install recreates only this checkout's .venv. No workspace is selected.
EOF
}

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
bin_dir=${UV_TOOL_BIN_DIR:-${HOME}/.local/bin}
platform=$(uname -s)
include_dev=0
include_tui=1
print_plan=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bin-dir) [ "$#" -ge 2 ] || die '--bin-dir requires a directory'; bin_dir=$2; shift 2 ;;
    --dev) include_dev=1; shift ;;
    --minimal) include_tui=0; shift ;;
    --tui) include_tui=1; shift ;;
    --plan) print_plan=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'error: unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

platform_name=$platform
[ "$platform" != Darwin ] || platform_name=macOS
printf 'Detected platform: %s\n' "$platform_name"
venv_path=$project_root/.venv
if [ "$print_plan" -eq 1 ]; then
  printf '%s\n' 'Installation plan:' \
    "  Python environment: $venv_path" \
    '  Environment policy: remove and recreate on every installation' \
    "  Bubble Tea TUI: $include_tui" "  Development tools: $include_dev" \
    "  Command directory: $bin_dir" "  Runtime launcher: $bin_dir/adk-agent-start" \
    '  Launch workspace: selected at runtime'
  exit 0
fi

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    [ "$platform" = Darwin ] || die "$1 is required; see ./install.sh --help"
    command -v brew >/dev/null 2>&1 || die 'Install Homebrew from https://brew.sh and rerun ./install.sh'
    brew install "$1"
    command -v "$1" >/dev/null 2>&1 || die "$1 is not on PATH after installation"
  fi
}
require_command uv
require_command git
if [ "$include_tui" -eq 1 ]; then
  require_command go
  go_version=$(go env GOVERSION)
  if ! printf '%s\n' "$go_version" | awk -F. '{sub(/^go/, "", $1); exit !($1 > 1 || ($1 == 1 && $2 >= 24))}'; then
    die "Go 1.24+ is required; found $go_version. Upgrade Go (brew upgrade go on macOS)."
  fi
fi

commands="adk-coding-agent adk-agent-start"
[ "$include_tui" -eq 0 ] || commands="$commands adk-agent-tui"
mkdir -p "$bin_dir"
for name in $commands; do
  target=$bin_dir/$name
  [ ! -e "$target" ] || [ -L "$target" ] || die "refusing to replace non-symlink: $target"
done
[ ! -L "$venv_path" ] || die "refusing to remove symlink environment: $venv_path"
if [ -e "$venv_path" ]; then
  printf 'Removing existing uv environment at %s\n' "$venv_path"
  rm -rf -- "$venv_path"
fi
printf 'Creating fresh uv environment at %s\n' "$venv_path"
# Ignore external uv project-environment overrides: this checkout owns its environment.
UV_PROJECT_ENVIRONMENT=$venv_path
export UV_PROJECT_ENVIRONMENT
uv venv --python '>=3.11' "$venv_path"
groups=--no-default-groups
[ "$include_dev" -eq 0 ] || groups=--all-groups
uv sync --project "$project_root" --locked "$groups"
"$venv_path/bin/python" -c 'import fastapi, fff, google.adk, httpx, pydantic, uvicorn, yaml'
for name in adk-coding-agent; do
  [ -x "$venv_path/bin/$name" ] || die "missing installed command: $name"
done
if [ "$include_dev" -eq 1 ]; then
  for name in pytest pyright ruff; do
    [ -x "$venv_path/bin/$name" ] || die "missing development command: $name"
  done
fi
if [ "$include_tui" -eq 1 ]; then
  printf '%s\n' 'Building Bubble Tea TUI'
  (cd "$project_root/clients/tui" && go build -trimpath -o "$venv_path/bin/adk-agent-tui" .)
fi

for name in $commands; do
  source=$venv_path/bin/$name
  [ "$name" != adk-agent-start ] || source=$project_root/start.sh
  temporary_link=$bin_dir/$name.tmp.$$
  trap 'rm -f "$temporary_link"' EXIT
  ln -s "$source" "$temporary_link"
  mv -f "$temporary_link" "$bin_dir/$name"
  trap - EXIT
done

printf '\nInstalled:\n  Python environment: %s\n  Command directory: %s\n' "$venv_path" "$bin_dir"
case ":${PATH}:" in
  *":$bin_dir:"*) ;;
  *) printf 'Add %s to PATH before running the commands below.\n' "$bin_dir" ;;
esac
if [ "$include_tui" -eq 1 ]; then
  cat <<'EOF'

Start server + TUI together (ChatGPT subscription):
  adk-agent-start run --workspace /absolute/path/to/repository

Or use two terminals:
  adk-agent-start server --workspace /absolute/path/to/repository
  adk-agent-start tui

Enter /login in the TUI if prompted, then /model to select a model.
Only add --trust-project after reviewing the workspace's instructions and skills.
State defaults to ~/.local/state/adk-coding-agent; the launcher announces all
paths and the token/environment handoff. No API key or local-model install is needed.
EOF
fi
