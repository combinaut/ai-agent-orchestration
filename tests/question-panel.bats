#!/usr/bin/env bats

load test_helper

setup() {
  setup_fixture
  unset HERDR_PANE_ID
  mkdir -p "$HOME/.claude/coordinator/questions"
  : >"$TMP/none.toml"
  cat >"$BIN/herdr-question" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TMP/question-herdr.calls"
[ "${HERDR_FAIL:-}" != "$2" ] || exit 1
[ "${HERDR_KILL_OPENER:-}" != "$2" ] || kill -KILL "$PPID"
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

@test "open splits the caller pane even with Coordinator question and to-do anchors" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  panes='{"result":{"panes":[{"pane_id":"architect","workspace_id":"asking"},{"pane_id":"todo","label":"To-do","workspace_id":"coordinator"},{"pane_id":"old-question","label":"Q: old","workspace_id":"coordinator"}]}}'
  run env HERDR_PANE_ID=architect HERDR_PANES="$panes" "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  grep -qx 'pane split architect --direction down --no-focus' "$TMP/question-herdr.calls"
  [ "$(cat "${question%.md}.asker-pane")" = architect ]
}

@test "a vanished caller pane falls back to the last question pane" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  panes='{"result":{"panes":[{"pane_id":"todo","label":"To-do"},{"pane_id":"old-question","label":"Q: old"}]}}'
  run env HERDR_PANE_ID=vanished HERDR_PANES="$panes" "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  grep -qx 'pane split old-question --direction down --no-focus' "$TMP/question-herdr.calls"
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
  printf architect >"${question%.md}.asker-pane"
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
  [ ! -e "${question%.md}.asker-pane" ]
}

@test "failed close keeps question files and removes the intentional-close marker" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf question >"$question"
  printf yes >"${question%.md}.answer"
  printf architect >"${question%.md}.asker-pane"
  local panes='{"result":{"panes":[{"pane_id":"question-pane","label":"Q: release","terminal_title_stripped":"python3 question-pane.py"}]}}'
  run env HERDR_FAIL=close HERDR_PANES="$panes" "$ROOT/ask-user-question" close release
  [ "$status" -ne 0 ]
  grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
  [ "$(cat "$question")" = question ]
  [ "$(cat "${question%.md}.answer")" = yes ]
  [ ! -e "$ORCH_CLOSED_DIR/question-pane" ]
  [ "$(cat "${question%.md}.asker-pane")" = architect ]
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

@test "hide closes the exact pane and keeps the question files" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf question >"$question"
  printf architect >"${question%.md}.asker-pane"
  printf '%s\n' 2000000000 >"${question%.md}.remind"
  local panes='{"result":{"panes":[{"pane_id":"question-pane","label":"Q: release"},{"pane_id":"other","label":"Q: release-notes"}]}}'
  run env HERDR_PANES="$panes" "$ROOT/ask-user-question" hide release
  [ "$status" -eq 0 ]
  grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
  run grep -qx 'pane close other' "$TMP/question-herdr.calls"
  [ "$status" -eq 1 ]
  [ -f "$ORCH_CLOSED_DIR/question-pane" ]
  [ -f "$question" ]
  [ "$(cat "${question%.md}.asker-pane")" = architect ]
  [ -f "${question%.md}.remind" ]
}

@test "hide rejects an invalid slug" {
  run "$ROOT/ask-user-question" hide bad_slug
  [ "$status" -eq 64 ]
}

@test "the answers watcher reopens a due reminder once and leaves a future one hidden" {
  questions="$HOME/.claude/coordinator/questions"
  printf due >"$questions/due.md"
  printf later >"$questions/later.md"
  printf '%s\n' 1 >"$questions/due.remind"
  printf '%s\n' 99999999999 >"$questions/later.remind"
  local panes='{"result":{"panes":[{"pane_id":"todo","tokens":{"todo":"1"}}]}}'
  run env ORCH_USER_QUESTIONS_DIR="$questions" HERDR_PANES="$panes" "$ORCH_PYTHON3" "$ROOT/coordinator/watchers/watchers.py" answers --state-dir "$TMP/state"
  [ "$status" -eq 0 ]
  grep -qx 'pane rename question-pane Q: due' "$TMP/question-herdr.calls"
  run grep -c 'pane rename' "$TMP/question-herdr.calls"
  [ "$output" -eq 1 ]
  [ ! -e "$questions/due.remind" ]
  [ -f "$questions/later.remind" ]
}

