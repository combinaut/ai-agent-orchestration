#!/usr/bin/env bats

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  HELPER="$ROOT/heavy-slot"
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/heavy-slot-bats.XXXXXX")
  export ORCH_HEAVY_SLOTS_DIR="$TMP/slots"
  export ORCH_HEAVY_SLOTS_MAX=4
  export TEST_ROOT="$TMP"
  export HOME="$TMP/home"
  TEST_HOME=
  mkdir -p "$ORCH_HEAVY_SLOTS_DIR" "$HOME"
}

teardown() {
  for pid in $(jobs -p); do kill "$pid" 2>/dev/null || true; done
  for pid in $(jobs -p); do wait "$pid" 2>/dev/null || true; done
  [ -z "$TEST_HOME" ] || rm -rf "$TEST_HOME"
  rm -rf "$TMP"
}

wait_for_file() {
  local file=$1 tries=0
  while { [ ! -e "$file" ] || [ ! -s "$file" ]; } && [ "$tries" -lt 100 ]; do sleep 0.05; tries=$((tries + 1)); done
  [ -s "$file" ]
}

run_with_timeout() {
  # shellcheck disable=SC2016 # Python receives this program literally.
  run python3 -c '
import subprocess
import sys

try:
    result = subprocess.run(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5)
except subprocess.TimeoutExpired as error:
    if error.stdout:
        sys.stdout.write(error.stdout)
    print("command timed out after 5 seconds", file=sys.stderr)
    raise SystemExit(124)

sys.stdout.write(result.stdout)
raise SystemExit(result.returncode)
' "$@"
}

@test "a missing configuration file keeps the default slot limit" {
  run env -u ORCH_HEAVY_SLOTS_MAX -u ORCH_CONFIG HOME="$TMP" "$HELPER" count
  [ "$status" -eq 0 ]
  [ "$output" = 0 ]
}

@test "an explicitly selected missing or non-file config fails even with an override" {
  for config in "$TMP/missing.env" "$TMP"; do
    run env ORCH_HEAVY_SLOTS_MAX=8 ORCH_CONFIG="$config" "$HELPER" count
    [ "$status" -eq 78 ]
    [ "$output" = "heavy-slot: cannot load $config: not a regular file" ]
  done
}

@test "a dangling configuration symlink fails loudly" {
  config="$TMP/dangling.env"
  ln -s "$TMP/missing-config-target" "$config"
  run env -u ORCH_HEAVY_SLOTS_MAX ORCH_CONFIG="$config" "$HELPER" count
  [ "$status" -eq 78 ]
  [ "$output" = "heavy-slot: cannot load $config: not a regular file" ]
}

@test "a valid configuration sets the slot limit" {
  config="$TMP/valid.toml"
  printf '[heavy_slots]\nmax = 6\n' >"$config"
  run env -u ORCH_HEAVY_SLOTS_MAX ORCH_CONFIG="$config" "$HELPER" count
  [ "$status" -eq 0 ]
  [ "$output" = 0 ]
}

@test "an unknown key in the configuration fails loudly" {
  config="$TMP/unknown.toml"
  printf '[heavy_slots]\nmaxx = 6\n' >"$config"
  run env -u ORCH_HEAVY_SLOTS_MAX ORCH_CONFIG="$config" "$HELPER" count
  [ "$status" -eq 78 ]
  [ "$output" = "heavy-slot: cannot load $config: heavy_slots.maxx: unknown key" ]
}

@test "a missing default configuration beside an old orchestration.env asks for install.sh" {
  mkdir -p "$HOME/.claude"
  printf 'ORCH_HEAVY_SLOTS_MAX="6"\n' >"$HOME/.claude/orchestration.env"
  run env -u ORCH_HEAVY_SLOTS_MAX -u ORCH_CONFIG "$HELPER" count
  [ "$status" -eq 78 ]
  [ "$output" = "heavy-slot: cannot load $HOME/.claude/orchestration.toml: missing; run install.sh to convert $HOME/.claude/orchestration.env" ]
}

