#!/bin/sh

set -eu

usage() {
  printf '%s\n' \
    'Install ADK Coding Agent from this checkout.' \
    '' \
    'Usage: ./install.sh [options]' \
    '' \
    'On macOS the default installation bootstraps missing Homebrew-managed' \
    'prerequisites, installs Magnitude, and builds the Bubble Tea TUI.' \
    '' \
    'Options:' \
    '  --bin-dir DIR       Install command links in DIR (default: ~/.local/bin)' \
    '  --dev               Include development dependency groups' \
    '  --magnitude         Install/update Magnitude with external-harness support' \
    '  --minimal           Install only the Python CLI and Gemini dependencies' \
    '  --no-local-models   Omit LiteLLM support for Magnitude/local endpoints' \
    '  --plan              Print the platform-aware installation plan and exit' \
    '  --tui               Build and install the Bubble Tea TUI (requires Go 1.24+)' \
    '  -h, --help          Show this help'
}

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bin_dir=${UV_TOOL_BIN_DIR:-${HOME}/.local/bin}
platform=$(uname -s)
include_dev=0
include_local_models=1
include_magnitude=0
include_tui=0
magnitude_requested=0
no_local_models=0
print_plan=0

if [ "$platform" = "Darwin" ]; then
  include_magnitude=1
  include_tui=1
fi

magnitude_version_supported() {
  candidate=${1#v}
  candidate=${candidate%%-*}
  candidate_major=${candidate%%.*}
  candidate_tail=${candidate#*.}
  candidate_minor=${candidate_tail%%.*}
  candidate_patch=${candidate_tail#*.}
  case "$candidate_major.$candidate_minor.$candidate_patch" in
    *[!0-9.]*|.*|*..*|*.) return 1 ;;
  esac
  if [ "$candidate_major" -gt 0 ]; then
    return 0
  fi
  if [ "$candidate_minor" -gt 0 ]; then
    return 0
  fi
  [ "$candidate_patch" -ge 8 ]
}

go_version_supported() {
  candidate=${1#go}
  candidate_major=${candidate%%.*}
  candidate_tail=${candidate#*.}
  candidate_minor=${candidate_tail%%.*}
  case "$candidate_major.$candidate_minor" in
    *[!0-9.]*|.*|*..*|*.) return 1 ;;
  esac
  if [ "$candidate_major" -gt 1 ]; then
    return 0
  fi
  [ "$candidate_major" -eq 1 ] && [ "$candidate_minor" -ge 24 ]
}

require_homebrew() {
  if ! command -v brew >/dev/null 2>&1; then
    printf '%s\n' \
      'error: the full macOS installation requires Homebrew.' \
      'Install it from https://brew.sh and rerun ./install.sh' >&2
    exit 1
  fi
}

install_brew_formula() {
  formula=$1
  command_name=$2
  description=$3
  require_homebrew
  printf 'Installing %s with Homebrew\n' "$description"
  brew install "$formula"
  hash -r 2>/dev/null || true
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'error: Homebrew installed %s, but `%s` is not on PATH\n' \
      "$formula" "$command_name" >&2
    exit 1
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bin-dir)
      if [ "$#" -lt 2 ]; then
        printf '%s\n' 'error: --bin-dir requires a directory' >&2
        exit 2
      fi
      bin_dir=$2
      shift 2
      ;;
    --dev)
      include_dev=1
      shift
      ;;
    --magnitude)
      magnitude_requested=1
      include_magnitude=1
      include_local_models=1
      shift
      ;;
    --minimal)
      include_local_models=0
      include_magnitude=0
      include_tui=0
      shift
      ;;
    --no-local-models)
      no_local_models=1
      include_local_models=0
      include_magnitude=0
      shift
      ;;
    --plan)
      print_plan=1
      shift
      ;;
    --tui)
      include_tui=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$magnitude_requested" -eq 1 ] && [ "$no_local_models" -eq 1 ]; then
  printf '%s\n' 'error: --magnitude cannot be combined with --no-local-models' >&2
  exit 2
fi

if [ "$platform" = "Darwin" ]; then
  platform_name=macOS
else
  platform_name=$platform
fi
printf 'Detected platform: %s\n' "$platform_name"

