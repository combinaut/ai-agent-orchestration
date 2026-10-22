#!/usr/bin/env bash
# Opens the to-do pane beside the calling pane unless a pane labelled "To-do" is already open.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$root/watchers/coord-lib.sh"
orch_load_config

label="To-do"
herdr=${HERDR_BIN_PATH:-${ORCH_HERDR_BIN:-herdr}}
python=${ORCH_PYTHON3:-python3}

existing=$("$herdr" pane list | "$python" -c '
import json, sys
label = sys.argv[1]
rows = json.load(sys.stdin).get("result", {}).get("panes", [])
matches = [row["pane_id"] for row in rows if isinstance(row, dict) and row.get("pane_id") and row.get("label") == label]
print(matches[-1] if matches else "")
' "$label")
if [ -n "$existing" ]; then
  echo "to-do pane already open: $existing"
  exit 0
fi

[ -n "${HERDR_PANE_ID:-}" ] || { echo "HERDR_PANE_ID is not set; run this from the Coordinator's pane" >&2; exit 69; }
pane=$("$herdr" pane split "$HERDR_PANE_ID" --direction right --no-focus |
  "$python" -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["pane_id"])')
if [ -n "${ORCH_USER_TODO_OUTPUT:-}" ]; then
  printf -v command 'env ORCH_USER_TODO_OUTPUT=%q %q %q' "$ORCH_USER_TODO_OUTPUT" "$python" "$root/user-todo-view.py"
else
  printf -v command '%q %q' "$python" "$root/user-todo-view.py"
fi
if ! "$herdr" pane run "$pane" "$command" || ! "$herdr" pane rename "$pane" "$label"; then
  "$root/watchers/close-pane.sh" "$pane"
  exit 1
fi
echo "to-do pane opened: $pane"
