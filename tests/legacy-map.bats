#!/usr/bin/env bats
# shellcheck disable=SC2154,SC2016 # bats provides status and output; fixture file bodies stay literal.
load test_helper

setup() {
  local rg
  rg=$(command -v rg)
  setup_fixture
  ln -s "$rg" "$BIN/rg"
  MAP="$ROOT/coordinator/legacy-map.sh"
  OUT="$TMP/out/legacy-map.tsv"
  export ORCH_LEGACY_MAP_OUT="$OUT"
  make_webapp
}

# write_rules <dir>: the committed repo settings whose rules the map follows.
write_rules() {
  cat >"$1/.orchestration.toml" <<'EOF2'
[legacy]
exempt = [".orchestration.toml", "app/js/exempt.js"]

[[legacy.rules]]
kind = "JSERB"
paths = ["**/*.js.erb"]

[[legacy.rules]]
kind = "ASSETJS"
paths = ["**/app/assets/javascripts/**/*.{js,coffee}"]

[[legacy.rules]]
kind = "JQUERY"
pattern = '\bjQuery\b|\$\(|\$\.ajax|ajax:(success|complete)|data-remote|remote: *true'
paths = ["{app,lib,clients}/**"]
exclude = ["**/tests/**", "**/*.spec.*"]
EOF2
}

# push_seed <message>: commits the seed and pushes it to origin/main.
push_seed() {
  git -C "$TMP/webapp-seed" add -A
  git -C "$TMP/webapp-seed" -c user.email=t@example.invalid -c user.name=t commit -qm "$1"
  git -C "$TMP/webapp-seed" push -q origin main
}

# make_webapp: BIG_REPO is a clone of a fixture origin; the working copy later diverges from origin/main.
make_webapp() {
  local seed="$TMP/webapp-seed"
  BIG_REPO="$TMP/webapp"
  mkdir -p "$seed/app/views/a" "$seed/app/assets/javascripts" "$seed/app/js" "$seed/lib" "$seed/clients/acme/app/assets/javascripts" "$seed/clients/acme/app/js" "$seed/app/tests" "$seed/docs"
  echo 'x' >"$seed/app/views/a/show.js.erb"
  : >"$seed/app/views/a/empty.js.erb"
  echo 'x' >"$seed/app/assets/javascripts/old.js"
  echo 'x' >"$seed/clients/acme/app/assets/javascripts/legacy.coffee"
  echo '$(".a").hide()' >"$seed/app/js/uses_dollar.js"
  echo 'jQuery.ajax({})' >"$seed/lib/uses_jquery.rb"
  echo 'data-remote' >"$seed/clients/acme/app/js/remote.js"
  echo '$(".a")' >"$seed/app/tests/skipped.js"
  echo '$(".a")' >"$seed/app/js/skipped.spec.js"
  echo '$(".a")' >"$seed/app/js/exempt.js"
  printf '$(".a")\0binary' >"$seed/app/js/binary.js"
  echo '$(".a")' >"$seed/app/assets/javascripts/inside.js"
  echo '$(".a")' >"$seed/docs/outside_scan.js"
  write_rules "$seed"
  git -C "$seed" init -q
  git -C "$seed" add -A
  git -C "$seed" -c user.email=t@example.invalid -c user.name=t commit -qm seed
  git -C "$seed" branch -M main
  git init --bare -q "$TMP/webapp-origin.git"
  git -C "$TMP/webapp-origin.git" symbolic-ref HEAD refs/heads/main
  git -C "$seed" remote add origin "$TMP/webapp-origin.git"
  git -C "$seed" push -q origin main
  git clone -q "$TMP/webapp-origin.git" "$BIG_REPO"
}

keep_previous_map() {
  mkdir -p "$(dirname "$OUT")"
  printf 'keep\n' >"$OUT"
}

@test "writes a row per file and kind the rules on origin/main count" {
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  [ "$(cat "$OUT")" = "$(printf 'ASSETJS\tapp/assets/javascripts/inside.js\nASSETJS\tapp/assets/javascripts/old.js\nASSETJS\tclients/acme/app/assets/javascripts/legacy.coffee\nJQUERY\tapp/js/uses_dollar.js\nJQUERY\tclients/acme/app/js/remote.js\nJQUERY\tlib/uses_jquery.rb\nJSERB\tapp/views/a/empty.js.erb\nJSERB\tapp/views/a/show.js.erb')" ]
  contains "$output" '3 ASSETJS'
  not_contains "$(cat "$OUT")" 'outside_scan'
  not_contains "$(cat "$OUT")" 'exempt.js'
  not_contains "$(cat "$OUT")" 'binary.js'
}