@test "a due reminder reopens beside its original asker instead of the watcher" {
  questions="$HOME/.claude/coordinator/questions"
  printf due >"$questions/due.md"
  printf architect >"$questions/due.asker-pane"
  printf '%s\n' 1 >"$questions/due.remind"
  panes='{"result":{"panes":[{"pane_id":"architect","workspace_id":"asking"},{"pane_id":"watcher","workspace_id":"coordinator"},{"pane_id":"todo","label":"To-do"}]}}'
  run env HERDR_PANE_ID=watcher ORCH_USER_QUESTIONS_DIR="$questions" HERDR_PANES="$panes" "$ORCH_PYTHON3" "$ROOT/coordinator/watchers/watchers.py" answers --state-dir "$TMP/state"
  [ "$status" -eq 0 ]
  grep -qx 'pane split architect --direction down --no-focus' "$TMP/question-herdr.calls"
  [ ! -e "$questions/due.remind" ]
}

@test "a reminder with a vanished saved asker falls back and clears the saved pane" {
  questions="$HOME/.claude/coordinator/questions"
  printf due >"$questions/due.md"
  for anchor in question todo; do
    printf gone >"$questions/due.asker-pane"
    printf '%s\n' 1 >"$questions/due.remind"
    if [ "$anchor" = question ]; then
      panes='{"result":{"panes":[{"pane_id":"watcher"},{"pane_id":"todo","label":"To-do"},{"pane_id":"question","label":"Q: other"}]}}'
    else
      panes='{"result":{"panes":[{"pane_id":"watcher"},{"pane_id":"todo","label":"To-do"}]}}'
    fi
    : >"$TMP/question-herdr.calls"
    run env HERDR_PANE_ID=watcher ORCH_USER_QUESTIONS_DIR="$questions" HERDR_PANES="$panes" "$ORCH_PYTHON3" "$ROOT/coordinator/watchers/watchers.py" answers --state-dir "$TMP/state"
    [ "$status" -eq 0 ]
    grep -qx "pane split $anchor --direction down --no-focus" "$TMP/question-herdr.calls"
    [ ! -e "$questions/due.asker-pane" ]
    [ ! -e "$questions/due.remind" ]
  done
}

@test "a legacy reminder ignores the watcher caller and uses the to-do fallback" {
  questions="$HOME/.claude/coordinator/questions"
  printf due >"$questions/due.md"
  printf '%s\n' 1 >"$questions/due.remind"
  panes='{"result":{"panes":[{"pane_id":"watcher"},{"pane_id":"todo","label":"To-do"}]}}'
  run env HERDR_PANE_ID=watcher ORCH_USER_QUESTIONS_DIR="$questions" HERDR_PANES="$panes" "$ORCH_PYTHON3" "$ROOT/coordinator/watchers/watchers.py" answers --state-dir "$TMP/state"
  [ "$status" -eq 0 ]
  grep -qx 'pane split todo --direction down --no-focus' "$TMP/question-herdr.calls"
  [ ! -e "$questions/due.asker-pane" ]
}

@test "an initial worker escalation through the watcher helper retains the caller" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf release >"$question"
  printf todo >"${question%.md}.asker-pane"
  panes='{"result":{"panes":[{"pane_id":"architect"},{"pane_id":"todo","label":"To-do"}]}}'
  run env HERDR_PANE_ID=architect HERDR_PANES="$panes" "$ORCH_PYTHON3" -c 'import sys; sys.path.insert(0, sys.argv[1]); import watchers; sys.exit(watchers.open_question("release", sys.argv[2]))' "$ROOT/coordinator/watchers" "$question"
  [ "$status" -eq 0 ]
  grep -qx 'pane split architect --direction down --no-focus' "$TMP/question-herdr.calls"
  [ "$(cat "${question%.md}.asker-pane")" = architect ]
}

@test "a failed reopen keeps the reminder for the next pass" {
  questions="$HOME/.claude/coordinator/questions"
  printf due >"$questions/due.md"
  printf '%s\n' 1 >"$questions/due.remind"
  run env ORCH_USER_QUESTIONS_DIR="$questions" HERDR_FAIL=run HERDR_PANES='{"result":{"panes":[{"pane_id":"todo","tokens":{"todo":"1"}}]}}' "$ORCH_PYTHON3" "$ROOT/coordinator/watchers/watchers.py" answers --state-dir "$TMP/state"
  [ -f "$questions/due.remind" ]
}

@test "a question named hide still opens with the slug and file form" {
  question="$HOME/.claude/coordinator/questions/hide.md"
  printf question >"$question"
  run env HERDR_PANES='{"result":{"panes":[{"pane_id":"todo","tokens":{"todo":"1"}}]}}' "$ROOT/ask-user-question" hide "$question"
  [ "$status" -eq 0 ]
  grep -qx 'pane rename question-pane Q: hide' "$TMP/question-herdr.calls"
}

