#!/usr/bin/env bats
# shellcheck disable=SC2154,SC2030,SC2031,SC2016 # bats provides status and output after run; exports stay inside each test.

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
  export ORCH_INSTALLER_URL="file://$WORK/install.sh"
  unset ORCH_REPO_PATH
  git init -q --bare "$ORIGIN"
  git -C "$ORIGIN" symbolic-ref HEAD refs/heads/main
  git init -q "$WORK"
  git -C "$WORK" checkout -q -b main
  cat >"$WORK/install.sh" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = --print-repo-path ]; then
  echo "${STUB_REPO_PATH:-$HOME/.claude/ai-agent-orchestration}"
  exit 0
fi
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

@test "a second run reuses the clone and runs its install.sh again" {
  /bin/bash "$ROOT/get.sh" >/dev/null
  rm "$STUB_OUT"
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  [ -f "$STUB_OUT" ]
  [ "$(git -C "$DIR" rev-parse HEAD)" = "$(git -C "$WORK" rev-parse HEAD)" ]
}

@test "the clone is not updated by get.sh, install.sh does that" {
  /bin/bash "$ROOT/get.sh" >/dev/null
  before=$(git -C "$DIR" rev-parse HEAD)
  printf 'new\n' >"$WORK/NEW"
  commit_work second
  git -C "$WORK" push -q "$ORIGIN" main
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  [ "$(git -C "$DIR" rev-parse HEAD)" = "$before" ]
}

@test "a foreign upstream is never followed" {
  /bin/bash "$ROOT/get.sh" >/dev/null
  git clone -q "$ORIGIN" "$TMP/foreign"
  printf '#!/usr/bin/env bash\necho STUB=FOREIGN\n' >"$TMP/foreign/install.sh"
  git -C "$TMP/foreign" -c user.email=t@example.invalid -c user.name=t commit -qam foreign
  git -C "$DIR" remote add foreign "$TMP/foreign"
  git -C "$DIR" fetch -q foreign
  git -C "$DIR" branch -q -u foreign/main
  before=$(git -C "$DIR" rev-parse HEAD)
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  if contains "$output" FOREIGN; then false; fi
  [ "$(git -C "$DIR" rev-parse HEAD)" = "$before" ]
}

@test "the clone folder comes from install.sh when ORCH_INSTALL_DIR is unset" {
  unset ORCH_INSTALL_DIR
  export STUB_REPO_PATH="$TMP/configured/clone"
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  [ -f "$STUB_REPO_PATH/install.sh" ]
  [ ! -e "$HOME/.claude/ai-agent-orchestration" ]
  contains "$(cat "$STUB_OUT")" "repo=$STUB_REPO_PATH"
}

@test "an existing configured clone is reused, no second clone appears" {
  unset ORCH_INSTALL_DIR
  export STUB_REPO_PATH="$TMP/configured/clone"
  /bin/bash "$ROOT/get.sh" >/dev/null
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -eq 0 ]
  contains "$output" "stub install ran"
  if contains "$output" "cloned"; then false; fi
  [ ! -e "$HOME/.claude/ai-agent-orchestration" ]
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

@test "a download cut off inside the final invocation runs nothing" {
  last=$(tail -n 1 "$ROOT/get.sh")
  body=$(sed '$d' "$ROOT/get.sh")
  n=${#last}
  i=1
  while [ "$i" -lt "$n" ]; do
    printf '%s\n%s' "$body" "${last:0:i}" >"$TMP/cut.sh"
    run /bin/bash "$TMP/cut.sh" --links-only
    [ ! -e "$DIR" ]
    [ ! -e "$STUB_OUT" ]
    i=$((i + 1))
  done
}

@test "the documented command runs nothing when curl fails part way" {
  mkdir "$TMP/curlbin"
  printf '#!/bin/sh\nhead -n 20 "%s"\nexit 18\n' "$ROOT/get.sh" >"$TMP/curlbin/curl"
  chmod +x "$TMP/curlbin/curl"
  run env PATH="$TMP/curlbin:$PATH" /bin/bash -c 's=$(curl -fsSL https://example.invalid/get.sh) && bash -c "${s:?empty download}" _ --links-only'
  [ "$status" -ne 0 ]
  [ ! -e "$DIR" ]
  [ ! -e "$STUB_OUT" ]
}

@test "a failed installer download stops before cloning" {
  unset ORCH_INSTALL_DIR
  export ORCH_INSTALLER_URL="file://$TMP/missing.sh"
  run /bin/bash "$ROOT/get.sh"
  [ "$status" -ne 0 ]
  contains "$output" "cannot download install.sh"
  [ ! -e "$HOME/.claude" ]
}
