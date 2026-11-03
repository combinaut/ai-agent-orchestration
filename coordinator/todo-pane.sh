#!/usr/bin/env bash
# Opens the to-do pane beside the calling pane and follows a recycled Coordinator tab.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$root/watchers/coord-lib.sh"
orch_load_config

label="To-do"
herdr=${HERDR_BIN_PATH:-${ORCH_HERDR_BIN:-herdr}}
python=${ORCH_PYTHON3:-python3}

decision=$("$herdr" pane list | "$python" -c '
import json, os, sys
label = sys.argv[1]
rows = json.load(sys.stdin).get("result", {}).get("panes", [])
matches = [row for row in rows if isinstance(row, dict) and row.get("pane_id") and row.get("label") == label]
caller = os.environ.get("HERDR_PANE_ID")
caller_row = next((row for row in rows if isinstance(row, dict) and row.get("pane_id") == caller), {})
caller_tab = caller_row.get("tab_id")
if not matches:
    print("open")
elif not caller or not caller_tab:
    print("unknown", matches[-1]["pane_id"])
else:
    existing = next((row for row in reversed(matches) if row.get("tab_id") == caller_tab), None)
    if existing:
        print("keep", existing["pane_id"])
    elif matches[-1].get("tab_id"):
        print("move", matches[-1]["pane_id"], caller_tab)
    else:
        print("unknown", matches[-1]["pane_id"])
' "$label")
read -r action existing caller_tab <<<"$decision"
case "$action" in
  keep)
    echo "to-do pane already open: $existing"
    exit 0
    ;;
  unknown)
    echo "cannot move to-do pane: unable to determine the caller and pane tabs: $existing" >&2
    exit 1
    ;;
  move)
    if ! "$herdr" pane move "$existing" --tab "$caller_tab" --target-pane "$HERDR_PANE_ID" --split right --no-focus; then
      echo "failed to move to-do pane: $existing" >&2
      exit 1
    fi
    echo "to-do pane moved: $existing"
    exit 0
    ;;
esac

[ -n "${HERDR_PANE_ID:-}" ] || { echo "HERDR_PANE_ID is not set; run this from the Coordinator's pane" >&2; exit 69; }
pane=$("$herdr" pane split "$HERDR_PANE_ID" --direction right --no-focus |
  "$python" -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["pane_id"])')
printf -v command '%q %q' "$python" "$root/user-todo-view.py"
if ! "$herdr" pane run "$pane" "$command" || ! "$herdr" pane rename "$pane" "$label"; then
  "$root/watchers/close-pane.sh" "$pane"
  exit 1
fi
echo "to-do pane opened: $pane"