@test "a missing relative nested configuration path terminates" {
  workdir="$TMP/relative-config"
  mkdir "$workdir"
  # shellcheck disable=SC2016 # The child Bash expands these positional parameters.
  run_with_timeout bash -c 'cd "$1" && exec env -u ORCH_HEAVY_SLOTS_MAX ORCH_CONFIG="$2" "$3" count' bash "$workdir" 'missing/nested/config.env' "$HELPER"
  [ "$status" -eq 78 ]
  [[ "$output" == *"not a regular file"* ]]
}

@test "a missing bare relative configuration path terminates" {
  workdir="$TMP/bare-config"
  mkdir "$workdir"
  # shellcheck disable=SC2016 # The child Bash expands these positional parameters.
  run_with_timeout bash -c 'cd "$1" && exec env -u ORCH_HEAVY_SLOTS_MAX ORCH_CONFIG="$2" "$3" count' bash "$workdir" 'config.env' "$HELPER"
  [ "$status" -eq 78 ]
  [[ "$output" == *"not a regular file"* ]]
}

@test "an unreadable configuration file fails loudly" {
  config="$TMP/unreadable.env"
  printf 'ORCH_HEAVY_SLOTS_MAX=8\n' >"$config"
  chmod 000 "$config"
  run env -u ORCH_HEAVY_SLOTS_MAX ORCH_CONFIG="$config" "$HELPER" count
  chmod 600 "$config"
  [ "$status" -eq 78 ]
  [ "$output" = "heavy-slot: cannot load $config: not readable" ]
}

@test "an unsearchable configuration parent fails loudly" {
  [ "$(id -u)" -ne 0 ] || skip "root can search an unreadable directory"
  parent="$TMP/unsearchable-config"
  config="$parent/orchestration.env"
  mkdir "$parent"
  chmod 000 "$parent"
  run env -u ORCH_HEAVY_SLOTS_MAX ORCH_CONFIG="$config" "$HELPER" count
  chmod 700 "$parent"
  [ "$status" -eq 78 ]
  [ "$output" = "heavy-slot: cannot load $config: not a regular file" ]
}

@test "an unsearchable nested configuration ancestor is not treated as missing" {
  [ "$(id -u)" -ne 0 ] || skip "root can search an unreadable directory"
  parent="$TMP/unsearchable-ancestor"
  config="$parent/missing/nested/orchestration.env"
  mkdir "$parent"
  chmod 000 "$parent"
  run env -u ORCH_HEAVY_SLOTS_MAX ORCH_CONFIG="$config" "$HELPER" count
  [ "$status" -eq 78 ]
  [ "$output" = "heavy-slot: cannot load $config: not a regular file" ]
  run env ORCH_HEAVY_SLOTS_MAX=8 ORCH_CONFIG="$config" "$HELPER" count
  chmod 700 "$parent"
  [ "$status" -eq 78 ]
  [ "$output" = "heavy-slot: cannot load $config: not a regular file" ]
}

