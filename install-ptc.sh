#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$project_root/install.sh" "$@"
