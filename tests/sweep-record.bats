#!/usr/bin/env bats
# shellcheck disable=SC2154 # run --separate-stderr sets stderr.

bats_require_minimum_version 1.5.0

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/sweep-record-bats.XXXXXX")
  REPO="$TMP/repo"
  git init -q "$REPO"
  for n in zero first second third; do
    git -C "$REPO" -c user.name=t -c user.email=t@example.com commit -q --allow-empty -m "$n"
  done
  ZERO_SHA=$(git -C "$REPO" rev-parse HEAD~3)
  FIRST_SHA=$(git -C "$REPO" rev-parse HEAD~2)
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  REC="$TMP/record.json"
  SESSION=11111111-2222-3333-4444-555555555555
  export CLAUDE_CONFIG_DIR="$TMP/claude" CODEX_HOME="$TMP/codex"
}

teardown() {
  rm -rf "$TMP"
}

# record <findings> <ids-json> [head]: writes the record file; BASE overrides its base.
record() {
  cat >"$REC" <<JSON
{"head": "${3:-$HEAD_SHA}", "base": "${BASE:-$ZERO_SHA}", "findings": $1, "validator_ids": $2, "extra": "ignored"}
JSON
}

# claude_agent <id> [brief] [later]: a Claude subagent transcript of SESSION.
claude_agent() {
  local dir="$CLAUDE_CONFIG_DIR/projects/-some-project/$SESSION/subagents"
  mkdir -p "$dir"
  printf '{"type":"user","message":{"role":"user","content":"%s"' "${2:-finalize-sweep validator $HEAD_SHA}" >"$dir/agent-$1.jsonl"
  printf '%s\n' '}}' >>"$dir/agent-$1.jsonl"
  [ -z "${3:-}" ] || printf '{"type":"assistant","message":{"content":"%s"}}\n' "$3" >>"$dir/agent-$1.jsonl"
}

# codex_agent <agent-path> <parent> [task] [later]: a Codex child rollout.
codex_agent() {
  local dir="$CODEX_HOME/sessions/2026/11/06"
  mkdir -p "$dir"
  printf '{"type":"session_meta","payload":{"id":"child-thread-id","source":{"subagent":{"thread_spawn":{"parent_thread_id":"%s","agent_path":"%s"}}}}}\n' \
    "$2" "$1" >"$dir/rollout-2026-11-06T10-00-00-child-thread-id.jsonl"
  printf '{"type":"event_msg","payload":{"type":"user_message","message":"%s"}}\n' "${3:-finalize-sweep validator $HEAD_SHA}" >>"$dir/rollout-2026-11-06T10-00-00-child-thread-id.jsonl"
  [ -z "${4:-}" ] || printf '{"type":"event_msg","payload":{"type":"agent_message","message":"%s"}}\n' "$4" >>"$dir/rollout-2026-11-06T10-00-00-child-thread-id.jsonl"
}

check() {
  cd "$REPO" || return 1
  local -a args=(check --record "$REC" --head "${PR_HEAD:-$HEAD_SHA}")
  [ -z "${EXPECT_BASE:-}" ] || args+=(--base "$EXPECT_BASE")
  [ -z "${SESSION_ARG-x}" ] || args+=(--session "${SESSION_ARG:-$SESSION}")
  [ -z "${NO_TRANSCRIPTS:-}" ] || args+=(--no-transcripts)
  run --separate-stderr "$ROOT/orch-sweep-record" "${args[@]}"
}

@test "a missing record fails with one stderr line" {
  check
  [ "$status" -eq 1 ]
  [ "$output" = "" ]
  [ "$stderr" = "ERROR: $REC: no sweep record" ]
}

@test "a record that is not JSON fails" {
  printf 'not json' >"$REC"
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: $REC: not valid JSON"* ]]
}

@test "a record that is not an object fails" {
  printf '[1]' >"$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: the record must be a JSON object" ]
}

@test "a record without a field fails naming it" {
  printf '{"head": "%s", "base": "%s", "findings": 0}' "$HEAD_SHA" "$ZERO_SHA" >"$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: missing field validator_ids" ]
}

