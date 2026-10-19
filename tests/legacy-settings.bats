#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031 # Bats runs each test in a subshell.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/legacy-settings-bats.XXXXXX")
  export HOME="$TMP/home"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  mkdir -p "$HOME" "$TMP/repo/app"
  : >"$ORCH_CONFIG"
  git -C "$TMP/repo" init -q
}

teardown() {
  rm -rf "$TMP"
}

classify_after_config() {
  (
    . "$ROOT/coordinator/watchers/coord-lib.sh"
    orch_load_config
    orch_load_repo_config "$TMP/repo/app"
    orch_export_config
    # shellcheck disable=SC2016 # Python source, not shell.
    cd "$ROOT/coordinator/watchers" && python3 -c 'import os, legacy_rules; r = legacy_rules.from_settings(os.environ); print(r and (r.classify("app/a.rb", "file-name"), r.classify("app/a.rb", "env-name"), r.classify("vendor/x.rb", "file-name")))'
  )
}

@test "rules set only in the repo file reach the scan" {
  printf '[legacy]\nexempt = ["vendor/**"]\n[[legacy.rules]]\nkind = "OLD"\npattern = "file-name"\npaths = ["**"]\n' >"$TMP/repo/.orchestration.toml"

  run classify_after_config

  [ "$status" -eq 0 ]
  [ "$output" = "('OLD', None, None)" ]
}

@test "exported rules override the repo file" {
  printf '[[legacy.rules]]\nkind = "OLD"\npattern = "file-name"\npaths = ["**"]\n' >"$TMP/repo/.orchestration.toml"
  export ORCH_LEGACY_RULES='[{"kind":"ENV","pattern":"env-name","paths":["**"]}]'

  run classify_after_config

  [ "$status" -eq 0 ]
  [ "$output" = "(None, 'ENV', None)" ]
}

@test "exported empty rules turn the rules of the repo file off" {
  printf '[[legacy.rules]]\nkind = "OLD"\npattern = "file-name"\npaths = ["**"]\n' >"$TMP/repo/.orchestration.toml"
  export ORCH_LEGACY_RULES=""

  run classify_after_config

  [ "$status" -eq 0 ]
  [ "$output" = "None" ]
}
