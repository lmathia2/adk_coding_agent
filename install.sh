#!/bin/sh

set -eu

usage() {
  printf '%s\n' \
    'Install ADK Coding Agent from this checkout.' \
    '' \
    'Usage: ./install.sh [options]' \
    '' \
    'Options:' \
    '  --bin-dir DIR       Install command links in DIR (default: ~/.local/bin)' \
    '  --dev               Include development dependency groups' \
    '  --magnitude         Install/update Magnitude with external-harness support' \
    '  --no-local-models   Omit LiteLLM support for Magnitude/local endpoints' \
    '  --tui               Build and install the Bubble Tea TUI (requires Go 1.24+)' \
    '  -h, --help          Show this help'
}

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bin_dir=${UV_TOOL_BIN_DIR:-${HOME}/.local/bin}
include_dev=0
include_local_models=1
include_magnitude=0
include_tui=0

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
      include_magnitude=1
      include_local_models=1
      shift
      ;;
    --no-local-models)
      include_local_models=0
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

if [ "$include_magnitude" -eq 1 ] && [ "$include_local_models" -eq 0 ]; then
  printf '%s\n' 'error: --magnitude cannot be combined with --no-local-models' >&2
  exit 2
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
  go_numeric=${go_version#go}
  go_major=${go_numeric%%.*}
  go_remainder=${go_numeric#*.}
  go_minor=${go_remainder%%.*}
  if [ "$go_major" -lt 1 ] || { [ "$go_major" -eq 1 ] && [ "$go_minor" -lt 24 ]; }; then
    printf 'error: --tui requires Go 1.24 or newer; found %s\n' "$go_version" >&2
    exit 1
  fi
fi

if [ "$include_magnitude" -eq 1 ]; then
  if ! command -v npm >/dev/null 2>&1; then
    printf '%s\n' 'error: --magnitude requires npm' >&2
    exit 1
  fi
  printf '%s\n' 'Installing Magnitude 0.0.8+ for external-harness support'
  npm install --global '@magnitudedev/cli@^0.0.8'
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

printf 'Creating or reusing uv environment at %s/.venv\n' "$project_root"
uv venv --allow-existing --python '>=3.11' "$project_root/.venv"
printf 'Syncing locked Python dependencies into %s/.venv\n' "$project_root"
if [ "$include_dev" -eq 1 ] && [ "$include_local_models" -eq 1 ]; then
  uv sync --project "$project_root" --frozen --all-groups --extra local-models
elif [ "$include_dev" -eq 1 ]; then
  uv sync --project "$project_root" --frozen --all-groups
elif [ "$include_local_models" -eq 1 ]; then
  uv sync --project "$project_root" --frozen --extra local-models
else
  uv sync --project "$project_root" --frozen
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
  printf '  %s\n' "$tui_target"
fi
case ":${PATH}:" in
  *":$bin_dir:"*) ;;
  *) printf '\nAdd %s to PATH to run the installed commands.\n' "$bin_dir" ;;
esac
if [ "$(uname -s)" = "Darwin" ]; then
  if command -v magnitude >/dev/null 2>&1; then
    magnitude_version=$(magnitude --version)
    if magnitude_version_supported "$magnitude_version"; then
      printf '\nMagnitude %s detected. Start the local-model harness with:\n' "$magnitude_version"
      printf '  adk-coding-agent serve-magnitude --workspace /absolute/path/to/repository\n'
    else
      printf '\nMagnitude %s is too old for external harnesses. Update it with:\n' "$magnitude_version"
      printf '  ./install.sh --magnitude\n'
    fi
  else
    printf '\nFor local models, install Magnitude and run its one-time setup:\n'
    printf '  ./install.sh --magnitude && magnitude --setup\n'
  fi
fi
