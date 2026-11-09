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

# codex_agent <agent-path> <parent> [thread-id]: a Codex child rollout.
codex_agent() {
  local dir="$CODEX_HOME/sessions/2026/11/06"
  local thread_id=${3:-child-thread-id}
  mkdir -p "$dir"
  printf '{"type":"session_meta","payload":{"id":"child-thread-id","source":{"subagent":{"thread_spawn":{"parent_thread_id":"%s","agent_path":"%s"}}}}}\n' \
    "$2" "$1" >"$dir/rollout-2026-11-06T10-00-00-$thread_id.jsonl"
  printf '%s\n' '{"type":"event_msg","payload":{"type":"agent_message","encrypted_content":"gAAAA..."}}' >>"$dir/rollout-2026-11-06T10-00-00-$thread_id.jsonl"
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

@test "a Codex encrypted child rollout validates by its task name" {
  codex_agent "/root/finalize_sweep_validator_${HEAD_SHA:0:12}" "$SESSION"
  record 2 "[\"/root/finalize_sweep_validator_${HEAD_SHA:0:12}\"]"
  check
  [ "$status" -eq 0 ]
}

@test "a Codex validator path must name the swept head" {
  codex_agent /root/correctness "$SESSION"
  record 2 '["/root/correctness"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: Codex validator /root/correctness must start with /root/finalize_sweep_validator_${HEAD_SHA:0:12}" ]
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
  codex_agent "/root/finalize_sweep_validator_${HEAD_SHA:0:12}" some-other-thread
  record 2 "[\"/root/finalize_sweep_validator_${HEAD_SHA:0:12}\"]"
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

@test "a newer valid Codex rollout wins over an older same-path rollout" {
  local dir="$CODEX_HOME/sessions/2026/11/06"
  local id="/root/finalize_sweep_validator_${HEAD_SHA:0:12}"
  codex_agent "$id" "$SESSION" 0-old
  touch -t 200001010000 "$dir/rollout-2026-11-06T10-00-00-0-old.jsonl"
  codex_agent "$id" "$SESSION" z-new
  record 2 "[\"$id\"]"
  check
  [ "$status" -eq 0 ]
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

# script_record [python statement]: a valid orch-sweep record, then the statement run on it as r.
script_record() {
  claude_agent v-1
  python3 - "$REC" "$HEAD_SHA" "$ZERO_SHA" "${1:-pass}" <<'PY'
import hashlib, json, sys
path, head, base, change = sys.argv[1:5]
empty = hashlib.sha256(b"").hexdigest()
tree = {"head": head, "status": empty, "diff": empty, "cached": empty, "untracked": {}, "untracked_hash": hashlib.sha256(b"{}").hexdigest()}
plan = [
    {"name": "lib", "lines": 30, "files": [{"path": "lib/a.py", "old": None, "status": "M", "lines": 20}, {"path": "tests/a.bats", "old": None, "status": "A", "lines": 10}]},
    {"name": "binary", "lines": 0, "files": [{"path": "img/x.png", "old": None, "status": "A", "lines": None}]},
]
r = {"head": head, "base": base, "clean": True, "mode": "agents", "slices": 2, "sweep_agents": 5,
     "validators": 1, "validator_ids": ["v-1"], "findings": 3,
     "verdicts": {"CONFIRMED_NEW": 2, "FALSE": 1}, "tool": "orch-sweep", "version": 1,
     "pr": "https://github.com/acme/app/pull/7", "runtime": "codex", "model": "gpt-test",
     "kind": "full", "tree": tree, "plan": plan, "pr_text": 1,
     "sweep_agent_ids": ["s-1", "s-2", "s-3", "s-4", "s-5"],
     "items": [
         {"id": "F1", "file": "lib/a.py", "line": 3, "category": "comment", "class": "fix", "verdict": "CONFIRMED_NEW", "summary": "x"},
         {"id": "F2", "file": "lib/a.py", "line": 9, "category": "comment", "class": "nit", "verdict": "CONFIRMED_NEW", "summary": "y"},
     ],
     "dropped": [{"id": "F3", "file": "lib/a.py", "line": 4, "category": "comment", "class": "nit", "verdict": "FALSE", "summary": "z"}],
     "open_fix": 1, "deviation": None}
exec(change)
json.dump(r, open(path, "w"))
PY
}

@test "a valid orch-sweep record passes the exact check" {
  script_record
  check
  [ "$status" -eq 0 ]
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$REC" --head "$HEAD_SHA" --session "$SESSION" --exact
  [ "$status" -eq 0 ]
}

@test "the exact check fails a record for an ancestor of the head" {
  script_record 'r["head"] = sys.argv[2]; r["tree"]["head"] = r["head"]'
  sed -i.bak "s/$HEAD_SHA/$FIRST_SHA/g" "$REC"
  cd "$REPO"
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$REC" --head "$HEAD_SHA" --session "$SESSION" --exact
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: swept $FIRST_SHA, not the exact head $HEAD_SHA" ]
}

@test "an orch-sweep record without a model field fails" {
  script_record 'del r["model"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: missing field model" ]
}

@test "an orch-sweep record needs two agents per slice plus the PR-text agent" {
  script_record 'r["sweep_agents"] = 4; r["sweep_agent_ids"] = r["sweep_agent_ids"][:4]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: 4 sweep_agents; 2 slices and pr_text 1 start 5" ]
}

@test "repeated sweep agent ids fail" {
  script_record 'r["sweep_agent_ids"][4] = "s-1"'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: 4 distinct sweep_agent_ids for 5 sweep agents" ]
}

@test "open_fix that does not count the fix items fails" {
  script_record 'r["open_fix"] = 0'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: open_fix is 0, the items hold 1 fix findings" ]
}

@test "items that do not match the kept verdicts fail" {
  script_record 'r["items"].pop()'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: 1 items for 2 kept verdicts" ]
}

@test "a dropped verdict in the items fails" {
  script_record 'r["items"][1]["verdict"] = "FALSE"'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: an item has a verdict that drops it" ]
}

@test "a path in two slices fails" {
  script_record 'r["plan"].append({"name": "docs", "lines": 0, "files": [dict(r["plan"][0]["files"][0], lines=0)]})'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: plan: lib/a.py is in more than one slice" ]
}

@test "a slice of several files over 400 lines fails" {
  script_record 'r["plan"][0]["files"][0]["lines"] = 395; r["plan"][0]["lines"] = 405'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: plan: slice lib: 405 lines in 2 files; only a single file may pass 400" ]
}

@test "a slice line count that differs from its files fails" {
  script_record 'r["plan"][0]["lines"] = 29'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: plan: slice lib: lines is 29, its files add up to 30" ]
}

@test "a slice line count that is not an integer fails" {
  script_record 'r["plan"][0]["lines"] = 30.0'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: plan: slice lib: lines is 30.0, its files add up to 30" ]
}

@test "a binary file outside the binary slice fails" {
  script_record 'r["plan"][0]["files"].append(r["plan"][1]["files"].pop()); r["plan"].pop(); r["slices"] = 1; r["sweep_agents"] = 3; r["sweep_agent_ids"] = r["sweep_agent_ids"][:3]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: plan: slice lib: binary files go only in the binary slice" ]
}

@test "an orch-sweep record of a kind other than full or text fails" {
  script_record 'r["kind"] = "incremental"'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: kind must be full or text" ]
}

@test "a tree taken at another head fails" {
  script_record 'r["tree"]["head"] = sys.argv[3]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: tree must hold the five tree values of a clean tree at head" ]
}

@test "a tree with only its head fails" {
  script_record 'r["tree"] = {"head": r["head"]}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: tree must hold the five tree values of a clean tree at head" ]
}

@test "a tree with a changed diff hash fails" {
  script_record 'r["tree"]["diff"] = "0" * 64'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: tree must hold the five tree values of a clean tree at head" ]
}

@test "dropped findings that do not make up the rest of the findings fail" {
  script_record 'r["dropped"] = []'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: 2 items and 0 dropped for 3 findings" ]
}

@test "a finding with a missing or mistyped field fails" {
  for change in 'r["dropped"][0]["file"] = None' 'r["dropped"][0]["line"] = "4"' 'r["dropped"][0]["line"] = True' \
    'r["items"][0]["summary"] = None' 'r["dropped"][0]["class"] = "major"' 'r["items"][0]["id"] = ""' \
    'r["dropped"][0]["category"] = 3' 'r["dropped"][0]["line"] = -1'; do
    script_record "$change"
    check
    [ "$status" -eq 1 ]
    [[ "$stderr" == "ERROR: $REC: finding "* ]] || { echo "$change: $stderr"; false; }
  done
}

@test "two findings with the same id fail" {
  script_record 'r["dropped"][0]["id"] = "F1"'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: finding id F1 appears twice" ]
}

@test "an orch-sweep record without version 1 fails" {
  script_record 'del r["version"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: missing field version" ]
  for change in 'r["version"] = 2' 'r["version"] = True' 'r["version"] = "1"'; do
    script_record "$change"
    check
    [ "$status" -eq 1 ]
    [ "$stderr" = "ERROR: $REC: version must be 1" ]
  done
}

@test "an orch-sweep record without a deviation field, or with a malformed deviation, fails" {
  script_record 'del r["deviation"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: missing field deviation" ]
  script_record 'r["deviation"] = {"ok": True}'
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: $REC: deviation must be null or name one of "* ]]
}

@test "an orch-sweep record whose pr is not a pull request URL fails" {
  for change in 'r["pr"] = "anything"' 'r["pr"] = None' 'r["pr"] = "https://github.com/acme/app/pull/7x"'; do
    script_record "$change"
    check
    [ "$status" -eq 1 ]
    [ "$stderr" = "ERROR: $REC: pr must be a GitHub pull request URL" ]
  done
}

@test "a validator id that is also a sweep agent id fails" {
  script_record 'r["validator_ids"] = ["s-3"]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator id s-3 is also a sweep agent id" ]
}

@test "an open_fix that is not an integer fails" {
  for change in 'r["open_fix"] = True' 'r["open_fix"] = "1"' 'r["open_fix"] = 1.0'; do
    script_record "$change"
    check
    [ "$status" -eq 1 ]
    [ "$stderr" = "ERROR: $REC: open_fix must be a non-negative integer" ] || { echo "$change: $stderr"; false; }
  done
}

@test "a dropped finding with a keep verdict fails" {
  script_record 'r["dropped"][0]["verdict"] = "CONFIRMED_NEW"'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: a dropped finding has a verdict that keeps it" ]
}

@test "an orch-sweep record without the PR-text agent fails" {
  script_record 'r["pr_text"] = 0; r["sweep_agents"] = 4; r["sweep_agent_ids"] = r["sweep_agent_ids"][:4]'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: pr_text must be 1; every orch-sweep run checks the PR title and body" ]
}

@test "findings whose verdicts differ from the verdict counts fail" {
  script_record 'r["dropped"][0]["verdict"] = "RESPECT_DROP"'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: the findings hold RESPECT_DROP 1, verdicts says 0" ]
}

@test "script records also require the session-bound validator transcript" {
  script_record
  rm -f "$CLAUDE_CONFIG_DIR/projects/-some-project/$SESSION/subagents/agent-v-1.jsonl"
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"is no supported Claude or Codex subagent transcript"* ]]
}

@test "script records retain strict clean count and verdict metadata checks" {
  script_record
  cd "$REPO" || return 1
  run python3 - "$ROOT/orch-sweep-record" "$REC" "$HEAD_SHA" "$SESSION" <<'PY'
import copy, json, subprocess, sys
checker, path, head, session = sys.argv[1:]
original = json.load(open(path))
cases = [
    ({"clean": False}, None, "clean must be true"),
    ({}, "clean", "missing field clean"),
    ({"mode": "unknown"}, None, "mode must be agents or serial"),
    ({}, "mode", "missing field mode"),
    ({"slices": 0}, None, "slices must be at least 1"),
    ({"sweep_agents": 0}, None, "sweep_agents must be at least 1"),
    ({"sweep_agents": 3}, None, "each slice starts two"),
    ({"validator_ids": [""]}, None, "validator_ids must be a list of non-empty strings"),
    ({"validator_ids": ["v-1", "v-2"]}, None, "2 validator_ids for 1 validators"),
    ({"validator_ids": ["v-1", "v-1"], "validators": 2}, None, "validator_ids repeat an id"),
    ({"validator_ids": [], "validators": 0}, None, "3 findings and no validator id"),
    ({"verdicts": {"unknown": 3}}, None, "unknown verdict unknown"),
    ({"verdicts": {"FALSE": -1}}, None, "verdicts must map each verdict to a count"),
    ({"verdicts": {"FALSE": True}}, None, "verdicts must map each verdict to a count"),
    ({"verdicts": {"FALSE": 2}}, None, "verdicts add up to 2, findings is 3"),
]
for field in ("slices", "sweep_agents", "validators", "findings"):
    for value in (-1, True, 0.5):
        cases.append(({field: value}, None, f"{field} must be a non-negative integer"))
for changes, drop, expected in cases:
    record = copy.deepcopy(original)
    record.update(changes)
    if drop:
        del record[drop]
    with open(path, "w") as out:
        json.dump(record, out)
    result = subprocess.run([checker, "check", "--record", path, "--head", head, "--session", session], capture_output=True, text=True)
    assert result.returncode == 1 and expected in result.stderr, (changes, drop, expected, result.returncode, result.stderr)
PY
  echo "$output"
  [ "$status" -eq 0 ]
}

@test "a failed ordinary-record git log cannot pass the check" {
  record 0 '[]'
  mkdir "$TMP/bin"
  real_git=$(command -v git)
  # shellcheck disable=SC2016 # The fake git expands its own arguments.
  printf '#!/bin/sh\n[ "$1" != log ] || exit 1\nexec "%s" "$@"\n' "$real_git" >"$TMP/bin/git"
  chmod +x "$TMP/bin/git"
  PATH="$TMP/bin:$PATH" check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"git log $HEAD_SHA..$HEAD_SHA failed" ]]
}
