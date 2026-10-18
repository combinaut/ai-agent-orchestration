#!/usr/bin/env bash
# Shared configuration for watcher entrypoints. Sourced, never run.

ORCH_SETTINGS="ORCH_BIG_REPO_REMOTES ORCH_PR_TABS_ORG ORCH_PR_TABS_DEFAULT_REPO ORCH_PR_TABS_OPEN_ALL ORCH_PR_TABS_TOPICS ORCH_PR_REPOS ORCH_PR_AUTHOR ORCH_PR_SINCE ORCH_MERGE_GATE_CHECKS ORCH_BOT_REVIEWER_LOGIN \
ORCH_GIT_WATCH_REPOS ORCH_PACKS_MAX ORCH_WORKTREES_MAX ORCH_AUDIT_PAGE_PRS ORCH_AUDIT_PAGE_NODES ORCH_LEGACY_REPOS ORCH_LEGACY_SCANS_MAX ORCH_LEGACY_READS_MAX ORCH_RECYCLE_PCT ORCH_RECYCLE_TOKENS ORCH_PARK_IDLE \
ORCH_HOST_PRESSURE_MIN_PCT ORCH_HOST_SWAP_MAX_MB ORCH_HOST_PROCS_MAX ORCH_HOST_FILES_MAX ORCH_HOST_LOAD_MAX \
ORCH_HOG_GB ORCH_HOG_GIT_GB ORCH_HOG_GIT_AGE_S ORCH_HEAVY_SLOTS_MAX ORCH_ALERT_ESCALATE_RATIO ORCH_ALERT_DIGEST_S ORCH_ALERT_QUIET_S"
ORCH_SETTINGS="$ORCH_SETTINGS ORCH_PYTHON3 ORCH_WATCHERS_PR_SCOPE ORCH_WATCHERS_WORKER_MODEL ORCH_WATCHERS_PRIMARY_REPO ORCH_USER_TODO_STATUS_DIR ORCH_USER_TODO_OUTPUT ORCH_USER_TODO_STATE ORCH_HERDR_BIN ORCH_HERDR_SOCKET ORCH_PATH ORCH_HEAVY_SLOTS_DIR ORCH_FOCUS_SKIP_ARCHITECTS ORCH_FOCUS_PATTERN ORCH_FOCUS_ARCHITECT ORCH_LEGACY_EXTRA_PATTERN ORCH_LEGACY_EXEMPT_PATHS"

orch_config_path() { printf '%s' "${ORCH_CONFIG:-$HOME/.claude/orchestration.env}"; }

orch_die() {
  echo "ERROR: $*" >&2
  exit 2
}

# Loads the config file; a variable already set in the environment wins over the file.
orch_load_config() {
  local cfg name keep=""
  cfg=$(orch_config_path)
  if [ -n "${ORCH_CONFIG:-}" ] && [ ! -f "$cfg" ]; then
    orch_die "ORCH_CONFIG is not a regular file: $cfg"
  fi
  for name in $ORCH_SETTINGS; do
    if [ -n "${!name+x}" ]; then
      keep="$keep $name"
      eval "__orch_keep_$name=\${$name}"
    fi
  done
  if [ -f "$cfg" ]; then
    # shellcheck disable=SC1090
    . "$cfg"
  fi
  for name in $keep; do
    eval "$name=\$__orch_keep_$name"
  done
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
