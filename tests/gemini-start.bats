#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031,SC2154 # Bats runs each test in a subshell and assigns status and output.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  SCRIPT="$ROOT/coordinator/gemini-start.sh"
  TMP=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/gemini-start-bats.XXXXXX")" && pwd -P)
  export HOME="$TMP/home"
  host_path=$PATH
  export PATH="$TMP/bin:$PATH"
  export HERDR_CALLS="$TMP/herdr.calls"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  unset ORCH_GEMINI_WORKER_MODEL ORCH_GEMINI_SMART_MODEL ORCH_PROVIDERS ORCH_HEAVY_SLOTS_DIR GIT_CONFIG_GLOBAL XDG_CONFIG_HOME
  mkdir -p "$HOME" "$TMP/bin" "$TMP/work tree"
  printf '[models]\ngemini_worker = "gemini-4-neon-medium"\ngemini_smart = "gemini-4-neon-high"\n' >"$ORCH_CONFIG"
  export PANE_CWD="$TMP/work tree"
  SETTINGS="$HOME/.gemini/antigravity-cli/settings.json"
  cat >"$TMP/bin/herdr" <<'SH'
#!/bin/sh
if [ "$1 $2" = "pane get" ]; then
  jq -n --arg cwd "${PANE_CWD_FOR_PANE:-$PANE_CWD}" '{result:{pane:{cwd:$cwd}}}'
  exit
fi
[ ! -f "$HOME/.gemini/antigravity-cli/settings.json" ] || cp "$HOME/.gemini/antigravity-cli/settings.json" "$HERDR_CALLS.settings-at-start"
printf '%s\n' "$*" >>"$HERDR_CALLS"
SH
  chmod +x "$TMP/bin/herdr"
  # Version-manager shims fail under a temp HOME, so system copies come first.
  for tool in python3 jq; do
    ln -s "$(PATH="/usr/bin:/bin:$host_path" command -v "$tool")" "$TMP/bin/$tool"
  done
  PLUGIN="$HOME/.gemini/config/plugins/ai-agent-orchestration"
  mkdir -p "$PLUGIN/rules" "$HOME/.claude"
  printf '{}\n' >"$PLUGIN/plugin.json"
  printf '{}\n' >"$PLUGIN/hooks.json"
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
  [ ! -e "$HOME/.gemini/antigravity-cli/settings.json" ]
}

@test "a missing agy guard hook is refused" {
  rm "$PLUGIN/hooks.json"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"$PLUGIN/hooks.json is missing, so agy would run commands without the Bash guard"* ]] || false
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
  export PANE_CWD="$TMP/wt"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($HOME/.gitconfig)"
  has_rule "read_file($HOME/.gitconfig.local)"
  has_rule "read_file($HOME/.gitignore_global)"
  has_rule "read_file($TMP/main/.git)"
}

@test "an includeIf for the pane's repo and git's default ignore and attributes files are readable" {
  git init -q "$TMP/main"
  printf '[includeIf "gitdir:%s/main/"]\n\tpath = ~/.gitconfig.work\n' "$TMP" >"$HOME/.gitconfig"
  printf '[user]\n\tname = work\n' >"$HOME/.gitconfig.work"
  mkdir -p "$HOME/.config/git"
  : >"$HOME/.config/git/ignore"
  : >"$HOME/.config/git/attributes"
  export PANE_CWD="$TMP/main"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($HOME/.gitconfig.work)"
  has_rule "read_file($HOME/.config/git/ignore)"
  has_rule "read_file($HOME/.config/git/attributes)"
}

@test "an excludes file set in an included git config is readable" {
  printf '[include]\n\tpath = ~/.gitconfig.inc\n' >"$HOME/.gitconfig"
  printf '[core]\n\texcludesFile = ~/.ignore.inc\n' >"$HOME/.gitconfig.inc"
  : >"$HOME/.ignore.inc"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($HOME/.ignore.inc)"
}

