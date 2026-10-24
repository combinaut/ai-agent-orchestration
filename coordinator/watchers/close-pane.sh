#!/bin/bash
# close-pane.sh <pane id>: closes a Herdr pane on purpose; the workers watcher reports it as closed, not dead.
# The marker is removed again when the close fails.
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/coord-lib.sh"
orch_load_config
pane="${1:?pane id required}"
case "$pane" in */* | . | .. | "") orch_die "not a pane id: $pane" ;; esac
dir="${ORCH_CLOSED_DIR:-$HOME/.claude/coordinator/watchers/prod-context/closed}"
mkdir -p "$dir"
touch "$dir/$pane"
# Usage ledger line, best effort: a failure here never stops the close or changes the exit code.
(orch_watcher_python && "$ORCH_WATCHER_PYTHON" -I "$root/usage_ledger.py" "$pane") ||
  echo "warning: usage ledger line not written for $pane" >&2
if ! "${HERDR_BIN_PATH:-${ORCH_HERDR_BIN:-herdr}}" pane close "$pane"; then
  rm -f "$dir/$pane"
  exit 1
fi
