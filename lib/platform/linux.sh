#!/usr/bin/env bash
platform_service_dir() { printf '%s/coordinator/watchers/service/linux' "$ORCH_REPO"; }
platform_service_suffix() { printf '%s' '.service.template'; }
platform_escape_service_value() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/$$/g' -e 's/%/%%/g'; }
platform_service_start_hint() { printf '%s' 'systemctl --user enable --now'; }

platform_migrate_legacy_watchers() { platform_finish_legacy_watcher_migration; }

platform_dependency_fix() {
  printf 'install %s with your distribution package manager (Debian/Ubuntu: sudo apt-get install %s)' "$1" "$1"
}

platform_shell_profile() { printf '%s' "$HOME/.profile"; }

# shellcheck disable=SC2016 # Printed for the user to run later.
platform_service_stop_hint() { printf 'systemctl --user disable --now %s' "$1"; }

platform_chrome_dir() { printf '%s' "$HOME/.config/google-chrome"; }
platform_gui_session() { [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ -z "${SSH_CONNECTION:-}${SSH_TTY:-}" ]; }

platform_copy_to_clipboard() {
  if [ -n "${WAYLAND_DISPLAY:-}" ] && command -v wl-copy >/dev/null 2>&1; then
    wl-copy
  elif [ -n "${DISPLAY:-}" ] && command -v xclip >/dev/null 2>&1; then
    xclip -selection clipboard >/dev/null 2>&1
  else
    return 1
  fi
}

platform_open_chrome_extensions() {
  local chrome
  chrome=$(command -v google-chrome || command -v google-chrome-stable) || return 1
  "$chrome" --version >/dev/null 2>&1 || return 1
  nohup "$chrome" chrome://extensions >/dev/null 2>&1 &
}

platform_load_unpacked_steps() { printf 'Developer mode on → Load unpacked → Ctrl+L → %s → Open' "$1"; }

# platform_run_detached <command...>: runs the command in a new session, waits for it, and returns its status.
platform_run_detached() { setsid --wait "$@" </dev/null; }

platform_user_units_dir() { printf '%s' "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"; }

# platform_remove_stale_services <current file names>: disables and deletes our user units that no template renders.
# A unit still active after it was disabled keeps its file and fails the call.
platform_remove_stale_services() {
  local file name status=0
  for file in "$(platform_user_units_dir)"/orch.coord-*; do
    [ -e "$file" ] || [ -L "$file" ] || continue
    name=${file##*/}
    case "$name" in *.service | *.timer) ;; *) continue ;; esac
    printf '%s\n' "$1" | grep -qxF "$name" && continue
    systemctl --user disable --now "$name" >/dev/null 2>&1 || true
    if systemctl --user is-active --quiet "$name" 2>/dev/null; then
      say "not removed" "$file (still active; run: systemctl --user disable --now $name)"
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

# platform_register_service <rendered unit> <changed 0|1>: enables the unit for login and starts it; a changed unit restarts.
# A unit without [Install] (started by its timer) is only linked.
# Status 2: already registered.
platform_register_service() {
  local file=$1 changed=${2:-0} name=${1##*/}
  if ! grep -q '^\[Install\]' "$file"; then
    local link
    link="$(platform_user_units_dir)/$name"
    [ ! -L "$link" ] || [ "$(readlink "$link")" = "$file" ] || rm -f "$link" || return 1
    [ "$changed" = 1 ] || [ ! -L "$link" ] || return 2
    [ -L "$link" ] || systemctl --user link "$file" || return 1
    systemctl --user daemon-reload
    return
  fi
  if [ "$changed" = 0 ] && systemctl --user is-enabled --quiet "$name" 2>/dev/null &&
    systemctl --user is-active --quiet "$name" 2>/dev/null; then
    return 2
  fi
  systemctl --user daemon-reload || return 1
  systemctl --user enable --now "$file" || return 1
  [ "$changed" = 0 ] || systemctl --user restart "$name"
}

# platform_restart_service <rendered unit>: reloads unit files and restarts the unit onto the current code and config.
platform_restart_service() { systemctl --user daemon-reload && systemctl --user restart "${1##*/}"; }
