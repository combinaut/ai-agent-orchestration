#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031 # Each Bats test has its own environment.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  PYTHON=$(python3 -c 'import sys; print(sys.executable)')
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/worker-signals-bats.XXXXXX")
  export HOME="$TMP/home" ORCH_CONFIG="$TMP/config.toml" ORCH_ENV=herdr
  mkdir -p "$HOME" "$TMP/bin"
  printf '[workers]\nask_timeout_s = 1\n' >"$ORCH_CONFIG"
  export HERDR_PANE_ID=worker-pane HERDR_WORKSPACE_ID=worker
  export HERDR_BIN_PATH="$TMP/bin/herdr"
  cat >"$HERDR_BIN_PATH" <<'SH'
#!/bin/sh
case "$1 $2" in
 'workspace list') printf '%s\n' '{"result":{"workspaces":[{"workspace_id":"worker","tokens":{"owner":"architect"}},{"workspace_id":"architect"}]}}' ;;
 'pane list') printf '%s\n' '{"result":{"panes":[{"pane_id":"worker-pane","workspace_id":"worker"},{"pane_id":"architect-pane","workspace_id":"architect"}]}}' ;;
 *) exit 1 ;;
esac
SH
  chmod +x "$HERDR_BIN_PATH"
}

teardown() {
  chmod -R u+w "$TMP" 2>/dev/null || true
  rm -rf "$TMP"
}

@test "worker signals wrapper resolves symlinks, ignores identical done and preserves changed done" {
  printf '[workers]\nask_timeout_s = 1\n[herdr]\nbin = "%s"\n' "$HERDR_BIN_PATH" >"$ORCH_CONFIG"
  unset HERDR_BIN_PATH
  ln -s "$ROOT/orch-signal" "$TMP/bin/orch-signal"
  run "$TMP/bin/orch-signal" heartbeat --new-job
  [ "$status" -eq 0 ]
  run "$TMP/bin/orch-signal" "done" --outcome succeeded --report /tmp/report --summary passed
  [ "$status" -eq 0 ]
  before=$(cat "$HOME/.claude/coordinator/worker-signals/"pane-*.json)
  run "$TMP/bin/orch-signal" "done" --outcome succeeded --report /tmp/report --summary passed
  [ "$status" -eq 0 ]
  [[ "$output" == 'orch-signal: done unchanged; no new completion' ]]
  [ "$(cat "$HOME/.claude/coordinator/worker-signals/"pane-*.json)" = "$before" ]
  run "$TMP/bin/orch-signal" "done" --outcome failed --report /tmp/report2 --summary failed
  [ "$status" -eq 0 ]
  [ "$(cat "$HOME/.claude/coordinator/worker-signals/"pane-*.json)" != "$before" ]
}

@test "worker signals configured timeout exits with one clear line" {
  run "$ROOT/orch-signal" ask --question 'Which version?' --options v1,v2
  [ "$status" -ne 0 ]
  [[ "$output" == 'orch-signal: question '*"timed out after 1s" ]] || { echo "$output"; false; }
  [ "${#lines[@]}" -eq 1 ]
}

@test "worker signals answer reaches a blocking ask through the public commands" {
  "$ROOT/orch-signal" ask --question 'Which version?' --timeout 20 >"$TMP/result" 2>"$TMP/error" &
  asker=$!
  for _ in {1..50}; do
    files=("$HOME/.claude/coordinator/worker-signals/"signal-*.json)
    [ ! -f "${files[0]}" ] || break
    sleep 0.1
  done
  id=${files[0]##*/}
  id=${id%.json}
  run env HERDR_PANE_ID=architect-pane HERDR_WORKSPACE_ID=architect "$ROOT/orch-signal" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"id=$id"* ]]
  run env HERDR_PANE_ID=architect-pane HERDR_WORKSPACE_ID=architect "$ROOT/orch-signal" answer "$id" v2
  [ "$status" -eq 0 ]
  wait "$asker"
  [ "$(cat "$TMP/result")" = v2 ]
  [ ! -s "$TMP/error" ]
}

