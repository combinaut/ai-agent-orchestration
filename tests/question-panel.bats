#!/usr/bin/env bats

load test_helper

setup() {
  setup_fixture
  mkdir -p "$HOME/.claude/coordinator/questions"
  : >"$TMP/none.toml"
  cat >"$BIN/herdr-question" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TMP/question-herdr.calls"
[ "${HERDR_FAIL:-}" != "$2" ] || exit 1
case "$1 $2" in
  'pane list') printf '%s\n' "${HERDR_PANES:?}" ;;
  'pane close')
    if [ -n "${HERDR_REQUIRE_QUESTION:-}" ]; then
      [ -f "$HERDR_REQUIRE_QUESTION" ] && [ -f "${HERDR_REQUIRE_QUESTION%.md}.answer" ] || exit 90
    fi
    ;;
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

@test "ask-user-question finds the to-do pane by its exact To-do label" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"other","label":"To-do list"},{"pane_id":"todo-pane","label":"To-do","terminal_title_stripped":"python3 user-todo-view.py"}]}}' "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  grep -qx 'pane split todo-pane --direction down --no-focus' "$TMP/question-herdr.calls"
}

@test "ask-user-question does not anchor on a label that only starts with To-do" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"other","label":"To-do list"}]}}' "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 69 ]
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

@test "close finds the exact renamed pane label before deleting files" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf question >"$question"
  printf yes >"${question%.md}.answer"
  export HERDR_REQUIRE_QUESTION="$question"
  local panes='{"result":{"panes":[{"pane_id":"question-pane","label":"Q: release","terminal_title_stripped":"python3 question-pane.py","title":"old title"},{"pane_id":"other","label":"Q: release-notes","terminal_title_stripped":"Q: release"}]}}'
  run env HERDR_PANES="$panes" "$ROOT/ask-user-question" close release
  [ "$status" -eq 0 ]
  grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
  run grep -qx 'pane close other' "$TMP/question-herdr.calls"
  [ "$status" -eq 1 ]
  [ -f "$ORCH_CLOSED_DIR/question-pane" ]
  [ ! -e "$question" ]
  [ ! -e "${question%.md}.answer" ]
}

@test "failed close keeps question files and removes the intentional-close marker" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf question >"$question"
  printf yes >"${question%.md}.answer"
  local panes='{"result":{"panes":[{"pane_id":"question-pane","label":"Q: release","terminal_title_stripped":"python3 question-pane.py"}]}}'
  run env HERDR_FAIL=close HERDR_PANES="$panes" "$ROOT/ask-user-question" close release
  [ "$status" -ne 0 ]
  grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
  [ "$(cat "$question")" = question ]
  [ "$(cat "${question%.md}.answer")" = yes ]
  [ ! -e "$ORCH_CLOSED_DIR/question-pane" ]
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

@test "diagnostic check and cycle leave live answers for the production journal" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf question >"$question"
  printf yes >"${question%.md}.answer"
  export REAL_QUESTION_PYTHON="$ORCH_PYTHON3"
  cat >"$BIN/preview-python" <<'EOF'
#!/bin/bash
case "$1" in -c | -I) exec "$REAL_QUESTION_PYTHON" "$@" ;; esac
if [ "${2:-}" = answers ]; then exec "$REAL_QUESTION_PYTHON" "$@"; fi
exit 0
EOF
  chmod +x "$BIN/preview-python"
  run env ORCH_PYTHON3="$BIN/preview-python" "$ROOT/coordinator/watchers/coord-check.sh" --state-dir "$TMP/preview"
  [ "$status" -eq 0 ]
  [ "$(cat "${question%.md}.answer")" = yes ]
  [ ! -e "$TMP/preview/answers.json" ]
  run env ORCH_PYTHON3="$BIN/preview-python" "$ROOT/coordinator/watchers/coord-cycle.sh" "$TMP/preview"
  [ "$status" -eq 0 ]
  [ "$(cat "${question%.md}.answer")" = yes ]
  [ ! -e "$TMP/preview/answers.json" ]
}

@test "failed pane run or rename closes the new pane intentionally" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf question >"$question"
  for failure in run rename; do
    rm -f "$TMP/question-herdr.calls" "$ORCH_CLOSED_DIR/question-pane"
    run env HERDR_FAIL="$failure" HERDR_PANES='{"result":{"panes":[{"pane_id":"todo","tokens":{"todo":"1"}}]}}' "$ROOT/ask-user-question" release "$question"
    [ "$status" -ne 0 ]
    grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
    [ -f "$ORCH_CLOSED_DIR/question-pane" ]
    [ -f "$question" ]
  done
}
