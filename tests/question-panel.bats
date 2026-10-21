#!/usr/bin/env bats

load test_helper

setup() {
  setup_fixture
  mkdir -p "$HOME/.claude/coordinator/questions"
  : >"$TMP/none.toml"
  cat >"$BIN/herdr-question" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TMP/question-herdr.calls"
case "$1 $2" in
  'pane list') printf '%s\n' "${HERDR_PANES:?}" ;;
  'pane split') printf '%s\n' '{"result":{"pane":{"pane_id":"question-pane"}}}' ;;
esac
EOF
  chmod +x "$BIN/herdr-question"
  export ORCH_CLOSED_DIR="$TMP/closed"
  python_bin=$(command -v python3)
  export HERDR_BIN_PATH="$BIN/herdr-question" ORCH_CONFIG="$TMP/none.toml" ORCH_PYTHON3="$python_bin"
}

@test "ask-user-question stacks below the to-do pane and does not focus it" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"todo-pane","tokens":{"todo":"1"}}]}}' "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  grep -qx 'pane split todo-pane --direction down --no-focus' "$TMP/question-herdr.calls"
  grep -q '^pane run question-pane ' "$TMP/question-herdr.calls"
  grep -qx 'pane rename question-pane Q: release' "$TMP/question-herdr.calls"
}

@test "ask-user-question closes its pane through close-pane and removes answer state" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  printf '1\n' >"${question%.md}.answer.seen"
  touch "${question%.md}.answer.processing" "${question%.md}.claim.answer.processing"
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"question-pane","terminal_title_stripped":"Q: release"}]}}' "$ROOT/ask-user-question" close release
  [ "$status" -eq 0 ]
  [ -f "$ORCH_CLOSED_DIR/question-pane" ]
  grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
  [ ! -e "$question" ]
  [ ! -e "${question%.md}.answer.seen" ]
  [ ! -e "${question%.md}.answer.processing" ]
  [ ! -e "${question%.md}.claim.answer.processing" ]
}

@test "ask-user-question rejects a missing question file" {
  run env HERDR_PANES='{"result":{"panes":[]}}' "$ROOT/ask-user-question" release "$HOME/.claude/coordinator/questions/missing.md"
  [ "$status" -eq 66 ]
}

@test "ask-user-question rejects unsafe close slugs without deleting siblings" {
  sibling="$HOME/.claude/coordinator/victim.md"
  printf keep >"$sibling"
  run env HERDR_PANES='{"result":{"panes":[]}}' "$ROOT/ask-user-question" close ../victim
  [ "$status" -eq 64 ]
  [ "$(cat "$sibling")" = keep ]
}

@test "close requires the exact question title" {
  export HERDR_PANES='{"result":{"panes":[{"pane_id":"other","terminal_title_stripped":"Q: release-notes"}]}}'
  run "$ROOT/ask-user-question" close release
  [ "$status" -eq 0 ]
  run grep -q 'pane close' "$TMP/question-herdr.calls"
  [ "$status" -eq 1 ]
}

@test "open rejects invalid slugs before opening a pane" {
  question="$HOME/.claude/coordinator/questions/Bad.md"
  touch "$question"
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"todo","tokens":{"todo":"1"}}]}}' "$ROOT/ask-user-question" Bad "$question"
  [ "$status" -eq 64 ]
}

@test "open rejects a missing canonical question file" {
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"todo","tokens":{"todo":"1"}}]}}' "$ROOT/ask-user-question" release "$HOME/.claude/coordinator/questions/release.md"
  [ "$status" -eq 66 ]
}

@test "open rejects a duplicate exact question pane" {
  question="$HOME/.claude/coordinator/questions/release.md"
  touch "$question"
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"existing","title":"Q: release"}]}}' "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 73 ]
}

@test "open stacks below the last question pane" {
  question="$HOME/.claude/coordinator/questions/release.md"
  touch "$question"
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"todo","tokens":{"todo":"1"}},{"pane_id":"first","title":"Q: first"},{"pane_id":"last","title":"Q: last"}]}}' "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  grep -qx 'pane split last --direction down --no-focus' "$TMP/question-herdr.calls"
}

@test "open fails when there is no anchor" {
  question="$HOME/.claude/coordinator/questions/release.md"
  touch "$question"
  run env HERDR_PANES='{"result":{"panes":[]}}' "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 69 ]
}

@test "open and close reject surplus arguments" {
  question="$HOME/.claude/coordinator/questions/release.md"
  touch "$question"
  local panes='{"result":{"panes":[{"pane_id":"todo","tokens":{"todo":"1"}}]}}'
  run env HERDR_PANES="$panes" "$ROOT/ask-user-question" release "$question" extra
  [ "$status" -eq 64 ]
  run env HERDR_PANES="$panes" "$ROOT/ask-user-question" close release extra
  [ "$status" -eq 64 ]
}
