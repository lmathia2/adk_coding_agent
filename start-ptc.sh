#!/bin/sh
set -eu

usage() {
  printf '%s\n' 'Usage: ./start-ptc.sh [WORKSPACE] [-- launcher/TUI options]' \
    'Starts the notebook-PTC server and terminal with isolated local state.'
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
workspace=${1:-$(pwd -P)}
[ "$#" -eq 0 ] || shift
state_root=${SKEIN_PTC_STATE_ROOT:-${HOME}/.local/state/skein-ptc}
PATH=${HOME}/.local/bin:${PATH}
export PATH

exec "$project_root/start.sh" run --notebook-ptc \
  --workspace "$workspace" --state-root "$state_root" "$@"
