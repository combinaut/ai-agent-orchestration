#!/usr/bin/env bats

load test_helper

setup() {
  setup_fixture
  : >"$TMP/none.toml"
  : >"$TMP/panes.json"
  cat >"$BIN/herdr-todo" <<'EOF2'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TMP/todo-herdr.calls"
[ "${HERDR_FAIL:-}" != "$2" ] || exit 1
case "$1 $2" in
  'pane list') printf '{"result":{"panes":[%s]}}\n' "$(cat "$TMP/panes.json")" ;;
  'pane split') printf '%s\n' '{"result":{"pane":{"pane_id":"todo-new"}}}' ;;
  'pane rename') printf '{"pane_id":"%s","label":"%s"}' "$3" "$4" >"$TMP/panes.json" ;;
esac
EOF2
  chmod +x "$BIN/herdr-todo"
  export ORCH_CLOSED_DIR="$TMP/closed"
  python_bin=$(command -v python3)
  export HERDR_BIN_PATH="$BIN/herdr-todo" ORCH_CONFIG="$TMP/none.toml" ORCH_PYTHON3="$python_bin"
  export HERDR_PANE_ID=coord-pane
  PANE="$ROOT/coordinator/todo-pane.sh"
}

teardown() { teardown_fixture; }

calls() { cat "$TMP/todo-herdr.calls" 2>/dev/null || true; }
splits() { calls | grep -c '^pane split ' || true; }

@test "opens the to-do pane beside the Coordinator without focus when none exists" {
  run "$PANE"
  [ "$status" -eq 0 ]
  contains "$(calls)" 'pane split coord-pane --direction right --no-focus'
  contains "$(calls)" "pane run todo-new "
  contains "$(calls)" "$ROOT/coordinator/user-todo-view.py"
  [[ "$(calls)" != *--popup* ]]
  contains "$(calls)" 'pane rename todo-new To-do'
  not_contains "$(calls)" 'pane focus'
}

@test "the pane command carries a configured to-do output file" {
  printf '[user_todo]\noutput = "/data/todo out.md"\n' >"$TMP/todo.toml"
  run env ORCH_CONFIG="$TMP/todo.toml" "$PANE"
  [ "$status" -eq 0 ]
  contains "$(calls)" 'env ORCH_USER_TODO_OUTPUT=/data/todo\ out.md '
}

@test "reuses a pane already labelled To-do and opens nothing" {
  printf '{"pane_id":"existing","label":"To-do"}' >"$TMP/panes.json"
  run "$PANE"
  [ "$status" -eq 0 ]
  [ "$(splits)" -eq 0 ]
  not_contains "$(calls)" 'pane run'
}

@test "a second call after the first opens no duplicate" {
  run "$PANE"
  [ "$status" -eq 0 ]
  run "$PANE"
  [ "$status" -eq 0 ]
  [ "$(splits)" -eq 1 ]
}

@test "a Coordinator successor in another pane keeps the existing pane" {
  printf '{"pane_id":"old-todo","label":"To-do","terminal_title_stripped":"python3 user-todo-view.py"}' >"$TMP/panes.json"
  run env HERDR_PANE_ID=successor-pane "$PANE"
  [ "$status" -eq 0 ]
  [ "$(splits)" -eq 0 ]
}

@test "only the exact label counts: To-do list and Q: To-do open a new pane" {
  printf '{"pane_id":"a","label":"To-do list"},{"pane_id":"b","label":"Q: To-do"},{"pane_id":"c","title":"To-do"}' >"$TMP/panes.json"
  run "$PANE"
  [ "$status" -eq 0 ]
  [ "$(splits)" -eq 1 ]
}

@test "without a Coordinator pane id nothing is opened" {
  run env -u HERDR_PANE_ID "$PANE"
  [ "$status" -eq 69 ]
  contains "$output" 'HERDR_PANE_ID'
  [ "$(splits)" -eq 0 ]
}

@test "a failed pane run or rename closes the new pane" {
  for failure in run rename; do
    rm -f "$TMP/todo-herdr.calls" "$ORCH_CLOSED_DIR/todo-new"
    run env HERDR_FAIL="$failure" "$PANE"
    [ "$status" -ne 0 ]
    contains "$(calls)" 'pane close todo-new'
    [ -f "$ORCH_CLOSED_DIR/todo-new" ]
  done
}

@test "a failed split reports the failure and runs nothing" {
  run env HERDR_FAIL=split "$PANE"
  [ "$status" -ne 0 ]
  not_contains "$(calls)" 'pane run'
}

# shellcheck disable=SC2088 # The tilde is matched literally.
@test "the Coordinator startup steps run the to-do pane script" {
  grep -qF '~/.claude/coordinator/todo-pane.sh' "$ROOT/skills/coordinate/references/startup.md"
}