@test "a change only in the working copy does not appear" {
  echo '$(".b")' >"$BIG_REPO/app/js/working_copy_only.js"
  echo 'x' >"$BIG_REPO/app/views/a/extra.js.erb"
  git -C "$BIG_REPO" add -A
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  not_contains "$(cat "$OUT")" 'working_copy_only'
  not_contains "$(cat "$OUT")" 'extra.js.erb'
}

@test "the committed rules come from origin/main, not the working copy" {
  printf '[[legacy.rules]]\nkind = "ALL"\npaths = ["**"]\n' >"$BIG_REPO/.orchestration.toml"
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  not_contains "$(cat "$OUT")" 'ALL'
  printf '[[legacy.rules]]\nkind = "DOCS"\npaths = ["docs/**"]\n' >"$TMP/webapp-seed/.orchestration.toml"
  push_seed 'docs rule'
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  [ "$(cat "$OUT")" = "$(printf 'DOCS\tdocs/outside_scan.js')" ]
}

@test "rules in the clone's personal file win over the committed ones" {
  printf '[[legacy.rules]]\nkind = "MINE"\npaths = ["docs/**"]\n' >"$BIG_REPO/.orchestration.local.toml"
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  [ "$(cat "$OUT")" = "$(printf 'MINE\tdocs/outside_scan.js')" ]
}

@test "a commit that reaches origin/main after the clone is fetched" {
  git clone -q "$TMP/webapp-origin.git" "$TMP/pusher"
  echo 'x' >"$TMP/pusher/app/views/a/new.js.erb"
  git -C "$TMP/pusher" add -A
  git -C "$TMP/pusher" -c user.email=t@example.invalid -c user.name=t commit -qm new
  git -C "$TMP/pusher" push -q origin main
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  contains "$(cat "$OUT")" 'app/views/a/new.js.erb'
}

@test "no rules says so in one line, succeeds and keeps the previous map" {
  printf '[legacy]\nscan = true\n' >"$TMP/webapp-seed/.orchestration.toml"
  push_seed 'no rules'
  keep_previous_map
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  [ "$output" = "legacy-map.sh: $BIG_REPO has no [[legacy.rules]] in its repo settings; nothing to map" ]
  [ "$(cat "$OUT")" = keep ]
}

@test "an invalid rule stops with the reason and keeps the previous map" {
  printf '[[legacy.rules]]\nkind = "A"\npaths = ["**"]\npattern = "("\n' >"$TMP/webapp-seed/.orchestration.toml"
  push_seed 'bad rules'
  keep_previous_map
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 1 ]
  contains "$output" 'legacy-map.sh: legacy.rules[1] (A): pattern is not a valid rg regex'
  [ "$(cat "$OUT")" = keep ]
}

@test "a removed legacy key stops with the conversion hint and keeps the previous map" {
  printf '[legacy]\nextra_pattern = "x"\n' >"$BIG_REPO/.orchestration.local.toml"
  keep_previous_map
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 1 ]
  contains "$output" 'legacy.extra_pattern: was removed; run install.sh to convert it to [[legacy.rules]]'
  [ "$(cat "$OUT")" = keep ]
}

@test "a failed write leaves no temp file and a nonzero status" {
  mkdir -p "$TMP/readonly"
  chmod 555 "$TMP/readonly"
  run env ORCH_LEGACY_MAP_OUT="$TMP/readonly/map.tsv" "$MAP" "$BIG_REPO"
  chmod 755 "$TMP/readonly"
  [ "$status" -ne 0 ]
  [ -z "$(find "$TMP/readonly" -type f)" ]
}

@test "no repository stops with usage" {
  run env -u ORCH_LEGACY_MAP_REPO "$MAP"
  [ "$status" -ne 0 ]
  contains "$output" 'usage: legacy-map.sh'
}

@test "the repository can come from ORCH_LEGACY_MAP_REPO" {
  run env ORCH_LEGACY_MAP_REPO="$BIG_REPO" "$MAP"
  [ "$status" -eq 0 ]
  contains "$(cat "$OUT")" 'JSERB'
}

@test "the default output is under the test HOME, never the live one" {
  run env -u ORCH_LEGACY_MAP_OUT "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  [ -f "$HOME/.claude/coordinator/legacy-map.tsv" ]
}

