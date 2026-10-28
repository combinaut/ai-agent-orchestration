#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031,SC2154 # Bats runs each test in a subshell and assigns status and output.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  SCRIPT="$ROOT/coordinator/codex-start.sh"
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/codex-start-bats.XXXXXX")
  export HOME="$TMP/home"
  export PATH="$TMP/bin:$PATH"
  export HERDR_CALLS="$TMP/herdr.calls"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  unset ORCH_CODEX_WORKER_MODEL ORCH_CODEX_SMART_MODEL
  mkdir -p "$HOME" "$TMP/bin"
  printf '[models]\ncodex_worker = "worker-model"\ncodex_smart = "smart-model"\n' >"$ORCH_CONFIG"
  cat >"$TMP/bin/herdr" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >>"$HERDR_CALLS"
SH
  chmod +x "$TMP/bin/herdr"
}

teardown() {
  rm -rf "$TMP"
}

@test "the worker tier starts Codex with hook trust bypassed on the worker model" {
  run "$SCRIPT" w1 w1-p2 worker
  [ "$status" -eq 0 ]
  [ "$(cat "$HERDR_CALLS")" = "agent start w1 --kind codex --pane w1-p2 -- --no-daemon --dangerously-bypass-hook-trust --model worker-model" ]
}

@test "the smart tier uses the smart model at medium reasoning effort" {
  run "$SCRIPT" w1 w1-p2 smart
  [ "$status" -eq 0 ]
  [ "$(cat "$HERDR_CALLS")" = "agent start w1 --kind codex --pane w1-p2 -- --no-daemon --dangerously-bypass-hook-trust --model smart-model -c model_reasoning_effort=medium" ]
}

@test "the smart tier passes the configured reasoning effort" {
  printf '[models]\ncodex_smart = "smart-model"\ncodex_smart_reasoning_effort = "high"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 w1-p2 smart
  [ "$status" -eq 0 ]
  [ "$(cat "$HERDR_CALLS")" = "agent start w1 --kind codex --pane w1-p2 -- --no-daemon --dangerously-bypass-hook-trust --model smart-model -c model_reasoning_effort=high" ]
}

@test "the worker tier ignores the reasoning effort" {
  printf '[models]\ncodex_worker = "worker-model"\ncodex_smart_reasoning_effort = "high"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 w1-p2 worker
  [ "$status" -eq 0 ]
  [ "$(cat "$HERDR_CALLS")" = "agent start w1 --kind codex --pane w1-p2 -- --no-daemon --dangerously-bypass-hook-trust --model worker-model" ]
}

@test "a reasoning effort that is not one lowercase word is refused" {
  for bad in "high xhigh" "-x" "High" ""; do
    run env ORCH_CODEX_SMART_REASONING_EFFORT="$bad" "$SCRIPT" w1 w1-p2 smart
    [ "$status" -ne 0 ]
  done
  [ ! -e "$HERDR_CALLS" ]
}

@test "extra arguments are refused" {
  run "$SCRIPT" w1 w1-p2 worker -s danger-full-access
  [ "$status" -eq 2 ]
  [ ! -e "$HERDR_CALLS" ]
}

@test "a bad tier, name or pane id is refused" {
  for args in "w1 w1-p2 other" "-s w1-p2 worker" "W1 w1-p2 worker" "w1 p2;x worker" "w1 -p2 worker"; do
    # shellcheck disable=SC2086 # The word split is the test input.
    run "$SCRIPT" $args
    [ "$status" -eq 2 ]
  done
  [ ! -e "$HERDR_CALLS" ]
}

@test "an unset model is refused" {
  printf '[models]\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 w1-p2 worker
  [ "$status" -eq 1 ]
  [ ! -e "$HERDR_CALLS" ]
}

@test "codex off in providers.enabled is refused" {
  printf '[providers]\nenabled = ["claude"]\n[models]\ncodex_worker = "worker-model"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 w1-p2 worker
  [ "$status" -eq 1 ]
  [ ! -e "$HERDR_CALLS" ]
}