@test "an explicit slot limit overrides an unreadable configuration file" {
  config="$TMP/unreadable-override.env"
  printf 'this is not shell syntax\n' >"$config"
  chmod 000 "$config"
  run env ORCH_HEAVY_SLOTS_MAX=8 ORCH_CONFIG="$config" "$HELPER" count
  chmod 600 "$config"
  [ "$status" -eq 0 ]
  [ "$output" = "heavy-slot: cannot load $config: not readable; using defaults
0" ]
}

@test "an explicit slot limit still reads the configuration and an exported slot directory wins" {
  config="$TMP/override.toml"
  configured_dir="$TMP/configured-slots"
  mkdir -p "$configured_dir"
  printf '[heavy_slots]\ndir = "%s"\n' "$configured_dir" >"$config"
  printf '%s\n' "$$" >"$configured_dir/agent-config"
  run env -u ORCH_HEAVY_SLOTS_DIR ORCH_HEAVY_SLOTS_MAX=8 ORCH_CONFIG="$config" "$HELPER" count
  [ "$status" -eq 0 ]
  [ "$output" = 1 ]
  run env ORCH_HEAVY_SLOTS_MAX=8 ORCH_CONFIG="$config" "$HELPER" count
  [ "$status" -eq 0 ]
  [ "$output" = 0 ]
}

@test "an explicit slot limit uses defaults for a syntax-broken configuration" {
  config="$TMP/syntax-broken.toml"
  configured_dir="$TMP/partially-configured-slots"
  TEST_HOME=$(mktemp -d "${TMPDIR:-/tmp}/heavy-slot-home.XXXXXX")
  mkdir -p "$configured_dir"
  printf '[heavy_slots]\ndir = "%s"\nif then\n' "$configured_dir" >"$config"
  printf '%s\n' "$$" >"$configured_dir/agent-config"
  run env -u ORCH_HEAVY_SLOTS_DIR HOME="$TEST_HOME" ORCH_HEAVY_SLOTS_MAX=8 ORCH_CONFIG="$config" "$HELPER" count
  [ "$status" -eq 0 ]
  [[ "$output" == "heavy-slot: cannot load $config: invalid TOML: "*"; using defaults
0" ]]
}

install_disappearing_mkdir() {
  mkdir "$TMP/bin"
  # shellcheck disable=SC2016 # The helper must receive these expressions literally.
  printf '%s\n' '#!/bin/sh' \
    'if [ "${1:-}" = "$TARGET_DIR" ] && [ "$(cat "$MARKER" 2>/dev/null || printf 0)" -lt "${RELEASES:-1}" ]; then' \
    '  [ -d "$TARGET_DIR" ] || /bin/mkdir "$TARGET_DIR"' \
    '  /bin/rmdir "$TARGET_DIR"' \
    '  count=$(cat "$MARKER" 2>/dev/null || printf 0)' \
    '  printf "%s" "$((count + 1))" >"$MARKER"' \
    '  exit 1' \
    'fi' \
    'exec /bin/mkdir "$@"' >"$TMP/bin/mkdir"
  chmod +x "$TMP/bin/mkdir"
}

install_failing_mkdir() {
  mkdir "$TMP/bin"
  # shellcheck disable=SC2016 # The helper must receive these expressions literally.
  printf '%s\n' '#!/bin/sh' \
    'case "${1:-}" in' \
    '  "$FAIL_ROOT"/.write-probe.*) [ ! -e "$MARKER" ] && { : >"$MARKER"; exec /bin/mkdir "$@"; }; exit 1 ;;' \
    '  "$FAIL_ROOT"/.lock|"$FAIL_ROOT"/.lock-reclaim) exit 1 ;;' \
    'esac' \
    'exec /bin/mkdir "$@"' >"$TMP/bin/mkdir"
  chmod +x "$TMP/bin/mkdir"
}

install_arriving_lock() {
  mkdir "$TMP/bin"
  # shellcheck disable=SC2016 # The helpers must receive these expressions literally.
  printf '%s\n' '#!/bin/sh' \
    'if [ "${1:-}" = "$TARGET_DIR" ] && [ ! -e "$MK_MARKER" ]; then : >"$MK_MARKER"; exit 1; fi' \
    'exec /bin/mkdir "$@"' >"$TMP/bin/mkdir"
  # shellcheck disable=SC2016 # The helper must receive these expressions literally.
  printf '%s\n' '#!/bin/sh' \
    'if [ "${1:-}" = "-d" ] && [ "${2:-}" = "$TARGET_DIR" ] && [ ! -e "$CHECK_MARKER" ]; then' \
    '  : >"$CHECK_MARKER"' \
    '  (/bin/mkdir "$TARGET_DIR"; : >"$ARRIVAL_MARKER"; sleep 0.1; /bin/rmdir "$TARGET_DIR"; : >"$RELEASE_MARKER") &' \
    '  exit 1' \
    'fi' \
    'if [ "${1:-}" = "-e" ] && [ "${2:-}" = "$TARGET_DIR" ] && [ ! -e "$E_MARKER" ]; then' \
    '  while [ ! -e "$RELEASE_MARKER" ]; do sleep 0.01; done' \
    '  : >"$E_MARKER"' \
    '  exit 0' \
    'fi' \
    'exec /bin/test "$@"' >"$TMP/bin/test"
  printf '%s\n' 'enable -n test' >"$TMP/disable-test"
  chmod +x "$TMP/bin/mkdir" "$TMP/bin/test"
}

install_blocking_mkdir() {
  mkdir "$TMP/bin"
  printf '#!/bin/sh\nexit 1\n' >"$TMP/bin/mkdir"
  chmod +x "$TMP/bin/mkdir"
}

install_failing_mv() {
  mkdir "$TMP/bin"
  printf '#!/bin/sh\nexit 1\n' >"$TMP/bin/mv"
  chmod +x "$TMP/bin/mv"
}

@test "take fails immediately when the slot directory is not writable" {
  chmod u-w "$ORCH_HEAVY_SLOTS_DIR"
  run "$HELPER" take agent-read-only --wait-max 0 -- bash -c 'exit 0'
  chmod u+w "$ORCH_HEAVY_SLOTS_DIR"
  [ "$status" -eq 73 ]
  [ "$output" = "heavy-slot: cannot write $ORCH_HEAVY_SLOTS_DIR (sandbox?); run this command escalated" ]
}

@test "take fails before locking when its write probe cannot create a directory" {
  install_blocking_mkdir
  run env PATH="$TMP/bin:$PATH" "$HELPER" take agent-probe --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 73 ]
  [ "$output" = "heavy-slot: cannot write $ORCH_HEAVY_SLOTS_DIR (sandbox?); run this command escalated" ]
}

@test "take fails with a persistent non-directory admission lock at the deadline" {
  : >"$ORCH_HEAVY_SLOTS_DIR/.lock"
  run "$HELPER" take agent-lock-path --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 73 ]
  [ "$output" = "heavy-slot: not a directory: $ORCH_HEAVY_SLOTS_DIR/.lock" ]
}

@test "take retries when an admission lock disappears after mkdir reports contention" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  install_disappearing_mkdir
  run env PATH="$TMP/bin:$PATH" TARGET_DIR="$ORCH_HEAVY_SLOTS_DIR/.lock" MARKER="$TMP/admission-race" "$HELPER" take agent-lock-race --wait-max 3 -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ -e "$TMP/admission-race" ]
}

