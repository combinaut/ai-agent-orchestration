#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

load test_helper

# on_tty <answers> <args...>: runs the clone installer on a pseudo-terminal, typing one answer per prompt.
on_tty() {
  run env PTY_ANSWERS="$1" python3 "$ROOT/tests/pty_run.py" /bin/bash "$CLONE/install.sh" "${@:2}"
}

config_line() { printf '%s="%s"' "$1" "$(config_get "$1")"; }

foreign_config() {
  mkdir -p "$HOME/.config/herdr"
  cat >"$HOME/.config/herdr/config.toml" <<'EOF'
# my own settings
[keys]
prefix = "ctrl-a"

# >>> my-plugin block
[plugins.mine]
enabled = true
# <<< my-plugin block

[ui]
sidebar = "left"
EOF
  cp "$HOME/.config/herdr/config.toml" "$TMP/config.toml.orig"
}

@test "a replaced skill directory is backed up outside the skills directory" {
  fresh_install
  rm "$HOME/.claude/skills/architect"
  mkdir "$HOME/.claude/skills/architect"
  printf mine >"$HOME/.claude/skills/architect/SKILL.md"
  install_ok
  [ -L "$HOME/.claude/skills/architect" ]
  [ "$(find "$HOME/.claude/skills" -mindepth 1 -maxdepth 1 | wc -l)" -eq "$(find "$CLONE/skills" -mindepth 1 -maxdepth 1 -type d | wc -l)" ]
  for backup in "$HOME"/.claude/backups/*/skills/architect/SKILL.md; do [ "$(cat "$backup")" = mine ]; done
}

@test "skill backups from an earlier installer move out of the skills directory" {
  fresh_install
  mkdir "$HOME/.claude/skills/architect.bak.20260101000000" "$HOME/.claude/skills/architect.bak.20260101000000.1"
  mkdir "$HOME/.claude/skills/mine.bak.notes" "$HOME/.claude/skills/mine.bak.20260101000000.notes"
  install_ok
  contains "$output" 'moved backup'
  [ ! -e "$HOME/.claude/skills/architect.bak.20260101000000" ]
  [ ! -e "$HOME/.claude/skills/architect.bak.20260101000000.1" ]
  [ -d "$HOME/.claude/skills/mine.bak.notes" ]
  [ -d "$HOME/.claude/skills/mine.bak.20260101000000.notes" ]
  [ "$(find "$HOME"/.claude/backups/*/skills -mindepth 1 -maxdepth 1 -name 'architect.bak.*' | wc -l | tr -d ' ')" = 2 ]
}

@test "a symlinked skills directory is not scanned for old backups" {
  fresh_install
  mkdir -p "$TMP/shared-skills/mine.bak.20260101000000"
  mv "$HOME/.claude/skills" "$TMP/old-skills"
  ln -s "$TMP/shared-skills" "$HOME/.claude/skills"
  install_ok
  [ -d "$TMP/shared-skills/mine.bak.20260101000000" ]
}

@test "settings backup lands under the backups directory" {
  fresh_install
  printf '%s\n' '{"theme":"dark"}' >"$HOME/.claude/settings.json"
  install_ok
  for backup in "$HOME"/.claude/backups/*/settings.json; do [ "$(cat "$backup")" = '{"theme":"dark"}' ]; done
  [ -z "$(find "$HOME/.claude" -maxdepth 1 -name 'settings.json.bak*')" ]
}

@test "first run on a terminal asks for empty keys and writes the answers" {
  fresh_install
  mkdir -p "$HOME/.codex"
  printf 'model = "codex-fixture"\n' >"$HOME/.codex/config.toml"
  on_tty $'\n\nsmart-fixture\n\n-'
  [ "$status" -eq 0 ]
  contains "$output" 'ORCH_PR_AUTHOR (- leaves it empty) [octo-login]: '
  stub=$(cd "$BATS_SERVICE_STUB_BIN" && pwd -P)/herdr
  contains "$output" "ORCH_HERDR_BIN (- leaves it empty) [$stub]: "
  contains "$output" 'ORCH_HERDR_SOCKET (- leaves it empty) []: '
  [ "$(config_line ORCH_PR_AUTHOR)" = 'ORCH_PR_AUTHOR="octo-login"' ]
  [ "$(config_line ORCH_CODEX_WORKER_MODEL)" = 'ORCH_CODEX_WORKER_MODEL="codex-fixture"' ]
  [ "$(config_line ORCH_CODEX_SMART_MODEL)" = 'ORCH_CODEX_SMART_MODEL="smart-fixture"' ]
  [ "$(config_line ORCH_HERDR_BIN)" = "ORCH_HERDR_BIN=\"$stub\"" ]
  [ "$(config_line ORCH_HERDR_SOCKET)" = 'ORCH_HERDR_SOCKET=""' ]
}

@test "a dash empties a placeholder key" {
  fresh_install
  on_tty $'-\n-\n-\n-\n-'
  [ "$status" -eq 0 ]
  [ "$(config_line ORCH_CODEX_WORKER_MODEL)" = 'ORCH_CODEX_WORKER_MODEL=""' ]
  [ "$(config_line ORCH_CODEX_SMART_MODEL)" = 'ORCH_CODEX_SMART_MODEL=""' ]
}

@test "an answer replaces the placeholder line of its key" {
  fresh_install
  config_set ORCH_HERDR_BIN '<later-placeholder>'
  on_tty $'-\n-\n-\n/opt/herdr/bin/herdr\n-'
  [ "$status" -eq 0 ]
  [ "$(grep -c '^bin = ' "$HOME/.claude/orchestration.toml")" = 1 ]
  [ "$(config_get ORCH_HERDR_BIN)" = /opt/herdr/bin/herdr ]
}

@test "an answer wins over an empty exported value for the rest of the run" {
  fresh_install
  : >"$HOME/.claude/orchestration.toml"
  config_set ORCH_WATCHERS_SERVICE 1 ORCH_PR_AUTHOR a ORCH_CODEX_WORKER_MODEL w ORCH_CODEX_SMART_MODEL s ORCH_HERDR_BIN "" ORCH_HERDR_SOCKET ""
  ORCH_HERDR_BIN="" ORCH_HERDR_SOCKET="" LAUNCHCTL_NOT_LOADED=1 on_tty "$BIN/herdr"$'\n/tmp/herdr-fixture.sock\n'
  [ "$status" -eq "$(unloaded_status)" ]
  contains "$output" 'rendered'
  not_contains "$output" 'needs'
}

@test "an empty exported value does not hide a filled key" {
  fresh_install
  config_set ORCH_PR_AUTHOR mine
  ORCH_PR_AUTHOR="" on_tty $'-\n-\n-\n-\n-\n-'
  [ "$status" -eq 0 ]
  not_contains "$output" 'ORCH_PR_AUTHOR ('
  [ "$(config_line ORCH_PR_AUTHOR)" = 'ORCH_PR_AUTHOR="mine"' ]
}

@test "the notifier wrapper loads the config and the environment wins" {
  cfg="$TMP/alert.toml"
  printf '%s\n' '[alerts]' 'digest_s = 60' 'quiet_s = 1800' 'escalate_ratio = 3' >"$cfg"
  printf '#!/bin/sh\nenv | grep "^ORCH_ALERT_" | sort\necho "args: $*"\n' >"$TMP/fake-python"
  chmod +x "$TMP/fake-python"
  run env ORCH_CONFIG="$cfg" ORCH_PYTHON3="$TMP/fake-python" ORCH_ALERT_ESCALATE_RATIO=2 "$ROOT/coordinator/watchers/notify-inbox.sh" --deliver
  [ "$status" -eq 0 ]
  contains "$output" 'ORCH_ALERT_DIGEST_S=60'
  contains "$output" 'ORCH_ALERT_QUIET_S=1800'
  contains "$output" 'ORCH_ALERT_ESCALATE_RATIO=2'
  contains "$output" "args: $ROOT/coordinator/watchers/notify-inbox.py --deliver"
}

@test "rendered inbox services start the config-loading wrapper" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  install_ok
  for unit in "$HOME"/.claude/coordinator/watchers/service/*/orch.coord-watcher-inbox-context.*; do
    case "$unit" in *.template) continue ;; esac
    contains "$(cat "$unit")" "$HOME/.claude/coordinator/watchers/notify-inbox.sh"
    not_contains "$(cat "$unit")" 'notify-inbox.py'
  done
}

