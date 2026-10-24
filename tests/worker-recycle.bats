#!/usr/bin/env bats
setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd)
  export ORCH_WORKER_SOFT_TOKENS=300000 ORCH_WORKER_HARD_TOKENS=450000 ORCH_WORKER_ASK_TIMEOUT_S=600
}
policy() { printf '%s' "$1" | python3 "$ROOT/coordinator/watchers/worker_recycle.py"; }
@test "FINISH under hard waits for done" {
  run policy '{"value":{"tokens":310000,"window":1000000},"answer":"FINISH 100000 type check"}'
  [ "$output" = WAIT_DONE ]
}
@test "FINISH over hard orders next clean point" {
  run policy '{"value":{"tokens":310000,"window":1000000},"answer":"FINISH 150000 type check"}'
  [ "$output" = HANDOFF_CLEAN_POINT ]
}
@test "silence defers re-ask until idle, never recycles" {
  run policy '{"value":{"tokens":310000,"window":1000000},"elapsed":600}'
  [ "$output" = REASK_WHEN_IDLE ]
  run policy '{"value":{"tokens":310000,"window":1000000},"elapsed":600,"idle":true}'
  [ "$output" = ASK ]
}
@test "live heavy slot blocks even hard and NOW until release" {
  run policy '{"value":{"tokens":450000},"slot":true,"answer":"NOW"}'
  [ "$output" = WAIT_SLOT ]
  run policy '{"value":{"tokens":450000}}'
  [ "$output" = HANDOFF_CALL_BOUNDARY ]
  run policy '{"value":{"tokens":320000,"window":1000000},"slot":true,"hard_pending":true}'
  [ "$output" = WAIT_SLOT ]
  run policy '{"value":{"tokens":320000,"window":1000000},"hard_pending":true}'
  [ "$output" = HANDOFF_CALL_BOUNDARY ]
}
@test "small model window clamps hard before soft" {
  run policy '{"value":{"tokens":120000,"window":200000}}'
  [ "$output" = HANDOFF_CALL_BOUNDARY ]
}
@test "equal hard and soft forces handoff at crossing" {
  export ORCH_WORKER_HARD_TOKENS=300000
  run policy '{"value":{"tokens":300000,"window":1000000}}'
  [ "$output" = HANDOFF_CALL_BOUNDARY ]
}
@test "NOW hands off and malformed estimates ask again" {
  run policy '{"value":{"tokens":300000,"window":1000000},"answer":"NOW"}'
  [ "$output" = HANDOFF ]
  run policy '{"value":{"tokens":300000,"window":1000000},"answer":"FINISH 10 checks"}'
  [ "$output" = WAIT_DONE ]
  run policy '{"value":{"tokens":300000,"window":1000000},"answer":"FINISH ten fix"}'
  [ "$output" = ASK_VALID_REPLY ]
}
@test "missing model windows do not clamp and Codex reads its cache" {
  model_home=$(mktemp -d)
  for model in claude-haiku-5-5 claude-sonnet-5-5 claude-opus-5-5 claude-fable-5-1 gpt-5.6-terra gpt-6.1-sol; do
    run env HOME="$model_home" python3 "$ROOT/coordinator/watchers/worker_recycle.py" <<<"{\"value\":{\"model\":\"$model\",\"tokens\":120000}}"
    [ "$output" = ASK ]
  done
  mkdir -p "$model_home/.codex"
  printf '%s\n' '{"models":[{"slug":"gpt-6.1-sol","context_window":1000000}]}' >"$model_home/.codex/models_cache.json"
  run env HOME="$model_home" python3 "$ROOT/coordinator/watchers/worker_recycle.py" <<<'{"value":{"model":"gpt-6.1-sol","tokens":120000}}'
  [ "$output" = ASK ]
  run env HOME="$model_home" python3 "$ROOT/coordinator/watchers/worker_recycle.py" <<<'{"value":{"model":"gpt-6.1-sol","tokens":120000,"window":200000}}'
  [ "$output" = HANDOFF_CALL_BOUNDARY ]
  rm -rf "$model_home"
}
@test "percentage fallback honors configured hard below sixty percent" {
  run policy '{"value":{"remaining":55,"window":1000000}}'
  [ "$output" = HANDOFF_CALL_BOUNDARY ]
}
@test "decision CLI loads configured thresholds" {
  config_file=$(mktemp)
  printf '%s\n' '[recycle]' 'worker_soft_tokens = 100' 'worker_hard_tokens = 200' >"$config_file"
  run env -u ORCH_WORKER_SOFT_TOKENS -u ORCH_WORKER_HARD_TOKENS ORCH_CONFIG="$config_file" python3 "$ROOT/coordinator/watchers/worker_recycle.py" <<<'{"value":{"tokens":200,"window":1000000}}'
  [ "$output" = HANDOFF_CALL_BOUNDARY ]
  rm -f "$config_file"
}
@test "pending unanswered ask waits until timeout" {
  run policy '{"value":{"tokens":310000,"window":1000000},"elapsed":300}'
  [ "$output" = WAIT_REPLY ]
  run policy '{"value":{"tokens":310000,"window":1000000},"elapsed":0,"pending":true}'
  [ "$output" = WAIT_REPLY ]
}

@test "known window percentage sensor reaches soft before hard" {
  run env PYTHONPATH="$ROOT/coordinator/watchers" python3 -c 'import worker_recycle as r; print(r.stage({"remaining":69,"window":1000000}))'
  [ "$output" = SOFT ]
}
@test "FINISH uses current consumption above soft" {
  run policy '{"value":{"tokens":420000,"window":1000000},"answer":"FINISH 100000 checks"}'
  [ "$output" = HANDOFF_CLEAN_POINT ]
}
@test "NOW accepts case and punctuation and FINISH accepts k tokens" {
  for answer in now NOW. nOw.; do
    run policy "{\"value\":{\"tokens\":300000,\"window\":1000000},\"answer\":\"$answer\"}"
    [ "$output" = HANDOFF ]
  done
  run policy '{"value":{"tokens":300000,"window":1000000},"answer":"FINISH 100k checks"}'
  [ "$output" = WAIT_DONE ]
}
@test "FINISH exact hard boundary requires handoff" {
  run policy '{"value":{"tokens":300000,"window":1000000},"answer":"FINISH 150000 checks"}'
  [ "$output" = HANDOFF_CLEAN_POINT ]
}
@test "unknown window hard cannot precede soft" {
  run env ORCH_WORKER_HARD_TOKENS=100000 python3 "$ROOT/coordinator/watchers/worker_recycle.py" <<<'{"value":{"tokens":180000}}'
  [ "$output" = ASK ]
}
