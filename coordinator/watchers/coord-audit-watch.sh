#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/coord-lib.sh"
orch_load_config
orch_watcher_python
orch_require ORCH_PR_REPOS ORCH_PR_AUTHOR
orch_export_config
exec "$ORCH_WATCHER_PYTHON" "$root/watchers.py" audit "$@"