@test "an answer keeps the comments and other keys of the config" {
  fresh_install
  before=$(grep -c '^#' "$HOME/.claude/orchestration.toml")
  keys=$(python3 "$ROOT/lib/orch_settings.py" keys "$HOME/.claude/orchestration.toml")
  on_tty $'-\n-\n-\n/opt/herdr/bin/herdr\n-'
  [ "$status" -eq 0 ]
  [ "$(grep -c '^#' "$HOME/.claude/orchestration.toml")" = "$before" ]
  [ "$(python3 "$ROOT/lib/orch_settings.py" keys "$HOME/.claude/orchestration.toml")" = "$keys" ]
  [ "$(config_get ORCH_HERDR_BIN)" = /opt/herdr/bin/herdr ]
}

@test "printed service start commands run as printed when the path has a space" {
  use_home "$TMP/home with space"
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  LAUNCHCTL_NOT_LOADED=1 on_tty ''
  [ "$status" -eq "$(unloaded_status)" ]
  # shellcheck disable=SC1003 # A literal backslash in the expected text.
  contains "$output" 'home\ with\ space/.claude/coordinator/watchers/service/'
  line=$(printf '%s\n' "$output" | tr -d '\r' | sed -n 's/^rendered .*(start: \(.*\); stop: .*)$/\1/p' | head -n 1)
  eval "set -- $line"
  case "$1" in
    launchctl) [ "$#" -eq 4 ] ;;
    systemctl) [ "$#" -eq 5 ] ;;
    *) false ;;
  esac
}

@test "a Herdr found through a relative PATH entry is offered as an absolute path" {
  fresh_install
  hide_herdr
  mkdir -p "$TMP/rel-bin"
  printf '#!/bin/sh\necho "herdr 0.9.1"\n' >"$TMP/rel-bin/herdr"
  chmod +x "$TMP/rel-bin/herdr"
  cd "$TMP"
  PATH="rel-bin:$PATH" on_tty $'-\n-\n-\n\n-'
  [ "$status" -eq 0 ]
  contains "$output" "ORCH_HERDR_BIN (- leaves it empty) [$(cd "$TMP/rel-bin" && pwd -P)/herdr]: "
  [ "$(config_line ORCH_HERDR_BIN)" = "ORCH_HERDR_BIN=\"$(cd "$TMP/rel-bin" && pwd -P)/herdr\"" ]
}

@test "macOS migration commands quote paths with spaces" {
  [ "$(uname -s)" = Darwin ] || skip "only the macOS adapter prints migration commands"
  use_home "$TMP/home with space"
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  install_ok
  # shellcheck disable=SC1003 # A literal backslash in the expected text.
  contains "$output" 'home\ with\ space/.claude/ai-agent-orchestration/install.sh'
  printf '%s\n' "$output" | grep -E "^  launchctl bootstrap gui/$(id -u) [^ ]*home\\\\ with\\\\ space/\.claude/coordinator/watchers/service/macos/" >/dev/null
}

@test "the pty helper fails when it times out" {
  run env PTY_TIMEOUT=1 python3 "$ROOT/tests/pty_run.py" /bin/sleep 5
  [ "$status" -eq 124 ]
  contains "$output" 'pty_run: timeout'
}

