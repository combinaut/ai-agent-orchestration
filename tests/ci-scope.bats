#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

setup() {
  BIN=$(mktemp -d "${TMPDIR:-/tmp}/ci-scope.XXXXXX")
  cat >"$BIN/gh" <<'STUB'
#!/bin/sh
[ -n "$GH_FAIL" ] && exit 1
while [ "$1" != --jq ]; do shift; done
if [ -n "$GH_JSON_FILE" ]; then jq -r "$2" <"$GH_JSON_FILE"; else printf '%s' "$GH_JSON" | jq -r "$2"; fi
STUB
  chmod +x "$BIN/gh"
  export PATH="$BIN:$PATH" GITHUB_REPOSITORY=o/r PR=1 EVENT=pull_request GH_FAIL='' GH_JSON='[]' GH_JSON_FILE=''
}

teardown() { rm -rf "$BIN"; }

scope() { run /bin/bash "$BATS_TEST_DIRNAME/../scripts/ci/macos-scope.sh"; }

@test "docs-only changes skip macOS" {
  GH_JSON='[{"filename":"README.md"},{"filename":"docs/a.sh"},{"filename":"LICENSE"}]' scope
  [ "$status" -eq 0 ]
  [ "$output" = macos=false ]
}

@test "workflow-only changes skip macOS" {
  GH_JSON='[{"filename":".github/workflows/test.yml"}]' scope
  [ "$output" = macos=false ]
}

@test "other GitHub files skip macOS" {
  GH_JSON='[{"filename":".github/dependabot.yml"}]' scope
  [ "$output" = macos=false ]
}

@test "validate workflow changes run macOS" {
  GH_JSON='[{"filename":".github/workflows/validate.yml"}]' scope
  [ "$output" = macos=true ]
}

@test "docs and a workflow skip macOS" {
  GH_JSON='[{"filename":"docs/a.md"},{"filename":".github/workflows/test.yml"}]' scope
  [ "$output" = macos=false ]
}

@test "a workflow and code run macOS" {
  GH_JSON='[{"filename":".github/workflows/test.yml"},{"filename":"install.sh"}]' scope
  [ "$output" = macos=true ]
}

@test "a rename from validate workflow runs macOS" {
  GH_JSON='[{"filename":".github/workflows/test.yml","previous_filename":".github/workflows/validate.yml"}]' scope
  [ "$output" = macos=true ]
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

@test "a code file ahead of a large documentation list runs macOS" {
  GH_JSON_FILE="$BIN/files.json"
  { printf '[{"filename":"install.sh"}'; seq 1 20000 | sed 's|.*|,{"filename":"docs/page-&.md"}|'; printf ']'; } >"$GH_JSON_FILE"
  export GH_JSON_FILE
  scope
  [ "$output" = macos=true ]
}

@test "a listing that reaches the 3000-file API cap runs macOS" {
  GH_JSON_FILE="$BIN/files.json"
  { printf '[{"filename":"docs/page-0.md"}'; seq 1 2999 | sed 's|.*|,{"filename":"docs/page-&.md"}|'; printf ']'; } >"$GH_JSON_FILE"
  export GH_JSON_FILE
  scope
  [ "$output" = macos=true ]
}
