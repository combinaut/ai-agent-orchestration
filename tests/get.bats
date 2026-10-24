#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/ao-get-bats.XXXXXX")
  export HOME="$TMP/home"
  mkdir -p "$HOME"
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=gc.auto GIT_CONFIG_VALUE_0=0
  ORIGIN="$TMP/origin.git"
  WORK="$TMP/work"
  DIR="$TMP/clone"
  export ORCH_INSTALL_DIR="$DIR" ORCH_REPO_URL="file://$ORIGIN"
  unset ORCH_REPO_PATH
  git init -q --bare "$ORIGIN"
  git -C "$ORIGIN" symbolic-ref HEAD refs/heads/main
  git init -q "$WORK"
  git -C "$WORK" checkout -q -b main
  cat >"$WORK/install.sh" <<'STUB'
#!/usr/bin/env bash
printf 'args=%s\nrepo=%s\npwd=%s\n' "$*" "$ORCH_REPO_PATH" "$PWD" >"${STUB_OUT:?}"
echo "stub install ran"
STUB
  export STUB_OUT="$TMP/stub.out"
  commit_work stub
  git -C "$WORK" push -q "$ORIGIN" main
}

teardown() { rm -rf "$TMP"; }

commit_work() {
  git -C "$WORK" add -A
  git -C "$WORK" -c user.email=test@example.invalid -c user.name=test commit -qm "$1"
}

contains() { printf '%s' "$1" | grep -F -- "$2" >/dev/null; }

@test "fresh run clones into ORCH_INSTALL_DIR and runs its install.sh" {
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  [ -f "$DIR/install.sh" ]
  contains "$output" "cloned"
  contains "$output" "stub install ran"
  contains "$(cat "$STUB_OUT")" "repo=$DIR"
}

@test "the default folder is ~/.claude/ai-agent-orchestration" {
  unset ORCH_INSTALL_DIR
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  [ -f "$HOME/.claude/ai-agent-orchestration/install.sh" ]
}

@test "a second run fast-forwards the clone and runs install.sh again" {
  /bin/bash "$ROOT/get.sh" >/dev/null
  printf 'new\n' >"$WORK/NEW"
  commit_work second
  git -C "$WORK" push -q "$ORIGIN" main
  rm "$STUB_OUT"
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  contains "$output" "updated"
  [ -f "$DIR/NEW" ]
  [ -f "$STUB_OUT" ]
}

@test "a second run with nothing new reports up to date" {
  /bin/bash "$ROOT/get.sh" >/dev/null
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  contains "$output" "up to date"
}

@test "a clone with uncommitted changes is refused and install.sh does not run" {
  /bin/bash "$ROOT/get.sh" >/dev/null
  printf 'mine\n' >>"$DIR/install.sh"
  rm "$STUB_OUT"
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -ne 0 ]
  contains "$output" "REFUSED"
  contains "$output" "uncommitted"
  [ ! -e "$STUB_OUT" ]
}

@test "a clone of another repo is refused" {
  git init -q "$DIR"
  git -C "$DIR" remote add origin https://example.invalid/other/repo.git
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -ne 0 ]
  contains "$output" "not $(printf '%s' combinaut/ai-agent-orchestration)"
  [ ! -e "$STUB_OUT" ]
}

@test "a folder that is not a git clone is refused" {
  mkdir -p "$DIR"
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -ne 0 ]
  contains "$output" "not a git clone"
}

@test "arguments reach install.sh, also when the script is piped to bash" {
  run /bin/bash -c 'cat "$1" | /bin/bash -s -- --env herdr --links-only' _ "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  contains "$(cat "$STUB_OUT")" "args=--env herdr --links-only"
}

@test "a missing git stops with a clear message" {
  mkdir "$TMP/nogit"
  for tool in bash uname dirname mkdir; do ln -s "$(command -v $tool)" "$TMP/nogit/$tool"; done
  run env PATH="$TMP/nogit" /bin/bash "$ROOT/get.sh"
  [ "$status" -ne 0 ]
  contains "$output" "git not found"
}

@test "an unsupported OS stops with a clear message" {
  mkdir "$TMP/fakeos"
  printf '#!/bin/sh\necho Plan9\n' >"$TMP/fakeos/uname"
  chmod +x "$TMP/fakeos/uname"
  run env PATH="$TMP/fakeos:$PATH" /bin/bash "$ROOT/get.sh"
  [ "$status" -ne 0 ]
  contains "$output" "unsupported platform: Plan9"
  [ ! -e "$DIR" ]
}

@test "a truncated download runs nothing" {
  run /bin/bash -c 'head -n "$(($(grep -n "^main()" "$1" | cut -d: -f1) + 2))" "$1" | /bin/bash' _ "$ROOT/get.sh"
  [ ! -e "$DIR" ]
  [ ! -e "$STUB_OUT" ]
}
