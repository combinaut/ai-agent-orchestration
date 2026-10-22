#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031 # Bats runs each test in a subshell.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/startup-reads-bats.XXXXXX")
  TMP=$(cd "$TMP" && pwd -P)
  export HOME="$TMP/home"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  mkdir -p "$HOME"
  for name in $(python3 "$ROOT/lib/orch_settings.py" names); do unset "$name"; done
  cat >"$TMP/herdr" <<'STUB'
#!/usr/bin/env bash
case "$1 $2" in
  'agent list') if [ -n "${AGENTS_STDERR:-}" ]; then cat "${AGENTS_JSON:?}" >&2; else cat "${AGENTS_JSON:?}"; fi; exit "${AGENTS_EXIT:-0}" ;;
  'workspace list') [ -z "${WORKSPACES_FAIL:-}" ] || exit 1; cat "${WORKSPACES_JSON:?}" ;;
esac
STUB
  chmod +x "$TMP/herdr"
  export HERDR_BIN_PATH="$TMP/herdr" AGENTS_JSON="$TMP/agents.json" WORKSPACES_JSON="$TMP/workspaces.json"
  cat >"$AGENTS_JSON" <<JSON
{"result":{"agents":[
 {"name":"architect-x","pane_id":"w1:p1","agent":"claude","agent_status":"working","workspace_id":"w1","cwd":"$HOME/repo","tokens":{"role":"architect","band":"long noise"},"agent_session":{"value":"uuid"}},
 {"pane_id":"w2:p1","agent_status":"idle","workspace_id":"w2","cwd":"/srv/other","tokens":{}},
 {"name":"near-home","pane_id":"w3:p1","agent":"codex","agent_status":"done","workspace_id":"w3","cwd":"${HOME}foo/x","tokens":{}}]}}
JSON
  printf '{"result":{"workspaces":[{"workspace_id":"w1","tokens":{}},{"workspace_id":"w2","tokens":{"owner":"w1"}}]}}\n' >"$WORKSPACES_JSON"
}

# has_line <line>: the output holds exactly this line. (A mid-test [[ ]] failure does not fail a test on bash 3.2.)
has_line() { printf '%s\n' "$output" | grep -qxF -- "$1"; }

teardown() {
  rm -rf "$TMP"
}

@test "orch-settings prints the built-in default of every global setting when no file exists" {
  want=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import orch_settings as s; print(len(s.scoped("global")))' "$ROOT/lib")
  run env -u ORCH_CONFIG "$ROOT/orch-settings"
  [ "$status" -eq 0 ]
  [ "$(printf '%s\n' "$output" | grep -c .)" -eq "$want" ]
  has_line ORCH_RECYCLE_TOKENS=300000
}

@test "orch-settings resolves default, then the file, then an exported variable" {
  printf '[models]\narchitect = "from-file"\ncoordinator = "file-coord"\n[recycle]\npark_idle = true\n' >"$ORCH_CONFIG"
  run env ORCH_COORDINATOR_MODEL=from-env "$ROOT/orch-settings"
  [ "$status" -eq 0 ]
  has_line ORCH_ARCHITECT_MODEL=from-file
  has_line ORCH_COORDINATOR_MODEL=from-env
  has_line ORCH_PARK_IDLE=1
  has_line ORCH_RECYCLE_TOKENS=300000
  [ "$(printf '%s\n' "$output" | grep -c 'file-coord')" -eq 0 ]
}

@test "orch-settings prints the enabled providers and stops on a list without claude" {
  : >"$ORCH_CONFIG"
  run env -u ORCH_PROVIDERS -u ORCH_PI_WORKER_MODEL -u ORCH_PI_SMART_MODEL "$ROOT/orch-settings"
  [ "$status" -eq 0 ]
  has_line 'ORCH_PROVIDERS=claude,codex'
  printf '[providers]\nenabled = ["claude"]\n[models]\ncodex_worker = ""\ncodex_smart = ""\n' >"$ORCH_CONFIG"
  run env -u ORCH_PROVIDERS "$ROOT/orch-settings"
  [ "$status" -eq 0 ]
  has_line 'ORCH_PROVIDERS=claude'
  printf '[providers]\nenabled = ["codex"]\n' >"$ORCH_CONFIG"
  run env -u ORCH_PROVIDERS "$ROOT/orch-settings"
  [ "$status" -eq 2 ]
  [[ "$output" == *'providers.enabled: must include "claude"'* ]]
}

@test "orch-settings defaults architect Remote Control on and accepts it off" {
  run env -u ORCH_CONFIG "$ROOT/orch-settings"
  [ "$status" -eq 0 ]
  has_line ORCH_REMOTE_CONTROL_ARCHITECTS=1

  printf '[remote_control]\narchitects = false\n' >"$ORCH_CONFIG"
  run "$ROOT/orch-settings"
  [ "$status" -eq 0 ]
  has_line ORCH_REMOTE_CONTROL_ARCHITECTS=0
}

@test "orch-settings prints one ORCH_NAME=value line per setting and no comments" {
  printf '# a comment\n[pr]\nauthor = "me"\n' >"$ORCH_CONFIG"
  run "$ROOT/orch-settings"
  [ "$status" -eq 0 ]
  has_line ORCH_PR_AUTHOR=me
  [ "$(printf '%s\n' "$output" | grep -vc '^ORCH_[A-Z0-9_]*=')" -eq 0 ]
}