@test "a head that is not a full SHA fails" {
  record 0 '[]' abc123
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"head 'abc123' is not a full 40-character SHA" ]]
}

@test "a record with no findings and no validators passes and ignores extra fields" {
  record 0 '[]'
  check
  [ "$status" -eq 0 ]
  [ "$output" = "OK: $REC covers $HEAD_SHA" ]
}

@test "findings and no validator id fail" {
  record 4 '[]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: 4 findings and no validator id" ]
}

@test "findings that are negative or not an integer fail" {
  record -1 '[]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: findings must be a non-negative integer" ]
  record true '[]'
  check
  [ "$status" -eq 1 ]
}

@test "validator_ids that is not a list of strings fails" {
  record 1 '"agent-1"'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator_ids must be a list of strings" ]
}

@test "a stale head fails and an ancestor head lists the commits since" {
  record 0 '[]' "$FIRST_SHA"
  check
  [ "$status" -eq 0 ]
  [[ "$output" == "OK: $REC swept $FIRST_SHA; commits since:"* ]]
  [[ "$output" == *second* && "$output" == *third* ]]
  PR_HEAD=$FIRST_SHA check
  [ "$status" -eq 0 ]
  [ "$output" = "OK: $REC covers $FIRST_SHA" ]
  record 0 '[]'
  PR_HEAD=$FIRST_SHA check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"not an ancestor of the PR head"* ]]
}

@test "a base that is not an ancestor of the head fails" {
  BASE=$HEAD_SHA record 0 '[]' "$FIRST_SHA"
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"is not an ancestor of $FIRST_SHA" ]]
}

@test "head equal to base fails" {
  BASE=$HEAD_SHA record 0 '[]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: head equals base; the sweep covered an empty diff" ]
}

@test "a SHA git cannot resolve fails" {
  record 0 '[]' 1111111111111111111111111111111111111111
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"git cannot resolve 1111111111111111111111111111111111111111" ]]
}

@test "--base must equal the record's base" {
  record 0 '[]'
  EXPECT_BASE=$FIRST_SHA check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: base $ZERO_SHA, expected $FIRST_SHA" ]
  EXPECT_BASE=$ZERO_SHA check
  [ "$status" -eq 0 ]
}

@test "a Claude subagent transcript of the session that names the head passes" {
  claude_agent a1b2c3
  record 2 '["a1b2c3"]'
  check
  [ "$status" -eq 0 ]
  [ "$output" = "OK: $REC covers $HEAD_SHA" ]
}

@test "a Codex subagent rollout spawned by the session that names the head passes" {
  codex_agent /root/correctness "$SESSION"
  record 2 '["/root/correctness"]'
  check
  [ "$status" -eq 0 ]
}

@test "an id with no transcript fails" {
  record 2 '["ghost"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator ghost is no supported Claude or Codex subagent transcript of session $SESSION; Pi and Cursor validators are unsupported" ]
}

@test "a transcript of another session fails" {
  claude_agent a1b2c3
  record 2 '["a1b2c3"]'
  SESSION_ARG=other-session check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator a1b2c3 is no supported Claude or Codex subagent transcript of session other-session; Pi and Cursor validators are unsupported" ]
}

@test "a Codex rollout spawned by another thread fails" {
  codex_agent /root/correctness some-other-thread
  record 2 '["/root/correctness"]'
  check
  [ "$status" -eq 1 ]
}

@test "a malformed Codex rollout is skipped" {
  local id=/root/correctness
  mkdir -p "$CODEX_HOME/sessions/2026/11/06"
  printf '%s\n' '{"type":"session_meta","payload":{"id":"child-thread-id","source":{"subagent":{"thread_spawn":[]}}}}' >"$CODEX_HOME/sessions/2026/11/06/rollout-2026-11-06T10-00-00-child-thread-id.jsonl"
  record 2 "[\"$id\"]"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator $id is no supported Claude or Codex subagent transcript of session $SESSION; Pi and Cursor validators are unsupported" ]
}

