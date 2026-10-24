#!/usr/bin/env bash
# Starts a pi worker in an existing herdr pane: pi-start.sh <agent-name> <pane-id> <worker|smart|provider/model-id> [extra pi args]
# The model comes from models.pi_worker or models.pi_smart, or is exactly one entry of models.pi_models (provider/model-id). A DeepSeek provider starts only in a repo
# whose personal .orchestration.local.toml sets pi.deepseek = true, wherever "deepseek" appears in the provider or the model id;
# any other provider starts anywhere.
case ${ZSH_EVAL_CONTEXT:-} in *:file*) echo "run it, do not source it" >&2; return 1 ;; esac
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
. "$root/watchers/coord-lib.sh"

[ $# -ge 3 ] || { echo "usage: pi-start.sh <agent-name> <pane-id> <worker|smart|provider/model-id> [extra pi args]" >&2; exit 2; }
name=$1 pane=$2 tier=$3
shift 3
for arg in "$@"; do
  case "$(printf '%s' "$arg" | tr '[:upper:]' '[:lower:]')" in
    --provider | --provider=* | --model | --model=* | --models | --models=* | *deepseek* | -e | --extension | --extension=* | \
      --api-key | --api-key=* | --session | --session=* | --session-id | --session-id=* | --session-dir | --session-dir=* | \
      --fork | --fork=* | --resume | --continue | -r | -c | -a | --approve)
      echo "pi-start.sh: '$arg' is not allowed in extra args; the provider and model come from the pi settings" >&2
      exit 2
      ;;
  esac
done
orch_need_cmd jq

orch_load_config
case "$tier" in
  worker) setting=ORCH_PI_WORKER_MODEL model=${ORCH_PI_WORKER_MODEL:-} ;;
  smart) setting=ORCH_PI_SMART_MODEL model=${ORCH_PI_SMART_MODEL:-} ;;
  *)
    # A model id starts only when it is exactly one entry of models.pi_models.
    setting=ORCH_PI_MODELS model=
    # Plain string equality per entry: the argument is never a pattern.
    case "$tier" in
      *,* | *\** | *\?* | *\[* | *\]* | *[[:space:]]*) ;;
      *)
        IFS=, read -r -a pi_entries <<<"${ORCH_PI_MODELS:-}"
        for entry in ${pi_entries[@]+"${pi_entries[@]}"}; do
          [ "$entry" != "$tier" ] || model=$entry
        done
        ;;
    esac
    if [ -z "$model" ]; then
      echo "pi-start.sh: '$tier' is not worker, smart or one entry of models.pi_models (ORCH_PI_MODELS) in $(orch_config_path)" >&2
      exit 2
    fi
    ;;
esac
not_configured="pi-start.sh: pi is not configured; set models.pi_$tier ($setting) in $(orch_config_path)"
[ "$setting" != ORCH_PI_MODELS ] || not_configured="pi-start.sh: pi is not configured; set models.pi_models (ORCH_PI_MODELS) in $(orch_config_path)"
if ! orch_provider_enabled pi; then
  # An empty providers.enabled turns pi on only when a pi model is set.
  [ -z "${ORCH_PROVIDERS:-}" ] || { echo "pi-start.sh: pi is off; add \"pi\" to providers.enabled (ORCH_PROVIDERS) in $(orch_config_path)" >&2; exit 1; }
  echo "$not_configured" >&2
  exit 1
fi
case "$model" in "" | "<"*">") echo "$not_configured" >&2; exit 1 ;; esac
case "$model" in ?*/?*) ;; *) echo "pi-start.sh: $setting must be provider/model-id, for example lmstudio/qwen/qwen3.6-35b-a3b" >&2; exit 1 ;; esac
provider=${model%%/*}
pi_model=${model#*/}

herdr_bin=${ORCH_HERDR_BIN:-herdr}
orch_need_cmd "$herdr_bin"
cwd=$("$herdr_bin" pane get "$pane" | jq -er '.result.pane.cwd') || { echo "pi-start.sh: cannot read the directory of pane $pane" >&2; exit 1; }
# The opt-in comes from the repo files of the pane's own checkout: an exported ORCH_PI_DEEPSEEK or GIT_* variable must not grant or redirect it.
unset ORCH_PI_DEEPSEEK GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
orch_load_repo_config "$cwd"
case "$(printf '%s' "$model" | tr '[:upper:]' '[:lower:]')" in *deepseek*) named=1 ;; *) named=0 ;; esac
if [ "$named" = 1 ] && [ "${ORCH_PI_DEEPSEEK:-0}" != 1 ]; then
  echo "pi-start.sh: DeepSeek is off for $cwd. Only the user can opt a repo in (pi.deepseek in its .orchestration.local.toml). Ask the user; do not edit the file." >&2
  exit 1
fi

# --models with one entry leaves nothing for Ctrl+P to cycle to.
exec "$herdr_bin" agent start "$name" --kind pi --pane "$pane" -- --provider "$provider" --model "$pi_model" --models "$provider/$pi_model" "$@"