@test "filled keys are never asked or overwritten" {
  fresh_install
  config_set ORCH_PR_AUTHOR mine-kept ORCH_CODEX_SMART_MODEL kept-model
  on_tty $'-\n-\n-'
  [ "$status" -eq 0 ]
  not_contains "$output" 'ORCH_PR_AUTHOR ('
  not_contains "$output" 'ORCH_CODEX_SMART_MODEL ('
  [ "$(config_line ORCH_PR_AUTHOR)" = 'ORCH_PR_AUTHOR="mine-kept"' ]
  [ "$(config_line ORCH_CODEX_SMART_MODEL)" = 'ORCH_CODEX_SMART_MODEL="kept-model"' ]
}

@test "answers with shell characters are written literally" {
  fresh_install
  # shellcheck disable=SC2016 # The literal value is the point.
  value='own"er/$HOME`x`\a'
  on_tty "$value"$'\n-\n-\n-\n-'
  [ "$status" -eq 0 ]
  [ "$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["pr"]["author"], end="")' "$HOME/.claude/orchestration.toml")" = "$value" ]
}

@test "a run without a terminal never asks and leaves the config alone" {
  fresh_install
  before=$(cat "$HOME/.claude/orchestration.toml")
  install_ok
  not_contains "$output" ']: '
  contains "$output" 'fill in'
  contains "$output" 'ORCH_HERDR_BIN ORCH_HERDR_SOCKET'
  [ "$(cat "$HOME/.claude/orchestration.toml")" = "$before" ]
}

@test "watcher services register for login, a re-run changes nothing and stale services go" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  use_service_state
  case "$(uname -s)" in
    Darwin)
      dir="$HOME/Library/LaunchAgents" suffix=plist calls=$LAUNCHCTL_CALLS state=$LAUNCHCTL_STATE PLATFORM_DIR=macos
      mkdir -p "$dir"
      printf stale >"$dir/orch.coord-watcher-gone.plist"
      printf stale >"$dir/orch.coord-watcher-gone.plist"
      ;;
    Linux)
      dir="$HOME/.config/systemd/user" suffix=service calls=$SYSTEMCTL_CALLS state=$SYSTEMCTL_STATE PLATFORM_DIR=linux
      mkdir -p "$dir"
      printf stale >"$dir/com.combinaut.coord-context-v2.service"
      printf stale >"$dir/orch.coord-watcher-gone.service"
      ;;
  esac
  printf 'other\n' >"$dir/com.example.other.$suffix"
  on_tty ''
  [ "$status" -eq 0 ]
  expected=$(find "$ROOT/coordinator/watchers/service/$PLATFORM_DIR" -name '*.template' | sed 's|.*/||; s|\.template$||' | LC_ALL=C sort)
  [ "$(printf '%s\n' "$expected" | wc -l)" -ge 8 ]
  case "$(uname -s)" in
    Darwin)
      [ "$(find "$dir" -name 'orch.coord-*' | sed 's|.*/||' | LC_ALL=C sort)" = "$expected" ]
      for name in $expected; do
        cmp -s "$dir/$name" "$HOME/.claude/coordinator/watchers/service/macos/$name"
      done
      ;;
    Linux)
      [ ! -e "$dir/com.combinaut.coord-context-v2.service" ] && [ ! -e "$dir/orch.coord-watcher-gone.service" ]
      contains "$(cat "$calls")" 'disable --now com.combinaut.coord-context-v2.service'
      ;;
  esac
  [ "$(LC_ALL=C sort "$state" | sed 's/\.service$//')" = "$(printf '%s\n' "$expected" | sed 's/\.[a-z]*$//')" ]
  [ -f "$dir/com.example.other.$suffix" ]
  before=$(cat "$calls")
  on_tty ''
  [ "$status" -eq 0 ]
  not_contains "$(sed "1,$(printf '%s\n' "$before" | wc -l)d" "$calls")" 'bootstrap'
  not_contains "$(sed "1,$(printf '%s\n' "$before" | wc -l)d" "$calls")" 'enable --now'
  not_contains "$output" 'registered      '
  not_contains "$output" 'restarted'
  [ "$(printf '%s\n' "$output" | tr -d '\r' | grep -c '(registered)$')" -eq "$(printf '%s\n' "$expected" | wc -l)" ]
  printf 'older-revision\n' >"$HOME/.claude/coordinator/watchers/.registered-revision"
  before=$(wc -l <"$calls")
  on_tty ''
  [ "$status" -eq 0 ]
  restarts=$(sed "1,${before}d" "$calls" | grep -E '^kickstart -k gui/[0-9]+/|^--user restart ' | sed 's|.*/||; s|^--user restart ||' | LC_ALL=C sort)
  [ "$restarts" = "$(printf '%s\n' "$expected" | sed 's/\.plist$//')" ]
  [ "$(printf '%s\n' "$output" | tr -d '\r' | grep -c '^restarted ')" -eq "$(printf '%s\n' "$expected" | wc -l)" ]
  [ "$(cut -d' ' -f1 "$HOME/.claude/coordinator/watchers/.registered-revision")" = "$(git -C "$HOME/.claude/ai-agent-orchestration" rev-parse HEAD)" ]
  config_set ORCH_ALERT_QUIET_S '900'
  on_tty ''
  [ "$status" -eq 0 ]
  [ "$(printf '%s\n' "$output" | tr -d '\r' | grep -c '^restarted ')" -eq "$(printf '%s\n' "$expected" | wc -l)" ]
}

@test "a watcher that fails to restart onto new code makes install exit non-zero" {
  fresh_install
  [ "$status" -eq 0 ]
  use_service_state
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  on_tty ''
  [ "$status" -eq 0 ]
  printf 'older-revision\n' >"$HOME/.claude/coordinator/watchers/.registered-revision"
  LAUNCHCTL_KICKSTART_FAIL=orch.coord-watcher-hog SYSTEMCTL_RESTART_FAIL=1 on_tty ''
  [ "$status" -eq 1 ]
  contains "$output" 'not restarted'
  contains "$output" 'Failed:'
  not_contains "$output" 'Done:'
}

