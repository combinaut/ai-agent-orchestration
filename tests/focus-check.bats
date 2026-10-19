#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031 # Bats runs each test in a subshell.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  SCRIPT="$ROOT/coordinator/watchers/focus-check.sh"
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/focus-check-bats.XXXXXX")
  export HOME="$TMP/home"
  export ORCH_HEAVY_SLOTS_DIR="$TMP/slots"
  export PATH="$TMP/bin:$PATH"
  export STATUS="$HOME/.claude/coordinator/status/Architect::Agent::Tooling.md"
  mkdir -p "$HOME/.claude/coordinator/status" "$ORCH_HEAVY_SLOTS_DIR" "$TMP/bin"
  printf '# Architect::Agent::Tooling\n\n## Waits on the user\n- PR 7 review -> https://github.com/acme/web/pull/7\n' >"$STATUS"

  cat >"$TMP/bin/herdr" <<'SH'
#!/bin/sh
if [ "$1" = pane ]; then
  printf '%s\n' '{"result":{"panes":[{"workspace_id":"tooling","terminal_title_stripped":"Architect::Agent::Tooling","tokens":{"role":"architect"},"agent_status":"idle"}]}}'
else
  printf '%s\n' '{"result":{"workspaces":[]}}'
fi
SH
  cat >"$TMP/bin/heavy-slot" <<'SH'
#!/bin/sh
printf '%b' "${HEAVY_SLOT_LIST:-}"
SH
  chmod +x "$TMP/bin/herdr" "$TMP/bin/heavy-slot"
}

teardown() {
  [ -z "${holder:-}" ] || kill "$holder" 2>/dev/null || true
  rm -rf "$TMP"
}

focus_line() {
  python3 - "$SCRIPT" <<'PY'
import os
import select
import signal
import subprocess
import sys

process = subprocess.Popen([sys.argv[1], "900"], stdout=subprocess.PIPE, text=True,
                           start_new_session=True)
try:
    if not select.select([process.stdout], [], [], 5)[0]:
        raise SystemExit("focus-check did not emit a line")
    print(process.stdout.readline().strip())
finally:
    os.killpg(process.pid, signal.SIGTERM)
    process.wait()
PY
}

@test "a live architect slot keeps the architect out of idle_no_worker" {
  sleep 60 3>&- &
  holder=$!
  printf '%s\n' "$holder" >"$ORCH_HEAVY_SLOTS_DIR/agenttooling-capture"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=none"* ]]
}

@test "a dead architect slot leaves the architect idle" {
  true 3>&- &
  dead_pid=$!
  wait "$dead_pid"
  printf '%s\n' "$dead_pid" >"$ORCH_HEAVY_SLOTS_DIR/agenttooling-capture"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
}

@test "an unreadable architect slot leaves the architect idle" {
  [ "$(id -u)" -ne 0 ] || skip "root can read mode-000 files"
  printf '%s\n' "$$" >"$ORCH_HEAVY_SLOTS_DIR/agenttooling-capture"
  chmod 000 "$ORCH_HEAVY_SLOTS_DIR/agenttooling-capture"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
}

@test "an out-of-range architect slot leaves the architect idle" {
  printf '999999999999999999999999999999999999999999999999999999999999999999\n' >"$ORCH_HEAVY_SLOTS_DIR/agenttooling-capture"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
}

@test "another architect's live slot leaves the architect idle" {
  sleep 60 3>&- &
  holder=$!
  printf '%s\n' "$holder" >"$ORCH_HEAVY_SLOTS_DIR/other-capture"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
}

@test "no slot leaves the architect idle" {
  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
}

@test "only live heavy-slot records contribute to the slot count" {
  export HEAVY_SLOT_LIST=$'agenttooling-live pid=1 age=1s live\nagenttooling-stale pid=999999 age=1s stale\n'

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"slots=1"* ]]
}

