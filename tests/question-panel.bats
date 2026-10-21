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
  export HERDR_BIN_PATH="$BIN/herdr-question" ORCH_CONFIG="$TMP/none.toml" ORCH_PYTHON3="$(command -v python3)"
}

@test "ask-user-question stacks below the to-do pane and does not focus it" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  export HERDR_PANES='{"result":{"panes":[{"pane_id":"todo-pane","tokens":{"todo":"1"}}]}}'
  run "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  grep -qx 'pane split todo-pane --direction down --no-focus' "$TMP/question-herdr.calls"
  grep -q '^pane run question-pane ' "$TMP/question-herdr.calls"
  grep -qx 'pane rename question-pane Q: release' "$TMP/question-herdr.calls"
}

@test "ask-user-question closes its pane through close-pane and removes answer state" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  printf '1\n' >"${question%.md}.answer.seen"
  export HERDR_PANES='{"result":{"panes":[{"pane_id":"question-pane","terminal_title_stripped":"Q: release"}]}}'
  run "$ROOT/ask-user-question" close release
  [ "$status" -eq 0 ]
  grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
  [ ! -e "$question" ]
  [ ! -e "${question%.md}.answer.seen" ]
}

@test "ask-user-question rejects a missing question file" {
  export HERDR_PANES='{"result":{"panes":[]}}'
  run "$ROOT/ask-user-question" release "$HOME/.claude/coordinator/questions/missing.md"
  [ "$status" -eq 66 ]
}
