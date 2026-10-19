#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031 # Bats runs each test in a subshell.

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/settings-bats.XXXXXX")
  TMP=$(cd "$TMP" && pwd -P)
  export HOME="$TMP/home"
  export ORCH_CONFIG="$TMP/orchestration.toml"
  SETTINGS="$ROOT/lib/orch_settings.py"
  mkdir -p "$HOME"
  for name in $(python3 "$SETTINGS" names); do unset "$name"; done
}

teardown() {
  rm -rf "$TMP"
}

# load_settings <global|repo dir>: prints NAME=value for every setting after loading.
load_settings() {
  # shellcheck disable=SC2016 # The child shell expands these.
  bash -c '. "$1/coordinator/watchers/coord-lib.sh"
    if [ "$2" = global ]; then orch_load_config; else orch_load_repo_config "$2"; fi
    for name in $ORCH_SETTINGS; do [ -z "${!name+x}" ] || printf "%s=%s\n" "$name" "${!name}"; done' _ "$ROOT" "$1"
}

# every_value <global|repo>: a TOML file setting each setting of the scope to a value unlike its default.
every_value() {
  python3 - "$ROOT/lib" "$1" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import orch_settings as s
values = {'str': 'from-file', 'int': 7, 'number': 7.5, 'bool': True, 'list': ['a', 'b'], 'json': [{'name': 'file'}]}
print(s.render({setting: values[setting.kind] for setting in s.scoped(sys.argv[2])}), end='')
PY
}