@test "take retries when an admission lock disappears in consecutive mkdir attempts" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  install_disappearing_mkdir
  run env PATH="$TMP/bin:$PATH" TARGET_DIR="$ORCH_HEAVY_SLOTS_DIR/.lock" MARKER="$TMP/admission-races" RELEASES=2 "$HELPER" take agent-lock-double-race --wait-max 3 -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ "$(cat "$TMP/admission-races")" -eq 2 ]
}

@test "a vanished admission lock honors a zero wait max instead of retrying forever" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  install_disappearing_mkdir
  run env PATH="$TMP/bin:$PATH" TARGET_DIR="$ORCH_HEAVY_SLOTS_DIR/.lock" MARKER="$TMP/admission-zero" "$HELPER" take agent-lock-zero --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 75 ]
  [[ "$output" == *'NEED SLOT'* ]]
}

@test "take waits when an admission lock arrives then releases between path checks" {
  install_arriving_lock
  run env PATH="$TMP/bin:$PATH" BASH_ENV="$TMP/disable-test" TARGET_DIR="$ORCH_HEAVY_SLOTS_DIR/.lock" MK_MARKER="$TMP/admission-arrival-mkdir" CHECK_MARKER="$TMP/admission-check" ARRIVAL_MARKER="$TMP/admission-arrival" RELEASE_MARKER="$TMP/admission-release" E_MARKER="$TMP/admission-exists" "$HELPER" take agent-lock-arrival --wait-max 3 -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ -e "$TMP/admission-release" ]
}

