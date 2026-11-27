#!/usr/bin/env bash
# Starts a Gemini (agy) worker in an existing herdr pane:
#   gemini-start.sh <agent-name> <pane-id> <worker|smart>
# The model comes from models.gemini_worker or models.gemini_smart (agy model ids, e.g. gemini-4-neon-medium).
# The worker runs in agy's Auto mode; no allow rule is shipped, so Auto's check sees every command
# that leaves the sandbox, including every orch-signal call. Never add command(orch-signal): the
# program it runs comes from the caller's environment (ORCH_LOADER_PYTHON, ORCH_PYTHON3, PATH).
case ${ZSH_EVAL_CONTEXT:-} in *:file*) echo "run it, do not source it" >&2; return 1 ;; esac
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
. "$root/watchers/coord-lib.sh"

[ $# -ge 3 ] || { echo "usage: gemini-start.sh <agent-name> <pane-id> <worker|smart>" >&2; exit 2; }
name=$1 pane=$2 tier=$3
shift 3
[ $# -eq 0 ] || { echo "gemini-start.sh: takes no extra arguments" >&2; exit 2; }
case "$tier" in worker | smart) ;; *) echo "gemini-start.sh: tier must be worker or smart, not '$tier'" >&2; exit 2 ;; esac

orch_load_config
setting=ORCH_GEMINI_WORKER_MODEL
[ "$tier" = worker ] || setting=ORCH_GEMINI_SMART_MODEL
# One Gemini model serves both tiers (same rule as Codex).
model=${!setting:-}
case "$model" in "" | "<"*">")
  other=ORCH_GEMINI_SMART_MODEL
  [ "$tier" = worker ] || other=ORCH_GEMINI_WORKER_MODEL
  model=${!other:-}
  ;;
esac
not_configured="gemini-start.sh: gemini is not configured; set models.gemini_worker or models.gemini_smart (ORCH_GEMINI_WORKER_MODEL, ORCH_GEMINI_SMART_MODEL) in $(orch_config_path)"
if ! orch_provider_enabled gemini; then
  [ -z "${ORCH_PROVIDERS:-}" ] || { echo "gemini-start.sh: gemini is off; add \"gemini\" to providers.enabled (ORCH_PROVIDERS) in $(orch_config_path)" >&2; exit 1; }
  echo "$not_configured" >&2
  exit 1
fi
case "$model" in "" | "<"*">") echo "$not_configured" >&2; exit 1 ;; esac

herdr_bin=${ORCH_HERDR_BIN:-herdr}
orch_need_cmd "$herdr_bin"

exec "$herdr_bin" agent start "$name" --kind agy --pane "$pane" -- --model "$model" --mode auto
