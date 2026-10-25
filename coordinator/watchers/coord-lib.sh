#!/usr/bin/env bash
# Shared configuration for watcher entrypoints. Sourced, never run.

ORCH_SETTINGS="ORCH_PR_AUTHOR ORCH_PR_SINCE ORCH_BOT_REVIEWER_LOGIN ORCH_PACKS_MAX ORCH_WORKTREES_MAX ORCH_AUDIT_PAGE_PRS ORCH_AUDIT_PAGE_NODES \
ORCH_LEGACY_SCANS_MAX ORCH_LEGACY_READS_MAX ORCH_LEGACY_MAP_REPO ORCH_HEAVY_SLOTS_MAX ORCH_HEAVY_SLOTS_DIR ORCH_HEAVY_SLOTS_KILL_GRACE \
ORCH_PROVIDERS ORCH_CLAUDE_TIERS ORCH_COORDINATOR_MODEL ORCH_ARCHITECT_MODEL ORCH_CODEX_WORKER_MODEL ORCH_CODEX_SMART_MODEL ORCH_PI_WORKER_MODEL ORCH_PI_SMART_MODEL ORCH_REMOTE_CONTROL_ARCHITECTS ORCH_RECYCLE_TOKENS ORCH_WORKER_SOFT_TOKENS ORCH_WORKER_HARD_TOKENS ORCH_WORKER_ASK_TIMEOUT_S ORCH_RECYCLE_PCT ORCH_PARK_IDLE ORCH_REPO_PATH ORCH_ENV ORCH_RADAR_PATH \
ORCH_WATCHERS_SERVICE ORCH_PYTHON3 ORCH_WATCHERS_PR_SCOPE ORCH_WATCHERS_WORKER_MODEL ORCH_WATCHERS_PRIMARY_REPO ORCH_PATH ORCH_CLOSED_DIR ORCH_HERDR_BIN ORCH_HERDR_SOCKET ORCH_HERDR_TODO_POPUP \
ORCH_USER_TODO_STATUS_DIR ORCH_USER_TODO_OUTPUT ORCH_USER_TODO_STATE ORCH_USER_QUESTIONS_DIR ORCH_HOST_PRESSURE_MIN_PCT ORCH_HOST_SWAP_MAX_MB ORCH_HOST_PROCS_MAX ORCH_HOST_FILES_MAX ORCH_HOST_LOAD_MAX \
ORCH_ALERT_ESCALATE_RATIO ORCH_ALERT_DIGEST_S ORCH_ALERT_QUIET_S ORCH_HOG_GB ORCH_HOG_GIT_GB ORCH_HOG_GIT_AGE_S ORCH_ASK_TIMEOUT_S ORCH_WAIT_S ORCH_WAIT_REPEAT_S ORCH_WORKER_IDLE_S ORCH_PR_TABS_ORG ORCH_PR_TABS_DEFAULT_REPO ORCH_PR_TABS_REPOS ORCH_PR_TABS_OPEN_ALL ORCH_PR_TABS_TERMINAL_APP \
ORCH_BIG_REPO ORCH_MERGE_GATE_CHECKS ORCH_REVIEW_FINALIZE_COMMANDS ORCH_REVIEW_FINALIZE_COMMENT ORCH_LEGACY_SCAN ORCH_LEGACY_RULES ORCH_LEGACY_EXEMPT ORCH_FOCUS_PATTERN ORCH_FOCUS_ARCHITECT ORCH_PI_DEEPSEEK"

orch_config_path() { printf '%s' "${ORCH_CONFIG:-$HOME/.claude/orchestration.toml}"; }

orch_die() {
  echo "ERROR: $*" >&2
  exit 2
}

orch_settings_module() {
  local self=${BASH_SOURCE[0]}
  [ ! -L "$self" ] || self=$(readlink "$self")
  printf '%s/../../lib/orch_settings.py' "$(cd "$(dirname "$self")" && pwd -P)"
}

