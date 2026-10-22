#!/usr/bin/env bats

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/ao-close-worker.XXXXXX")
  BIN="$TMP/bin" FX="$TMP/fx"
  mkdir -p "$BIN" "$FX" "$TMP/home/.claude/coordinator/status" "$TMP/wt"
  git -C "$TMP/wt" init -q -b feat/task
  : >"$TMP/none.toml"
  STATUS="$TMP/home/.claude/coordinator/status/architect-demo.md"
  printf '# Architect::demo\n\n## Work\n- one\n\n## Closed workers\n\n## Waits on the user\n- (none)\n' >"$STATUS"
  cat >"$BIN/herdr" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$HERDR_CALLS"
key=${3//:/_}
case "$1 $2" in
  'agent get') cat "$FX/agent-$key.json" ;;
  'workspace get') cat "$FX/ws-$key.json" ;;
  'pane close') [ -z "${HERDR_CLOSE_FAIL:-}" ] ;;
  *) exit 1 ;;
esac
STUB
  chmod +x "$BIN/herdr"
  python_bin=$(command -v python3)
  export HOME="$TMP/home" FX HERDR_CALLS="$TMP/herdr.calls" HERDR_BIN_PATH="$BIN/herdr" ORCH_CONFIG="$TMP/none.toml" \
    ORCH_CLOSED_DIR="$TMP/closed" ORCH_PYTHON3="$python_bin" HERDR_PANE_ID=wA:p1 HERDR_WORKSPACE_ID=wA
  unset ORCH_USER_TODO_STATUS_DIR
  agent wA:p1 architect-demo wA architect arch-session "$ROOT"
  workspace wA "" "$ROOT"
  agent wW:p1 fix-it wW "" 1111-aaaa "$TMP/wt"
  workspace wW wA "$TMP/wt"
}

teardown() {
  rm -rf "$TMP"
}

# agent <pane> <name> <workspace> <role> <session> <cwd> [kind]
agent() {
  printf '{"result":{"agent":{"pane_id":"%s","name":"%s","workspace_id":"%s","cwd":"%s","tokens":{"role":"%s"},"agent_session":{"agent":"%s","value":"%s"}}}}\n' \
    "$1" "$2" "$3" "$6" "$4" "${7:-claude}" "$5" >"$FX/agent-${1//:/_}.json"
}

# workspace <id> <owner> <checkout>
workspace() {
  printf '{"result":{"workspace":{"workspace_id":"%s","tokens":{"owner":"%s"},"worktree":{"checkout_path":"%s"}}}}\n' \
    "$1" "$2" "$3" >"$FX/ws-$1.json"
}

not_closed() {
  if grep -q 'pane close' "$HERDR_CALLS"; then
    return 1
  fi
}

close_worker() {
  run "$ROOT/coordinator/watchers/close-worker.sh" "$@"
}

@test "closes a finished worker and writes one recovery line" {
  close_worker wW:p1 --task "fix the thing" --report /r/fix.md
  [ "$status" -eq 0 ]
  grep -qx 'pane close wW:p1' "$HERDR_CALLS"
  [ -f "$ORCH_CLOSED_DIR/wW:p1" ]
  line=$(grep -c 'wW:p1' "$STATUS")
  [ "$line" -eq 1 ]
  grep -q '^- .* wW:p1 (fix-it): fix the thing | report /r/fix.md | branch feat/task | worktree '"$TMP/wt"' | resume: claude --resume 1111-aaaa$' "$STATUS"
}

@test "refuses an architect pane" {
  agent wW:p1 other-arch wW architect s "$TMP/wt"
  before=$(cat "$STATUS")
  close_worker wW:p1 --task t
  [ "$status" -eq 3 ]
  not_closed
  [ "$(cat "$STATUS")" = "$before" ]
}

@test "refuses an architect identified only by its name" {
  agent wW:p1 Architect::legacy wW "" s "$TMP/wt"
  before=$(cat "$STATUS")
  close_worker wW:p1 --task t
  [ "$status" -eq 3 ]
  not_closed
  [ "$(cat "$STATUS")" = "$before" ]
}

@test "refuses a caller whose name is a path" {
  for name in 'Architect::../../other' 'a/b' '..' 'a\\b'; do
    agent wA:p1 "$name" wA architect arch-session "$ROOT"
    : >"$HERDR_CALLS"
    close_worker wW:p1 --task t
    [ "$status" -eq 3 ]
    not_closed
    [ ! -e "$TMP/home/.claude/coordinator/other.md" ]
  done
}