@test "orch-settings fails loudly on an ORCH_CONFIG that is not a file" {
  export ORCH_CONFIG="$TMP/missing.toml"
  run "$ROOT/orch-settings"
  [ "$status" -eq 2 ]
  printf '%s' "$output" | grep -qF "ORCH_CONFIG is not a regular file"
}

@test "orch-settings fails loudly on an invalid file" {
  printf '[pr]\nbogus = 1\n' >"$ORCH_CONFIG"
  run "$ROOT/orch-settings"
  [ "$status" -eq 2 ]
  printf '%s' "$output" | grep -qF "pr.bogus: unknown key"
}

@test "orch-agents prints one short line per agent with owner and role" {
  run "$ROOT/orch-agents"
  [ "$status" -eq 0 ]
  [ "$output" = "architect-x w1:p1 claude working w1 owner=- role=architect ~/repo
- w2:p1 - idle w2 owner=w1 role=- /srv/other
near-home w3:p1 codex done w3 owner=- role=- ${HOME}foo/x" ] || { echo "$output"; false; }
}

@test "orch-agents still lists agents when the workspace list fails" {
  export WORKSPACES_FAIL=1
  run "$ROOT/orch-agents"
  [ "$status" -eq 0 ]
  has_line "- w2:p1 - idle w2 owner=- role=- /srv/other"
}

@test "orch-agents fails loudly when herdr fails" {
  printf 'not json\n' >"$AGENTS_JSON"
  run "$ROOT/orch-agents"
  [ "$status" -eq 2 ]
  printf '%s' "$output" | grep -q '^ERROR: herdr agent list failed'
}

@test "orch-agents puts only herdr's error message in the ERROR line, from stdout or stderr" {
  printf '{"id":"x","error":{"code":"no_server","message":"no herdr server is running"}}\n' >"$AGENTS_JSON"
  export AGENTS_EXIT=1
  run "$ROOT/orch-agents"
  [ "$status" -eq 2 ]
  [ "$output" = "ERROR: herdr agent list failed: no herdr server is running" ] || { echo "$output"; false; }
  export AGENTS_STDERR=1
  run "$ROOT/orch-agents"
  [ "$status" -eq 2 ]
  [ "$output" = "ERROR: herdr agent list failed: no herdr server is running" ] || { echo "$output"; false; }
}

@test "orch-agents falls back to the raw text when the error is not JSON" {
  printf 'boom\n' >"$AGENTS_JSON"
  export AGENTS_EXIT=1 AGENTS_STDERR=1
  run "$ROOT/orch-agents"
  [ "$status" -eq 2 ]
  [ "$output" = "ERROR: herdr agent list failed: boom" ] || { echo "$output"; false; }
}

@test "orch-agents rejects every unexpected herdr shape instead of printing an empty list" {
  for body in '{"id":"x","error":{"message":"m"}}' '{"result":{"items":[]}}' '{"result":[]}' '[]' '{"result":{"agents":["a"]}}' '{"result":{"agents":{}}}'; do
    printf '%s\n' "$body" >"$AGENTS_JSON"
    run "$ROOT/orch-agents"
    [ "$status" -eq 2 ] || { echo "$body -> $status $output"; false; }
    printf '%s' "$output" | grep -q '^ERROR: herdr agent list failed' || { echo "$body -> $output"; false; }
  done
}

@test "orch-settings lets an exported but empty variable win, like orch_set_default" {
  printf '[pr]\nauthor = "me"\n' >"$ORCH_CONFIG"
  run env ORCH_PR_AUTHOR= "$ROOT/orch-settings"
  [ "$status" -eq 0 ]
  has_line ORCH_PR_AUTHOR=
}

@test "orch-settings rejects an option and a missing explicit file" {
  printf '[pr]\nauthor = "dash"\n' >"$TMP/-x"
  cd "$TMP"
  for arg in --help -x "$TMP/nope.toml"; do
    run "$ROOT/orch-settings" "$arg"
    [ "$status" -eq 2 ] || { echo "$arg -> $status"; false; }
    printf '%s' "$output" | grep -q '^ERROR: ' || { echo "$arg -> $output"; false; }
  done
}

@test "orch-settings reads an explicit existing file" {
  printf '[pr]\nauthor = "explicit"\n' >"$TMP/other.toml"
  run "$ROOT/orch-settings" "$TMP/other.toml"
  [ "$status" -eq 0 ]
  has_line ORCH_PR_AUTHOR=explicit
}

@test "orch-settings refuses a value that would split into two lines" {
  printf '[pr]\nauthor = "a\\nORCH_ARCHITECT_MODEL=evil"\n' >"$ORCH_CONFIG"
  run "$ROOT/orch-settings"
  [ "$status" -eq 2 ]
  printf '%s' "$output" | grep -q 'ORCH_PR_AUTHOR: the value contains a control character'
  [ "$(printf '%s\n' "$output" | grep -c '^ORCH_ARCHITECT_MODEL=')" -eq 0 ]
}
