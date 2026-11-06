#!/usr/bin/env bats

@test "CI sweep gate rejects stale, untrusted and malformed records" {
  run python3 "$BATS_TEST_DIRNAME/test_sweep_ci.py"
  [ "$status" -eq 0 ]
}

@test "each CI sweep guard has a unique mutation kill" {
  run python3 "$BATS_TEST_DIRNAME/sweep-ci-mutations.py"
  [ "$status" -eq 0 ]
}
