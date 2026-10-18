#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031 # Bats runs each test in a subshell.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/legacy-settings-bats.XXXXXX")
  export HOME="$TMP/home"
  export ORCH_CONFIG="$TMP/orchestration.env"
  mkdir -p "$HOME"
}

teardown() {
  rm -rf "$TMP"
}

classify_after_config() {
  (
    . "$ROOT/coordinator/watchers/coord-lib.sh"
    orch_load_config
    orch_export_config
    # shellcheck disable=SC2016 # Python source, not shell.
    cd "$ROOT/coordinator/watchers" && python3 -c 'import audit; print(audit.classify("app/a.rb", "file-name"), audit.classify("app/a.rb", "env-name"), audit.classify("scripts/x.js", "$(x)"))'
  )
}

@test "a legacy pattern set only in the config file reaches the scan" {
  printf 'ORCH_LEGACY_EXTRA_PATTERN="file-name"\n' >"$ORCH_CONFIG"

  run classify_after_config

  [ "$status" -eq 0 ]
  [ "$output" = "JQUERY None JQUERY" ]
}

@test "an exported legacy pattern overrides the config file" {
  printf 'ORCH_LEGACY_EXTRA_PATTERN="file-name"\n' >"$ORCH_CONFIG"
  export ORCH_LEGACY_EXTRA_PATTERN="env-name"

  run classify_after_config

  [ "$status" -eq 0 ]
  [ "$output" = "None JQUERY JQUERY" ]
}

@test "an exported empty legacy pattern overrides the config file" {
  printf 'ORCH_LEGACY_EXTRA_PATTERN="file-name"\n' >"$ORCH_CONFIG"
  export ORCH_LEGACY_EXTRA_PATTERN=""

  run classify_after_config

  [ "$status" -eq 0 ]
  [ "$output" = "None None JQUERY" ]
}
