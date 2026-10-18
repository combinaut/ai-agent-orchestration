#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/coord-lib.sh"
orch_load_config
orch_export_config
orch_watcher_python
state="${1:?state directory required}"
"$root/coord-check.sh" --state-dir "$state"
# Dry-run preview only. Owner must explicitly enable delivery later.
exec "$ORCH_WATCHER_PYTHON" "$root/notify-inbox.py" --state-dir "$state" --cursor "$state/preview.cursor" --modes context workers hog balance host audit todo
