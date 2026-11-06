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

@test "SIGINT during admission lock release removes the lease" {
  mkdir "$TMP/bin"
  cat >"$TMP/bin/rm" <<'STUB'
#!/usr/bin/env bash
wrapper=
if [ "${2:-}" = "$ORCH_HEAVY_SLOTS_DIR/.lock/pid" ] && [ ! -e "$TEST_ROOT/interrupted" ]; then
  wrapper=$(cat "$2")
fi
/bin/rm "$@"
status=$?
if [ "$status" -eq 0 ] && [ -n "$wrapper" ]; then
  : >"$TEST_ROOT/interrupted"
  case "$INTERRUPT_TARGET" in
    group) kill -INT -- "-$wrapper" ;;
    parent) kill -INT "$wrapper" ;;
  esac
fi
exit "$status"
STUB
  chmod +x "$TMP/bin/rm"
  for target in parent group; do
    rm -f "$TMP/interrupted"
    # Isolate the helper group so the injected signal cannot reach the runner.
    # shellcheck disable=SC2016 # Python receives this program literally.
    run env PATH="$TMP/bin:$PATH" INTERRUPT_TARGET="$target" python3 -c '
import signal, subprocess, sys
signal.signal(signal.SIGINT, signal.SIG_DFL)
result = subprocess.run([sys.argv[1], "take", "agent-release-interrupt", "--", "sleep", "30"], timeout=30, start_new_session=True)
raise SystemExit(0 if result.returncode == 130 else 1)
' "$HELPER"
    [ "$status" -eq 0 ]
    [ -f "$TMP/interrupted" ]
    [ ! -e "$ORCH_HEAVY_SLOTS_DIR/agent-release-interrupt" ]
    [ ! -d "$ORCH_HEAVY_SLOTS_DIR/.lock" ]
  done
}

@test "SIGINT terminates an asynchronous child and removes the slot" {
  # shellcheck disable=SC2016,SC2026 # The child shell, not this Bats shell, expands its literal script.
  run env ORCH_HEAVY_SLOTS_KILL_GRACE=2 python3 -c '
import os, signal, subprocess, sys, time
helper, slot_dir = sys.argv[1:]
kill_grace, allowance = int(os.environ["ORCH_HEAVY_SLOTS_KILL_GRACE"]), 5
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
deadline = time.monotonic() + kill_grace + allowance
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
  [ "$status" -eq 0 ] || { echo "$output"; false; }
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

EVENTS() { printf '%s' "$ORCH_HEAVY_SLOTS_DIR/.events"; }

@test "list shows the note of a live job and note=- without one" {
  "$HELPER" take agent-noted --note 'first-run.bats for PR 106' -- sleep 5 &
  "$HELPER" take agent-plain -- sleep 5 &
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-noted"
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-plain"
  run "$HELPER" list
  printf '%s\n' "$output" | grep -Eq '^agent-noted pid=[0-9]+ age=[0-9]+s note=first-run.bats for PR 106 live$'
  printf '%s\n' "$output" | grep -Eq '^agent-plain pid=[0-9]+ age=[0-9]+s note=- live$'
}

@test "the note file is removed when the job ends" {
  run "$HELPER" take agent-noted --note hello -- true
  [ "$status" -eq 0 ]
  [ ! -e "$ORCH_HEAVY_SLOTS_DIR/.notes/agent-noted" ]
}

@test "note keeps one printable line of at most 60 characters" {
  run "$HELPER" take agent-note --note "$(printf 'a\nb\tc %.0sx' 1)$(printf 'y%.0s' $(seq 1 100))" -- bash -c 'exit 2'
  [ "$status" -eq 2 ]
  [ "$(wc -l <"$(EVENTS)")" -eq 1 ]
  note=$(sed -e 's/.* note=//' "$(EVENTS)")
  [ "${#note}" -le 60 ]
  [[ "$note" == "a b c x"* ]]
}

@test "a failing job writes one FAIL line with name, pid, code, elapsed and note" {
  run "$HELPER" take agent-fail --note 'unit tests' -- bash -c 'exit 3'
  [ "$status" -eq 3 ]
  [ "$(wc -l <"$(EVENTS)")" -eq 1 ]
  grep -Eq '^[0-9]+ SLOT FAIL agent-fail pid=[0-9]+ code=3 elapsed=[0-9]+s note=unit tests$' "$(EVENTS)"
}

@test "a job without a note writes note=-" {
  run "$HELPER" take agent-fail -- bash -c 'exit 1'
  grep -Eq '^[0-9]+ SLOT FAIL agent-fail pid=[0-9]+ code=1 elapsed=[0-9]+s note=-$' "$(EVENTS)"
}

@test "a job killed from outside writes one FAIL line with its code, not KILLED" {
  run "$HELPER" take agent-killed --note 'big run' -- bash -c 'kill -KILL $$'
  [ "$status" -eq 137 ]
  [ "$(wc -l <"$(EVENTS)")" -eq 1 ]
  grep -Eq '^[0-9]+ SLOT FAIL agent-killed pid=[0-9]+ code=137 elapsed=[0-9]+s note=big run$' "$(EVENTS)"
}

@test "a job that exits 143 or 137 on its own writes FAIL, not KILLED" {
  for code in 143 137; do
    : >|"$(EVENTS)"
    run "$HELPER" take "agent-exit$code" -- bash -c "exit $code"
    [ "$status" -eq "$code" ]
    [ "$(wc -l <"$(EVENTS)")" -eq 1 ]
    grep -Eq "^[0-9]+ SLOT FAIL agent-exit$code pid=[0-9]+ code=$code " "$(EVENTS)"
  done
}

@test "stopping heavy-slot with TERM writes one KILLED line for the job" {
  "$HELPER" take agent-stopped --note stopped -- sleep 30 &
  pid=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-stopped"
  kill -TERM "$pid"
  wait "$pid" || true
  [ "$(wc -l <"$(EVENTS)")" -eq 1 ]
  grep -Eq '^[0-9]+ SLOT KILLED agent-stopped pid=[0-9]+ code=143 elapsed=[0-9]+s note=stopped$' "$(EVENTS)"
}

@test "a job past the long-run threshold writes exactly one RUNLONG line" {
  ORCH_HEAVY_SLOTS_LONG_RUN=1 run "$HELPER" take agent-long --note 'slow suite' -- sleep 4
  [ "$status" -eq 0 ]
  [ "$(grep -c '^[0-9]* SLOT RUNLONG' "$(EVENTS)")" -eq 1 ]
  grep -Eq '^[0-9]+ SLOT RUNLONG agent-long pid=[0-9]+ elapsed=[0-9]+s note=slow suite$' "$(EVENTS)"
  [ "$(wc -l <"$(EVENTS)")" -eq 1 ]
}

# Bats runs tests in parallel inside one process group. A take started through in_own_session leads
# its own group, so $! is that group's id and pgrep -g "$!" sees only that take and its watcher.
in_own_session() {
  # shellcheck disable=SC2016 # Python receives this program literally.
  exec python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}

group_live() {
  local member
  for member in $(pgrep -g "$1"); do
    case $(ps -o stat= -p "$member" 2>/dev/null) in
      '' | Z*) ;;
      *) return 0 ;;
    esac
  done
  return 1
}

