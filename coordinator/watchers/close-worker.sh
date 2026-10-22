#!/bin/bash
# close-worker.sh <pane id> --task <text> [--report <path>]: closes the pane of a finished worker with close-pane.sh, then
# writes its recovery line to the calling architect's status file. Removes no worktree and no branch.
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/coord-lib.sh"
orch_load_config
orch_watcher_python
exec "$ORCH_WATCHER_PYTHON" -I "$root/close_worker.py" "$@"
