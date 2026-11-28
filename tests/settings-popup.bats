#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

load test_helper

setup() {
  setup_fixture
  HCONF="$HOME/.config/herdr/config.toml"
  mkdir -p "$(dirname "$HCONF")"
  printf '[ui]\nsidebar_width = 40\n' >"$HCONF"
  START='# >>> ai-agent-orchestration settings key'
  TODO_START='# >>> ai-agent-orchestration to-do key'
}

teardown() { teardown_fixture; }

popups_install() { run env "$@" /bin/bash "$CLONE/install.sh" --env herdr; [ "$status" -eq 0 ]; }
count() { grep -c "$1" "$HCONF" || true; }

@test "settings popup on by default: links the plugin and binds prefix+shift+s" {
  fresh_install
  install_ok --env herdr
  [ "$(cat "$HERDR_SETTINGS_PLUGIN")" = "$(cd "$CLONE" && pwd -P)/env/herdr/settings-plugin" ]
  [ "$(count "$START")" -eq 1 ]
  contains "$(cat "$HCONF")" 'key = "prefix+shift+s"'
  contains "$(cat "$HCONF")" 'command = "combinaut.settings.open"'
}

@test "settings popup switched off removes only its plugin and block" {
  fresh_install
  popups_install ORCH_HERDR_SETTINGS_POPUP=1
  popups_install ORCH_HERDR_SETTINGS_POPUP=0
  [ ! -f "$HERDR_SETTINGS_PLUGIN" ]
  contains "$(cat "$HERDR_CALLS")" 'plugin unlink combinaut.settings'
  not_contains "$(cat "$HCONF")" "$START"
  not_contains "$(cat "$HCONF")" 'prefix+shift+s'
  [ -f "$HERDR_TODO_PLUGIN" ]
  [ "$(count "$TODO_START")" -eq 1 ]
}

@test "settings popup false in the config file is never installed" {
  fresh_install
  python3 -I "$CLONE/lib/orch_settings.py" set "$HOME/.claude/orchestration.toml" ORCH_HERDR_SETTINGS_POPUP 0
  install_ok --env herdr
  [ ! -f "$HERDR_SETTINGS_PLUGIN" ]
  not_contains "$(cat "$HCONF")" "$START"
}

@test "the to-do popup switches off without touching the settings popup" {
  fresh_install
  popups_install ORCH_HERDR_TODO_POPUP=1
  popups_install ORCH_HERDR_TODO_POPUP=0
  [ ! -f "$HERDR_TODO_PLUGIN" ]
  [ -f "$HERDR_SETTINGS_PLUGIN" ]
  [ "$(count "$START")" -eq 1 ]
  not_contains "$(cat "$HCONF")" "$TODO_START"
}

@test "a second install with both popups changes nothing" {
  fresh_install
  popups_install ORCH_HERDR_SETTINGS_POPUP=1
  cp "$HCONF" "$TMP/after-first"
  : >"$HERDR_CALLS"
  popups_install ORCH_HERDR_SETTINGS_POPUP=1
  cmp "$HCONF" "$TMP/after-first"
  not_contains "$(cat "$HERDR_CALLS")" 'server reload-config'
  not_contains "$(cat "$HERDR_CALLS")" 'plugin link '"$(cd "$CLONE" && pwd -P)/env/herdr/settings-plugin"
}

@test "an edit inside the settings block is replaced" {
  fresh_install
  popups_install ORCH_HERDR_SETTINGS_POPUP=1
  sed 's/prefix+shift+s/prefix+y/' "$HCONF" >"$TMP/edited" && cp "$TMP/edited" "$HCONF"
  popups_install ORCH_HERDR_SETTINGS_POPUP=1
  not_contains "$(cat "$HCONF")" 'prefix+y'
  [ "$(count 'prefix+shift+s')" -eq 1 ]
}

@test "an unpaired settings marker stops the install before either popup changes" {
  fresh_install
  printf '[ui]\n%s (managed by install.sh; edits inside are replaced)\n[theme]\n' "$START" >"$HCONF"
  cp "$HCONF" "$TMP/before"
  run /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'unpaired'
  cmp "$HCONF" "$TMP/before"
  [ ! -f "$HERDR_SETTINGS_PLUGIN" ]
  [ ! -f "$HERDR_TODO_PLUGIN" ]
}

@test "a failed settings plugin link stops the install without its key block" {
  fresh_install
  run env HERDR_LINK_FAIL=settings-plugin /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  not_contains "$output" 'Herdr plugin combinaut.settings ->'
  not_contains "$(cat "$HCONF")" "$START"
}

@test "Herdr reloads the first popup key when the second popup step fails" {
  fresh_install
  : >"$HERDR_CALLS"
  run env HERDR_LINK_FAIL=settings-plugin /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  [ "$(count "$TODO_START")" -eq 1 ]
  contains "$(cat "$HERDR_CALLS")" 'server reload-config'
}
