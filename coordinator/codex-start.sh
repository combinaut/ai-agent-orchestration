#!/usr/bin/env bash
# Starts a Codex worker in an existing herdr pane: codex-start.sh <agent-name> <pane-id> <worker|smart>
# Takes no extra arguments; the smart tier adds -c model_reasoning_effort=$ORCH_CODEX_SMART_REASONING_EFFORT (default medium). The model comes from models.codex_worker or models.codex_smart; an empty one falls back to the other tier.
case ${ZSH_EVAL_CONTEXT:-} in *:file*) echo "run it, do not source it" >&2; return 1 ;; esac
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
. "$root/watchers/coord-lib.sh"

[ $# -eq 3 ] || { echo "usage: codex-start.sh <agent-name> <pane-id> <worker|smart> (no extra arguments)" >&2; exit 2; }
name=$1 pane=$2 tier=$3
case "$tier" in worker | smart) ;; *) echo "codex-start.sh: tier must be worker or smart, not '$tier'" >&2; exit 2 ;; esac
[[ "$name" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || { echo "codex-start.sh: bad agent name '$name'" >&2; exit 2; }
[[ "$pane" =~ ^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$ ]] || { echo "codex-start.sh: bad pane id '$pane'" >&2; exit 2; }

orch_load_config
if ! orch_provider_enabled codex; then
  [ -z "${ORCH_PROVIDERS:-}" ] || { echo "codex-start.sh: codex is off; add \"codex\" to providers.enabled (ORCH_PROVIDERS) in $(orch_config_path)" >&2; exit 1; }
fi
setting=ORCH_CODEX_WORKER_MODEL other=ORCH_CODEX_SMART_MODEL
[ "$tier" = worker ] || { setting=ORCH_CODEX_SMART_MODEL other=ORCH_CODEX_WORKER_MODEL; }
model=${!setting:-}
case "$model" in "" | "<"*">") model=${!other:-} ;; esac
case "$model" in "" | "<"*">" | -*) echo "codex-start.sh: set models.codex_$tier ($setting) in $(orch_config_path)" >&2; exit 1 ;; esac

herdr_bin=${ORCH_HERDR_BIN:-herdr}
orch_need_cmd "$herdr_bin"
extra=()
[ "$tier" != smart ] || extra=(-c "model_reasoning_effort=${ORCH_CODEX_SMART_REASONING_EFFORT:-medium}")
exec "$herdr_bin" agent start "$name" --kind codex --pane "$pane" -- --no-daemon --dangerously-bypass-hook-trust --model "$model" ${extra[@]+"${extra[@]}"}