assert_group_empty() {
  local tries=0
  while group_live "$1" && [ "$tries" -lt 100 ]; do sleep 0.1; tries=$((tries + 1)); done
  ! group_live "$1"
}

wait_for_exit() {
  local tries=0
  while kill -0 "$1" 2>/dev/null && [ "$tries" -lt 150 ]; do sleep 0.1; tries=$((tries + 1)); done
  ! kill -0 "$1" 2>/dev/null
}

wait_for_group_process() {
  local group=$1 tries=0
  shift
  while ! pgrep -g "$group" "$@" >/dev/null && [ "$tries" -lt 50 ]; do sleep 0.1; tries=$((tries + 1)); done
  pgrep -g "$group" "$@" >/dev/null
}

@test "a short job leaves no long-run watcher or its sleep behind" {
  ORCH_HEAVY_SLOTS_LONG_RUN=29 in_own_session "$HELPER" take agent-wstop-ok -- sleep 1 &
  pid=$!
  wait_for_exit "$pid"
  wait "$pid"
  assert_group_empty "$pid"
}

@test "stopping heavy-slot with TERM leaves no long-run watcher or its sleep behind" {
  ORCH_HEAVY_SLOTS_LONG_RUN=28 in_own_session "$HELPER" take agent-wstop-term -- sleep 30 &
  pid=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-wstop-term"
  wait_for_group_process "$pid" -x -f 'sleep 2[78]'
  kill -TERM "$pid"
  wait "$pid" || true
  assert_group_empty "$pid"
}

