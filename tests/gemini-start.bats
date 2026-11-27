#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031,SC2154 # Bats runs each test in a subshell and assigns status and output.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  SCRIPT="$ROOT/coordinator/gemini-start.sh"
  TMP=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/gemini-start-bats.XXXXXX")" && pwd -P)
  export HOME="$TMP/home"
  export PATH="$TMP/bin:$PATH"
  export HERDR_CALLS="$TMP/herdr.calls"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  export HERDR_PANE_CWD="$TMP"
  unset ORCH_GEMINI_WORKER_MODEL ORCH_GEMINI_SMART_MODEL ORCH_PROVIDERS ORCH_HEAVY_SLOTS_DIR GIT_CONFIG_GLOBAL XDG_CONFIG_HOME
  mkdir -p "$HOME" "$TMP/bin"
  printf '[models]\ngemini_worker = "gemini-4-neon-medium"\ngemini_smart = "gemini-4-neon-high"\n' >"$ORCH_CONFIG"
  cat >"$TMP/bin/herdr" <<'SH'
#!/bin/sh
if [ "$1 $2" = "pane get" ]; then
  printf '{"result":{"pane":{"cwd":"%s"}}}\n' "$HERDR_PANE_CWD"
  exit 0
fi
printf '%s\n' "$*" >>"$HERDR_CALLS"
SH
  chmod +x "$TMP/bin/herdr"
  ln -s "$(command -v python3)" "$TMP/bin/python3"
  PLUGIN="$HOME/.gemini/config/plugins/ai-agent-orchestration"
  mkdir -p "$PLUGIN/rules" "$HOME/.claude"
  printf '{}\n' >"$PLUGIN/plugin.json"
  printf 'rules\n' >"$HOME/.claude/CLAUDE.md"
  ln -s "$HOME/.claude/CLAUDE.md" "$PLUGIN/rules/AGENTS.md"
  printf 'sandbox\n' >"$PLUGIN/rules/orch-agy-sandbox.md"
}

# allow_rules: the permissions.allow entries of the --settings JSON in the agent start call, one per line.
allow_rules() {
  python3 -c 'import json, sys; line = open(sys.argv[1]).read(); print("\n".join(json.loads(line.split(" --settings ", 1)[1])["permissions"]["allow"]))' "$HERDR_CALLS"
}

# has_rule <rule>: the start call grants exactly this rule.
has_rule() {
  allow_rules | grep -qxF "$1"
}

teardown() {
  rm -rf "$TMP"
}

@test "worker tier starts agy with --model and --mode auto" {
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [[ "$(cat "$HERDR_CALLS")" == "agent start w1 --kind agy --pane pane1 -- --model gemini-4-neon-medium --mode auto --settings {"* ]] || false
}

@test "smart tier starts agy with the smart model" {
  run "$SCRIPT" w1 pane1 smart
  [ "$status" -eq 0 ]
  [[ "$(cat "$HERDR_CALLS")" == "agent start w1 --kind agy --pane pane1 -- --model gemini-4-neon-high --mode auto --settings {"* ]] || false
}

@test "one set Gemini model serves both tiers" {
  printf '[models]\ngemini_worker = ""\ngemini_smart = "gemini-4-neon-high"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [[ "$(cat "$HERDR_CALLS")" == "agent start w1 --kind agy --pane pane1 -- --model gemini-4-neon-high --mode auto --settings {"* ]] || false
}

@test "an empty tier setting with no fallback means gemini is not configured" {
  printf '[models]\ngemini_worker = ""\ngemini_smart = ""\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"gemini is not configured"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "placeholder models with gemini listed are refused" {
  printf '[providers]\nenabled = ["claude", "gemini"]\n[models]\ngemini_worker = "<gemini-worker-model>"\ngemini_smart = "<gemini-smart-model>"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"gemini is not configured"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "extra args cannot change the model or mode" {
  for arg in --model --model=x --mode --mode=plan --dangerously-skip-permissions --remote-agent --remote-control --sandbox --continue --conversation --settings --unknown; do
    run "$SCRIPT" w1 pane1 worker "$arg"
    [ "$status" -eq 2 ]
    [[ "$output" == *"takes no extra arguments"* ]] || false
  done
  [ ! -e "$HERDR_CALLS" ]
}

