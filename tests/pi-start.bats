#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031,SC2154 # Bats runs each test in a subshell and assigns status and output.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  SCRIPT="$ROOT/coordinator/pi-start.sh"
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/pi-start-bats.XXXXXX")
  export HOME="$TMP/home"
  export PATH="$TMP/bin:$PATH"
  export HERDR_CALLS="$TMP/herdr.calls"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  unset ORCH_PI_WORKER_MODEL ORCH_PI_SMART_MODEL ORCH_PI_MODELS ORCH_PI_DEEPSEEK
  mkdir -p "$HOME" "$TMP/bin" "$TMP/repo"
  git -C "$TMP/repo" init -q
  git -C "$TMP/repo" -c user.email=t@example.invalid -c user.name=t commit -q --allow-empty -m init
  git -C "$TMP/repo" worktree add -q -b side "$TMP/side"
  printf '[models]\npi_worker = "lmstudio/qwen/qwen3.6-35b-a3b"\npi_smart = "deepseek/some-model"\n' >"$ORCH_CONFIG"
  export PANE_CWD="$TMP/repo"
  # The fake herdr answers pane get with PANE_CWD and records every other call.
  cat >"$TMP/bin/herdr" <<'SH'
#!/bin/sh
if [ "$1 $2" = "pane get" ]; then
  printf '{"result":{"pane":{"cwd":"%s"}}}\n' "$PANE_CWD"
else
  printf '%s\n' "$*" >>"$HERDR_CALLS"
fi
SH
  chmod +x "$TMP/bin/herdr"
}

# A PATH without the host's own herdr: the test dir (jq, python3 linked in) plus the system directories.
only_system_path() {
  ln -s "$(command -v jq)" "$TMP/bin/jq"
  ln -s "$(command -v python3)" "$TMP/bin/python3"
  PATH="$TMP/bin:/usr/bin:/bin"
  ! command -v herdr >/dev/null
}

teardown() {
  rm -rf "$TMP"
}

@test "a local provider starts in any repo with the provider and model split" {
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ "$(cat "$HERDR_CALLS")" = "agent start w1 --kind pi --pane pane1 -- --provider lmstudio --model qwen/qwen3.6-35b-a3b --models lmstudio/qwen/qwen3.6-35b-a3b" ]
}

@test "DeepSeek is refused in a repo that did not opt in" {
  run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 1 ]
  [[ "$output" == *"DeepSeek is off"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "DeepSeek starts in a repo whose local file opts in" {
  printf '[pi]\ndeepseek = true\n' >"$TMP/repo/.orchestration.local.toml"
  run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 0 ]
  [ "$(cat "$HERDR_CALLS")" = "agent start w1 --kind pi --pane pane1 -- --provider deepseek --model some-model --models deepseek/some-model" ]
}

@test "a committed pi.deepseek does not opt in" {
  printf '[pi]\ndeepseek = true\n' >"$TMP/repo/.orchestration.toml"
  run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 1 ]
  [ ! -e "$HERDR_CALLS" ]
}

@test "an exported ORCH_PI_DEEPSEEK does not opt in" {
  ORCH_PI_DEEPSEEK=1 run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 1 ]
  [ ! -e "$HERDR_CALLS" ]
}

@test "a linked worktree uses the opt-in of its main checkout" {
  printf '[pi]\ndeepseek = true\n' >"$TMP/repo/.orchestration.local.toml"
  PANE_CWD="$TMP/side" run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 0 ]
  [[ "$(cat "$HERDR_CALLS")" == *"--provider deepseek"* ]] || false
}

@test "an empty tier setting means pi is not configured" {
  printf '[models]\npi_worker = ""\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"pi is not configured"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "extra args cannot change the provider or name DeepSeek" {
  for arg in --provider --model=x --models deepseek/x DEEPSEEK/X; do
    run "$SCRIPT" w1 pane1 worker "$arg"
    [ "$status" -eq 2 ]
  done
  [ ! -e "$HERDR_CALLS" ]
}

@test "other extra args pass through" {
  run "$SCRIPT" w1 pane1 worker --thinking low
  [ "$status" -eq 0 ]
  [[ "$(cat "$HERDR_CALLS")" == *"--models lmstudio/qwen/qwen3.6-35b-a3b --thinking low" ]] || false
}