if [ "$print_plan" -eq 1 ]; then
  printf '%s\n' \
    '' \
    'Installation plan:' \
    "  Python environment: $project_root/.venv" \
    '  Environment policy: remove and recreate on every installation' \
    "  Local-model support: $include_local_models" \
    "  Magnitude: $include_magnitude" \
    "  Bubble Tea TUI: $include_tui" \
    "  Development tools: $include_dev" \
    "  Command directory: $bin_dir" \
    '  Launch workspace: selected at runtime from the server terminal'
  if [ "$platform" = "Darwin" ]; then
    printf '%s\n' '  Missing uv, Git, Node.js/npm, and Go are installed with Homebrew.'
  fi
  exit 0
fi

if [ "$platform" = "Darwin" ]; then
  if ! command -v uv >/dev/null 2>&1; then
    install_brew_formula uv uv uv
  fi
  if ! command -v git >/dev/null 2>&1; then
    install_brew_formula git git Git
  fi
  if [ "$include_magnitude" -eq 1 ] && ! command -v npm >/dev/null 2>&1; then
    install_brew_formula node npm Node.js
  fi
  if [ "$include_tui" -eq 1 ]; then
    if ! command -v go >/dev/null 2>&1; then
      install_brew_formula go go Go
    elif ! go_version_supported "$(go env GOVERSION)"; then
      require_homebrew
      printf 'Updating Go to 1.24 or newer with Homebrew\n'
      if brew list --versions go >/dev/null 2>&1; then
        brew upgrade go
      else
        brew install go
      fi
      hash -r 2>/dev/null || true
    fi
  fi
fi

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' \
    'error: uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  printf '%s\n' 'error: git is required' >&2
  exit 1
fi

if [ "$include_tui" -eq 1 ]; then
  if ! command -v go >/dev/null 2>&1; then
    printf '%s\n' 'error: --tui requires Go 1.24 or newer' >&2
    exit 1
  fi
  go_version=$(go env GOVERSION)
  if ! go_version_supported "$go_version"; then
    printf 'error: --tui requires Go 1.24 or newer; found %s\n' "$go_version" >&2
    exit 1
  fi
fi

if [ "$include_magnitude" -eq 1 ]; then
  if ! command -v npm >/dev/null 2>&1; then
    printf '%s\n' 'error: --magnitude requires npm' >&2
    exit 1
  fi
  magnitude_ready=0
  if command -v magnitude >/dev/null 2>&1; then
    magnitude_version=$(magnitude --version)
    if magnitude_version_supported "$magnitude_version"; then
      magnitude_ready=1
      printf 'Using compatible Magnitude %s\n' "$magnitude_version"
    fi
  fi
  if [ "$magnitude_ready" -eq 0 ]; then
    printf '%s\n' 'Installing Magnitude 0.0.8+ for external-harness support'
    npm install --global '@magnitudedev/cli@^0.0.8'
  fi
  if ! command -v magnitude >/dev/null 2>&1; then
    printf '%s\n' 'error: npm installed Magnitude, but `magnitude` is not on PATH' >&2
    exit 1
  fi
  magnitude_version=$(magnitude --version)
  if ! magnitude_version_supported "$magnitude_version"; then
    printf 'error: Magnitude 0.0.8+ is required; found %s\n' "$magnitude_version" >&2
    exit 1
  fi
fi

mkdir -p "$bin_dir"
cli_target="$bin_dir/adk-coding-agent"
if [ -e "$cli_target" ] && [ ! -L "$cli_target" ]; then
  printf 'error: refusing to replace non-symlink: %s\n' "$cli_target" >&2
  exit 1
fi
if [ "$include_tui" -eq 1 ]; then
  tui_target="$bin_dir/adk-agent-tui"
  if [ -e "$tui_target" ] && [ ! -L "$tui_target" ]; then
    printf 'error: refusing to replace non-symlink: %s\n' "$tui_target" >&2
    exit 1
  fi
fi

venv_path="$project_root/.venv"
if [ -e "$venv_path" ]; then
  printf 'Removing existing uv environment at %s\n' "$venv_path"
  rm -rf -- "$venv_path"
fi
printf 'Creating fresh uv environment at %s\n' "$venv_path"
uv venv --python '>=3.11' "$venv_path"
printf 'Syncing locked Python dependencies into %s/.venv\n' "$project_root"
if [ "$include_dev" -eq 1 ] && [ "$include_local_models" -eq 1 ]; then
  uv sync --project "$project_root" --frozen --all-groups --extra local-models