@test "take retries when a reclaim lock disappears after mkdir reports contention" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock" "$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim"
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  install_disappearing_mkdir
  run env PATH="$TMP/bin:$PATH" TARGET_DIR="$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim" MARKER="$TMP/reclaim-race" "$HELPER" take agent-reclaim-race --wait-max 3 -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ -e "$TMP/reclaim-race" ]
}

@test "take retries when a reclaim lock disappears in consecutive mkdir attempts" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock" "$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim"
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  install_disappearing_mkdir
  run env PATH="$TMP/bin:$PATH" TARGET_DIR="$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim" MARKER="$TMP/reclaim-races" RELEASES=2 "$HELPER" take agent-reclaim-double-race --wait-max 3 -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ "$(cat "$TMP/reclaim-races")" -eq 2 ]
}

@test "take waits when a reclaim lock arrives then releases between path checks" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  install_arriving_lock
  run env PATH="$TMP/bin:$PATH" BASH_ENV="$TMP/disable-test" TARGET_DIR="$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim" MK_MARKER="$TMP/reclaim-arrival-mkdir" CHECK_MARKER="$TMP/reclaim-check" ARRIVAL_MARKER="$TMP/reclaim-arrival" RELEASE_MARKER="$TMP/reclaim-release" E_MARKER="$TMP/reclaim-exists" "$HELPER" take agent-reclaim-arrival --wait-max 3 -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ -e "$TMP/reclaim-release" ]
}

@test "take fails when admission lock creation persistently fails without a lock" {
  install_failing_mkdir
  run env PATH="$TMP/bin:$PATH" FAIL_ROOT="$ORCH_HEAVY_SLOTS_DIR" MARKER="$TMP/admission-failure" "$HELPER" take agent-lock-failure --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 73 ]
  [ "$output" = "heavy-slot: cannot write $ORCH_HEAVY_SLOTS_DIR (sandbox?); run this command escalated" ]
}

@test "take fails when reclaim lock creation persistently fails without a lock" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  install_failing_mkdir
  run env PATH="$TMP/bin:$PATH" FAIL_ROOT="$ORCH_HEAVY_SLOTS_DIR" MARKER="$TMP/reclaim-failure" "$HELPER" take agent-reclaim-failure --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 73 ]
  [ "$output" = "heavy-slot: cannot write $ORCH_HEAVY_SLOTS_DIR (sandbox?); run this command escalated" ]
}

@test "a failed reclaim honors zero wait max" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  install_failing_mv
  run_with_timeout env PATH="$TMP/bin:$PATH" "$HELPER" take agent-reclaim-deadline --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 75 ]
  [[ "$output" == *'NEED SLOT'* ]]
}

@test "eight parallel takers never run more than the cap" {
  mkdir "$TMP/running"
  for i in $(seq 1 8); do
    # shellcheck disable=SC2016 # The child expands these variables when it runs.
    "$HELPER" take agent-job"$i" -- bash -c '
      mkdir "$TEST_ROOT/running/$$"
      ls "$TEST_ROOT/running" | wc -l >>"$TEST_ROOT/observed"
      sleep 6
      rmdir "$TEST_ROOT/running/$$"
    ' &
  done
  wait
  [ "$(sort -nr "$TMP/observed" | head -n 1)" -le 4 ]
}

@test "stale pid is not counted" {
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/agent-stale"
  run "$HELPER" count
  [ "$status" -eq 0 ]
  [ "$output" = 0 ]
  run "$HELPER" list
  [[ "$output" == *"agent-stale pid=999999"* ]]
  [[ "$output" == *stale ]]
}

@test "slot is removed after a normal exit and a failure" {
  run "$HELPER" take agent-normal -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ ! -e "$ORCH_HEAVY_SLOTS_DIR/agent-normal" ]
  run "$HELPER" take agent-failure -- bash -c 'exit 23'
  [ "$status" -eq 23 ]
  [ ! -e "$ORCH_HEAVY_SLOTS_DIR/agent-failure" ]
}

