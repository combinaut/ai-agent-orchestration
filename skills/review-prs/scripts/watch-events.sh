#!/usr/bin/env bash
# watch-events.sh <log>: streams watcher events from <log>, from line one, or after the line saved in <log>.pos.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -u
log=${1:?usage: watch-events.sh <log>}
pos=$log.pos
events='^(new|ready|changed|cleared|merged|closed|PR fetch failing|recovered|Traceback|Error)'
start=$(($(cat "$pos" 2>/dev/null || echo 0) + 1))
n=$((start - 1))
tail -n +"$start" -F "$log" | while IFS= read -r line; do
  n=$((n + 1))
  echo "$n" >"$pos"
  [[ $line =~ $events ]] || continue
  printf '%s\n' "$line"
done