# orch_python: a Python 3.11 or newer for the TOML reader (ORCH_PYTHON3 from the environment, python3, Homebrew).
orch_python() {
  local candidate tried=""
  if [ -n "${ORCH_LOADER_PYTHON:-}" ]; then
    printf '%s' "$ORCH_LOADER_PYTHON"
    return 0
  fi
  for candidate in ${ORCH_PYTHON3:+"$ORCH_PYTHON3"} python3 ${ORCH_PYTHON_FALLBACKS-/opt/homebrew/bin/python3 /usr/local/bin/python3} python3.14 python3.13 python3.12 python3.11; do
    tried="$tried $candidate"
    command -v "$candidate" >/dev/null 2>&1 || continue
    if [ "$("$candidate" -c 'import sys; print(sys.version_info >= (3, 11))' 2>/dev/null)" = True ]; then
      ORCH_LOADER_PYTHON=$(command -v "$candidate")
      printf '%s' "$ORCH_LOADER_PYTHON"
      return 0
    fi
  done
  echo "ERROR: reading the TOML config needs Python 3.11 or newer; none found (tried:$tried). Install one (macOS: brew install python; Linux: your package manager) or export ORCH_PYTHON3=/path/to/python3.11+." >&2
  return 2
}

# orch_watcher_python: ORCH_PYTHON3 when it is 3.11 or newer, else the loader's Python; sets ORCH_WATCHER_PYTHON.
orch_watcher_python() {
  ORCH_WATCHER_PYTHON=${ORCH_PYTHON3:-python3}
  [ "$("$ORCH_WATCHER_PYTHON" -c 'import sys; print(sys.version_info >= (3, 11))' 2>/dev/null)" != True ] || return 0
  orch_python >/dev/null || exit 2
  ORCH_WATCHER_PYTHON=$ORCH_LOADER_PYTHON
}

orch_set_default() {
  [ -n "${!1+x}" ] || printf -v "$1" '%s' "$2"
}

# orch_read_settings <global <file> | repo <dir>>: sets each setting the reader prints and the environment leaves unset.
orch_read_settings() {
  local out
  orch_python >/dev/null || exit 2
  out=$("$ORCH_LOADER_PYTHON" -I "$(orch_settings_module)" "$@") || exit 2
  eval "$out"
}

# Loads the global config file; a variable already set in the environment wins over the file.
orch_load_config() {
  local cfg
  cfg=$(orch_config_path)
  if [ -n "${ORCH_CONFIG:-}" ] && [ ! -f "$cfg" ]; then
    orch_die "ORCH_CONFIG is not a regular file: $cfg"
  fi
  if [ ! -f "$cfg" ]; then
    [ -n "${ORCH_CONFIG:-}" ] || [ ! -f "$HOME/.claude/orchestration.env" ] ||
      orch_die "$cfg is missing and $HOME/.claude/orchestration.env is not read any more. Run install.sh to convert it."
    return 0
  fi
  orch_read_settings global "$cfg"
}

# orch_provider_enabled <name>: true when providers.enabled turns the provider on; a settings error exits 2.
orch_provider_enabled() {
  local enabled
  orch_python >/dev/null || exit 2
  # shellcheck disable=SC2086 # One name per word.
  enabled=$(
    for name in $ORCH_SETTINGS; do [ -z "${!name+x}" ] || export "${name?}"; done
    "$ORCH_LOADER_PYTHON" -I "$(orch_settings_module)" providers
  ) || exit 2
  case ",$enabled," in *",$1,"*) return 0 ;; esac
  return 1
}

# orch_load_repo_config <dir>: repo settings of the checkout containing <dir>; built-in defaults outside a checkout.
orch_load_repo_config() {
  orch_read_settings repo "${1:-.}"
}

orch_require() {
  local name missing=""
  for name in "$@"; do
    [ -n "${!name:-}" ] || missing="$missing $name"
  done
  [ -z "$missing" ] || orch_die "required setting(s) not set:$missing. Set them in $(orch_config_path) or export them."
}

orch_need_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || orch_die "required command not found on PATH: $c"
  done
}

orch_export_config() {
  local name
  for name in $ORCH_SETTINGS; do export "${name?}"; done
}
