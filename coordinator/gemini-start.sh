#!/usr/bin/env bash
# Starts a Gemini (agy) worker in an existing herdr pane:
#   gemini-start.sh <agent-name> <pane-id> <worker|smart>
# It adds the pane's folder to trustedWorkspaces in agy's settings, so agy shows no trust prompt.
# The model comes from models.gemini_worker or models.gemini_smart (agy model ids, e.g. gemini-4-neon-medium).
# The worker runs in agy's Auto mode. --settings adds read_file/write_file sandbox paths only
# (agy-sandbox-settings.py); no command rule is shipped, so Auto's check sees every command that
# leaves the sandbox, including every orch-signal call. Never add command(orch-signal): the
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

plugin="$HOME/.gemini/config/plugins/ai-agent-orchestration"
for f in plugin.json rules/AGENTS.md rules/orch-agy-sandbox.md; do
  [ -e "$plugin/$f" ] || { echo "gemini-start.sh: $plugin/$f is missing or dangling, so agy would start without the orch skills and your rules; run install.sh" >&2; exit 1; }
done

herdr_bin=${ORCH_HERDR_BIN:-herdr}
orch_need_cmd "$herdr_bin"
orch_need_cmd jq
cwd=$("$herdr_bin" pane get "$pane" | jq -er '.result.pane.cwd') || { echo "gemini-start.sh: cannot read the directory of pane $pane" >&2; exit 1; }
case "$cwd" in /*) ;; *) echo "gemini-start.sh: pane $pane has no absolute directory: '$cwd'" >&2; exit 1 ;; esac

# agy asks for trust unless trustedWorkspaces holds the folder's exact path.
"$ORCH_LOADER_PYTHON" -I - "$HOME/.gemini/antigravity-cli/settings.json" "$cwd" <<'PY'
import fcntl, json, os, stat, sys, tempfile

shown = sys.argv[1]
path, folder = os.path.realpath(shown), os.path.realpath(sys.argv[2])


def refuse(why):
    sys.exit(f"gemini-start.sh: {shown} {why}; fix it, then start again")


def trust():
    try:
        with open(path, "rb") as stream:
            mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
            raw = stream.read()
    except FileNotFoundError:
        mode, raw = 0o600, b"{}"
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        refuse("is not valid JSON")
    if not isinstance(data, dict):
        refuse("is not a JSON object")
    trusted = data.setdefault("trustedWorkspaces", [])
    if not isinstance(trusted, list):
        refuse("has a trustedWorkspaces that is not a list")
    if folder in trusted:
        return
    trusted.append(folder)
    fd, temp = tempfile.mkstemp(prefix=".settings.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


try:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    # Serializes concurrent starts; agy does not take this lock.
    with open(path + ".orch-lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        trust()
except OSError as error:
    sys.exit(f"gemini-start.sh: cannot write {shown}: {error}")
PY

reports="$HOME/.claude/worker-reports"
slots=${ORCH_HEAVY_SLOTS_DIR:-"$HOME/.claude/heavy-slots"}
mkdir -p "$reports" "$slots"
python=$(orch_python) || exit 2
sandbox=$("$python" -I "$root/agy-sandbox-settings.py" --cwd "$cwd" \
  --read "$(orch_config_path)" \
  --write "$reports" --write "$slots") || { echo "gemini-start.sh: could not build the agy sandbox settings" >&2; exit 1; }

exec "$herdr_bin" agent start "$name" --kind agy --pane "$pane" -- --model "$model" --mode auto --settings "$sandbox"