# twice_signal <TERM|HUP>: send the signal twice to a take whose job ignores both; the second must not skip cleanup (Bash 3.2 runs it after the first exit).
twice_signal() {
  local name=agent-twice-$1
  # shellcheck disable=SC2016 # The child Bash expands nothing here.
  ORCH_HEAVY_SLOTS_LONG_RUN=26 ORCH_HEAVY_SLOTS_KILL_GRACE=3 in_own_session "$HELPER" take "$name" -- bash -c 'trap "" TERM HUP; sleep 20' &
  pid=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/$name"
  wait_for_group_process "$pid" -x -f 'sleep 2[56]'
  kill -"$1" "$pid"
  wait_for_file "$(EVENTS)"
  sleep 1
  kill -"$1" "$pid"
  wait "$pid" || true
  [ ! -e "$ORCH_HEAVY_SLOTS_DIR/$name" ]
  assert_group_empty "$pid"
}

@test "a second identical TERM while the job is stopping still cleans up the slot and the watcher" {
  twice_signal TERM
}

@test "a second identical HUP while the job is stopping still cleans up the slot and the watcher" {
  twice_signal HUP
}

@test "the long-run watcher outlives its RUNLONG line and is gone when the job ends" {
  ORCH_HEAVY_SLOTS_LONG_RUN=1 in_own_session "$HELPER" take agent-wstop-fired -- sleep 3 &
  pid=$!
  tries=0
  while ! grep -q ' SLOT RUNLONG ' "$(EVENTS)" 2>/dev/null && [ "$tries" -lt 50 ]; do sleep 0.1; tries=$((tries + 1)); done
  grep -q ' SLOT RUNLONG ' "$(EVENTS)"
  # heavy-slot itself and its watcher both carry the job name.
  [ "$(pgrep -g "$pid" -f 'heavy-slot take agent-wstop-fired ' | wc -l)" -ge 2 ]
  # After RUNLONG the watcher sleeps 30 s a time instead of spinning.
  wait_for_group_process "$pid" -x -f 'sleep 30'
  wait "$pid"
  [ "$(grep -c ' SLOT RUNLONG ' "$(EVENTS)")" -eq 1 ]
  assert_group_empty "$pid"
}

@test "stopping the long-run watcher prints nothing of its own" {
  for n in $(seq 1 40); do
    run "$HELPER" take agent-quiet -- true
    [ "$status" -eq 0 ]
    [ -z "$output" ]
  done
}

@test "a job under the long-run threshold writes no RUNLONG line" {
  ORCH_HEAVY_SLOTS_LONG_RUN=60 run "$HELPER" take agent-short -- sleep 1
  [ ! -s "$(EVENTS)" ]
}

@test "long-run 0 turns RUNLONG off" {
  ORCH_HEAVY_SLOTS_LONG_RUN=0 run "$HELPER" take agent-off -- sleep 2
  [ ! -s "$(EVENTS)" ]
}

@test "a non-numeric long-run is refused" {
  ORCH_HEAVY_SLOTS_LONG_RUN=soon run "$HELPER" take agent-bad -- true
  [ "$status" -eq 64 ]
  [[ "$output" == *ORCH_HEAVY_SLOTS_LONG_RUN* ]]
}

@test "a job that waits for a slot writes one WAIT line however long it waits" {
  ORCH_HEAVY_SLOTS_MAX=1 "$HELPER" take agent-holder -- sleep 12 &
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-holder"
  ORCH_HEAVY_SLOTS_MAX=1 run "$HELPER" take agent-waiter --note 'queued run' --wait-max 40 -- true
  [ "$status" -eq 0 ]
  [ "$(grep -c '^[0-9]* SLOT WAIT' "$(EVENTS)")" -eq 1 ]
  grep -Eq '^[0-9]+ SLOT WAIT agent-waiter pid=[0-9]+ slots=1/1 note=queued run$' "$(EVENTS)"
}

@test "concurrent jobs append whole lines: none lost, none interleaved" {
  note=$(printf 'n%.0s' $(seq 1 60))
  for n in $(seq 1 40); do
    # shellcheck disable=SC2016 # The child Bash expands $1.
    ORCH_HEAVY_SLOTS_MAX=100 "$HELPER" take "agent-job$n" --note "$note" -- bash -c 'until [ -e "$1" ]; do sleep 0.02; done; exit 1' _ "$TMP/go" &
  done
  tries=0
  while [ "$("$HELPER" count)" -lt 40 ] && [ "$tries" -lt 120 ]; do sleep 0.5; tries=$((tries + 1)); done
  [ "$("$HELPER" count)" -eq 40 ]
  : >"$TMP/go"
  wait
  [ "$(wc -l <"$(EVENTS)")" -eq 40 ]
  [ "$(grep -Ec "^[0-9]+ SLOT FAIL agent-job[0-9]+ pid=[0-9]+ code=1 elapsed=[0-9]+s note=$note\$" "$(EVENTS)")" -eq 40 ]
  [ "$(sed -e 's/^[0-9]* SLOT FAIL \(agent-job[0-9]*\) .*/\1/' "$(EVENTS)" | sort -u | wc -l)" -eq 40 ]
}