@test "a failed registration keeps the revision so the next run retries" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  printf 'current\n' >"$HOME/.claude/coordinator/watchers/.registered-revision"
  LAUNCHCTL_NOT_LOADED=1 SYSTEMCTL_FAIL=1 on_tty ''
  [ "$status" -eq 1 ]
  contains "$output" 'not registered'
  [ ! -e "$HOME/.claude/coordinator/watchers/.registered-revision" ]
}

@test "a stale service that will not stop keeps its file and the revision" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  use_service_state
  case "$(uname -s)" in
    Darwin)
      stale="$HOME/Library/LaunchAgents/orch.coord-watcher-gone.plist"
      printf '%s\n' orch.coord-watcher-gone >"$LAUNCHCTL_STATE"
      use_stuck_service orch.coord-watcher-gone ""
      ;;
    Linux)
      stale="$HOME/.config/systemd/user/com.combinaut.coord-context-v2.service"
      printf '%s\n' com.combinaut.coord-context-v2.service >"$SYSTEMCTL_STATE"
      use_stuck_service "" com.combinaut.coord-context-v2.service
      ;;
  esac
  mkdir -p "$(dirname "$stale")"
  printf stale >"$stale"
  on_tty ''
  [ "$status" -eq 1 ]
  contains "$output" 'not removed'
  [ -f "$stale" ]
  [ ! -e "$HOME/.claude/coordinator/watchers/.registered-revision" ]
  contains "$output" 'watcher service registration (stop the services named above'
  not_contains "$(cat "$LAUNCHCTL_CALLS" "$SYSTEMCTL_CALLS" 2>/dev/null)" 'bootstrap gui'
  not_contains "$(cat "$LAUNCHCTL_CALLS" "$SYSTEMCTL_CALLS" 2>/dev/null)" 'enable --now'
}

@test "a stopped watcher starts again, a stuck one keeps its old copy, and turning services off removes them" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  use_service_state
  on_tty ''
  [ "$status" -eq 0 ]
  case "$(uname -s)" in
    Darwin)
      dir="$HOME/Library/LaunchAgents" calls=$LAUNCHCTL_CALLS
      grep -vxF orch.coord-watcher-host "$LAUNCHCTL_STATE" >"$TMP/state" && mv "$TMP/state" "$LAUNCHCTL_STATE"
      before=$(wc -l <"$calls")
      on_tty ''
      contains "$(sed "1,${before}d" "$calls")" "bootstrap gui/$(id -u) $dir/orch.coord-watcher-host.plist"
      printf 'old copy\n' >"$dir/orch.coord-watcher-hog.plist"
      LAUNCHCTL_STUCK=orch.coord-watcher-hog on_tty ''
      contains "$output" 'launchctl bootout gui/'"$(id -u)"'/orch.coord-watcher-hog, then run install.sh again'
      not_contains "$output" 'orch.coord-watcher-hog.plist (run: launchctl bootstrap'
      [ "$(cat "$dir/orch.coord-watcher-hog.plist")" = 'old copy' ]
      ;;
    Linux)
      dir="$HOME/.config/systemd/user" calls=$SYSTEMCTL_CALLS
      before=$(wc -l <"$calls")
      SYSTEMCTL_INACTIVE=orch.coord-watcher-host.service on_tty ''
      contains "$(sed "1,${before}d" "$calls")" "enable --now $HOME/.claude/coordinator/watchers/service/linux/orch.coord-watcher-host.service"
      mkdir -p "$dir"
      : >"$dir/orch.coord-watcher-host.service"
      ;;
  esac
  config_set ORCH_WATCHERS_SERVICE 0
  on_tty ''
  [ "$status" -eq 0 ]
  [ -z "$(find "$dir" -name 'orch.coord-watcher-*')" ]
}

@test "an update restores a missing current watcher registration" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  use_service_state
  on_tty ''
  [ "$status" -eq 0 ]
  case "$(uname -s)" in
    Darwin)
      dir="$HOME/Library/LaunchAgents"
      watcher=orch.coord-watcher-hog
      rm "$dir/$watcher.plist"
      grep -vxF "$watcher" "$LAUNCHCTL_STATE" >"$TMP/state" && mv "$TMP/state" "$LAUNCHCTL_STATE"
      before=$(wc -l <"$LAUNCHCTL_CALLS")
      on_tty ''
      [ "$status" -eq 0 ]
      [ -f "$dir/$watcher.plist" ]
      contains "$(sed "1,${before}d" "$LAUNCHCTL_CALLS")" "bootstrap gui/$(id -u) $dir/$watcher.plist"

      rm "$dir/$watcher.plist"
      before=$(wc -l <"$LAUNCHCTL_CALLS")
      LAUNCHCTL_STUCK="$watcher" on_tty ''
      [ "$status" -eq 1 ]
      [ -f "$dir/$watcher.plist" ]
      contains "$output" "not registered  $HOME/.claude/coordinator/watchers/service/macos/$watcher.plist (still loaded; run: launchctl bootout gui/$(id -u)/$watcher, then run install.sh again)"
      ;;
    Linux)
      watcher=orch.coord-watcher-hog.service
      grep -vxF "$watcher" "$SYSTEMCTL_STATE" >"$TMP/state" && mv "$TMP/state" "$SYSTEMCTL_STATE"
      before=$(wc -l <"$SYSTEMCTL_CALLS")
      on_tty ''
      [ "$status" -eq 0 ]
      grep -qxF "$watcher" "$SYSTEMCTL_STATE"
      contains "$(sed "1,${before}d" "$SYSTEMCTL_CALLS")" "enable --now $HOME/.claude/coordinator/watchers/service/linux/$watcher"
      ;;
  esac
}

