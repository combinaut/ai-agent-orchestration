#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/coord-lib.sh"
orch_load_config
orch_export_config
orch_watcher_python
failed=0
for mode in context workers hog balance host audit todo answers; do
  args=("$@")
  [ "$mode" != answers ] || args+=(--preview-answers)
  if ! "$ORCH_WATCHER_PYTHON" "$root/watchers.py" "$mode" "${args[@]}"; then
    printf '%s check failed\n' "$mode" >&2
    failed=1
  fi
done
exit "$failed"