elif [ "$include_dev" -eq 1 ]; then
  uv sync --project "$project_root" --frozen --all-groups
elif [ "$include_local_models" -eq 1 ]; then
  uv sync --project "$project_root" --frozen --no-default-groups --extra local-models
else
  uv sync --project "$project_root" --frozen --no-default-groups
fi

venv_python="$project_root/.venv/bin/python"
cli_source="$project_root/.venv/bin/adk-coding-agent"
if [ ! -x "$venv_python" ] || [ ! -x "$cli_source" ]; then
  printf '%s\n' 'error: uv did not create the expected local environment commands' >&2
  exit 1
fi
"$venv_python" -c 'import sys; assert sys.version_info >= (3, 11)'
"$venv_python" -c 'import fastapi, fff, google.adk, pydantic, uvicorn, yaml'
if [ "$include_local_models" -eq 1 ]; then
  "$venv_python" -c 'import importlib.util; assert importlib.util.find_spec("litellm") is not None'
fi
if [ "$include_dev" -eq 1 ]; then
  for dev_command in pytest pyright ruff; do
    if [ ! -x "$project_root/.venv/bin/$dev_command" ]; then
      printf 'error: missing development command in .venv: %s\n' "$dev_command" >&2
      exit 1
    fi
  done
fi
if [ -e "$cli_target" ] && [ ! -L "$cli_target" ]; then
  printf 'error: refusing to replace non-symlink: %s\n' "$cli_target" >&2
  exit 1
fi
temporary_link="$cli_target.tmp.$$"
trap 'rm -f "$temporary_link"' EXIT HUP INT TERM
ln -s "$cli_source" "$temporary_link"
mv -f "$temporary_link" "$cli_target"
trap - EXIT HUP INT TERM

if [ "$include_tui" -eq 1 ]; then
  temporary_tui=$(mktemp "${TMPDIR:-/tmp}/adk-agent-tui.XXXXXX")
  trap 'rm -f "$temporary_tui"' EXIT HUP INT TERM
  printf '%s\n' 'Building Bubble Tea TUI'
  (cd "$project_root/clients/tui" && go build -trimpath -o "$temporary_tui" .)
  tui_source="$project_root/.venv/bin/adk-agent-tui"
  install -m 0755 "$temporary_tui" "$tui_source"
  rm -f "$temporary_tui"
  trap - EXIT HUP INT TERM
  temporary_link="$tui_target.tmp.$$"
  trap 'rm -f "$temporary_link"' EXIT HUP INT TERM
  ln -s "$tui_source" "$temporary_link"
  mv -f "$temporary_link" "$tui_target"
  trap - EXIT HUP INT TERM
fi

printf '\nInstalled:\n  Python environment: %s/.venv\n  CLI: %s\n' "$project_root" "$cli_target"
if [ "$include_tui" -eq 1 ]; then
  printf '  TUI: %s\n' "$tui_target"
fi
case ":${PATH}:" in
  *":$bin_dir:"*) ;;
  *) printf '\nAdd %s to PATH to run the installed commands.\n' "$bin_dir" ;;
esac
if [ "$include_magnitude" -eq 1 ] && [ "$include_tui" -eq 1 ]; then
  printf '%s\n' \
    '' \
    'Installation is complete. If Magnitude has not selected and installed a model yet, run:' \
    '  magnitude setup' \
    '' \
    'After Magnitude setup, run the coding agent in two terminals:' \
    '' \
    'Terminal 1 — from the repository the coding agent should edit:' \
    '  STATE_ROOT="$HOME/.local/state/adk-coding-agent"' \
    '  mkdir -p "$STATE_ROOT"' \
    '  adk-coding-agent serve-magnitude --workspace "$(pwd)" --state-root "$STATE_ROOT"' \
    '' \
    'Terminal 2 — connect the Bubble Tea TUI:' \
    '  STATE_ROOT="$HOME/.local/state/adk-coding-agent"' \
    '  export ADK_CODING_AGENT_TOKEN="$(cat "$STATE_ROOT/server/auth-token")"' \
    '  adk-agent-tui --server ws://127.0.0.1:8765/v1/agent' \
    '' \
    'The server terminal must remain running while the TUI is connected.'
fi
