#!/bin/bash
# Lists lines a PR adds that build on legacy front-end code. Usage: legacy-scan.sh <pr-number> [repo] (defaults to watchers.primary_repo)
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/watchers/coord-lib.sh"
orch_load_config
orch_export_config
orch_watcher_python
exec "$ORCH_WATCHER_PYTHON" "$root/watchers/audit.py" "$@"