@test "a Claude SHA outside the validator brief fails" {
  claude_agent a1b2c3 'sweep slice comments' "finalize-sweep validator $HEAD_SHA"
  record 2 '["a1b2c3"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator a1b2c3 brief must include finalize-sweep validator and name $HEAD_SHA" ]
}

@test "a Claude validator brief may use text blocks" {
  local dir="$CLAUDE_CONFIG_DIR/projects/-some-project/$SESSION/subagents"
  mkdir -p "$dir"
  printf '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"finalize-sweep validator "},{"type":"text","text":"%s"}]}}\n' "$HEAD_SHA" >"$dir/agent-a1b2c3.jsonl"
  record 2 '["a1b2c3"]'
  check
  [ "$status" -eq 0 ]
}

@test "a Codex validator task must include the marker and head" {
  codex_agent /root/correctness "$SESSION" 'sweep slice comments' "$HEAD_SHA"
  record 2 '["/root/correctness"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator /root/correctness brief must include finalize-sweep validator and name $HEAD_SHA" ]
}

@test "a transcript written before the head commit fails" {
  claude_agent a1b2c3
  touch -t 200001010000 "$CLAUDE_CONFIG_DIR/projects/-some-project/$SESSION/subagents/agent-a1b2c3.jsonl"
  record 2 '["a1b2c3"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator a1b2c3 was written before commit $HEAD_SHA" ]
}

@test "an unreadable transcript fails with one ERROR line" {
  mkdir -p "$CLAUDE_CONFIG_DIR/projects/-some-project/$SESSION/subagents/agent-a1b2c3.jsonl"
  record 2 '["a1b2c3"]'
  check
  [ "$status" -eq 1 ]
  [ "${stderr#"ERROR: $REC: validator a1b2c3 transcript cannot read: "}" != "$stderr" ]
  [ "$(printf '%s\n' "$stderr" | wc -l | tr -d ' ')" -eq 1 ]
}

@test "a transcript that does not name the head fails" {
  claude_agent a1b2c3 'finalize-sweep validator some other commit'
  record 2 '["a1b2c3"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator a1b2c3 brief must include finalize-sweep validator and name $HEAD_SHA" ]
}

@test "an id with a path separator fails" {
  record 2 '["../x"]'
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"is not a plain id" ]]
}

@test "findings without --session fail" {
  claude_agent a1b2c3
  record 2 '["a1b2c3"]'
  SESSION_ARG='' check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: --session <worker session id> is required" ]
}

@test "--no-transcripts accepts the ids unresolved and says so" {
  record 2 '["ghost"]'
  NO_TRANSCRIPTS=1 SESSION_ARG='' check
  [ "$status" -eq 0 ]
  [ "$output" = "OK: $REC covers $HEAD_SHA; attestation only" ]
}

@test "a missing --head fails with one ERROR line and exit 1" {
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$REC"
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: the following arguments are required: --head" ]
}

@test "an unknown subcommand fails with one ERROR line and exit 1" {
  run --separate-stderr "$ROOT/orch-sweep-record" bogus
  [ "$status" -eq 1 ]
  [ "$(printf '%s\n' "$stderr" | grep -c '^ERROR:')" = 1 ]
}

@test "a control character in a usage error is escaped" {
  run --separate-stderr "$ROOT/orch-sweep-record" "$(printf 'bad\nsub')"
  [ "$status" -eq 1 ]
  [ "$(printf '%s\n' "$stderr" | wc -l | tr -d ' ')" -eq 1 ]
}

@test "the checker runs through a symlink" {
  mkdir "$TMP/bin"
  ln -s "$ROOT/orch-sweep-record" "$TMP/bin/orch-sweep-record"
  record 0 '[]'
  cd "$REPO" || return 1
  run "$TMP/bin/orch-sweep-record" check --record "$REC" --head "$HEAD_SHA"
  [ "$status" -eq 0 ]
}