@test "a model without a provider prefix and a bad tier are refused" {
  printf '[models]\npi_worker = "qwen-only"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  run "$SCRIPT" w1 pane1 big
  [ "$status" -eq 2 ]
}

@test "a tracked .orchestration.local.toml does not opt in" {
  printf '[pi]\ndeepseek = true\n' >"$TMP/repo/.orchestration.local.toml"
  git -C "$TMP/repo" add -f .orchestration.local.toml
  run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 1 ]
  [[ "$output" == *"DeepSeek is off"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "an exported GIT_DIR cannot point the opt-in at another repo" {
  git init -q "$TMP/opted"
  printf '[pi]\ndeepseek = true\n' >"$TMP/opted/.orchestration.local.toml"
  GIT_DIR="$TMP/opted/.git" run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 1 ]
  [ ! -e "$HERDR_CALLS" ]
}

@test "DeepSeek named in the model id of another provider needs the opt-in" {
  printf '[models]\npi_worker = "openrouter/DeepSeek/deepseek-v4-pro"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"DeepSeek is off"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
  printf '[pi]\ndeepseek = true\n' >"$TMP/repo/.orchestration.local.toml"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
}

@test "an empty provider is refused" {
  printf '[models]\npi_worker = "/deepseek-v4-pro"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"provider/model-id"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "the refusal tells the session not to edit the file" {
  run "$SCRIPT" w1 pane1 smart
  [[ "$output" == *"Only the user can opt a repo in"* ]] || false
  [[ "$output" == *"Ask the user; do not edit the file."* ]] || false
}

@test "extra args cannot load extensions, keys or old sessions" {
  for arg in -e --extension --extension=x --api-key --session --session-id --session-id=x --session-dir --fork --resume --continue -r -c -a --approve; do
    run "$SCRIPT" w1 pane1 worker "$arg"
    [ "$status" -eq 2 ]
  done
  [ ! -e "$HERDR_CALLS" ]
}

@test "the model scope is pinned to the one model so Ctrl+P has nothing to cycle to" {
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [[ "$(cat "$HERDR_CALLS")" == *" --model qwen/qwen3.6-35b-a3b --models lmstudio/qwen/qwen3.6-35b-a3b" ]] || false
}

@test "herdr is called by the configured absolute path, not the one on PATH" {
  mkdir -p "$TMP/real"
  sed "s#HERDR_CALLS#HERDR_REAL_CALLS#" "$TMP/bin/herdr" >"$TMP/real/herdr"
  chmod +x "$TMP/real/herdr"
  printf '[herdr]\nbin = "%s"\n[models]\npi_worker = "lmstudio/q"\n' "$TMP/real/herdr" >"$ORCH_CONFIG"
  HERDR_REAL_CALLS="$TMP/real.calls" run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ ! -e "$HERDR_CALLS" ]
  [ -s "$TMP/real.calls" ]
}

@test "a configured herdr path works when no herdr is on PATH" {
  mkdir -p "$TMP/real"
  sed "s#HERDR_CALLS#HERDR_REAL_CALLS#" "$TMP/bin/herdr" >"$TMP/real/herdr"
  chmod +x "$TMP/real/herdr"
  rm "$TMP/bin/herdr"
  only_system_path
  printf '[herdr]\nbin = "%s"\n[models]\npi_worker = "lmstudio/q"\n' "$TMP/real/herdr" >"$ORCH_CONFIG"
  HERDR_REAL_CALLS="$TMP/real.calls" run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ -s "$TMP/real.calls" ]
}

@test "a herdr that is neither configured nor on PATH is refused before anything runs" {
  rm "$TMP/bin/herdr"
  only_system_path
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -ne 0 ]
  [[ "$output" == *"herdr"* ]] || false
}

@test "an unreadable git index does not let the personal file opt in" {
  printf '[pi]\ndeepseek = true\n' >"$TMP/repo/.orchestration.local.toml"
  printf 'not an index' >"$TMP/repo/.git/index"
  run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 1 ]
  [[ "$output" == *"DeepSeek is off"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "pi left out of providers.enabled is refused before anything runs" {
  printf '[providers]\nenabled = ["claude", "codex"]\n' >>"$ORCH_CONFIG"
  run "$SCRIPT" w1 p1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *'pi is off; add "pi" to providers.enabled (ORCH_PROVIDERS)'* ]]
  [ ! -e "$HERDR_CALLS" ]
}