@test "macOS registration persists a missing watcher before launchd confirms it" {
  rendered="$TMP/orch.coord-watcher-hog.plist"
  agents="$TMP/LaunchAgents"
  calls="$TMP/launchctl.calls"
  loaded=0
  stuck=0
  printf '<plist/>\n' >"$rendered"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/macos.sh"
  platform_launch_agents_dir() { printf '%s' "$agents"; }
  launchctl() {
    printf '%s\n' "$*" >>"$calls"
    case "$1" in
      print) [ "$loaded" = 1 ] ;;
      bootstrap) [ -f "$3" ] && loaded=1 ;;
      bootout) [ "$stuck" = 1 ] || loaded=0 ;;
    esac
  }

  platform_register_service "$rendered"
  [ -f "$agents/orch.coord-watcher-hog.plist" ]
  contains "$(cat "$calls")" "bootstrap gui/$(id -u) $agents/orch.coord-watcher-hog.plist"

  rm "$agents/orch.coord-watcher-hog.plist"
  : >"$calls"
  platform_register_service "$rendered"
  [ -f "$agents/orch.coord-watcher-hog.plist" ]
  [ "$(grep -c '^bootout ' "$calls")" -eq 1 ]
  [ "$(grep -c '^bootstrap ' "$calls")" -eq 1 ]
}

@test "macOS registration waits for a slow unload before it bootstraps" {
  rendered="$TMP/orch.coord-watcher-hog.plist"
  agents="$TMP/LaunchAgents"
  calls="$TMP/launchctl.calls"
  prints_left=0
  mkdir -p "$agents"
  printf '<plist new/>\n' >"$rendered"
  printf '<plist old/>\n' >"$agents/orch.coord-watcher-hog.plist"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/macos.sh"
  platform_launch_agents_dir() { printf '%s' "$agents"; }
  launchctl() {
    printf '%s\n' "$*" >>"$calls"
    case "$1" in
      print) if [ "$prints_left" -gt 0 ]; then prints_left=$((prints_left - 1)); return 0; fi; return 1 ;;
      bootout) prints_left=2 ;;
      bootstrap) [ "$prints_left" -eq 0 ] ;;
    esac
  }
  platform_register_service "$rendered"
  [ "$(cat "$agents/orch.coord-watcher-hog.plist")" = '<plist new/>' ]
  [ "$(grep -c '^bootstrap ' "$calls")" -eq 1 ]
}

@test "macOS registration retries a bootstrap that fails right after the unload" {
  rendered="$TMP/orch.coord-watcher-hog.plist"
  agents="$TMP/LaunchAgents"
  calls="$TMP/launchctl.calls"
  loaded=0
  failures=2
  printf '<plist/>\n' >"$rendered"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/macos.sh"
  platform_launch_agents_dir() { printf '%s' "$agents"; }
  launchctl() {
    printf '%s\n' "$*" >>"$calls"
    case "$1" in
      print) [ "$loaded" = 1 ] ;;
      bootstrap)
        if [ "$failures" -gt 0 ]; then failures=$((failures - 1)); return 5; fi
        loaded=1
        ;;
    esac
  }
  platform_register_service "$rendered"
  [ "$loaded" = 1 ]
  [ "$(grep -c '^bootstrap ' "$calls")" -eq 3 ]
}

@test "macOS registration accepts a failed bootstrap when launchd reports the job loaded" {
  rendered="$TMP/orch.coord-watcher-hog.plist"
  agents="$TMP/LaunchAgents"
  prints=0
  printf '<plist/>\n' >"$rendered"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/macos.sh"
  platform_launch_agents_dir() { printf '%s' "$agents"; }
  launchctl() {
    case "$1" in
      print) prints=$((prints + 1)); [ "$prints" -gt 2 ] ;;
      bootstrap) return 5 ;;
    esac
  }
  platform_register_service "$rendered"
}

@test "macOS registration starts a job that launchd loads without running" {
  rendered="$TMP/orch.coord-watcher-hog.plist"
  agents="$TMP/LaunchAgents"
  calls="$TMP/launchctl.calls"
  loaded=0
  running=0
  printf '<plist/>\n' >"$rendered"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/macos.sh"
  platform_launch_agents_dir() { printf '%s' "$agents"; }
  launchctl() {
    printf '%s\n' "$*" >>"$calls"
    case "$1" in
      print) [ "$loaded" = 1 ] ;;
      bootstrap) loaded=1 ;;
      kickstart) [ "$loaded" = 1 ] && running=1 ;;
    esac
  }
  platform_register_service "$rendered"
  [ "$running" = 1 ]
  [ "$(grep -n "^kickstart gui/$(id -u)/orch.coord-watcher-hog$" "$calls" | cut -d: -f1)" -gt "$(grep -n '^bootstrap ' "$calls" | cut -d: -f1)" ]
}

@test "macOS registration unloads a job it cannot start and tells the user to run install.sh again" {
  rendered="$TMP/orch.coord-watcher-hog.plist"
  agents="$TMP/LaunchAgents"
  loaded=0
  printf '<plist/>\n' >"$rendered"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/macos.sh"
  say() { printf '%-15s %s\n' "$1" "$2"; }
  platform_launch_agents_dir() { printf '%s' "$agents"; }
  launchctl() {
    case "$1" in
      print) [ "$loaded" = 1 ] ;;
      bootstrap) loaded=1 ;;
      kickstart) return 1 ;;
      bootout) loaded=0 ;;
    esac
  }
  status=0
  platform_register_service "$rendered" >"$TMP/register.out" || status=$?
  [ "$status" -eq 3 ]
  [ "$loaded" = 0 ]
  [ -f "$agents/orch.coord-watcher-hog.plist" ]
  grep -qF "did not start and was unloaded; run install.sh again" "$TMP/register.out"
  [ "$(grep -cF "launchctl bootstrap" "$TMP/register.out")" -eq 0 ]
}