@test "cleanup preserves a child status and lease when locking fails" {
  # shellcheck disable=SC2016 # The child shell expands the literal path.
  run_with_timeout "$HELPER" take agent-corrupt-cleanup -- bash -c '
    deadline=$(( $(date +%s) + 5 ))
    while :; do
      read -r pid _ <"$1" 2>/dev/null || pid=
      [ "$pid" = "$$" ] && [ ! -d "$2/.lock" ] && break
      [ "$(date +%s)" -ge "$deadline" ] && exit 70
      sleep 0.05
    done
    : >"$2/.lock"
    exit 42
  ' _ "$ORCH_HEAVY_SLOTS_DIR/agent-corrupt-cleanup" "$ORCH_HEAVY_SLOTS_DIR"
  [ "$status" -eq 42 ]
  [ -e "$ORCH_HEAVY_SLOTS_DIR/agent-corrupt-cleanup" ]
}

@test "cleanup preserves a successor lease" {
  # shellcheck disable=SC2016 # The child shell expands its literal script.
  run "$HELPER" take agent-successor -- bash -c '
    deadline=$(( $(date +%s) + 5 ))
    while :; do
      read -r pid _ <"$1" 2>/dev/null || pid=
      [ "$pid" = "$$" ] && [ ! -d "$2/.lock" ] && break
      [ "$(date +%s)" -ge "$deadline" ] && exit 70
      sleep 0.05
    done
    printf "999999\\n" >"$1"
    : >"$2/.lock"
  ' _ "$ORCH_HEAVY_SLOTS_DIR/agent-successor" "$ORCH_HEAVY_SLOTS_DIR"
  [ "$status" -eq 0 ]
  [ "$(cat "$ORCH_HEAVY_SLOTS_DIR/agent-successor")" = 999999 ]
}

@test "SIGTERM forwards to the child and removes the slot" {
  "$HELPER" take agent-signal -- bash -c 'trap "exit 0" TERM; while :; do sleep 1; done' &
  wrapper=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-signal"
  read -r child _ <"$ORCH_HEAVY_SLOTS_DIR/agent-signal"
  kill -TERM "$wrapper"
  wait "$wrapper" || status=$?
  [ "${status:-0}" -eq 143 ]
  [ ! -e "$ORCH_HEAVY_SLOTS_DIR/agent-signal" ]
  run kill -0 "$child"
  [ "$status" -ne 0 ]
}

@test "SIGINT terminates an asynchronous child and removes the slot" {
  # shellcheck disable=SC2016,SC2026 # The child shell, not this Bats shell, expands its literal script.
  run python3 -c '
import os, signal, subprocess, sys, time
helper, slot_dir = sys.argv[1:]
signal.signal(signal.SIGINT, signal.SIG_DFL)
slot = os.path.join(slot_dir, "agent-interrupt")
ready = os.path.join(slot_dir, "agent-interrupt.ready")
terminated = os.path.join(slot_dir, "agent-interrupt.terminated")
wrapper = subprocess.Popen([helper, "take", "agent-interrupt", "--", "bash", "-c",
                            "trap \"printf terminated > \\\"$2\\\"; exit 0\" TERM; printf ready > \"$1\"; while :; do sleep 1; done", "_", ready, terminated])
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    if (os.path.getsize(slot) if os.path.exists(slot) else 0) and os.path.exists(ready):
        break
    time.sleep(0.05)
else:
    wrapper.terminate()
    wrapper.wait()
    raise SystemExit("child never became ready")
child = int(open(slot).read().split()[0])
os.kill(wrapper.pid, signal.SIGINT)
deadline = time.monotonic() + 15
try:
    status = wrapper.wait(timeout=max(0, deadline - time.monotonic()))
except subprocess.TimeoutExpired:
    wrapper.terminate()
    wrapper.wait()
    raise SystemExit("SIGINT left the wrapper waiting on its child")
if status != 130:
    raise SystemExit(f"expected 130, got {status}; slots={os.listdir(slot_dir)}")
if os.path.lexists(slot):
    raise SystemExit("SIGINT left the lease behind")
if not os.path.exists(terminated):
    raise SystemExit("SIGINT did not terminate the child")
try:
    os.kill(child, 0)
except ProcessLookupError:
    pass
else:
    raise SystemExit("SIGINT left the child running")
' "$HELPER" "$ORCH_HEAVY_SLOTS_DIR"
  [ "$status" -eq 0 ]
}