@test "refuses the Coordinator" {
  agent wW:p1 coordinator wW coordinator s "$TMP/wt"
  close_worker wW:p1 --task t
  [ "$status" -eq 3 ]
  not_closed
}

@test "refuses a worker whose workspace owner is not the caller" {
  workspace wW wOther "$TMP/wt"
  before=$(cat "$STATUS")
  close_worker wW:p1 --task t
  [ "$status" -eq 3 ]
  not_closed
  [ "$(cat "$STATUS")" = "$before" ]
}

@test "refuses a worker whose workspace has no owner tag, also when the caller has no workspace id" {
  workspace wW "" "$TMP/wt"
  unset HERDR_WORKSPACE_ID
  close_worker wW:p1 --task t
  [ "$status" -eq 3 ]
  not_closed
}

@test "refuses the caller's own pane" {
  close_worker wA:p1 --task t
  [ "$status" -eq 3 ]
  not_closed
}

@test "refuses a pane id that is a path" {
  close_worker ../victim --task t
  [ "$status" -eq 2 ]
  not_closed
}

@test "refuses a caller that is not an architect pane" {
  agent wA:p1 plain wA "" s "$ROOT"
  close_worker wW:p1 --task t
  [ "$status" -eq 3 ]
  not_closed
}

@test "a failed close writes no recovery line and leaves no closed mark" {
  before=$(cat "$STATUS")
  HERDR_CLOSE_FAIL=1 close_worker wW:p1 --task t
  [ "$status" -eq 1 ]
  [ "$(cat "$STATUS")" = "$before" ]
  [ ! -e "$ORCH_CLOSED_DIR/wW:p1" ]
}

@test "keeps the last 10 lines of Closed workers and every other section as it was" {
  for i in 1 2 3 4 5 6 7 8 9 10; do
    printf -- '- old %s\n' "$i" >>"$TMP/old.txt"
  done
  { printf '# Architect::demo\n\n## Work\n- one\n\n## Closed workers\n'; cat "$TMP/old.txt"; printf '\n## Waits on the user\n- (none)\n'; } >"$STATUS"
  close_worker wW:p1 --task t
  [ "$status" -eq 0 ]
  [ "$(sed -n '/^## Closed workers$/,/^## Waits/p' "$STATUS" | grep -c '^- ')" -eq 10 ]
  [ "$(grep -cx -- '- old 1' "$STATUS")" -eq 0 ]
  grep -qx -- '- old 2' "$STATUS"
  grep -q 'wW:p1' "$STATUS"
  [ "$(sed -n '1,/^## Closed workers$/p' "$STATUS")" = "$(printf '# Architect::demo\n\n## Work\n- one\n\n## Closed workers')" ]
  [ "$(tail -3 "$STATUS")" = "$(printf '\n## Waits on the user\n- (none)')" ]
}

@test "adds the Closed workers section when the status file has none" {
  printf '# Architect::demo\n\n## Work\n- one\n' >"$STATUS"
  close_worker wW:p1 --task t
  [ "$status" -eq 0 ]
  [ "$(sed -n '1,4p' "$STATUS")" = "$(printf '# Architect::demo\n\n## Work\n- one')" ]
  grep -qx '## Closed workers' "$STATUS"
}

@test "a status file that cannot be written gives exit 4 after the close, with the line on stderr" {
  : >"$TMP/notadir"
  ORCH_USER_TODO_STATUS_DIR="$TMP/notadir" close_worker wW:p1 --task t --report /r/x.md
  [ "$status" -eq 4 ]
  grep -qx 'pane close wW:p1' "$HERDR_CALLS"
  [ -f "$ORCH_CLOSED_DIR/wW:p1" ]
  [[ "$output" == *"closed, but the status file is unwritable"* ]]
  [[ "$output" == *"- "*" wW:p1 (fix-it): t | report /r/x.md | branch feat/task"* ]]
}

@test "a task with newlines becomes one line" {
  close_worker wW:p1 --task $'first\nsecond\n## Waits on the user' --report $'/r\n/x'
  [ "$status" -eq 0 ]
  [ "$(grep -c 'wW:p1' "$STATUS")" -eq 1 ]
  grep -q 'wW:p1 (fix-it): first second ## Waits on the user | report /r /x |' "$STATUS"
  [ "$(grep -c '^## Waits on the user$' "$STATUS")" -eq 1 ]
}

@test "the resume command follows the agent kind" {
  agent wW:p1 fix-it wW "" 2222-bbbb "$TMP/wt" codex
  close_worker wW:p1 --task t
  [ "$status" -eq 0 ]
  grep -q 'resume: codex resume 2222-bbbb$' "$STATUS"
}
