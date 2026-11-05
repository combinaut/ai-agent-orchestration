#!/usr/bin/env bats
# shellcheck disable=SC2154 # run --separate-stderr sets stderr.

bats_require_minimum_version 1.5.0

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/sweep-record-bats.XXXXXX")
  REPO="$TMP/repo"
  git init -q "$REPO"
  for n in first second third; do
    git -C "$REPO" -c user.name=t -c user.email=t@example.com commit -q --allow-empty -m "$n"
  done
  FIRST_SHA=$(git -C "$REPO" rev-parse HEAD~2)
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  REC="$TMP/record.json"
}

teardown() {
  rm -rf "$TMP"
}

# record <findings> <validators> <verdicts-json> [head]: writes the record file.
record() {
  local ids="" i
  for ((i = 1; i <= $2; i++)); do ids="${ids:+$ids, }\"agent-$i\""; done
  cat >"$REC" <<JSON
{"head": "${4:-$HEAD_SHA}", "base": "abc123", "clean": ${CLEAN:-true}, "slices": 3, "sweep_agents": 6,
 "validators": $2, "validator_ids": [$ids], "findings": $1, "verdicts": $3}
JSON
}

check() {
  cd "$REPO" || return 1
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$REC" --head "${1:-$HEAD_SHA}"
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

@test "a record without a field fails naming it" {
  record 4 1 '{"FALSE": 4}'
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); del d["validator_ids"]; json.dump(d, open(sys.argv[1], "w"))' "$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: missing field validator_ids" ]
}

@test "a record for an ancestor of the head passes and lists the commits since" {
  record 4 1 '{"FALSE": 4}' "$FIRST_SHA"
  check
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "OK: $REC swept $FIRST_SHA; commits since:" ]
  [ "${#lines[@]}" -eq 3 ]
  [[ "${lines[1]}" == *" third" ]]
  [[ "${lines[2]}" == *" second" ]]
}

@test "a record whose head is not an ancestor fails" {
  record 4 1 '{"FALSE": 4}' "$HEAD_SHA"
  check "$FIRST_SHA"
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: swept $HEAD_SHA, which is not an ancestor of the PR head $FIRST_SHA" ]
}

@test "a SHA git cannot resolve fails" {
  record 4 1 '{"FALSE": 4}' fedcba9876543210fedcba9876543210fedcba98
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: git cannot resolve fedcba9876543210fedcba9876543210fedcba98" ]
}

@test "a head that is not a full SHA fails" {
  record 4 1 '{"FALSE": 4}' --upload-pack=x
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: '--upload-pack=x' is not a full 40-character SHA" ]
}

@test "findings with no validator fail" {
  record 64 0 '{"FALSE": 64}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: 64 findings and no validator started" ]
}

@test "verdict counts that do not add up to the findings fail" {
  record 5 1 '{"FALSE": 2, "CONFIRMED_NEW": 2}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: verdicts add up to 4, findings is 5" ]
}

@test "a valid record passes" {
  record 5 1 '{"FALSE": 3, "CONFIRMED_NEW": 2}'
  check
  [ "$status" -eq 0 ]
  [ "$output" = "OK: $REC covers $HEAD_SHA" ]
  [ "$stderr" = "" ]
}

@test "a sweep with no findings and no validators passes" {
  record 0 0 '{}'
  check
  [ "$status" -eq 0 ]
}

@test "a count that is not a whole number fails" {
  record 5 1 '{"FALSE": 5}'
  sed -i.bak 's/"validators": 1/"validators": "1"/' "$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validators must be a non-negative integer" ]
}

@test "validator ids that do not match the validator count fail" {
  record 5 3 '{"FALSE": 5}'
  sed -i.bak 's/"agent-2", "agent-3"//; s/, \]/]/' "$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: 1 validator_ids for 3 validators" ]
}

@test "a blank validator id fails" {
  record 5 1 '{"FALSE": 5}'
  sed -i.bak 's/"agent-1"/""/' "$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator_ids must be a list of non-empty strings" ]
}

@test "a verdict outside the skill's list fails naming it" {
  record 5 1 '{"PENDING": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: unknown verdict PENDING" ]
}

@test "a record from a dirty tree fails" {
  CLEAN=false record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: $REC: clean must be true"* ]]
}

@test "a record without the clean field fails" {
  record 5 1 '{"FALSE": 5}'
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); del d["clean"]; json.dump(d, open(sys.argv[1], "w"))' "$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: missing field clean" ]
}

@test "every verdict the checker accepts is a row of the skill's verdict table" {
  local verdicts v
  # shellcheck disable=SC2016 # Backticks in the grep pattern are literal.
  mapfile -t verdicts < <(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import sweep_record; print(*sweep_record.VERDICTS, sep="\n")' "$ROOT/lib")
  [ "${#verdicts[@]}" -eq "$(grep -c '^| `[A-Z_]*` |' "$ROOT/skills/finalize-sweep/SKILL.md")" ]
  for v in "${verdicts[@]}"; do grep -qF "| \`$v\` |" "$ROOT/skills/finalize-sweep/SKILL.md"; done
}