@test "paths that do not exist get no rule" {
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($ORCH_CONFIG)"
  ! allow_rules | grep -qF '.config/git' || false
  ! allow_rules | grep -qF '.git)' || false
  ! allow_rules | grep -qF 'gitconfig' || false
}

@test "a symlinked path is granted at its link and its target" {
  mkdir -p "$TMP/elsewhere"
  mv "$ORCH_CONFIG" "$TMP/elsewhere/orchestration.toml"
  ln -s "$TMP/elsewhere/orchestration.toml" "$ORCH_CONFIG"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  has_rule "read_file($ORCH_CONFIG)"
  has_rule "read_file($TMP/elsewhere/orchestration.toml)"
}

trusted() {
  jq -c '.trustedWorkspaces' "$SETTINGS"
}

mode_of() {
  python3 -c 'import os, stat, sys; print(oct(stat.S_IMODE(os.stat(sys.argv[1]).st_mode)))' "$1"
}

settings_dir() {
  (cd "${SETTINGS%/*}" && shopt -s dotglob && printf '%s ' *)
}

seed_settings() {
  mkdir -p "${SETTINGS%/*}"
  printf '%s\n' "$1" >"$SETTINGS"
}

@test "a symlinked pane folder is trusted by its resolved path" {
  ln -s "$PANE_CWD" "$TMP/link"
  PANE_CWD_FOR_PANE="$TMP/link" run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ "$(trusted)" = "[\"$PANE_CWD\"]" ]
}

@test "other keys, existing entries and the file mode are kept" {
  seed_settings '{"model": "m", "permissions": {"deny": ["command(git clean)"]}, "note": "é", "trustedWorkspaces": ["/", "/a"]}'
  chmod 640 "$SETTINGS"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ "$(jq -cS 'del(.trustedWorkspaces)' "$SETTINGS")" = '{"model":"m","note":"é","permissions":{"deny":["command(git clean)"]}}' ]
  [ "$(trusted)" = "[\"/\",\"/a\",\"$PANE_CWD\"]" ]
  grep -q 'é' "$SETTINGS"
  [ "$(mode_of "$SETTINGS")" = 0o640 ]
}

@test "a trusted folder is not added twice and the file is not rewritten" {
  seed_settings "{\"trustedWorkspaces\":[\"$PANE_CWD\"]}"
  cp "$SETTINGS" "$TMP/before"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  cmp "$TMP/before" "$SETTINGS"
}

@test "a missing settings file is created private, with the folder, before agy starts" {
  umask 022
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ "$(jq -c '.trustedWorkspaces' "$HERDR_CALLS.settings-at-start")" = "[\"$PANE_CWD\"]" ]
  [ "$(mode_of "$SETTINGS")" = 0o600 ]
}

refused_unchanged() {
  seed_settings "$1"
  cp "$SETTINGS" "$TMP/before"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"gemini-start.sh: $SETTINGS $2; fix it"* ]] || false
  [[ "$output" != *Traceback* ]] || false
  cmp "$TMP/before" "$SETTINGS"
  [ ! -e "$HERDR_CALLS" ]
}

@test "broken JSON is refused and left unchanged" {
  refused_unchanged '{"trustedWorkspaces": [' 'is not valid JSON'
}

@test "a top level that is not an object is refused and left unchanged" {
  refused_unchanged '[]' 'is not a JSON object'
}

@test "trustedWorkspaces that is not a list is refused and left unchanged" {
  refused_unchanged '{"trustedWorkspaces": "/a"}' 'has a trustedWorkspaces that is not a list'
}

@test "a symlinked settings file stays a link and its target is updated" {
  mkdir -p "${SETTINGS%/*}" "$TMP/dotfiles"
  printf '{}\n' >"$TMP/dotfiles/agy.json"
  ln -s "$TMP/dotfiles/agy.json" "$SETTINGS"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ -L "$SETTINGS" ]
  [ "$(jq -c '.trustedWorkspaces' "$TMP/dotfiles/agy.json")" = "[\"$PANE_CWD\"]" ]
}