@test "SIGTERM kills a child that ignores TERM and removes the slot" {
  export ORCH_HEAVY_SLOTS_KILL_GRACE=1
  "$HELPER" take agent-ignore-term -- bash -c 'trap "" TERM; while :; do sleep 1; done' &
  wrapper=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-ignore-term"
  read -r child _ <"$ORCH_HEAVY_SLOTS_DIR/agent-ignore-term"
  kill -TERM "$wrapper"
  wait "$wrapper" || status=$?
  [ "${status:-0}" -eq 143 ]
  [ ! -e "$ORCH_HEAVY_SLOTS_DIR/agent-ignore-term" ]
  run kill -0 "$child"
  [ "$status" -ne 0 ]
}

@test "child exit code passes through" {
  run "$HELPER" take agent-exit -- bash -c 'exit 42'
  [ "$status" -eq 42 ]
}

@test "child receives piped stdin" {
  run bash -c 'printf hello | "$1" take agent-stdin -- cat' _ "$HELPER"
  [ "$status" -eq 0 ]
  [ "$output" = hello ]
}

@test "helper lease parses with the shared watcher parser" {
  "$HELPER" take agent-parser -- bash -c 'sleep 10' &
  wrapper=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-parser"
  run env PYTHONPATH="$ROOT/coordinator/watchers" python3 -c '
from pathlib import Path
from watcher_platform import read_slots
import sys
rows, errors = read_slots(Path(sys.argv[1]))
assert not errors
assert rows["slot:agent-parser"]["alive"]
' "$ORCH_HEAVY_SLOTS_DIR"
  [ "$status" -eq 0 ]
  kill -TERM "$wrapper"
  wait "$wrapper" 2>/dev/null || true
}

@test "SIGTERM removes a grandchild with its process group" {
  # shellcheck disable=SC2016 # The child shell expands its literal script.
  "$HELPER" take agent-grandchild -- bash -c 'sleep 30 & printf "%s" "$!" >"$1"; wait' _ "$TMP/grandchild" &
  wrapper=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-grandchild"
  wait_for_file "$TMP/grandchild"
  grandchild=$(cat "$TMP/grandchild")
  kill -TERM "$wrapper"
  wait "$wrapper" || status=$?
  [ "${status:-0}" -eq 143 ]
  run kill -0 "$grandchild"
  [ "$status" -ne 0 ]
}

@test "wait max expires with NEED SLOT and a distinct status" {
  sleep 10 &
  holder=$!
  printf '%s\n' "$holder" >"$ORCH_HEAVY_SLOTS_DIR/agent-holder"
  # shellcheck disable=SC2030,SC2031 # Each Bats test intentionally sets its own environment.
  export ORCH_HEAVY_SLOTS_MAX=1
  run "$HELPER" take agent-wait --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 75 ]
  [[ "$output" == *'NEED SLOT'* ]]
  kill "$holder"
  wait "$holder" 2>/dev/null || true
}

@test "wait max also bounds a held admission lock" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  printf '%s\n' "$$" >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  date +%s >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  run "$HELPER" take agent-lock-wait --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 75 ]
  [[ "$output" == *'NEED SLOT'* ]]
}

@test "an old lock is broken" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  run "$HELPER" take agent-lock -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ ! -d "$ORCH_HEAVY_SLOTS_DIR/.lock" ]
}

@test "an old metadata-less lock is recovered" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  touch -t 200001010000 "$ORCH_HEAVY_SLOTS_DIR/.lock"
  run "$HELPER" take agent-empty-lock -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
}

