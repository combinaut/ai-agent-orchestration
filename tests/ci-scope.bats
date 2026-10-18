#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

setup() {
  BIN=$(mktemp -d "${TMPDIR:-/tmp}/ci-scope.XXXXXX")
  cat >"$BIN/gh" <<'STUB'
#!/bin/sh
[ -n "$GH_FAIL" ] && exit 1
while [ "$1" != --jq ]; do shift; done
printf '%s' "$GH_JSON" | jq -r "$2"
STUB
  chmod +x "$BIN/gh"
  export PATH="$BIN:$PATH" GITHUB_REPOSITORY=o/r PR=1 EVENT=pull_request GH_FAIL='' GH_JSON='[]'
}

teardown() { rm -rf "$BIN"; }

scope() { run /bin/bash "$BATS_TEST_DIRNAME/../.github/scripts/macos-scope.sh"; }

@test "docs-only changes skip macOS" {
  GH_JSON='[{"filename":"README.md"},{"filename":"docs/a.sh"},{"filename":"LICENSE"}]' scope
  [ "$status" -eq 0 ]
  [ "$output" = macos=false ]
}

@test "a code file runs macOS" {
  GH_JSON='[{"filename":"README.md"},{"filename":"install.sh"}]' scope
  [ "$output" = macos=true ]
}

@test "a rename from code into docs runs macOS" {
  GH_JSON='[{"filename":"docs/moved.md","previous_filename":"install.sh"}]' scope
  [ "$output" = macos=true ]
}

@test "an empty file list runs macOS" {
  scope
  [ "$output" = macos=true ]
}

@test "a failing file listing runs macOS" {
  GH_FAIL=1 scope
  [ "$status" -eq 0 ]
  [ "$output" = macos=true ]
}

@test "pushes run macOS" {
  EVENT=push GH_JSON='[{"filename":"README.md"}]' scope
  [ "$output" = macos=true ]
}