@test "a valid file sets the settings scripts read" {
  printf '[pr]\nauthor = "me"\n[recycle]\npark_idle = true\n[watchers]\npath = ["~/bin", "/usr/bin"]\n' >"$ORCH_CONFIG"

  run load_settings global

  [ "$status" -eq 0 ]
  [ "$output" = "ORCH_PR_AUTHOR=me
ORCH_PARK_IDLE=1
ORCH_PATH=$HOME/bin:/usr/bin" ] || { echo "$output"; false; }
}

@test "an exported variable wins over the global file for every global setting" {
  every_value global >"$ORCH_CONFIG"
  run load_settings global
  [ "$status" -eq 0 ]
  [ "$(printf '%s\n' "$output" | grep -c .)" -eq "$(python3 "$SETTINGS" keys "$ORCH_CONFIG" | wc -w)" ]
  for name in $(python3 "$SETTINGS" keys "$ORCH_CONFIG"); do
    [[ "$output" != *"$name=env-$name"* ]]
    export "$name=env-$name"
  done

  run load_settings global

  [ "$status" -eq 0 ]
  for name in $(python3 "$SETTINGS" keys "$ORCH_CONFIG"); do
    printf '%s\n' "$output" | grep -qxF "$name=env-$name"
  done
}

@test "a variable set but not exported also wins over the file" {
  printf '[heavy_slots]\nmax = 2\ndir = "/from/file"\n' >"$ORCH_CONFIG"

  # shellcheck disable=SC2016 # The child shell expands these.
  run bash -c '. "$1/coordinator/watchers/coord-lib.sh"; ORCH_HEAVY_SLOTS_DIR=/shell; orch_load_config; echo "$ORCH_HEAVY_SLOTS_MAX $ORCH_HEAVY_SLOTS_DIR"' _ "$ROOT"

  [ "$status" -eq 0 ]
  [ "$output" = "2 /shell" ]
}

@test "an exported variable wins over both repo files for every repo setting" {
  mkdir -p "$TMP/repo/sub"
  git -C "$TMP/repo" init -q
  every_value repo >"$TMP/repo/.orchestration.toml"
  printf '[focus]\npattern = "local"\n' >"$TMP/repo/.orchestration.local.toml"
  run load_settings "$TMP/repo/sub"
  [ "$status" -eq 0 ]
  printf '%s\n' "$output" | grep -qxF "ORCH_FOCUS_PATTERN=local"
  printf '%s\n' "$output" | grep -qxF "ORCH_BIG_REPO=1"
  names=$(printf '%s\n' "$output" | sed 's/=.*//')
  [ "$(printf '%s\n' "$names" | wc -l)" -eq 7 ]
  for name in $names; do export "$name=env-$name"; done

  run load_settings "$TMP/repo/sub"

  [ "$status" -eq 0 ]
  for name in $names; do
    printf '%s\n' "$output" | grep -qxF "$name=env-$name"
  done
}

@test "outside a checkout every repo setting has its built-in default" {
  run load_settings "$TMP"

  [ "$status" -eq 0 ]
  printf '%s\n' "$output" | grep -qxF "ORCH_BIG_REPO=0"
  printf '%s\n' "$output" | grep -qxF "ORCH_FOCUS_PATTERN="
}

@test "each config problem stops with one error naming the file and key" {
  while IFS='|' read -r text problem; do
    printf '%b' "$text" >"$ORCH_CONFIG"
    run load_settings global
    [ "$status" -eq 2 ]
    [ "$output" = "ERROR: $ORCH_CONFIG: $problem" ] || { echo "$output"; false; }
  done <<'CASES'
[pr]\nnope = 1\n|pr.nope: unknown key
[heavy_slots]\nmax = "4"\n|heavy_slots.max: must be an integer, got str
[guard]\nbig_repo = true\n|guard.big_repo: is a repo setting; set it in a repo's .orchestration.toml or .orchestration.local.toml
CASES
  printf '[pr\n' >"$ORCH_CONFIG"
  run load_settings global
  [ "$status" -eq 2 ]
  [[ "$output" == "ERROR: $ORCH_CONFIG: invalid TOML: "* ]]
}

@test "a global key in a repo file stops with an error naming the file and key" {
  mkdir "$TMP/repo"
  git -C "$TMP/repo" init -q
  printf '[heavy_slots]\nmax = 2\n' >"$TMP/repo/.orchestration.local.toml"

  run load_settings "$TMP/repo"

  [ "$status" -eq 2 ]
  [[ "$output" == "ERROR: "*"/repo/.orchestration.local.toml: heavy_slots.max: is a global setting; set it in the global ~/.claude/orchestration.toml" ]]
}

@test "no Python 3.11 or newer stops the loader with what to install" {
  mkdir "$TMP/oldpy"
  for name in python3 python3.11 python3.12 python3.13 python3.14; do
    printf '#!/bin/sh\nexit 1\n' >"$TMP/oldpy/$name"
    chmod +x "$TMP/oldpy/$name"
  done
  printf '[pr]\nauthor = "me"\n' >"$ORCH_CONFIG"

  PATH="$TMP/oldpy:$PATH" ORCH_PYTHON_FALLBACKS="" run load_settings global

  [ "$status" -eq 2 ]
  [[ "$output" == "ERROR: reading the TOML config needs Python 3.11 or newer; none found"* ]]
  [[ "$output" == *"brew install python"* ]]
}

@test "an exported ORCH_PYTHON3 of 3.11 or newer runs the loader" {
  mkdir "$TMP/oldpy"
  printf '#!/bin/sh\nexit 1\n' >"$TMP/oldpy/python3"
  chmod +x "$TMP/oldpy/python3"
  printf '[pr]\nauthor = "me"\n' >"$ORCH_CONFIG"

  ORCH_PYTHON3="$(command -v python3)" PATH="$TMP/oldpy:$PATH" ORCH_PYTHON_FALLBACKS="" run load_settings global

  [ "$status" -eq 0 ]
  printf '%s\n' "$output" | grep -qxF "ORCH_PR_AUTHOR=me"
}

@test "a missing global file with an old orchestration.env asks for install.sh" {
  unset ORCH_CONFIG
  mkdir -p "$HOME/.claude"
  printf 'ORCH_PR_AUTHOR="me"\n' >"$HOME/.claude/orchestration.env"

  run load_settings global

  [ "$status" -eq 2 ]
  [[ "$output" == *"orchestration.env is not read any more. Run install.sh to convert it."* ]]
}

@test "the old and the new loader give the same values for a converted file" {
  old="$TMP/orchestration.env"
  # shellcheck disable=SC2016 # The old loader expands $HOME when it sources the file.
  cat >"$old" <<'ENV'
# Watched repos
ORCH_PR_AUTHOR="me"
ORCH_PR_SINCE=""
ORCH_PACKS_MAX="50"
ORCH_HEAVY_SLOTS_MAX="8"
ORCH_RECYCLE_PCT="55"
ORCH_PARK_IDLE="0"
ORCH_WATCHERS_SERVICE="1"
ORCH_ALERT_ESCALATE_RATIO="1.5"
ORCH_HOG_GB="0.5"
ORCH_PATH="$HOME/.local/bin:/usr/bin:/bin"
ORCH_PR_TABS_OPEN_ALL="is:pr is:open author:me"
ORCH_PR_TABS_TOPICS='[{"name":"W","color":"blue","any":["\\bw\\b","a|b"]}]'
ORCH_REPO_PATH=""
ENV
  # shellcheck disable=SC2016 # The child shell expands these.
  old_values=$(bash -c 'set +u; . "$1"; set | grep "^ORCH_" | grep -v "^ORCH_CONFIG="' _ "$old")

  run python3 "$SETTINGS" migrate "$old" "$ORCH_CONFIG" "$TMP/snippets"
  [ "$status" -eq 0 ]
  grep -qx '# Watched repos' "$ORCH_CONFIG"
  # shellcheck disable=SC2016 # The child shell expands these.
  new_values=$(bash -c '. "$1/coordinator/watchers/coord-lib.sh"; orch_load_config; set | grep "^ORCH_" | grep -v "^ORCH_SETTINGS=\|^ORCH_CONFIG=\|^ORCH_LOADER_PYTHON="' _ "$ROOT")

  diff <(echo "$old_values") <(echo "$new_values")
}

migration_fixture() {
  CLONE="$TMP/clone"
  git init -q "$CLONE"
  git -C "$CLONE" remote add origin git@github.com:acme/webapp.git
  OLD="$TMP/orchestration.env"
  cat >"$OLD" <<ENV
ORCH_PR_REPOS="acme/webapp acme/tools"
ORCH_GIT_WATCH_REPOS="$CLONE"
ORCH_WATCHERS_PRIMARY_REPO="acme/webapp"
ORCH_BIG_REPO_REMOTES="acme/webapp"
ORCH_LEGACY_REPOS="acme/webapp"
ORCH_LEGACY_EXTRA_PATTERN="old-widget"
ORCH_LEGACY_EXEMPT_PATHS="a.js b.js"
ORCH_MERGE_GATE_CHECKS="Gate A|Gate B"
ORCH_FOCUS_PATTERN="billing"
ORCH_FOCUS_ARCHITECT="Billing"
ORCH_V2_PR_SCOPE="org:acme"
ORCH_SOMETHING_ELSE="x"
ENV
}

@test "migration keeps repo values out of the global file and writes them to the clone or a snippet" {
  migration_fixture

  run python3 "$SETTINGS" migrate "$OLD" "$ORCH_CONFIG" "$TMP/snippets"

  [ "$status" -eq 0 ]
  [[ "$output" == *"renamed ORCH_V2_PR_SCOPE to ORCH_WATCHERS_PR_SCOPE"* ]]
  [[ "$output" == *"not converted (unknown keys): ORCH_SOMETHING_ELSE"* ]]
  [[ "$output" == *"created $CLONE/.orchestration.local.toml (acme/webapp)"* ]]
  [[ "$output" == *"added .orchestration.local.toml to the info/exclude of $CLONE"* ]]
  [[ "$output" == *"wrote $TMP/snippets/acme/tools.toml"* ]]
  [ "$(grep -cE '^\[guard\]|^(big_repo|scan|extra_pattern|exempt_paths|merge_gate_checks|pattern|architect) =' "$ORCH_CONFIG")" = 0 ]
  grep -qx 'pr_scope = "org:acme"' "$ORCH_CONFIG"
  run load_settings "$CLONE"
  [ "$status" -eq 0 ]
  [ "$output" = "ORCH_BIG_REPO=1
ORCH_MERGE_GATE_CHECKS=Gate A|Gate B
ORCH_LEGACY_SCAN=1
ORCH_LEGACY_EXTRA_PATTERN=old-widget
ORCH_LEGACY_EXEMPT_PATHS=a.js b.js
ORCH_FOCUS_PATTERN=billing
ORCH_FOCUS_ARCHITECT=Billing" ]
  [ "$(cat "$TMP/snippets/acme/tools.toml")" = '# Repo settings for acme/tools, converted from orchestration.env.

[audit]
merge_gate_checks = ["Gate A", "Gate B"]' ]
  git -C "$CLONE" check-ignore -q .orchestration.local.toml
}

@test "migration adds the local file to info/exclude once and never overwrites a local file" {
  migration_fixture
  python3 "$SETTINGS" migrate "$OLD" "$ORCH_CONFIG" "$TMP/snippets" >/dev/null
  rm "$ORCH_CONFIG"

  run python3 "$SETTINGS" migrate "$OLD" "$ORCH_CONFIG" "$TMP/snippets"

  [ "$status" -eq 0 ]
  [[ "$output" == *"kept $CLONE/.orchestration.local.toml (already has the converted acme/webapp settings)"* ]]
  [ "$(grep -cx '.orchestration.local.toml' "$CLONE/.git/info/exclude")" -eq 1 ]
  printf '[focus]\npattern = "mine"\n' >"$CLONE/.orchestration.local.toml"
  rm "$ORCH_CONFIG"

  run python3 "$SETTINGS" migrate "$OLD" "$ORCH_CONFIG" "$TMP/snippets"

  [ "$status" -eq 0 ]
  [[ "$output" == *"kept $CLONE/.orchestration.local.toml (yours, not merged); merge the snippet below by hand"* ]]
  [[ "$output" == *"wrote $TMP/snippets/acme/webapp.toml"* ]]
  [ "$(cat "$CLONE/.orchestration.local.toml")" = '[focus]
pattern = "mine"' ]
}

@test "migration skips info/exclude when git already ignores the local file" {
  migration_fixture
  printf '.orchestration.local.toml\n' >"$CLONE/.gitignore"

  run python3 "$SETTINGS" migrate "$OLD" "$ORCH_CONFIG" "$TMP/snippets"

  [ "$status" -eq 0 ]
  [[ "$output" != *"info/exclude"* ]]
  [ "$(grep -cx '.orchestration.local.toml' "$CLONE/.git/info/exclude")" = 0 ]
}

@test "migration refuses an old file that stops early or has a syntax error, and accepts a trailing false" {
  printf '%s\n' 'ORCH_PR_AUTHOR="me"' 'exit 0' 'ORCH_PR_REPOS="a/b"' >"$TMP/early.env"
  run python3 "$SETTINGS" migrate "$TMP/early.env" "$ORCH_CONFIG" "$TMP/snippets"
  [ "$status" -eq 2 ]
  [[ "$output" == *"early.env: stops before its end"* ]]
  [ ! -e "$ORCH_CONFIG" ]
  printf '%s\n' 'ORCH_PR_AUTHOR="me"' 'if then' >"$TMP/broken.env"
  run python3 "$SETTINGS" migrate "$TMP/broken.env" "$ORCH_CONFIG" "$TMP/snippets"
  [ "$status" -eq 2 ]
  [[ "$output" == *"broken.env: bash syntax error"* ]]
  [ ! -e "$ORCH_CONFIG" ]
  printf '%s\n' 'ORCH_PR_AUTHOR="me"' 'false' >"$TMP/false.env"
  run python3 "$SETTINGS" migrate "$TMP/false.env" "$ORCH_CONFIG" "$TMP/snippets"
  [ "$status" -eq 0 ]
  grep -qx 'author = "me"' "$ORCH_CONFIG"
}

@test "watchers run on ORCH_PYTHON3 when it is 3.11 or newer, else on the loader's Python" {
  mkdir "$TMP/oldpy"
  printf '#!/bin/sh\nexit 1\n' >"$TMP/oldpy/old-python"
  chmod +x "$TMP/oldpy/old-python"
  printf '[watchers]\npython3 = "%s"\n' "$TMP/oldpy/old-python" >"$ORCH_CONFIG"
  # shellcheck disable=SC2016 # The child shell expands these.
  run bash -c '. "$1/coordinator/watchers/coord-lib.sh"; orch_load_config; orch_watcher_python; printf "%s" "$ORCH_WATCHER_PYTHON"' _ "$ROOT"
  [ "$status" -eq 0 ]
  [ "$output" != "$TMP/oldpy/old-python" ]
  [ "$("$output" -c 'import sys; print(sys.version_info >= (3, 11))')" = True ]
  printf '[watchers]\npython3 = "%s"\n' "$(command -v python3)" >"$ORCH_CONFIG"
  # shellcheck disable=SC2016 # The child shell expands these.
  run bash -c '. "$1/coordinator/watchers/coord-lib.sh"; orch_load_config; orch_watcher_python; printf "%s" "$ORCH_WATCHER_PYTHON"' _ "$ROOT"
  [ "$output" = "$(command -v python3)" ]
}

@test "migration keeps values the old file builds from other variables and ignores exported settings" {
  # shellcheck disable=SC2016 # The old file is sourced, so $USER_FOR_TEST expands then.
  printf '%s\n' 'ORCH_PR_AUTHOR="$USER_FOR_TEST"' 'ORCH_PR_SINCE="2026-01-01"' >"$TMP/derived.env"

  USER_FOR_TEST=config-user ORCH_PR_SINCE=exported run python3 "$SETTINGS" migrate "$TMP/derived.env" "$ORCH_CONFIG" "$TMP/snippets"

  [ "$status" -eq 0 ]
  grep -qx 'author = "config-user"' "$ORCH_CONFIG"
  grep -qx 'since = "2026-01-01"' "$ORCH_CONFIG"
}

@test "migrated personal settings reach existing and new linked worktrees of the clone" {
  migration_fixture
  git -C "$CLONE" -c user.email=t@example.invalid -c user.name=t commit -q --allow-empty -m init
  git -C "$CLONE" worktree add -q -b before "$TMP/wt-before"

  run python3 "$SETTINGS" migrate "$OLD" "$ORCH_CONFIG" "$TMP/snippets"
  [ "$status" -eq 0 ]
  git -C "$CLONE" worktree add -q -b after "$TMP/wt-after"

  for tree in "$TMP/wt-before" "$TMP/wt-after"; do
    run load_settings "$tree"
    [ "$status" -eq 0 ]
    printf '%s\n' "$output" | grep -qxF "ORCH_BIG_REPO=1"
    printf '%s\n' "$output" | grep -qxF "ORCH_FOCUS_PATTERN=billing"
    run python3 "$ROOT/hooks/guard-bash.py" <<<"{\"tool_name\":\"Bash\",\"cwd\":\"$tree\",\"tool_input\":{\"command\":\"git grep needle\"}}"
    [ "$status" -eq 2 ]
  done
  printf '[focus]\npattern = "mine"\n' >"$TMP/wt-after/.orchestration.local.toml"
  run load_settings "$TMP/wt-after"
  printf '%s\n' "$output" | grep -qxF "ORCH_FOCUS_PATTERN=mine"
  printf '%s\n' "$output" | grep -qxF "ORCH_BIG_REPO=1"
  printf '[guard]\nbig_repo = false\n' >"$TMP/wt-after/.orchestration.local.toml"
  run python3 "$ROOT/hooks/guard-bash.py" <<<"{\"tool_name\":\"Bash\",\"cwd\":\"$TMP/wt-after\",\"tool_input\":{\"command\":\"git grep needle\"}}"
  [ "$status" -eq 0 ]
}

@test "migration drops empty merge gate entries and checks every repo layer before writing" {
  migration_fixture
  sed -i.orig 's/^ORCH_MERGE_GATE_CHECKS=.*/ORCH_MERGE_GATE_CHECKS="Gate A|"/' "$OLD"

  run python3 "$SETTINGS" migrate "$OLD" "$ORCH_CONFIG" "$TMP/snippets"

  [ "$status" -eq 0 ]
  grep -qx 'merge_gate_checks = \["Gate A"\]' "$CLONE/.orchestration.local.toml"
  run load_settings "$CLONE"
  [ "$status" -eq 0 ]
  printf '%s\n' "$output" | grep -qxF "ORCH_MERGE_GATE_CHECKS=Gate A"
}

@test "repos whose names join to the same text get separate migration snippets" {
  cat >"$TMP/collide.env" <<'ENV'
ORCH_BIG_REPO_REMOTES="acme/web-app"
ORCH_LEGACY_REPOS="acme-web/app"
ENV

  run python3 "$SETTINGS" migrate "$TMP/collide.env" "$ORCH_CONFIG" "$TMP/snippets"

  [ "$status" -eq 0 ]
  [[ "$output" == *"wrote $TMP/snippets/acme/web-app.toml"* ]]
  [[ "$output" == *"wrote $TMP/snippets/acme-web/app.toml"* ]]
  grep -qx 'big_repo = true' "$TMP/snippets/acme/web-app.toml"
  [ "$(grep -c scan "$TMP/snippets/acme/web-app.toml")" = 0 ]
  grep -qx 'scan = true' "$TMP/snippets/acme-web/app.toml"
  [ "$(grep -c 'big_repo' "$TMP/snippets/acme-web/app.toml")" = 0 ]
}
