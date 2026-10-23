#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031 # Each Bats test has its own environment.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
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

teardown() { rm -rf "$TMP"; }

@test "worker signals wrapper resolves symlinks and completion is idempotent" {
  ln -s "$ROOT/orch-signal" "$TMP/bin/orch-signal"
  run "$TMP/bin/orch-signal" heartbeat --new-job
  [ "$status" -eq 0 ]
  run "$TMP/bin/orch-signal" "done" --outcome succeeded --report /tmp/report --summary passed
  [ "$status" -eq 0 ]
  before=$(cat "$HOME/.claude/coordinator/worker-signals/"pane-*.json)
  run "$TMP/bin/orch-signal" "done" --outcome failed --report /tmp/report --summary failed
  [ "$status" -eq 0 ]
  [ "$(cat "$HOME/.claude/coordinator/worker-signals/"pane-*.json)" = "$before" ]
}

@test "worker signals configured timeout exits with one clear line" {
  run "$ROOT/orch-signal" ask --question 'Which version?' --options v1,v2
  [ "$status" -ne 0 ]
  [[ "$output" == 'orch-signal: question '*"timed out after 1s" ]]
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
