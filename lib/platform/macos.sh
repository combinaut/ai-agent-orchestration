#!/usr/bin/env bash
platform_service_dir() { printf '%s/coordinator/watchers/service/macos' "$ORCH_REPO"; }
platform_service_suffix() { printf '%s' '.plist.template'; }
platform_escape_service_value() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g'; }
# shellcheck disable=SC2016 # Printed for the user to run later.
platform_service_start_hint() { printf '%s' 'launchctl bootstrap gui/$(id -u)'; }

platform_migrate_legacy_watchers() {
  local old_label new_label new_file uid loaded_old="" loaded_new="" missing=0

  uid=$(id -u)
  while IFS=' ' read -r old_label new_label; do
    new_file="$CLAUDE_DIR/coordinator/watchers/service/macos/$new_label.plist"
    launchctl print "gui/$uid/$old_label" >/dev/null 2>&1 || continue
    loaded_old="$loaded_old$old_label\n"
    if [ ! -f "$new_file" ]; then
      missing=1
      continue
    fi
    loaded_new="$loaded_new$new_file\n"
  done <<JOBS
${LEGACY_LABEL_PREFIX}context-v2 orch.coord-watcher-context
${LEGACY_LABEL_PREFIX}inbox-v2-context orch.coord-watcher-inbox-context
JOBS
  if [ -n "$loaded_old" ]; then
    # shellcheck disable=SC2034 # Read by install.sh.
    WATCHER_MIGRATION_PENDING=1
    [ "$missing" = 0 ] || { say "migrate" "set ORCH_WATCHERS_SERVICE=1 and rerun install.sh"; return 0; }
    printf '%s\n' "MIGRATE WATCHERS: old services stay broken until these commands run:"
    printf '%b' "$loaded_old" | while IFS= read -r old_label; do
      [ -z "$old_label" ] || printf '%s\n' "  launchctl bootout gui/$uid/$old_label"
    done
    printf '  %q\n' "$ORCH_REPO/install.sh"
    printf '%b' "$loaded_new" | while IFS= read -r new_file; do
      [ -z "$new_file" ] || printf '  launchctl bootstrap gui/%s %q\n' "$uid" "$new_file"
    done
    printf '%s\n' "  (These commands are not run by install.sh.)"
    return 0
  fi
  platform_finish_legacy_watcher_migration
}

platform_dependency_fix() {
  case "$1" in
    git) printf '%s' 'xcode-select --install' ;;
    *) printf 'brew install %s' "$1" ;;
  esac
}

platform_shell_profile() { printf '%s' "$HOME/.zprofile"; }

# shellcheck disable=SC2016 # Printed for the user to run later.
platform_service_stop_hint() { printf 'launchctl bootout gui/$(id -u)/%s' "$1"; }

platform_chrome_dir() { printf '%s' "$HOME/Library/Application Support/Google/Chrome"; }
platform_gui_session() { [ -z "${SSH_CONNECTION:-}${SSH_TTY:-}" ]; }
platform_copy_to_clipboard() { command -v pbcopy >/dev/null 2>&1 && pbcopy; }
platform_open_chrome_extensions() { open -a "Google Chrome" "chrome://extensions" >/dev/null 2>&1; }
platform_load_unpacked_steps() { printf 'Developer mode on → Load unpacked → ⌘⇧G → %s → Open' "$1"; }

# platform_run_detached <command...>: runs the command in a new session, waits for it, and returns its status.
platform_run_detached() {
  python3 -c 'import os, sys
if os.getpgrp() == os.getpid():
    pid = os.fork()
    if pid:
        sys.exit(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]))
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])' "$@" </dev/null
}

platform_launch_agents_dir() { printf '%s' "$HOME/Library/LaunchAgents"; }

# platform_wait_unloaded <uid> <label>: bootout returns before the job has exited; print keeps answering until it is gone.
# Status 1: still loaded after the bounded wait.
platform_wait_unloaded() {
  local tries=${ORCH_LAUNCHD_WAIT_TRIES:-40}
  while [ "$tries" -gt 0 ] && launchctl print "gui/$1/$2" >/dev/null 2>&1; do
    sleep "${ORCH_LAUNCHD_WAIT_SLEEP:-0.5}"
    tries=$((tries - 1))
  done
  ! launchctl print "gui/$1/$2" >/dev/null 2>&1
}

# platform_remove_stale_services <current file names>: boots out and deletes our LaunchAgents, old and new names, that no template renders.
# A job still loaded after its bootout keeps its file and fails the call.
platform_remove_stale_services() {
  local file name uid status=0
  uid=$(id -u)
  for file in "$(platform_launch_agents_dir)"/orch.coord-*.plist "$(platform_launch_agents_dir)/${LEGACY_LABEL_PREFIX:?}"*.plist; do
    [ -e "$file" ] || [ -L "$file" ] || continue
    name=${file##*/}
    printf '%s\n' "$1" | grep -qxF "$name" && continue
    launchctl bootout "gui/$uid/${name%.plist}" >/dev/null 2>&1 || true
    if ! platform_wait_unloaded "$uid" "${name%.plist}"; then
      say "not removed" "$file (still loaded; run: launchctl bootout gui/$uid/${name%.plist})"
      status=1
      continue
    fi
    if rm -f "$file"; then
      say "removed" "$file"
    else
      say "not removed" "$file"
      status=1
    fi
  done
  return "$status"
}

# platform_copy_service <rendered plist> <LaunchAgents destination>: atomically installs a login copy.
platform_copy_service() {
  local file=$1 dest=$2 tmp
  mkdir -p "$(dirname "$dest")" || return 1
  tmp=$(mktemp "$dest.XXXXXX") || return 1
  if ! { cp "$file" "$tmp" && chmod 644 "$tmp" && mv "$tmp" "$dest"; }; then
    rm -f "$tmp"
    return 1
  fi
}

# platform_register_service <rendered plist>: launchd loads ~/Library/LaunchAgents at login, so the plist is copied there
# and bootstrapped when the copy changed or the job is not loaded. Status 2: already registered.
platform_register_service() {
  local file=$1 uid label dest missing_copy=0
  uid=$(id -u)
  label=$(basename "$file" .plist)
  dest="$(platform_launch_agents_dir)/$label.plist"
  [ -e "$dest" ] || [ -L "$dest" ] || missing_copy=1
  if [ "$missing_copy" = 1 ]; then
    platform_copy_service "$file" "$dest" || return 1
  elif [ ! -L "$dest" ] && cmp -s "$file" "$dest"; then
    launchctl print "gui/$uid/$label" >/dev/null 2>&1 && return 2
  fi
  launchctl bootout "gui/$uid/$label" >/dev/null 2>&1 || true
  if ! platform_wait_unloaded "$uid" "$label"; then
    # An existing installed copy is replaced only after its old job unloads.
    return 1
  fi
  if [ "$missing_copy" = 0 ]; then
    platform_copy_service "$file" "$dest" || return 1
  fi
  # bootstrap can fail with an I/O error while launchd finishes the unload; a loaded job counts as registered.
  for _ in 1 2 3 4 5; do
    launchctl bootstrap "gui/$uid" "$dest" && return 0
    launchctl print "gui/$uid/$label" >/dev/null 2>&1 && return 0
    sleep "${ORCH_LAUNCHD_WAIT_SLEEP:-0.5}"
  done
  return 1
}

# platform_restart_service <rendered plist>: restarts the loaded job onto the current code.
platform_restart_service() { launchctl kickstart -k "gui/$(id -u)/$(basename "$1" .plist)"; }