@test "macOS registration waits for a job it cannot start to unload before it reports it" {
  rendered="$TMP/orch.coord-watcher-hog.plist"
  agents="$TMP/LaunchAgents"
  loaded=0
  lingering=0
  printf '<plist/>\n' >"$rendered"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/macos.sh"
  say() { :; }
  platform_launch_agents_dir() { printf '%s' "$agents"; }
  launchctl() {
    case "$1" in
      print)
        [ "$loaded" = 1 ] && return 0
        [ "$lingering" -gt 0 ] || return 1
        lingering=$((lingering - 1))
        ;;
      bootstrap) loaded=1 ;;
      kickstart) return 1 ;;
      bootout)
        loaded=0
        lingering=3
        ;;
    esac
  }
  status=0
  ORCH_LAUNCHD_WAIT_SLEEP=0 ORCH_LAUNCHD_WAIT_TRIES=5 platform_register_service "$rendered" >/dev/null || status=$?
  [ "$status" -eq 3 ]
  [ "$lingering" -eq 0 ]
}

@test "macOS registration tells the user to boot out a job that never unloads" {
  rendered="$TMP/orch.coord-watcher-hog.plist"
  agents="$TMP/LaunchAgents"
  loaded=0
  stuck=0
  printf '<plist/>\n' >"$rendered"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/macos.sh"
  say() { printf '%-15s %s\n' "$1" "$2"; }
  platform_launch_agents_dir() { printf '%s' "$agents"; }
  launchctl() {
    case "$1" in
      print) [ "$loaded" = 1 ] || [ "$stuck" = 1 ] ;;
      bootstrap) loaded=1 ;;
      kickstart)
        stuck=1
        return 1
        ;;
      bootout) loaded=0 ;;
    esac
  }
  status=0
  ORCH_LAUNCHD_WAIT_SLEEP=0 ORCH_LAUNCHD_WAIT_TRIES=3 platform_register_service "$rendered" >"$TMP/register.out" || status=$?
  [ "$status" -eq 3 ]
  grep -qF "launchctl bootout gui/$(id -u)/orch.coord-watcher-hog" "$TMP/register.out"
  grep -qF "then run install.sh again" "$TMP/register.out"
}

@test "Linux registration turns a restart exit code of 3 into status 1" {
  unit="$TMP/orch.coord-watcher-hog.service"
  printf '[Service]\n[Install]\nWantedBy=default.target\n' >"$unit"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/linux.sh"
  systemctl() {
    case "$2" in
      restart) return 3 ;;
    esac
  }
  status=0
  platform_register_service "$unit" 1 || status=$?
  [ "$status" -eq 1 ]
}

@test "Linux registration turns a daemon-reload exit code of 3 into status 1 for a unit without [Install]" {
  unit="$TMP/orch.coord-watcher-hog.service"
  printf '[Service]\n' >"$unit"
  # shellcheck disable=SC1090
  source "$ROOT/lib/platform/linux.sh"
  platform_user_units_dir() { printf '%s' "$TMP/units"; }
  systemctl() {
    case "$2" in
      daemon-reload) return 3 ;;
    esac
  }
  status=0
  platform_register_service "$unit" 1 || status=$?
  [ "$status" -eq 1 ]
}

@test "install fails and prints the bootout command for a job that will not start or unload" {
  [ "$(uname -s)" = Darwin ] || skip "only the macOS adapter starts jobs with kickstart"
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  use_service_state
  ORCH_LAUNCHD_WAIT_SLEEP=0 ORCH_LAUNCHD_WAIT_TRIES=2 LAUNCHCTL_KICKSTART_FAIL=orch.coord-watcher-hog LAUNCHCTL_STUCK=orch.coord-watcher-hog on_tty ''
  [ "$status" -eq 1 ]
  contains "$output" 'launchctl bootout gui/'"$(id -u)"'/orch.coord-watcher-hog, then run install.sh again'
  not_contains "$output" 'orch.coord-watcher-hog.plist (run: launchctl bootstrap'
  not_contains "$output" 'Done:'
  [ ! -e "$HOME/.claude/coordinator/watchers/.registered-revision" ]
}

@test "install exits non-zero and says so when a watcher service is not registered" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  LAUNCHCTL_NOT_LOADED=1 SYSTEMCTL_FAIL=1 on_tty ''
  [ "$status" -ne 0 ]
  contains "$output" 'not registered'
  contains "$output" 'Failed:'
  not_contains "$output" 'Done:'
}

@test "a service folder that cannot be written reports the failure and bootstraps nothing" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  use_service_state
  case "$(uname -s)" in
    Darwin) dir="$HOME/Library/LaunchAgents" stale=orch.coord-watcher-gone.plist ;;
    Linux) dir="$HOME/.config/systemd/user" stale=orch.coord-watcher-gone.service ;;
  esac
  mkdir -p "$dir"
  printf stale >"$dir/$stale"
  chmod 555 "$dir"
  on_tty ''
  chmod 755 "$dir"
  [ "$status" -eq 1 ]
  contains "$output" "not removed     $dir/$stale"
  [ -f "$dir/$stale" ]
  [ "$(uname -s)" != Darwin ] || not_contains "$(cat "$LAUNCHCTL_CALLS")" bootstrap
  [ ! -e "$HOME/.claude/coordinator/watchers/.registered-revision" ]
}

