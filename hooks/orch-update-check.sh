#!/usr/bin/env bash
# SessionStart hook: says when the ai-agent-orchestration clone is behind origin or Radar is behind its pin.
# Never pulls. Network and repository failures are silent; the remote check gives up after 3 seconds.
# An invalid explicitly selected configuration is reported as an error.
set -u

if [ -n "${ORCH_CONFIG:-}" ] && [ ! -f "$ORCH_CONFIG" ]; then
  printf 'orch-update-check: ORCH_CONFIG is not a regular file: %s\n' "$ORCH_CONFIG" >&2
  exit 1
fi

self=${BASH_SOURCE[0]}
[ -L "$self" ] && self=$(readlink "$self")
repo=$(cd "$(dirname "$self")/.." 2>/dev/null && pwd -P) || exit 0
git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || exit 0

behind=""

branch=$(git -C "$repo" symbolic-ref -q --short HEAD)
remote=$(git -C "$repo" config "branch.$branch.remote")
merge=$(git -C "$repo" config "branch.$branch.merge")
if [ -n "$branch" ] && [ -n "$remote" ] && [ -n "$merge" ] && tmp=$(mktemp 2>/dev/null); then
  # Job control puts git and its helpers in their own process group, so a timeout ends all of them.
  set -m
  GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=3" \
    git -C "$repo" ls-remote "$remote" "$merge" >"$tmp" 2>/dev/null &
  pid=$!
  set +m
  ticks=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$ticks" -ge 30 ]; then
      kill -TERM -- "-$pid" 2>/dev/null
      break
    fi
    sleep 0.1
    ticks=$((ticks + 1))
  done
  if wait "$pid" 2>/dev/null; then
    remote_sha=$(awk 'NR == 1 { print $1 }' "$tmp")
    if [ -n "$remote_sha" ] && ! git -C "$repo" merge-base --is-ancestor "$remote_sha" HEAD 2>/dev/null; then
      behind=1
    fi
  fi
  rm -f "$tmp"
fi

pin_file="$repo/env/herdr/radar.pin"
# shellcheck source=coordinator/watchers/coord-lib.sh
radar=$(. "$repo/coordinator/watchers/coord-lib.sh" && orch_load_config 2>/dev/null && printf '%s' "${ORCH_RADAR_PATH:-}")
case "$radar" in \~/*) radar="$HOME/${radar#\~/}" ;; esac
[ -n "$radar" ] || radar="$HOME/.claude/herdr-radar"
if [ -f "$pin_file" ] && git -C "$radar" rev-parse --git-dir >/dev/null 2>&1; then
  pin=$(tr -d '[:space:]' <"$pin_file")
  head=$(git -C "$radar" rev-parse HEAD 2>/dev/null)
  if [ "$head" != "$pin" ] && ! git -C "$radar" merge-base --is-ancestor "$pin" HEAD 2>/dev/null; then
    behind=1
  fi
fi

[ -n "$behind" ] || exit 0
msg="ai-agent-orchestration setup out of date, run install.sh ($repo/install.sh)"
if command -v jq >/dev/null 2>&1; then
  jq -cn --arg m "$msg" '{systemMessage: $m, hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $m}}'
else
  printf '%s\n' "$msg"
fi
