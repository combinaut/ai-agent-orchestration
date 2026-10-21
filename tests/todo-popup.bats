#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

load test_helper

setup() {
  setup_fixture
  HCONF="$HOME/.config/herdr/config.toml"
  mkdir -p "$(dirname "$HCONF")"
  printf '[ui]\nsidebar_width = 40\n' >"$HCONF"
  START='# >>> ai-agent-orchestration to-do key'
}

teardown() { teardown_fixture; }

popup_install() { run env "$@" /bin/bash "$CLONE/install.sh" --env herdr; [ "$status" -eq 0 ]; }

@test "popup off by default: no plugin link, no key block" {
  fresh_install
  install_ok --env herdr
  not_contains "$(cat "$HERDR_CALLS")" 'plugin link '"$CLONE/env/herdr/todo-plugin"
  [ ! -f "$HERDR_TODO_PLUGIN" ]
  not_contains "$(cat "$HCONF")" "$START"
}

@test "popup on: links the plugin and writes the prefix+t block" {
  fresh_install
  popup_install ORCH_HERDR_TODO_POPUP=1
  [ "$(cat "$HERDR_TODO_PLUGIN")" = "$(cd "$CLONE" && pwd -P)/env/herdr/todo-plugin" ]
  contains "$(cat "$HCONF")" "$START"
  contains "$(cat "$HCONF")" 'command = "combinaut.todo.open"'
  contains "$(cat "$HCONF")" 'key = "prefix+t"'
  contains "$(cat "$HERDR_CALLS")" 'server reload-config'
  head -n 2 "$HCONF" | grep -qx '\[ui\]'
}

@test "popup on: second install changes nothing" {
  fresh_install
  popup_install ORCH_HERDR_TODO_POPUP=1
  cp "$HCONF" "$TMP/after-first"
  : >"$HERDR_CALLS"
  popup_install ORCH_HERDR_TODO_POPUP=1
  cmp "$HCONF" "$TMP/after-first"
  [ "$(grep -c "$START" "$HCONF")" -eq 1 ]
  not_contains "$(cat "$HERDR_CALLS")" 'plugin link '"$CLONE/env/herdr/todo-plugin"
  not_contains "$(cat "$HERDR_CALLS")" 'server reload-config'
  contains "$output" 'already linked'
}

@test "popup switched off again removes the plugin and the block" {
  fresh_install
  popup_install ORCH_HERDR_TODO_POPUP=1
  popup_install ORCH_HERDR_TODO_POPUP=0
  [ ! -f "$HERDR_TODO_PLUGIN" ]
  contains "$(cat "$HERDR_CALLS")" 'plugin unlink combinaut.todo'
  not_contains "$(cat "$HCONF")" "$START"
  [ "$(cat "$HCONF")" = "$(printf '[ui]\nsidebar_width = 40')" ]
}

@test "popup block is written through a symlinked config and survives other edits" {
  fresh_install
  mkdir -p "$TMP/dropbox"
  mv "$HCONF" "$TMP/dropbox/config.toml"
  ln -s "$TMP/dropbox/config.toml" "$HCONF"
  popup_install ORCH_HERDR_TODO_POPUP=1
  [ -L "$HCONF" ]
  contains "$(cat "$TMP/dropbox/config.toml")" "$START"
  printf '[theme]\nname = "x"\n' >>"$TMP/dropbox/config.toml"
  popup_install ORCH_HERDR_TODO_POPUP=1
  contains "$(cat "$TMP/dropbox/config.toml")" 'name = "x"'
  [ "$(grep -c "$START" "$TMP/dropbox/config.toml")" -eq 1 ]
}

@test "popup setting in the config file turns it on" {
  fresh_install
  install_ok --env herdr
  python3 -I "$CLONE/lib/orch_settings.py" set "$HOME/.claude/orchestration.toml" ORCH_HERDR_TODO_POPUP 1
  install_ok --env herdr
  contains "$(cat "$HCONF")" "$START"
}

@test "an unpaired start marker refuses to rewrite and leaves the file untouched" {
  fresh_install
  printf '[ui]\nsidebar_width = 40\n\n%s (managed by install.sh; edits inside are replaced)\n[[keys.command]]\nkey = "prefix+t"\n\n# >>> herdr-radar tab-bar block\nkeep = 1\n# <<< herdr-radar tab-bar block\n[theme]\nname = "x"\n' "$START" >"$HCONF"
  cp "$HCONF" "$TMP/before"
  for enabled in 1 0; do
    run env ORCH_HERDR_TODO_POPUP=$enabled /bin/bash "$CLONE/install.sh" --env herdr
    [ "$status" -ne 0 ]
    contains "$output" 'unpaired'
    cmp "$HCONF" "$TMP/before"
  done
  [ ! -f "$HERDR_TODO_PLUGIN" ]
}

@test "a failing temporary file leaves the config unchanged" {
  fresh_install
  cp "$HCONF" "$TMP/before"
  run env ORCH_HERDR_TODO_POPUP=1 MKTEMP_FAIL_TODO=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'cannot create a temporary file'
  cmp "$HCONF" "$TMP/before"
}

@test "an end marker before the start marker refuses to rewrite" {
  fresh_install
  printf '[ui]\nsidebar_width = 40\n# <<< ai-agent-orchestration to-do key\n[theme]\nname = "x"\n%s (managed by install.sh; edits inside are replaced)\n' "$START" >"$HCONF"
  cp "$HCONF" "$TMP/before"
  run env ORCH_HERDR_TODO_POPUP=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  cmp "$HCONF" "$TMP/before"
}