@test "turning services off with a stale service that will not stop drops the revision" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  use_service_state
  on_tty ''
  [ "$status" -eq 0 ]
  [ -e "$HOME/.claude/coordinator/watchers/.registered-revision" ]
  case "$(uname -s)" in
    Darwin)
      stale="$HOME/Library/LaunchAgents/orch.coord-watcher-gone.plist"
      printf '%s\n' orch.coord-watcher-gone >>"$LAUNCHCTL_STATE"
      use_stuck_service orch.coord-watcher-gone ""
      ;;
    Linux)
      stale="$HOME/.config/systemd/user/orch.coord-watcher-gone.service"
      printf '%s\n' orch.coord-watcher-gone.service >>"$SYSTEMCTL_STATE"
      use_stuck_service "" orch.coord-watcher-gone.service
      ;;
  esac
  mkdir -p "$(dirname "$stale")"
  printf stale >"$stale"
  config_set ORCH_WATCHERS_SERVICE 0
  on_tty ''
  [ "$status" -eq 1 ]
  contains "$output" 'not removed'
  [ ! -e "$HOME/.claude/coordinator/watchers/.registered-revision" ]
}

@test "failed stale cleanup with no rendered services makes install fail" {
  fresh_install
  [ "$status" -eq 0 ]
  use_service_state
  case "$(uname -s)" in
    Darwin)
      stale="$HOME/Library/LaunchAgents/orch.coord-watcher-gone.plist"
      printf '%s\n' orch.coord-watcher-gone >"$LAUNCHCTL_STATE"
      use_stuck_service orch.coord-watcher-gone ""
      ;;
    Linux)
      stale="$HOME/.config/systemd/user/orch.coord-watcher-gone.service"
      printf '%s\n' orch.coord-watcher-gone.service >"$SYSTEMCTL_STATE"
      use_stuck_service "" orch.coord-watcher-gone.service
      ;;
  esac
  mkdir -p "$(dirname "$stale")"
  printf stale >"$stale"
  printf 'old revision\n' >"$HOME/.claude/coordinator/watchers/.registered-revision"
  on_tty ''
  [ "$status" -eq 1 ]
  contains "$output" 'not removed'
  contains "$output" 'Failed:'
  not_contains "$output" 'Done:'
  [ -f "$stale" ]
  [ ! -e "$HOME/.claude/coordinator/watchers/.registered-revision" ]
}

@test "services are not offered while macOS legacy jobs are still loaded" {
  [ "$(uname -s)" = Darwin ] || skip "only the macOS adapter defers the migration"
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  rm -f "$HOME/.claude/coordinator/watchers/.migration-state-copied"
  on_tty 'y'
  [ "$status" -eq 0 ]
  contains "$output" 'MIGRATE WATCHERS'
  contains "$output" 'watcher service registration (finish the migration above, then run install.sh again)'
  not_contains "$(cat "$LAUNCHCTL_CALLS")" bootstrap
}

@test "legacy watcher state is copied before services are offered" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  rm -f "$HOME/.claude/coordinator/watchers/.migration-state-copied"
  mkdir -p "$HOME/.claude/coordinator/v2/prod-context"
  printf '{"legacy":true}' >"$HOME/.claude/coordinator/v2/prod-context/host.json"
  LAUNCHCTL_NOT_LOADED=1 on_tty 'y'
  [ "$status" -eq "$(unloaded_status)" ]
  copied=$(printf '%s\n' "$output" | grep -n 'copied state' | head -n 1 | cut -d: -f1)
  started=$(printf '%s\n' "$output" | grep -n -E '^(registered|not registered) ' | head -n 1 | cut -d: -f1)
  [ -n "$copied" ] && [ -n "$started" ] && [ "$copied" -lt "$started" ]
  [ "$(cat "$HOME/.claude/coordinator/watchers/prod-context/host.json")" = '{"legacy":true}' ]
}

@test "Radar daemon started by the installer survives the installer's exit" {
  fresh_install
  run python3 -c 'import os, sys
os.setpgrp()
open(sys.argv[1], "w").write(str(os.getpgrp()))
os.execvp(sys.argv[2], sys.argv[2:])' "$TMP/install.pgid" /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  [ -s "$TMP/daemon.pid" ]
  kill -HUP -- "-$(cat "$TMP/install.pgid")" 2>/dev/null || true
  kill -TERM -- "-$(cat "$TMP/install.pgid")" 2>/dev/null || true
  sleep 1
  kill -0 "$(cat "$TMP/daemon.pid")"
}

@test "a Radar restart starts the new daemon only after the old one has exited" {
  fresh_install
  install_ok --env herdr
  old=$(cat "$TMP/daemon.pid")
  git -C "$HOME/.claude/herdr-radar" checkout -q main
  run env HERDR_STOP_DELAY=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  new=$(cat "$TMP/daemon.pid")
  [ "$new" != "$old" ]
  kill -0 "$new"
  sleep 2
  kill -0 "$new"
}

@test "a checkout path with regex characters is matched literally" {
  fresh_install
  checkout="$TMP/projects/radar(work)[dev]"
  run env ORCH_RADAR_PATH="$checkout" /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  kill -0 "$(cat "$TMP/daemon.pid")"
}

@test "a restart ignores another session's daemon from the same checkout" {
  fresh_install
  install_ok --env herdr
  (sleep 60 >/dev/null 2>&1 &
    echo $! >"$TMP/other.pid")
  git -C "$HOME/.claude/herdr-radar" checkout -q main
  run env ORCH_RADAR_WAIT_TRIES=10 ORCH_RADAR_WAIT_SLEEP=0.2 /bin/bash "$CLONE/install.sh" --env herdr
  other=$(cat "$TMP/other.pid")
  alive=0
  kill -0 "$other" 2>/dev/null && alive=1
  kill "$other" 2>/dev/null || true
  [ "$status" -eq 0 ]
  [ "$alive" -eq 1 ]
  kill -0 "$(cat "$TMP/daemon.pid")"
}