@test "a providers list without claude stops the pi start with the settings error" {
  printf '[providers]\nenabled = ["pi"]\n' >>"$ORCH_CONFIG"
  run "$SCRIPT" w1 p1 worker
  [ "$status" -eq 2 ]
  [[ "$output" == *'providers.enabled: must include "claude"'* ]]
  [ ! -e "$HERDR_CALLS" ]
}

@test "with pi models empty, a list without pi says pi is off and the automatic list says pi is not configured" {
  printf '[providers]\nenabled = ["claude"]\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 p1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *'pi is off; add "pi" to providers.enabled'* ]]
  : >"$ORCH_CONFIG"
  run "$SCRIPT" w1 p1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *'pi is not configured; set models.pi_worker'* ]]
  [ ! -e "$HERDR_CALLS" ]
}

@test "with pi models empty, an exported list without claude is the settings error" {
  : >"$ORCH_CONFIG"
  ORCH_PROVIDERS=codex run "$SCRIPT" w1 p1 worker
  [ "$status" -eq 2 ]
  [[ "$output" == *'providers.enabled (ORCH_PROVIDERS): must include "claude"'* ]]
  [ ! -e "$HERDR_CALLS" ]
}

@test "a model listed in pi_models starts with that provider, model and one --models entry" {
  printf '[models]\npi_models = ["lmstudio/other/model-b", "lmstudio/model-c"]\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 lmstudio/other/model-b
  [ "$status" -eq 0 ]
  [ "$(cat "$HERDR_CALLS")" = "agent start w1 --kind pi --pane pane1 -- --provider lmstudio --model other/model-b --models lmstudio/other/model-b" ]
}

@test "a model that is not exactly one pi_models entry exits 2" {
  printf '[models]\npi_worker = "lmstudio/w"\npi_models = ["lmstudio/model-b"]\n' >"$ORCH_CONFIG"
  for id in lmstudio/model lmstudio/model-b-2 model-b lmstudio/w lmstudio/other; do
    run "$SCRIPT" w1 pane1 "$id"
    [ "$status" -eq 2 ]
  done
  [ ! -e "$HERDR_CALLS" ]
}

@test "a listed DeepSeek model is refused in a repo that did not opt in" {
  printf '[models]\npi_models = ["deepseek/listed"]\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 deepseek/listed
  [ "$status" -eq 1 ]
  [[ "$output" == *"DeepSeek is off"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
  printf '[pi]\ndeepseek = true\n' >"$TMP/repo/.orchestration.local.toml"
  run "$SCRIPT" w1 pane1 deepseek/listed
  [ "$status" -eq 0 ]
}

@test "pi_models alone counts as configured; worker with an empty slot still fails" {
  printf '[models]\npi_models = ["lmstudio/model-b"]\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 lmstudio/model-b
  [ "$status" -eq 0 ]
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"pi is not configured"* ]] || false
}

@test "a glob or comma in the model argument never matches a pi_models entry" {
  printf '[models]\npi_models = ["a/x"]\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 a/x
  [ "$status" -eq 0 ]
  rm -f "$HERDR_CALLS"
  printf '[models]\npi_models = ["lmstudio/qwen/x", "deepseek/deepseek-flash", "a/x", "b/y"]\n' >"$ORCH_CONFIG"
  for id in 'lmstudio/*' '?mstudio/qwen/x' '[d]eepseek/[d]eepseek-flash' 'a/x,b/y' 'a/x b/y' '*'; do
    run "$SCRIPT" w1 pane1 "$id"
    [ "$status" -eq 2 ]
  done
  [ ! -e "$HERDR_CALLS" ]
}

@test "list entries with whitespace or a glob are never expanded, and a file cannot stand in" {
  touch "$TMP/repo/lmstudio-evil"
  mkdir -p "$TMP/repo/lmstudio"
  touch "$TMP/repo/lmstudio/evil"
  export ORCH_PI_MODELS="lmstudio/a b,lmstudio/*"
  for id in lmstudio/a b lmstudio/evil; do
    run "$SCRIPT" w1 pane1 "$id"
    [ "$status" -eq 2 ]
  done
  [ ! -e "$HERDR_CALLS" ]
}