denied_questions() {
  export ORCH_USER_QUESTIONS_DIR="$TMP/questions"
  mkdir -p "$ORCH_USER_QUESTIONS_DIR"
  chmod 555 "$ORCH_USER_QUESTIONS_DIR"
}

denied_signals() {
  mkdir -p "$HOME/.claude/coordinator/worker-signals"
  chmod 555 "$HOME/.claude/coordinator/worker-signals"
}

denied_missing_questions() {
  export ORCH_USER_QUESTIONS_DIR="$TMP/denied/questions"
  mkdir -p "$TMP/denied"
  chmod 555 "$TMP/denied"
}

denied_missing_signals() {
  mkdir -p "$HOME/.claude/coordinator"
  chmod 555 "$HOME/.claude/coordinator"
}

pending_question() {
  export ORCH_USER_QUESTIONS_DIR="$TMP/questions"
  "$ROOT/orch-signal" ask --question 'Which version?' --options v1,v2 --timeout 20 >/dev/null 2>&1 &
  asker=$!
  for _ in {1..50}; do
    files=("$HOME/.claude/coordinator/worker-signals/"signal-*.json)
    [ ! -f "${files[0]}" ] || break
    sleep 0.1
  done
  id=${files[0]##*/}
  id=${id%.json}
}

stop_asker() {
  kill "$asker" 2>/dev/null || true
  wait "$asker" 2>/dev/null || true
}

deny_with_stub() {
  mkdir -p "$TMP/stub"
  cat >"$TMP/stub/sitecustomize.py" <<'PY'
import errno
import os
import pathlib

mode = os.environ.get("ORCH_TEST_DENY")
code = getattr(errno, os.environ.get("ORCH_TEST_ERRNO", "EPERM"))


def denied(path=None):
    return PermissionError(code, os.strerror(code), str(path) if path else None)


if mode == "reminder":
    import question_remind

    original = question_remind.atomic_write

    def deny_reminder(path, text):
        if str(path).endswith(".remind"):
            raise denied(path)
        return original(path, text)

    question_remind.atomic_write = deny_reminder
elif mode == "flock":
    import fcntl

    def deny_flock(fd, operation):
        raise denied()

    fcntl.flock = deny_flock
elif mode in ("unlink-remind", "unlink-signal"):
    import watchers

    watchers.open_question = lambda *args, **kwargs: 0
    unlink = pathlib.Path.unlink
    suffix = ".remind" if mode == "unlink-remind" else ".json"

    def deny_unlink(self, missing_ok=False):
        if self.name.endswith(suffix):
            raise denied(self)
        return unlink(self, missing_ok=missing_ok)

    pathlib.Path.unlink = deny_unlink
PY
  export PYTHONPATH="$TMP/stub:$ROOT/coordinator/watchers"
}

assert_escalate() {
  [ "$status" -eq 73 ]
  local dir=${1//\/\//\/}
  [ "$output" = "orch-signal: cannot write $dir (sandbox?); run this command escalated" ] || { echo "$output"; false; }
}

@test "worker signals ask exits 73 naming the questions directory when its lock is unwritable" {
  denied_questions
  run "$ROOT/orch-signal" ask --question 'Which version?' --timeout 5
  assert_escalate "$TMP/questions"
}

@test "worker signals list exits 73 naming the questions directory when its lock is unwritable" {
  denied_questions
  run "$ROOT/orch-signal" list
  assert_escalate "$TMP/questions"
}

@test "worker signals heartbeat exits 73 naming the questions directory when its lock is unwritable" {
  denied_questions
  run "$ROOT/orch-signal" heartbeat --new-job
  assert_escalate "$TMP/questions"
}

@test "worker signals done exits 73 naming the questions directory when its lock is unwritable" {
  denied_questions
  run "$ROOT/orch-signal" "done" --outcome succeeded --report /tmp/report --summary passed
  assert_escalate "$TMP/questions"
}

@test "worker signals answer exits 73 naming the questions directory when its lock is unwritable" {
  denied_questions
  run env HERDR_PANE_ID=architect-pane HERDR_WORKSPACE_ID=architect "$ROOT/orch-signal" answer signal-00000000000000000000000000000000 v2
  assert_escalate "$TMP/questions"
}

@test "worker signals heartbeat exits 73 naming the signals directory when it is unwritable" {
  denied_signals
  run "$ROOT/orch-signal" heartbeat --new-job
  assert_escalate "$HOME/.claude/coordinator/worker-signals"
}

@test "worker signals ask exits 73 naming the signals directory when it is unwritable" {
  denied_signals
  run "$ROOT/orch-signal" ask --question 'Which version?' --timeout 5
  assert_escalate "$HOME/.claude/coordinator/worker-signals"
}

@test "worker signals other write errors keep exit 1" {
  mkdir -p "$HOME/.claude/coordinator"
  : >"$HOME/.claude/coordinator/worker-signals"
  run "$ROOT/orch-signal" heartbeat --new-job
  [ "$status" -eq 1 ]
  [[ "$output" == 'orch-signal: '* ]]
}

@test "worker signals done exits 73 naming the signals directory when it is unwritable" {
  denied_signals
  run "$ROOT/orch-signal" "done" --outcome succeeded --report /tmp/report --summary passed
  assert_escalate "$HOME/.claude/coordinator/worker-signals"
}

@test "worker signals list names the questions directory when it is missing under a denied parent" {
  denied_missing_questions
  run "$ROOT/orch-signal" list
  assert_escalate "$TMP/denied/questions"
}

@test "worker signals ask names the questions directory when it is missing under a denied parent" {
  denied_missing_questions
  run "$ROOT/orch-signal" ask --question 'Which version?' --timeout 5
  assert_escalate "$TMP/denied/questions"
}

@test "worker signals heartbeat names the signals directory when it is missing under a denied parent" {
  export ORCH_USER_QUESTIONS_DIR="$TMP/questions"
  denied_missing_signals
  run "$ROOT/orch-signal" heartbeat --new-job
  assert_escalate "$HOME/.claude/coordinator/worker-signals"
}

@test "worker signals done names the signals directory when it is missing under a denied parent" {
  export ORCH_USER_QUESTIONS_DIR="$TMP/questions"
  denied_missing_signals
  run "$ROOT/orch-signal" "done" --outcome succeeded --report /tmp/report --summary passed
  assert_escalate "$HOME/.claude/coordinator/worker-signals"
}

@test "worker signals answer exits 73 naming the signals directory when the answer file is unwritable" {
  pending_question
  chmod 555 "$HOME/.claude/coordinator/worker-signals"
  run env HERDR_PANE_ID=architect-pane HERDR_WORKSPACE_ID=architect "$ROOT/orch-signal" answer "$id" v2
  stop_asker
  assert_escalate "$HOME/.claude/coordinator/worker-signals"
}

@test "worker signals answer --to-user exits 73 naming the questions directory when the panel file is unwritable" {
  pending_question
  chmod 555 "$TMP/questions"
  run env HERDR_PANE_ID=architect-pane HERDR_WORKSPACE_ID=architect "$ROOT/orch-signal" answer "$id" --to-user
  stop_asker
  assert_escalate "$TMP/questions"
}

@test "worker signals answer --to-user exits 73 naming the questions directory when the reminder file is unwritable" {
  pending_question
  deny_with_stub
  run env ORCH_TEST_DENY=reminder HERDR_PANE_ID=architect-pane HERDR_WORKSPACE_ID=architect "$ROOT/orch-signal" answer "$id" --to-user
  stop_asker
  assert_escalate "$TMP/questions"
}

@test "worker signals list exits 73 naming the questions directory when a blocking flock is denied" {
  deny_with_stub
  run env ORCH_TEST_DENY=flock "$ROOT/orch-signal" list
  assert_escalate "${ORCH_USER_QUESTIONS_DIR:-$HOME/.claude/coordinator/questions}"
}

@test "worker signals ask exits 73 naming the questions directory when a non-blocking flock is denied" {
  deny_with_stub
  run env ORCH_TEST_DENY=flock "$ROOT/orch-signal" ask --question 'Which version?' --timeout 5
  assert_escalate "${ORCH_USER_QUESTIONS_DIR:-$HOME/.claude/coordinator/questions}"
}

@test "worker signals answer --to-user exits 73 naming the questions directory when the reminder cleanup is denied" {
  pending_question
  deny_with_stub
  run env ORCH_TEST_DENY=unlink-remind HERDR_PANE_ID=architect-pane HERDR_WORKSPACE_ID=architect "$ROOT/orch-signal" answer "$id" --to-user
  stop_asker
  assert_escalate "$TMP/questions"
}

@test "worker signals ask exits 73 naming the signals directory when the answered record cleanup is denied" {
  export ORCH_USER_QUESTIONS_DIR="$TMP/questions"
  deny_with_stub
  ORCH_TEST_DENY=unlink-signal "$ROOT/orch-signal" ask --question 'Which version?' --timeout 20 >"$TMP/result" 2>&1 &
  asker=$!
  for _ in {1..50}; do
    files=("$HOME/.claude/coordinator/worker-signals/"signal-*.json)
    [ ! -f "${files[0]}" ] || break
    sleep 0.1
  done
  id=${files[0]##*/}
  id=${id%.json}
  run env HERDR_PANE_ID=architect-pane HERDR_WORKSPACE_ID=architect "$ROOT/orch-signal" answer "$id" v2
  [ "$status" -eq 0 ]
  wait "$asker" && code=0 || code=$?
  [ "$code" -eq 73 ]
  status=73 output=$(cat "$TMP/result")
  assert_escalate "$HOME/.claude/coordinator/worker-signals"
}

@test "worker signals denied flock exits 73 for EPERM, EACCES and EROFS" {
  deny_with_stub
  for name in EPERM EACCES EROFS; do
    run env ORCH_TEST_DENY=flock ORCH_TEST_ERRNO=$name "$ROOT/orch-signal" list
    assert_escalate "$HOME/.claude/coordinator/questions"
  done
}

@test "worker signals a denied to-do write keeps its own path in the error" {
  deny_with_stub
  todo="$HOME/.claude/coordinator/todo-answers"
  mkdir -p "$todo"
  chmod 555 "$todo"
  run env PYTHONPATH="$ROOT/coordinator/watchers" "$PYTHON" -c '
import sys
import todo_answers

try:
    todo_answers.write(todo_answers.directory() / "answer.json", {})
except OSError as error:
    print(error)
    sys.exit(3)
'
  [ "$status" -eq 3 ]
  [[ "$output" == *"/todo-answers/"* ]] || { echo "$output"; false; }
  [[ "$output" != *"coordinator/worker-signals"* ]]
}

@test "worker signals a mode 000 record in a writable directory exits 1 naming the file" {
  run "$ROOT/orch-signal" heartbeat --new-job
  [ "$status" -eq 0 ]
  chmod 000 "$HOME/.claude/coordinator/worker-signals/"pane-*.json
  run "$ROOT/orch-signal" heartbeat --new-job
  [ "$status" -eq 1 ]
  [[ "$output" == *"Permission denied"*"/worker-signals/pane-"* ]] || { echo "$output"; false; }
  [[ "$output" != *"escalated"* ]]
}

@test "worker signals list names a relative questions directory when its lock is unwritable" {
  mkdir -p "$TMP/cwd/rel"
  chmod 555 "$TMP/cwd/rel"
  cd "$TMP/cwd"
  run env ORCH_USER_QUESTIONS_DIR=rel "$ROOT/orch-signal" list
  [ "$status" -eq 73 ]
  [ "$output" = "orch-signal: cannot write rel (sandbox?); run this command escalated" ] || { echo "$output"; false; }
}

@test "worker signals list names the questions directory when two levels are missing under a denied parent" {
  export ORCH_USER_QUESTIONS_DIR="$TMP/denied/new/questions"
  mkdir -p "$TMP/denied"
  chmod 555 "$TMP/denied"
  run "$ROOT/orch-signal" list
  assert_escalate "$TMP/denied/new/questions"
}

@test "worker signals heartbeat names the signals directory when two levels are missing under a denied parent" {
  export ORCH_USER_QUESTIONS_DIR="$TMP/questions"
  mkdir -p "$HOME/.claude"
  chmod 555 "$HOME/.claude"
  run "$ROOT/orch-signal" heartbeat --new-job
  assert_escalate "$HOME/.claude/coordinator/worker-signals"
}