@test "cleanup tolerates a lease removed before exit" {
  # shellcheck disable=SC2016 # The child shell expands its literal script.
  run "$HELPER" take agent-missing-lease -- bash -c 'rm -f "$1"' _ "$ORCH_HEAVY_SLOTS_DIR/agent-missing-lease"
  [ "$status" -eq 0 ]
  [ ! -d "$ORCH_HEAVY_SLOTS_DIR/.lock" ]
}

@test "an old lock with a live holder is not evicted" {
  sleep 10 &
  holder=$!
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock"
  printf '%s\n' "$holder" >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  run "$HELPER" take agent-live-lock --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 75 ]
  kill "$holder"
  wait "$holder" 2>/dev/null || true
}

@test "cleanup terminates lingering group children" {
  # shellcheck disable=SC2016 # The child shell expands its literal script.
  run "$HELPER" take agent-lingering -- bash -c '
    deadline=$(( $(date +%s) + 5 ))
    while :; do
      read -r pid _ <"$3" 2>/dev/null || pid=
      [ "$pid" = "$$" ] && [ ! -d "$2/.lock" ] && break
      [ "$(date +%s)" -ge "$deadline" ] && exit 70
      sleep 0.05
    done
    sleep 20 & printf "%s" "$!" >"$1"
    : >"$2/.lock"
  ' _ "$TMP/lingering" "$ORCH_HEAVY_SLOTS_DIR" "$ORCH_HEAVY_SLOTS_DIR/agent-lingering"
  [ "$status" -eq 0 ]
  child=$(cat "$TMP/lingering")
  [ -e "$ORCH_HEAVY_SLOTS_DIR/agent-lingering" ]
  run kill -0 "$child"
  [ "$status" -ne 0 ]
}

@test "an abandoned reclaim mutex is recovered" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock" "$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim"
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  touch -t 200001010000 "$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim"
  run "$HELPER" take agent-reclaim -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ ! -d "$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim" ]
}

@test "wait max bounds a fresh reclaim mutex" {
  mkdir "$ORCH_HEAVY_SLOTS_DIR/.lock" "$ORCH_HEAVY_SLOTS_DIR/.lock-reclaim"
  printf '999999\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/pid"
  printf '1\n' >"$ORCH_HEAVY_SLOTS_DIR/.lock/started"
  run "$HELPER" take agent-reclaim-wait --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 75 ]
  [[ "$output" == *'NEED SLOT'* ]]
}

@test "leading-zero wait max is parsed as decimal" {
  sleep 10 &
  holder=$!
  printf '%s\n' "$holder" >"$ORCH_HEAVY_SLOTS_DIR/agent-leading-zero"
  # shellcheck disable=SC2030,SC2031 # Each Bats test intentionally sets its own environment.
  export ORCH_HEAVY_SLOTS_MAX=1
  run "$HELPER" take agent-leading-zero-wait --wait-max 00 -- bash -c 'exit 0'
  [ "$status" -eq 75 ]
  [[ "$output" == *'NEED SLOT'* ]]
  kill "$holder"
  wait "$holder" 2>/dev/null || true
}

@test "a duplicate live name is refused" {
  sleep 10 &
  holder=$!
  printf '%s\n' "$holder" >"$ORCH_HEAVY_SLOTS_DIR/agent-duplicate"
  run "$HELPER" take agent-duplicate --wait-max 0 -- bash -c 'exit 0'
  [ "$status" -eq 65 ]
  [[ "$output" == *'already live'* ]]
  kill "$holder"
  wait "$holder" 2>/dev/null || true
}

@test "a dangling slot symlink is unlinked before leasing" {
  ln -s "$TMP/target" "$ORCH_HEAVY_SLOTS_DIR/agent-link"
  run "$HELPER" take agent-link -- bash -c 'exit 0'
  [ "$status" -eq 0 ]
  [ ! -e "$TMP/target" ]
  [ ! -L "$ORCH_HEAVY_SLOTS_DIR/agent-link" ]
}