@test "usage names --note" {
  run "$HELPER" take
  [ "$status" -eq 64 ]
  [[ "$output" == *"[--note text]"* ]]
}

@test "a passing job that leaves a process behind writes no event" {
  run "$HELPER" take agent-leftover -- bash -c 'sleep 30 & exit 0'
  [ "$status" -eq 0 ]
  [ ! -s "$(EVENTS)" ]
}

@test "a failing job that leaves a process behind writes exactly one FAIL line" {
  run "$HELPER" take agent-leftover -- bash -c 'sleep 30 & exit 1'
  [ "$status" -eq 1 ]
  [ "$(wc -l <"$(EVENTS)")" -eq 1 ]
  grep -Eq '^[0-9]+ SLOT FAIL agent-leftover pid=[0-9]+ code=1 ' "$(EVENTS)"
}

@test "each spool line starts with the epoch of the event" {
  before=$(date +%s)
  run "$HELPER" take agent-epoch -- bash -c 'exit 1'
  after=$(date +%s)
  stamp=$(sed -e 's/ .*//' "$(EVENTS)")
  [ "$stamp" -ge "$before" ]
  [ "$stamp" -le "$after" ]
}

@test "a spool over 64 KB takes no more lines" {
  head -c 70000 /dev/zero | tr '\0' x >"$(EVENTS)"
  run "$HELPER" take agent-full -- bash -c 'exit 1'
  [ "$status" -eq 1 ]
  [ "$(wc -c <"$(EVENTS)" | tr -d ' ')" -eq 70000 ]
}

@test "a spool under the cap still takes lines" {
  head -c 60000 /dev/zero | tr '\0' x >"$(EVENTS)"
  printf '\n' >>"$(EVENTS)"
  run "$HELPER" take agent-room -- bash -c 'exit 1'
  grep -Eq '^[0-9]+ SLOT FAIL agent-room ' "$(EVENTS)"
}

@test "cleanup keeps the note of a slot that names another pid" {
  "$HELPER" take agent-owner --note mine -- sleep 30 &
  pid=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-owner"
  child=$(cat "$ORCH_HEAVY_SLOTS_DIR/agent-owner")
  printf '424242\n' >"$ORCH_HEAVY_SLOTS_DIR/agent-owner"
  printf 'theirs\n' >"$ORCH_HEAVY_SLOTS_DIR/.notes/agent-owner"
  kill -TERM "$child"
  wait "$pid" || true
  [ "$(cat "$ORCH_HEAVY_SLOTS_DIR/.notes/agent-owner")" = theirs ]
}

@test "a long-run value with a leading zero is read as decimal" {
  ORCH_HEAVY_SLOTS_LONG_RUN=08 run "$HELPER" take agent-octal -- sleep 10
  [ "$status" -eq 0 ]
  [ "$(grep -c ' SLOT RUNLONG' "$(EVENTS)")" -eq 1 ]
}

@test "the long-run watcher sleeps at most 30 seconds at a time" {
  ORCH_HEAVY_SLOTS_LONG_RUN=1234 in_own_session "$HELPER" take agent-nap -- sleep 4 &
  pid=$!
  wait_for_group_process "$pid" -x -f 'sleep 30'
  if pgrep -g "$pid" -x -f 'sleep 1234'; then false; fi
  wait "$pid"
}

@test "a second signal while the job is stopping writes no second KILLED line" {
  # shellcheck disable=SC2016 # The child Bash expands $1.
  ORCH_HEAVY_SLOTS_KILL_GRACE=30 "$HELPER" take agent-twice -- bash -c 'trap "echo t >>\"\$1\"" TERM; trap "" HUP; while :; do sleep 0.1; done' _ "$TMP/terms" &
  pid=$!
  wait_for_file "$ORCH_HEAVY_SLOTS_DIR/agent-twice"
  child=$(cat "$ORCH_HEAVY_SLOTS_DIR/agent-twice")
  kill -TERM "$pid"
  wait_for_file "$(EVENTS)"
  kill -HUP "$pid"
  tries=0
  while [ "$(wc -l <"$TMP/terms" 2>/dev/null || echo 0)" -lt 2 ] && [ "$tries" -lt 200 ]; do sleep 0.05; tries=$((tries + 1)); done
  [ "$(wc -l <"$TMP/terms")" -ge 2 ]
  kill -KILL -- "-$child"
  wait "$pid" || true
  [ "$(grep -c ' SLOT KILLED' "$(EVENTS)")" -eq 1 ]
}