@test "the file is replaced by rename and no temp file stays" {
  seed_settings '{}'
  ln "$SETTINGS" "$TMP/old-inode"
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ "$(cat "$TMP/old-inode")" = '{}' ]
  [ "$(settings_dir)" = 'settings.json settings.json.orch-lock ' ]
}

@test "a failed write leaves the file and no temp file, and agy does not start" {
  seed_settings '{}'
  mkdir "$TMP/inject"
  printf 'import os\ndef fail(*a, **k):\n    raise OSError("disk full")\nos.replace = fail\n' >"$TMP/inject/fault.py"
  loader=$(. "$ROOT/coordinator/watchers/coord-lib.sh" && orch_python)
  # -I ignores PYTHONPATH, so the wrapper loads the fault itself.
  cat >"$TMP/bin/failing-python" <<SH
#!/bin/sh
if [ "\$2" = - ]; then
  { cat "$TMP/inject/fault.py"; cat; } >"$TMP/inject/run.py"
  shift 2
  exec "$loader" -I "$TMP/inject/run.py" "\$@"
fi
exec "$loader" "\$@"
SH
  chmod +x "$TMP/bin/failing-python"
  ORCH_LOADER_PYTHON="$TMP/bin/failing-python" run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot write $SETTINGS: disk full"* ]] || false
  [ "$(cat "$SETTINGS")" = '{}' ]
  [ "$(settings_dir)" = 'settings.json settings.json.orch-lock ' ]
  [ ! -e "$HERDR_CALLS" ]
}

@test "concurrent starts keep both folders" {
  mkdir -p "$TMP/a" "$TMP/b" "${SETTINGS%/*}"
  # A large file widens the gap between read and write, so unlocked writers lose an entry.
  python3 -c 'import json, sys; json.dump({"pad": ["x" * 100] * 100000, "trustedWorkspaces": []}, open(sys.argv[1], "w"))' "$SETTINGS"
  exec 9>"$SETTINGS.orch-lock"
  python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX)'
  PANE_CWD="$TMP/a" "$SCRIPT" w1 pane1 worker 9>&- &
  first=$!
  PANE_CWD="$TMP/b" "$SCRIPT" w2 pane2 worker 9>&- &
  second=$!
  sleep 2
  held=$(trusted)
  exec 9>&-
  wait "$first"
  wait "$second"
  [ "$held" = '[]' ]
  [ "$(jq -c '.trustedWorkspaces | sort' "$SETTINGS")" = "[\"$TMP/a\",\"$TMP/b\"]" ]
}

@test "an unreadable pane directory is refused" {
  cat >"$TMP/bin/herdr" <<'SH'
#!/bin/sh
[ "$1" = pane ] && exit 1
printf x >>"$HERDR_CALLS"
SH
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot read the directory of pane pane1"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
  [ ! -e "$SETTINGS" ]
}

@test "a pane directory that is not absolute is refused" {
  PANE_CWD_FOR_PANE=relative run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 1 ]
  [[ "$output" == *"pane pane1 has no absolute directory"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
  [ ! -e "$SETTINGS" ]
}

@test "a missing settings directory is created private" {
  umask 022
  run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 0 ]
  [ "$(mode_of "${SETTINGS%/*}")" = 0o700 ]
}

@test "a host without jq is refused by name" {
  loader=$(. "$ROOT/coordinator/watchers/coord-lib.sh" && orch_python)
  # A PATH of only these tools, so no system jq is on it.
  mkdir "$TMP/nojq"
  for tool in env bash sh dirname readlink; do
    ln -s "$(PATH="/usr/bin:/bin" command -v "$tool")" "$TMP/nojq/$tool"
  done
  ln -s "$TMP/bin/herdr" "$TMP/nojq/herdr"
  PATH="$TMP/nojq" ORCH_LOADER_PYTHON="$loader" run "$SCRIPT" w1 pane1 worker
  [ "$status" -eq 2 ]
  [[ "$output" == *"required command not found on PATH: jq"* ]] || false
  [ ! -e "$HERDR_CALLS" ]
  [ ! -e "$SETTINGS" ]
}
