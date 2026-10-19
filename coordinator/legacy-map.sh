#!/usr/bin/env bash
# Writes legacy-map.tsv: one <kind>\t<path> row per file on origin/main of the repo in $1 (or $ORCH_LEGACY_MAP_REPO) with a line its legacy rules count.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
. "$root/watchers/coord-lib.sh"
repo=${1:-${ORCH_LEGACY_MAP_REPO:-}}
out=${ORCH_LEGACY_MAP_OUT:-$HOME/.claude/coordinator/legacy-map.tsv}

[ -n "$repo" ] || { echo "usage: legacy-map.sh <repo> (or set ORCH_LEGACY_MAP_REPO)" >&2; exit 1; }
git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || { echo "legacy-map.sh: $repo is not a git repository" >&2; exit 1; }
orch_watcher_python

git -C "$repo" fetch -q origin main
status=0
rows=$("$ORCH_WATCHER_PYTHON" "$root/watchers/legacy_rules.py" map "$repo" origin/main) || status=$?
[ "$status" -ne 3 ] || exit 0
[ "$status" -eq 0 ] || exit "$status"

mkdir -p "$(dirname "$out")"
trap 'rm -f "$out.tmp"' EXIT
if [ -n "$rows" ]; then printf '%s\n' "$rows" >"$out.tmp"; else : >"$out.tmp"; fi
mv "$out.tmp" "$out"
[ -z "$rows" ] || printf '%s\n' "$rows" | cut -f1 | sort | uniq -c