@test "install.sh links the script and renders the daily job only when a repo is set" {
  fresh_install
  [ -L "$HOME/.claude/coordinator/legacy-map.sh" ]
  [ -x "$HOME/.claude/coordinator/legacy-map.sh" ]
  config_set ORCH_WATCHERS_SERVICE '1' ORCH_HERDR_BIN '/bin/echo' ORCH_HERDR_SOCKET '/tmp/socket'
  install_ok
  [ -z "$(find "$HOME/.claude/coordinator/service" -type f)" ]
  contains "$output" 'daily legacy map job (set ORCH_LEGACY_MAP_REPO'
  config_set ORCH_LEGACY_MAP_REPO "$BIG_REPO"
  use_service_state
  mkdir -p "$HOME/.config/systemd/user"
  ln -s /elsewhere/com.combinaut.coord-legacy-map.service "$HOME/.config/systemd/user/com.combinaut.coord-legacy-map.service"
  install_ok
  case "$(uname -s)" in
    Darwin)
      unit="$HOME/.claude/coordinator/service/macos/com.combinaut.coord-legacy-map.plist"
      [ -f "$unit" ]
      contains "$(cat "$unit")" "<string>$HOME/.claude/coordinator/legacy-map.sh</string>"
      contains "$(cat "$unit")" "<string>$BIG_REPO</string>"
      contains "$(cat "$unit")" '<key>StartCalendarInterval</key>'
      contains "$(cat "$unit")" '<key>ORCH_PYTHON3</key>'
      contains "$output" 'launchctl bootstrap'
      units=$(cat "$unit")
      ;;
    Linux)
      dir="$HOME/.claude/coordinator/service/linux"
      contains "$(cat "$dir/com.combinaut.coord-legacy-map.service")" "ExecStart=\"$HOME/.claude/coordinator/legacy-map.sh\" \"$BIG_REPO\""
      contains "$(cat "$dir/com.combinaut.coord-legacy-map.timer")" 'OnCalendar=*-*-* 06:00:00'
      contains "$(cat "$dir/com.combinaut.coord-legacy-map.service")" 'Environment="ORCH_PYTHON3=/'
      contains "$output" 'systemctl --user link'
      units=$(cat "$dir"/com.combinaut.coord-legacy-map.service "$dir"/com.combinaut.coord-legacy-map.timer)
      contains "$output" 'systemctl --user enable --now'
      contains "$output" 'systemctl --user disable --now com.combinaut.coord-legacy-map.timer'
      ;;
  esac
  not_contains "$units" '@ORCH_'
  case "$(uname -s)" in
    Darwin)
      cmp -s "$unit" "$HOME/Library/LaunchAgents/com.combinaut.coord-legacy-map.plist"
      grep -qxF com.combinaut.coord-legacy-map "$LAUNCHCTL_STATE"
      ;;
    Linux)
      [ "$(readlink "$HOME/.config/systemd/user/com.combinaut.coord-legacy-map.service")" = "$dir/com.combinaut.coord-legacy-map.service" ]
      grep -qxF com.combinaut.coord-legacy-map.timer "$SYSTEMCTL_STATE"
      ;;
  esac
  mkdir -p "$TMP/other-webapp"
  config_set ORCH_LEGACY_MAP_REPO "$TMP/other-webapp"
  : >"$SYSTEMCTL_CALLS"
  install_ok
  case "$(uname -s)" in
    Darwin) contains "$(cat "$HOME/Library/LaunchAgents/com.combinaut.coord-legacy-map.plist")" "<string>$TMP/other-webapp</string>" ;;
    Linux) contains "$(cat "$SYSTEMCTL_CALLS")" 'user daemon-reload' ;;
  esac
  config_set ORCH_LEGACY_MAP_REPO ""
  install_ok
  case "$(uname -s)" in
    Darwin)
      [ ! -e "$HOME/Library/LaunchAgents/com.combinaut.coord-legacy-map.plist" ]
      not_contains "$(cat "$LAUNCHCTL_STATE")" legacy-map
      ;;
    Linux) [ ! -L "$HOME/.config/systemd/user/com.combinaut.coord-legacy-map.service" ] ;;
  esac
}