@test "configuration selects the Python and Herdr executables" {
  cat >"$TMP/bin/configured-python" <<'SH'
#!/bin/sh
touch "$CONFIG_PYTHON_USED"
exec python3 "$@"
SH
  cat >"$TMP/bin/configured-herdr" <<'SH'
#!/bin/sh
touch "$CONFIG_HERDR_USED"
if [ "$1" = pane ]; then
  printf '%s\n' '{"result":{"panes":[{"workspace_id":"tooling","terminal_title_stripped":"Architect::Agent::Tooling","tokens":{"role":"architect"},"agent_status":"idle"}]}}'
else
  printf '%s\n' '{"result":{"workspaces":[]}}'
fi
SH
  chmod +x "$TMP/bin/configured-python" "$TMP/bin/configured-herdr"
  export CONFIG_PYTHON_USED="$TMP/configured-python-used"
  export CONFIG_HERDR_USED="$TMP/configured-herdr-used"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  printf '[watchers]\npython3 = "%s"\n[herdr]\nbin = "%s"\n' "$TMP/bin/configured-python" "$TMP/bin/configured-herdr" >"$ORCH_CONFIG"

  run focus_line

  [ "$status" -eq 0 ]
  [ -e "$CONFIG_PYTHON_USED" ]
  [ -e "$CONFIG_HERDR_USED" ]
}

@test "empty configured executables fall back to PATH" {
  export ORCH_CONFIG="$TMP/orchestration.toml"
  printf '[watchers]\npython3 = ""\n[herdr]\nbin = ""\n' >"$ORCH_CONFIG"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
}

@test "an empty configured slot directory falls back to the default directory" {
  export ORCH_HEAVY_SLOTS_DIR=""
  mkdir -p "$HOME/.claude/heavy-slots"
  sleep 60 3>&- &
  holder=$!
  printf '%s\n' "$holder" >"$HOME/.claude/heavy-slots/agenttooling-capture"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=none"* ]]
}

@test "an unavailable heavy-slot command reports an unknown slot count" {
  rm "$TMP/bin/heavy-slot"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  printf '[watchers]\npython3 = "%s"\n' "$(command -v python3)" >"$ORCH_CONFIG"
  export PATH="$TMP/bin:/usr/bin:/bin"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"slots=?"* ]]
}

@test "an idle architect without open work is resting and not reported" {
  printf '# Architect::Agent::Tooling\n\n## Waits on the user\n\n## Next\n- later\n' >"$STATUS"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=none"* ]]
}

@test "a missing status file means no waits on the user" {
  rm "$STATUS"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=none"* ]]
}

@test "a paused architect is left out of the focus report" {
  printf 'Paused: on leave until Monday\n' >>"$STATUS"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=none"* ]]
}

@test "a pause line inside a code block does not pause" {
  fence='```'
  printf '%s\nPaused: example\n%s\n' "$fence" "$fence" >>"$STATUS"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
}

@test "an unreadable env adapter is a focus read failure" {
  printf '#!/bin/sh\nexit 1\n' >"$TMP/bin/herdr"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == "FOCUS READFAIL open work: "* ]]
}

open_work_fixture() {
  rm "$STATUS"
  export WORK_REPO="$TMP/web"
  git init -q -b main "$WORK_REPO"
  git -C "$WORK_REPO" remote add origin git@github.com:acme/web.git
  git -C "$WORK_REPO" -c user.email=t@example.invalid -c user.name=t commit -q --allow-empty -m init
  git -C "$WORK_REPO" worktree add -q -b feature "$TMP/web-feature"
  cat >"$TMP/bin/herdr" <<'SH'
#!/bin/sh
if [ "$1" = pane ]; then
  printf '{"result":{"panes":[{"workspace_id":"tooling","terminal_title_stripped":"Architect::Agent::Tooling","tokens":{"role":"architect"},"agent_status":"idle"}%s]}}\n' "${WORKER_PANE:-}"
else
  printf '{"result":{"workspaces":[{"workspace_id":"w1","label":"feature","tokens":{"owner":"tooling"},"worktree":{"checkout_path":"%s","repo_root":"%s"}}]}}\n' "$TMP/web-feature" "$WORK_REPO"
fi
SH
  cat >"$TMP/bin/gh" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >>"$TMP/gh-calls"
[ -z "${GH_FAIL:-}" ] || exit 1
printf '%s\n' "${GH_PRS:-[]}"
SH
  chmod +x "$TMP/bin/herdr" "$TMP/bin/gh"
  export TMP
}

@test "an open PR on a branch of an owned workspace is open work" {
  open_work_fixture
  export GH_PRS='[{"headRefName":"other"},{"headRefName":"feature"}]'

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
  [ "$(cat "$TMP/gh-calls")" = "pr list -R acme/web --state open --json headRefName --limit 200" ]
}

@test "an owned workspace without an open PR is not open work" {
  open_work_fixture
  export GH_PRS='[{"headRefName":"other"}]'

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=none"* ]]
}

@test "a PR lookup that fails counts as open work" {
  open_work_fixture
  export GH_FAIL=1

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
}