@test "an opener killed after the split leaves no duplicate pane on the next open" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  todo='{"pane_id":"todo-pane","tokens":{"todo":"1"}}'
  run env HERDR_KILL_OPENER=rename HERDR_PANES="{\"result\":{\"panes\":[$todo]}}" "$ROOT/ask-user-question" release "$question"
  [ "$status" -ne 0 ]
  [ -s "${question%.md}.opening" ]
  : >"$TMP/question-herdr.calls"
  run env HERDR_PANES="{\"result\":{\"panes\":[$todo,{\"pane_id\":\"question-pane\"}]}}" "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  [ "$(grep -n -m1 '^pane close question-pane$' "$TMP/question-herdr.calls" | cut -d: -f1)" -lt "$(grep -n -m1 '^pane split ' "$TMP/question-herdr.calls" | cut -d: -f1)" ]
  [ ! -e "${question%.md}.opening" ]
}

@test "an opener killed while the split runs leaves no marker and the next open closes no pane" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  todo='{"pane_id":"todo-pane","tab_id":"t1","tokens":{"todo":"1"}}'
  run env HERDR_KILL_OPENER=split HERDR_PANES="{\"result\":{\"panes\":[$todo]}}" "$ROOT/ask-user-question" release "$question"
  [ "$status" -ne 0 ]
  [ ! -s "${question%.md}.opening" ]
  : >"$TMP/question-herdr.calls"
  panes="$todo,{\"pane_id\":\"orphan-pane\",\"tab_id\":\"t1\"},{\"pane_id\":\"b\",\"tab_id\":\"t1\"}"
  run env HERDR_PANES="{\"result\":{\"panes\":[$panes]}}" "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  run grep -q '^pane close' "$TMP/question-herdr.calls"
  [ "$status" -eq 1 ]
}

@test "a question named hide opens from a bare relative file name and from an absolute path" {
  questions="$HOME/.claude/coordinator/questions"
  printf 'Hide?\n' >"$questions/hide.md"
  panes='{"result":{"panes":[{"pane_id":"todo-pane","tokens":{"todo":"1"}}]}}'
  cd "$questions"
  run env HERDR_PANES="$panes" "$ROOT/ask-user-question" hide hide.md
  [ "$status" -eq 0 ]
  grep -qx 'pane rename question-pane Q: hide' "$TMP/question-herdr.calls"
  : >"$TMP/question-herdr.calls"
  run env HERDR_PANES="$panes" "$ROOT/ask-user-question" hide "$questions/hide.md"
  [ "$status" -eq 0 ]
  grep -qx 'pane rename question-pane Q: hide' "$TMP/question-herdr.calls"
}

hold_lock() {
  "$python_bin" -c 'import fcntl, sys, time
lock = open(sys.argv[1], "a")
fcntl.flock(lock, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(float(sys.argv[3]))' "$HOME/.claude/coordinator/questions/.answers.lock" "$TMP/lock-ready" "$1" &
  holder=$!
  until [ -e "$TMP/lock-ready" ]; do sleep 0.05; done
}

@test "an open waits for the answers lock instead of racing a running open" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  hold_lock 2
  env HERDR_PANES='{"result":{"panes":[{"pane_id":"todo-pane","tokens":{"todo":"1"}}]}}' "$ROOT/ask-user-question" release "$question" &
  opener=$!
  sleep 1
  run grep -q '^pane split' "$TMP/question-herdr.calls"
  [ "$status" -ne 0 ]
  wait "$opener"
  wait "$holder"
  grep -q '^pane split' "$TMP/question-herdr.calls"
}

@test "an open started by the lock holder does not wait on its own lock" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  hold_lock 5
  run env ORCH_ANSWERS_LOCK_HELD=1 HERDR_PANES='{"result":{"panes":[{"pane_id":"todo-pane","tokens":{"todo":"1"}}]}}' "$ROOT/ask-user-question" release "$question"
  kill "$holder"
  [ "$status" -eq 0 ]
  grep -q '^pane split' "$TMP/question-herdr.calls"
}

@test "hide and close look the pane up while holding the answers lock" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  panes='{"result":{"panes":[{"pane_id":"question-pane","label":"Q: release"}]}}'
  hold_lock 2
  env HERDR_PANES="$panes" "$ROOT/ask-user-question" hide release &
  hider=$!
  sleep 1
  run grep -q '^pane list' "$TMP/question-herdr.calls"
  [ "$status" -ne 0 ]
  wait "$hider"
  wait "$holder"
  grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
  : >"$TMP/question-herdr.calls"
  rm -f "$TMP/lock-ready"
  hold_lock 2
  env HERDR_PANES="$panes" "$ROOT/ask-user-question" close release &
  closer=$!
  sleep 1
  run grep -q '^pane list' "$TMP/question-herdr.calls"
  [ "$status" -ne 0 ]
  wait "$closer"
  wait "$holder"
  grep -qx 'pane close question-pane' "$TMP/question-herdr.calls"
}

