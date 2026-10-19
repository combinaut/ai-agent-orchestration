#!/bin/bash
# Prints one FOCUS line every interval (focus_check.py).
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/coord-lib.sh"
orch_load_config
orch_export_config
unset ORCH_LOADER_PYTHON
orch_python >/dev/null || exit 2
INTERVAL=${1:-900}
while true; do
  "$ORCH_LOADER_PYTHON" "$root/focus_check.py"
  sleep "$INTERVAL"
done