@test "a worker pane that is not working is open work" {
  open_work_fixture
  export WORKER_PANE=',{"workspace_id":"w1","terminal_title_stripped":"worker","agent":"claude","tokens":{},"agent_status":"done"}'

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"idle_no_worker=Agent::Tooling"* ]]
  [ ! -e "$TMP/gh-calls" ]
}

@test "a working worker keeps its architect out of the idle list" {
  open_work_fixture
  export WORKER_PANE=',{"workspace_id":"w1","terminal_title_stripped":"worker","agent":"claude","tokens":{},"agent_status":"working"}'

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"workers=1 "* ]]
  [[ "$output" == *"idle_no_worker=none"* ]]
}

focus_fixture() {
  cat >"$TMP/bin/herdr" <<'SH'
#!/bin/sh
if [ "$1" = pane ]; then
  printf '{"result":{"panes":[
    {"workspace_id":"a","terminal_title_stripped":"Architect::Alpha","tokens":{"role":"architect"},"agent_status":"idle","cwd":"%s"},
    {"workspace_id":"b","terminal_title_stripped":"Architect::Beta","tokens":{"role":"architect"},"agent_status":"idle","cwd":"%s"},
    {"workspace_id":"w1","terminal_title_stripped":"worker","agent":"claude","tokens":{},"agent_status":"working"}]}}\n' "$FOCUS_REPO" "${BETA_REPO:-$FOCUS_REPO}"
else
  printf '%s\n' '{"result":{"workspaces":[{"workspace_id":"w1","label":"Billing-fix","tokens":{"owner":"a"}}]}}'
fi
SH
  export ORCH_CONFIG="$TMP/orchestration.toml"
  : >"$ORCH_CONFIG"
  export FOCUS_REPO="$TMP/repo"
  mkdir -p "$FOCUS_REPO/sub"
  git -C "$FOCUS_REPO" init -q
}

@test "a focus pattern counts matching workers case-insensitively and flags architects without one" {
  focus_fixture
  printf '[focus]\npattern = "billing|invoice"\n' >"$FOCUS_REPO/.orchestration.toml"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"focus_workers_live=1"* ]]
  [[ "$output" == *"no_focus_worker=Beta"* ]]
}

@test "the focus architect is left out of the no-worker list" {
  focus_fixture
  printf '[focus]\npattern = "billing"\narchitect = "Beta"\n' >"$FOCUS_REPO/.orchestration.toml"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"no_focus_worker=none"* ]]
}

@test "an empty focus pattern turns the focus check off" {
  focus_fixture
  printf '[focus]\npattern = ""\n' >"$FOCUS_REPO/.orchestration.toml"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"focus_workers_live=0"* ]]
  [[ "$output" == *"no_focus_worker=none"* ]]
}

@test "an unset focus pattern turns the focus check off" {
  focus_fixture

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"focus_workers_live=0"* ]]
  [[ "$output" == *"no_focus_worker=none"* ]]
}

@test "an architect is judged by the focus pattern of the repo it works in" {
  focus_fixture
  export BETA_REPO="$TMP/elsewhere"
  mkdir -p "$BETA_REPO"
  printf '[focus]\npattern = "invoice"\n' >"$FOCUS_REPO/.orchestration.toml"
  printf '[focus]\npattern = "billing"\n' >"$FOCUS_REPO/.orchestration.local.toml"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"focus_workers_live=1"* ]]
  [[ "$output" == *"no_focus_worker=none"* ]]
}

@test "a paused architect is left out of the no-focus list" {
  focus_fixture
  printf '[focus]\npattern = "billing"\n' >"$FOCUS_REPO/.orchestration.toml"
  printf 'Paused: holiday\n' >"$HOME/.claude/coordinator/status/Architect::Beta.md"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"no_focus_worker=none"* ]]
}

@test "the environment overrides the configured focus pattern" {
  focus_fixture
  printf '[focus]\npattern = "nomatch"\n' >"$FOCUS_REPO/.orchestration.toml"
  export ORCH_FOCUS_PATTERN="billing"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == *"focus_workers_live=1"* ]]
}

@test "an invalid focus pattern reports a read failure naming the setting" {
  focus_fixture
  printf '[focus]\npattern = "("\n' >"$FOCUS_REPO/.orchestration.toml"

  run focus_line

  [ "$status" -eq 0 ]
  [[ "$output" == "FOCUS READFAIL ORCH_FOCUS_PATTERN "* ]]
}