@test "another session's daemon does not satisfy the check when this session starts none" {
  fresh_install
  install_ok --env herdr
  mv "$TMP/daemon.pid" "$TMP/other.pid"
  run env HERDR_NO_DAEMON=1 ORCH_RADAR_WAIT_TRIES=2 ORCH_RADAR_WAIT_SLEEP=0 /bin/bash "$CLONE/install.sh" --env herdr
  kill "$(cat "$TMP/other.pid")" 2>/dev/null || true
  [ "$status" -ne 0 ]
  contains "$output" 'Radar daemon is not running after state-start'
}

@test "a first install does not wait for a daemon that was never there" {
  fresh_install
  run env ORCH_RADAR_WAIT_TRIES=3 ORCH_RADAR_WAIT_SLEEP=0 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  not_contains "$output" 'did not exit'
}

@test "a replacement daemon started during the stop satisfies the check" {
  fresh_install
  install_ok --env herdr
  git -C "$HOME/.claude/herdr-radar" checkout -q main
  run env HERDR_STOP_REPLACES=1 HERDR_STOP_DELAY=1 ORCH_RADAR_WAIT_TRIES=6 ORCH_RADAR_WAIT_SLEEP=0.5 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  kill -0 "$(cat "$TMP/daemon.pid")"
}

@test "an install whose Radar daemon never appears fails instead of reporting it running" {
  fresh_install
  run env HERDR_NO_DAEMON=1 ORCH_RADAR_WAIT_TRIES=2 ORCH_RADAR_WAIT_SLEEP=0 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'Radar daemon is not running after state-start'
  [ "$(grep -c 'state-start' "$HERDR_CALLS")" -eq 2 ]
}

@test "existing Herdr keeps its install and its config outside Radar's blocks" {
  fresh_install
  foreign_config
  printf upstream >"$HERDR_PLUGIN"
  run env ORCH_HERDR_TODO_POPUP=0 HERDR_RADAR_BLOCK=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  contains "$output" 'already installed (0.9.1'
  [ ! -e "$CURL_CALLS" ] || not_contains "$(cat "$CURL_CALLS")" https://herdr.dev/install.sh
  [ ! -e "$HOME/.local/bin/herdr" ]
  contains "$(cat "$HERDR_CALLS")" 'plugin uninstall hhdebb.herdr-radar'
  [ "$(cat "$HERDR_PLUGIN")" = "$(cd "$HOME/.claude" && pwd -P)/herdr-radar-runtime" ]
  contains "$output" 'your blocks unchanged'
  contains "$(cat "$HOME/.config/herdr/config.toml")" '# >>> herdr-radar theme block'
  sed '/^# >>> herdr-radar .* block$/,/^# <<< herdr-radar .* block$/d' "$HOME/.config/herdr/config.toml" >"$TMP/config.toml.outside"
  cmp "$TMP/config.toml.orig" "$TMP/config.toml.outside"
}

@test "a change after an unmatched Radar marker is reported" {
  fresh_install
  mkdir -p "$HOME/.config/herdr"
  printf '%s\n' '[keys]' 'prefix = "ctrl-a"' '# >>> herdr-radar theme block' '[ui]' 'sidebar = "left"' >"$HOME/.config/herdr/config.toml"
  run env HERDR_EDIT_TAIL=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  contains "$output" 'WARNING'
}

@test "a filled config value wins over an empty exported one for the rest of the run" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's'
  ORCH_HERDR_BIN="" LAUNCHCTL_NOT_LOADED=1 on_tty ''
  [ "$status" -eq "$(unloaded_status)" ]
  not_contains "$output" 'ORCH_HERDR_BIN ('
  contains "$output" 'rendered'
}

@test "a failed Radar start still checks the config and keeps the previous file" {
  fresh_install
  foreign_config
  run env HERDR_CLOBBER_CONFIG=1 HERDR_FAIL_START=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'WARNING'
  contains "$output" 'Radar state-start failed'
  for backup in "$HOME"/.claude/backups/*/home/.config/herdr/config.toml; do cmp "$TMP/config.toml.orig" "$backup"; done
}

@test "a config change outside Radar's blocks is reported with the previous file kept" {
  fresh_install
  foreign_config
  run env HERDR_CLOBBER_CONFIG=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  contains "$output" 'WARNING'
  for backup in "$HOME"/.claude/backups/*/home/.config/herdr/config.toml; do cmp "$TMP/config.toml.orig" "$backup"; done
}

@test "setup answers a key written as a dotted top-level key" {
  fresh_install
  printf '%s\n' 'pr.since = ""' 'pr.author = ""' '[models]' 'codex_worker = "w"' 'codex_smart = "s"' \
    '[herdr]' 'bin = "/opt/herdr"' 'socket = "/tmp/sock"' >"$HOME/.claude/orchestration.toml"
  on_tty $'owner-a\n'
  [ "$status" -eq 0 ]
  [ "$(config_get ORCH_PR_AUTHOR)" = owner-a ]
}

@test "with codex off the installer neither asks for nor lists the codex models" {
  fresh_install
  contains "$output" 'ORCH_CODEX_WORKER_MODEL'
  config_set ORCH_PROVIDERS claude
  install_ok
  not_contains "$output" 'ORCH_CODEX_WORKER_MODEL'
  not_contains "$output" 'ORCH_CODEX_SMART_MODEL'
  on_tty $'\n\n-'
  [ "$status" -eq 0 ]
  not_contains "$output" 'ORCH_CODEX_WORKER_MODEL ('
  not_contains "$output" 'ORCH_CODEX_SMART_MODEL ('
  [ "$(config_line ORCH_CODEX_WORKER_MODEL)" = 'ORCH_CODEX_WORKER_MODEL="<codex-worker-model>"' ]
}

@test "a providers list without claude stops the installer with the settings error" {
  fresh_install
  ORCH_PROVIDERS=codex install_fails
  contains "$output" 'providers.enabled (ORCH_PROVIDERS): must include "claude"'
}
