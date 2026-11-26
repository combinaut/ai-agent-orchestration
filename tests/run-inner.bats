#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

load test_helper

@test "the suite stops with an error when node is missing" {
  mkdir -p "$TMP/nonode"
  ln -s "$(command -v dirname)" "$TMP/nonode/dirname"
  run env PATH="$TMP/nonode" /bin/bash "$ROOT/tests/run-inner.sh"
  [ "$status" -ne 0 ]
  contains "$output" 'node not found'
}