@test "a bad tier is refused with the script's own message" {
  run "$SCRIPT" w1 pane1 fast
  [ "$status" -eq 2 ]
  [[ "$output" == *"tier must be worker or smart, not 'fast'"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "sourcing from zsh is refused" {
  if ! command -v zsh >/dev/null 2>&1; then
    skip "zsh not installed"
  fi
  run zsh -c 'source "$1"' -- "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"run it, do not source it"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "gemini left out of providers.enabled is refused" {
  printf '[providers]\nenabled = ["claude", "codex"]\n' >>"$ORCH_CONFIG"
  run "$SCRIPT" w1 p1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"gemini is off; add \"gemini\" to providers.enabled (ORCH_PROVIDERS)"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "empty providers.enabled turns gemini on when a model is set" {
  printf '[models]\ngemini_worker = "gemini-4-neon-medium"\n' >"$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [[ "$(cat "$HERDR_CALLS")" == "agent start w1 --kind agy --pane pane1 -- --model gemini-4-neon-medium --mode auto --settings {"* ]] || false
}

@test "a missing agy plugin manifest is refused" {
  rm "$PLUGIN/plugin.json"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"$PLUGIN/plugin.json is missing or dangling"*"run install.sh"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "a dangling agy rules link is refused" {
  rm "$HOME/.claude/CLAUDE.md"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"$PLUGIN/rules/AGENTS.md is missing or dangling"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "a missing agy sandbox rule is refused" {
  rm "$PLUGIN/rules/orch-agy-sandbox.md"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"$PLUGIN/rules/orch-agy-sandbox.md is missing or dangling"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
}

@test "agy gets read rules for the orch checkout and config and write rules for reports and slots" {
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($ROOT)"
  has_rule "read_file($ORCH_CONFIG)"
  has_rule "write_file($HOME/.claude/worker-reports)"
  has_rule "write_file($HOME/.claude/heavy-slots)"
  [ -d "$HOME/.claude/worker-reports" ]
  [ -d "$HOME/.claude/heavy-slots" ]
  ! allow_rules | grep -qvE '^(read_file|write_file)\(/' || false
}

@test "a configured heavy-slot dir gets the write rule" {
  export ORCH_HEAVY_SLOTS_DIR="$TMP/slots"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "write_file($TMP/slots)"
  ! has_rule "write_file($HOME/.claude/heavy-slots)" || false
}

@test "the global git config, its includes, the excludes file and the worktree's git dir are readable" {
  printf '[include]\n\tpath = ~/.gitconfig.local\n[core]\n\texcludesFile = ~/.gitignore_global\n' >"$HOME/.gitconfig"
  printf '[user]\n\tname = probe\n' >"$HOME/.gitconfig.local"
  : >"$HOME/.gitignore_global"
  git init -q "$TMP/main"
  git -C "$TMP/main" -c user.email=a@b -c user.name=a commit -q --allow-empty -m init
  git -C "$TMP/main" worktree add -q "$TMP/wt" 2>/dev/null
  export HERDR_PANE_CWD="$TMP/wt"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($HOME/.gitconfig)"
  has_rule "read_file($HOME/.gitconfig.local)"
  has_rule "read_file($HOME/.gitignore_global)"
  has_rule "read_file($TMP/main/.git)"
}

@test "paths that do not exist get no rule" {
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($ORCH_CONFIG)"
  ! allow_rules | grep -qF 'orchestration.env' || false
  ! allow_rules | grep -qF '.git)' || false
  ! allow_rules | grep -qF 'gitconfig' || false
}

@test "a symlinked path is granted at its link and its target" {
  mkdir -p "$TMP/elsewhere"
  printf 'X=1\n' >"$TMP/elsewhere/orchestration.env"
  ln -s "$TMP/elsewhere/orchestration.env" "$HOME/.claude/orchestration.env"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($HOME/.claude/orchestration.env)"
  has_rule "read_file($TMP/elsewhere/orchestration.env)"
}
