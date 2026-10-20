#!/usr/bin/env bash
# prs.sh <prs.py arguments>: runs prs.py on the orchestration's Python 3.11+.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
here="$(cd "$(dirname "$0")" && pwd -P)"
. "$here/../../../coordinator/watchers/coord-lib.sh"
orch_load_config
py=$(orch_python) || exit 2
exec "$py" "$here/prs.py" "$@"
