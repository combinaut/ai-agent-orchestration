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
}

teardown() {
  rm -rf "$TMP"
}

# record <findings> <validators> <verdicts-json> [head]: writes the record file; BASE, SLICES, AGENTS, MODE and CLEAN override its defaults.
record() {
  local ids="" i
  for ((i = 1; i <= $2; i++)); do ids="${ids:+$ids, }\"agent-$i\""; done
  cat >"$REC" <<JSON
{"head": "${4:-$HEAD_SHA}", "base": "${BASE:-$ZERO_SHA}", "clean": ${CLEAN:-true}, "mode": "${MODE:-agents}", "slices": ${SLICES:-3}, "sweep_agents": ${AGENTS:-6},
 "validators": $2, "validator_ids": [$ids], "findings": $1, "verdicts": $3}
JSON
}

drop_field() {
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); del d[sys.argv[2]]; json.dump(d, open(sys.argv[1], "w"))' "$REC" "$1"
}

check() {
  cd "$REPO" || return 1
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$REC" --head "${1:-$HEAD_SHA}" ${EXPECT_BASE:+--base "$EXPECT_BASE"}
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
  drop_field validator_ids
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
  [ "$stderr" = "ERROR: $REC: head '--upload-pack=x' is not a full 40-character SHA" ]
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
  drop_field clean
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: missing field clean" ]
}

@test "every verdict the checker accepts is a row of the skill's verdict table" {
  local verdicts v count=0
  verdicts=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import sweep_record; print(*sweep_record.VERDICTS, sep="\n")' "$ROOT/lib")
  while IFS= read -r v; do
    grep -qF "| \`$v\` |" "$ROOT/skills/finalize-sweep/SKILL.md"
    count=$((count + 1))
  done <<<"$verdicts"
  # shellcheck disable=SC2016 # Backticks in the grep pattern are literal.
  [ "$count" -eq "$(grep -c '^| `[A-Z_]*` |' "$ROOT/skills/finalize-sweep/SKILL.md")" ]
}

@test "duplicate validator ids fail" {
  record 5 2 '{"FALSE": 5}'
  sed -i.bak 's/"agent-2"/"agent-1"/' "$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: validator_ids repeat an id" ]
}

@test "a base that is not a full SHA fails" {
  BASE=abc123 record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: base 'abc123' is not a full 40-character SHA" ]
}

@test "a base git cannot resolve fails" {
  BASE=fedcba9876543210fedcba9876543210fedcba98 record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: git cannot resolve fedcba9876543210fedcba9876543210fedcba98" ]
}

@test "a base that is not an ancestor of the swept head fails" {
  BASE=$HEAD_SHA record 5 1 '{"FALSE": 5}' "$FIRST_SHA"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: base $HEAD_SHA is not an ancestor of the swept $FIRST_SHA" ]
}

@test "--base must equal the record's base" {
  record 5 1 '{"FALSE": 5}'
  EXPECT_BASE=$HEAD_SHA check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: base is $ZERO_SHA, expected $HEAD_SHA" ]
}

@test "--base equal to the record's base passes" {
  record 5 1 '{"FALSE": 5}'
  EXPECT_BASE=$ZERO_SHA check
  [ "$status" -eq 0 ]
}

@test "a sweep with zero slices fails" {
  SLICES=0 record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: slices must be at least 1" ]
}

@test "a sweep with zero sweep agents fails" {
  AGENTS=0 record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: sweep_agents must be at least 1" ]
}

@test "a verdict key holding a newline still gives one stderr line" {
  record 5 1 '{"PENDING\nSECOND": 5}'
  check
  [ "$status" -eq 1 ]
  [ "${#lines[@]}" -eq 0 ]
  [ "$stderr" = "ERROR: $REC: unknown verdict PENDING\\nSECOND" ]
}

@test "a --head that is not a full SHA blames the argument, not the record" {
  record 5 1 '{"FALSE": 5}'
  check abc123
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: --head 'abc123' is not a full 40-character SHA" ]
}

@test "a --base that is not a full SHA blames the argument, not the record" {
  record 5 1 '{"FALSE": 5}'
  EXPECT_BASE=abc123 check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: --base 'abc123' is not a full 40-character SHA" ]
}

@test "a failed git log fails instead of passing with no commits" {
  local real
  real=$(command -v git)
  mkdir "$TMP/bin"
  # shellcheck disable=SC2016 # The fake git expands its own $1 and "$@".
  printf '#!/bin/sh\n[ "$1" = log ] && exit 1\nexec %s "$@"\n' "$real" >"$TMP/bin/git"
  chmod +x "$TMP/bin/git"
  record 4 1 '{"FALSE": 4}' "$FIRST_SHA"
  PATH="$TMP/bin:$PATH" check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: git log $FIRST_SHA..$HEAD_SHA failed" ]
}