@test "the legacy map repo defaults to the watched clone whose repo settings turn the legacy scan on" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE 1 ORCH_HERDR_BIN /bin/echo ORCH_HERDR_SOCKET /tmp/socket ORCH_GIT_WATCH_REPOS "/elsewhere/other $BIG_REPO"
  printf '%s\n' '[legacy]' 'scan = true' >"$BIG_REPO/.orchestration.local.toml"
  use_service_state
  install_ok
  case "$(uname -s)" in
    Darwin) contains "$(cat "$HOME/.claude/coordinator/service/macos/com.combinaut.coord-legacy-map.plist")" "<string>$BIG_REPO</string>" ;;
    Linux) contains "$(cat "$HOME/.claude/coordinator/service/linux/com.combinaut.coord-legacy-map.service")" "\"$BIG_REPO\"" ;;
  esac
}

@test "a watched clone without the legacy scan leaves the legacy map default off" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE 1 ORCH_HERDR_BIN /bin/echo ORCH_HERDR_SOCKET /tmp/socket ORCH_GIT_WATCH_REPOS "$BIG_REPO"
  install_ok
  contains "$output" 'daily legacy map job (set ORCH_LEGACY_MAP_REPO'
  [ -z "$(find "$HOME/.claude/coordinator/service" -type f)" ]
}

@test "a broken repo file in a watched clone stops install before the legacy map job changes" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE 1 ORCH_HERDR_BIN /bin/echo ORCH_HERDR_SOCKET /tmp/socket ORCH_GIT_WATCH_REPOS "$BIG_REPO"
  printf '%s\n' '[heavy_slots]' 'max = 2' >"$BIG_REPO/.orchestration.local.toml"
  run /bin/bash "$CLONE/install.sh"
  [ "$status" -ne 0 ]
  contains "$output" "/webapp/.orchestration.local.toml: heavy_slots.max: is a global setting"
  contains "$output" 'cannot read the repo settings of the audit.git_watch_repos clones'
  not_contains "$output" 'daily legacy map job'
}

@test "install.sh converts removed legacy keys in a watched clone's personal file and in the snippets" {
  fresh_install
  config_set ORCH_GIT_WATCH_REPOS "$BIG_REPO"
  printf '%s\n' '# mine' '[legacy]' 'scan = true' 'extra_pattern = "uber-select"' 'exempt_paths = ["a.js"]' >"$BIG_REPO/.orchestration.local.toml"
  mkdir -p "$HOME/.claude/orchestration-repo-snippets/acme"
  printf '%s\n' '[legacy]' 'exempt_paths = ["b.js"]' >"$HOME/.claude/orchestration-repo-snippets/acme/tools.toml"
  install_ok
  contains "$output" "converted $BIG_REPO/.orchestration.local.toml: legacy.extra_pattern -> [[legacy.rules]]"
  contains "$output" 'legacy.scan has no built-in rules'
  [ "$(cat "$BIG_REPO/.orchestration.local.toml")" = "$(printf '%s\n' '# mine' '[legacy]' 'scan = true' 'exempt = ["a.js"]' '' '[[legacy.rules]]' 'kind = "LEGACY"' 'pattern = "uber-select"' 'paths = ["**"]')" ]
  [ "$(cat "$HOME/.claude/orchestration-repo-snippets/acme/tools.toml")" = "$(printf '%s\n' '[legacy]' 'exempt = ["b.js"]')" ]
  install_ok
  not_contains "$output" 'converted'
}

@test "install.sh stops on a removed legacy key it cannot convert and leaves the file" {
  fresh_install
  config_set ORCH_GIT_WATCH_REPOS "$BIG_REPO"
  printf '%s\n' '[legacy]' 'exempt_paths = ["a.js"]' 'exempt = ["b.js"]' >"$BIG_REPO/.orchestration.local.toml"
  run /bin/bash "$CLONE/install.sh"
  [ "$status" -ne 0 ]
  contains "$output" 'cannot convert the removed legacy settings'
  [ "$(cat "$BIG_REPO/.orchestration.local.toml")" = "$(printf '%s\n' '[legacy]' 'exempt_paths = ["a.js"]' 'exempt = ["b.js"]')" ]
}

@test "install.sh also converts the personal file of a watched worktree's main checkout" {
  fresh_install
  git -C "$BIG_REPO" worktree add -q -b side "$TMP/side"
  config_set ORCH_GIT_WATCH_REPOS "$TMP/side"
  printf '%s\n' '[legacy]' 'exempt_paths = ["a.js"]' >"$BIG_REPO/.orchestration.local.toml"
  install_ok
  contains "$output" "converted $(cd "$BIG_REPO" && pwd -P)/.orchestration.local.toml: legacy.exempt_paths -> legacy.exempt"
  [ "$(cat "$BIG_REPO/.orchestration.local.toml")" = "$(printf '%s\n' '[legacy]' 'exempt = ["a.js"]')" ]
}
