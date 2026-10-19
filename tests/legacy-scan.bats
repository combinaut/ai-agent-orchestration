#!/usr/bin/env bats
# shellcheck disable=SC2154,SC2016 # bats provides status and output; stub bodies stay literal.
load test_helper

setup() {
  setup_fixture
  SCAN="$ROOT/coordinator/legacy-scan.sh"
  printf '#!/bin/sh\necho "HTTP 502" >&2\nexit 1\n' >"$BIN/gh"
  chmod +x "$BIN/gh"
  : >"$TMP/no-config"
  export ORCH_CONFIG="$TMP/no-config"
}

@test "a failed PR read reports the reason" {
  run "$SCAN" 7 owner/product
  [ "$status" -eq 1 ]
  contains "$output" 'legacy-scan failed: HTTP 502'
}

@test "started with zsh or sh it runs under bash" {
  for shell in zsh sh; do
    run "$shell" "$SCAN" 7 owner/product
    [ "$status" -eq 1 ]
    contains "$output" 'legacy-scan failed: HTTP 502'
  done
}