@test "a failed split leaves no marker, so a later user terminal in the tab is never closed" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  todo='{"pane_id":"todo-pane","tab_id":"t1","tokens":{"todo":"1"}}'
  run env HERDR_FAIL=split HERDR_PANES="{\"result\":{\"panes\":[$todo]}}" "$ROOT/ask-user-question" release "$question"
  [ "$status" -ne 0 ]
  [ ! -s "${question%.md}.opening" ]
  : >"$TMP/question-herdr.calls"
  panes="$todo,{\"pane_id\":\"user-shell\",\"tab_id\":\"t1\",\"terminal_title_stripped\":\"zsh\"}"
  run env HERDR_PANES="{\"result\":{\"panes\":[$panes]}}" "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  run grep -qx 'pane close user-shell' "$TMP/question-herdr.calls"
  [ "$status" -eq 1 ]
}

@test "a stale marker naming a pane that is gone closes nothing and is cleared" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  printf 'gone-pane\n' >"${question%.md}.opening"
  todo='{"pane_id":"todo-pane","tab_id":"t1","tokens":{"todo":"1"}}'
  panes="$todo,{\"pane_id\":\"user-shell\",\"tab_id\":\"t1\"}"
  run env HERDR_PANES="{\"result\":{\"panes\":[$panes]}}" "$ROOT/ask-user-question" release "$question"
  [ "$status" -eq 0 ]
  run grep -q '^pane close' "$TMP/question-herdr.calls"
  [ "$status" -eq 1 ]
  [ ! -e "${question%.md}.opening" ]
}

answer_events() {
  "$ORCH_PYTHON3" -c 'import json, sys; print("\n".join(json.loads(sys.stdin.read()).get("events", [])))'
}

@test "option keys answer with the file's own id and label; a missing option writes nothing" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf '# Which release?\n\n## Options\n\n1. Alpha\n2. Beta\n' >"$question"
  printf '2' | "$ORCH_PYTHON3" "$ROOT/coordinator/question-pane.py" "$question"
  [ "$(cat "${question%.md}.answer")" = '2 "Beta"' ]
  run env ORCH_USER_QUESTIONS_DIR="$HOME/.claude/coordinator/questions" "$ORCH_PYTHON3" "$ROOT/coordinator/watchers/watchers.py" answers --preview-answers --state-dir "$TMP/state"
  [ "$status" -eq 0 ]
  [ "$(answer_events <<<"$output")" = 'ANSWER release: 2 "Beta"' ]
  rm -f "${question%.md}.answer"
  printf '5' | "$ORCH_PYTHON3" "$ROOT/coordinator/question-pane.py" "$question"
  [ ! -e "${question%.md}.answer" ]
}

@test "a question file without an Options section still answers with the digit alone" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf 'Which release?\n' >"$question"
  printf '7' | "$ORCH_PYTHON3" "$ROOT/coordinator/question-pane.py" "$question"
  [ "$(cat "${question%.md}.answer")" = 7 ]
}

@test "option labels are normalised to the answers watcher's whitespace collapse" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf '# Which release?\n\n## Options\n\n1. Beta  \n2. A  B\n' >"$question"
  printf '1' | "$ORCH_PYTHON3" "$ROOT/coordinator/question-pane.py" "$question"
  [ "$(cat "${question%.md}.answer")" = '1 "Beta"' ]
  rm -f "${question%.md}.answer"
  printf '2' | "$ORCH_PYTHON3" "$ROOT/coordinator/question-pane.py" "$question"
  [ "$(cat "${question%.md}.answer")" = '2 "A B"' ]
}

@test "a label holding double quotes round-trips through the documented quote rule" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf '# Which release?\n\n## Options\n\n1. say "hi"\n' >"$question"
  printf '1' | "$ORCH_PYTHON3" "$ROOT/coordinator/question-pane.py" "$question"
  answer=$(cat "${question%.md}.answer")
  [ "$answer" = '1 "say "hi""' ]
  [ "$("$ORCH_PYTHON3" -c 'import re, sys; print(re.match(r"^([1-9]) \"(.*)\"$", sys.argv[1]).group(2))' "$answer")" = 'say "hi"' ]
}

@test "a second heading after ## Options ends the option section" {
  question="$HOME/.claude/coordinator/questions/release.md"
  printf '# Which release?\n\n## Options\n\n1. Alpha\n\n## Notes\n\n2. Beta\n' >"$question"
  printf '2' | "$ORCH_PYTHON3" "$ROOT/coordinator/question-pane.py" "$question"
  [ ! -e "${question%.md}.answer" ]
}
