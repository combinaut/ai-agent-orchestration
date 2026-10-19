#!/usr/bin/env bash
# Writes legacy-map.tsv: one <kind>\t<path> row per file on origin/main of the repo in $1 (or $ORCH_LEGACY_MAP_REPO) that its .orchestration/legacy-rules.toml counts.
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
repo=${1:-${ORCH_LEGACY_MAP_REPO:-}}
out=${ORCH_LEGACY_MAP_OUT:-$HOME/.claude/coordinator/legacy-map.tsv}

[ -n "$repo" ] || { echo "usage: legacy-map.sh <repo> (or set ORCH_LEGACY_MAP_REPO)" >&2; exit 1; }
git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || { echo "legacy-map.sh: $repo is not a git repository" >&2; exit 1; }

git -C "$repo" fetch -q origin main
rows=$("${ORCH_PYTHON3:-python3}" "$root/watchers/legacy_rules.py" map "$repo" origin/main)

mkdir -p "$(dirname "$out")"
trap 'rm -f "$out.tmp"' EXIT
[ -z "$rows" ] || printf '%s\n' "$rows" >"$out.tmp"
[ -n "$rows" ] || : >"$out.tmp"
mv "$out.tmp" "$out"
[ -z "$rows" ] || printf '%s\n' "$rows" | cut -f1 | sort | uniq -c