@test "a serial record with findings passes and says there is no independent validator" {
  MODE=serial AGENTS=0 record 5 0 '{"FALSE": 5}'
  check
  [ "$status" -eq 0 ]
  [ "$output" = "OK: $REC covers $HEAD_SHA; serial: no independent validator" ]
}

@test "a serial record in the ancestor case says it on the first line" {
  MODE=serial AGENTS=0 record 5 0 '{"FALSE": 5}' "$FIRST_SHA"
  check
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "OK: $REC swept $FIRST_SHA; serial: no independent validator; commits since:" ]
}

@test "a serial record that counts sweep agents fails" {
  MODE=serial record 5 0 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: a serial record has no sweep agents, validators or validator ids" ]
}

@test "a serial record that counts a validator fails" {
  MODE=serial AGENTS=0 record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: a serial record has no sweep agents, validators or validator ids" ]
}

@test "a serial record with zero slices fails" {
  MODE=serial AGENTS=0 SLICES=0 record 5 0 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: slices must be at least 1" ]
}

@test "a mode other than agents or serial fails" {
  MODE=parallel record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: mode must be agents or serial" ]
}

@test "a record without the mode field fails" {
  record 5 1 '{"FALSE": 5}'
  drop_field mode
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: missing field mode" ]
}

@test "a serial record that lists a validator id fails" {
  MODE=serial AGENTS=0 record 5 0 '{"FALSE": 5}'
  sed -i.bak 's/"validator_ids": \[\]/"validator_ids": ["agent-1"]/' "$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: a serial record has no sweep agents, validators or validator ids" ]
}

@test "fewer than two sweep agents per slice fail" {
  SLICES=25 AGENTS=1 record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: 1 sweep_agents for 25 slices; each slice starts two" ]
}

@test "more sweep agents than two per slice pass" {
  SLICES=3 AGENTS=8 record 5 1 '{"FALSE": 5}'
  check
  [ "$status" -eq 0 ]
}

@test "a negative verdict count fails" {
  record 64 1 '{"FALSE": 65, "NOT_WORTH_IT": -1}'
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: verdicts must map each verdict to a count" ]
}

@test "a record that is not a JSON object fails" {
  printf '[1, 2]' >"$REC"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: the record must be a JSON object" ]
}

@test "a record path that cannot be read says so, not bad JSON" {
  mkdir "$REC"
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: $REC: cannot read: "* ]]
}

@test "a record whose head is its base swept nothing and fails" {
  BASE=$FIRST_SHA record 0 0 '{}' "$FIRST_SHA"
  check
  [ "$status" -eq 1 ]
  [ "$stderr" = "ERROR: $REC: head equals base; the sweep covered an empty diff" ]
}

@test "an unknown flag fails with one ERROR line and exit 1" {
  cd "$REPO" || return 1
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$REC" --head "$HEAD_SHA" --bogus
  [ "$status" -eq 1 ]
  [ "$output" = "" ]
  [ "$stderr" = "ERROR: unrecognized arguments: --bogus" ]
}

@test "a missing --head fails with one ERROR line and exit 1" {
  cd "$REPO" || return 1
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$REC"
  [ "$status" -eq 1 ]
  [ "$output" = "" ]
  [ "$stderr" = "ERROR: the following arguments are required: --head" ]
}

@test "an unknown subcommand fails with one ERROR line and exit 1" {
  cd "$REPO" || return 1
  run --separate-stderr "$ROOT/orch-sweep-record" bogus
  [ "$status" -eq 1 ]
  [ "${#lines[@]}" -eq 0 ]
  [[ "$stderr" == "ERROR: "* ]]
  [ "$(printf '%s\n' "$stderr" | wc -l)" -eq 1 ]
}

@test "a missing --record fails with one ERROR line and exit 1" {
  cd "$REPO" || return 1
  run --separate-stderr "$ROOT/orch-sweep-record" check --head "$HEAD_SHA"
  [ "$status" -eq 1 ]
  [ "$output" = "" ]
  [ "$stderr" = "ERROR: the following arguments are required: --record" ]
}

@test "a control character in a usage error is escaped" {
  cd "$REPO" || return 1
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$REC" --head "$HEAD_SHA" $'--bo\ngus'
  [ "$status" -eq 1 ]
  [ "$stderr" = 'ERROR: unrecognized arguments: --bo\ngus' ]
}
