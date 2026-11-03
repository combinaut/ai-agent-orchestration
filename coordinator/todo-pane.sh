#!/usr/bin/env bash
# Opens the to-do pane beside the calling pane and follows a recycled Coordinator tab.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$root/watchers/coord-lib.sh"
orch_load_config

label="To-do"
herdr=${HERDR_BIN_PATH:-${ORCH_HERDR_BIN:-herdr}}
python=${ORCH_PYTHON3:-python3}

pane_rows=$("$herdr" pane list | "$python" -c '
import json, os, sys
label = sys.argv[1]
rows = json.load(sys.stdin).get("result", {}).get("panes", [])
matches = [row for row in rows if isinstance(row, dict) and row.get("pane_id") and row.get("label") == label]
existing = matches[-1] if matches else {}
caller = os.environ.get("HERDR_PANE_ID")
caller_row = next((row for row in rows if isinstance(row, dict) and row.get("pane_id") == caller), {})
print(existing.get("pane_id", ""))
print(existing.get("tab_id", ""))
print(caller_row.get("tab_id", ""))
' "$label")
newline=$'\n'
existing=${pane_rows%%$newline*}
pane_rows=${pane_rows#*$newline}
existing_tab=${pane_rows%%$newline*}
caller_tab=${pane_rows#*$newline}
if [ -n "$existing" ]; then
  if [ -n "$existing_tab" ] && [ "$existing_tab" = "$caller_tab" ]; then
    echo "to-do pane already open: $existing"
    exit 0
  fi
  if [ -z "${HERDR_PANE_ID:-}" ] || [ -z "$existing_tab" ] || [ -z "$caller_tab" ]; then
    echo "cannot move to-do pane: unable to determine the caller and pane tabs" >&2
    exit 1
  fi
  if ! "$herdr" pane move "$existing" --tab "$caller_tab" --target-pane "$HERDR_PANE_ID" --split right; then
    echo "failed to move to-do pane: $existing" >&2
    exit 1
  fi
  if ! "$herdr" pane focus --direction left; then
    echo "to-do pane moved but failed to return focus to the Coordinator: $existing" >&2
    exit 1
  fi
  echo "to-do pane moved: $existing"
  exit 0
fi

[ -n "${HERDR_PANE_ID:-}" ] || { echo "HERDR_PANE_ID is not set; run this from the Coordinator's pane" >&2; exit 69; }
pane=$("$herdr" pane split "$HERDR_PANE_ID" --direction right --no-focus |
  "$python" -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["pane_id"])')
printf -v command '%q %q' "$python" "$root/user-todo-view.py"
if ! "$herdr" pane run "$pane" "$command" || ! "$herdr" pane rename "$pane" "$label"; then
  "$root/watchers/close-pane.sh" "$pane"
  exit 1
fi
echo "to-do pane opened: $pane"
