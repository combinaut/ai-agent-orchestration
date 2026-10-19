#!/usr/bin/env bats
# shellcheck disable=SC2154,SC2016 # bats provides status and output; fixture file bodies stay literal.
load test_helper

setup() {
  local python
  python=$(command -v python3)
  setup_fixture
  # The fixture PATH starts at /usr/bin, whose macOS python3 has no tomllib.
  ln -s "$python" "$BIN/python3"
  MAP="$ROOT/coordinator/legacy-map.sh"
  OUT="$TMP/out/legacy-map.tsv"
  export ORCH_LEGACY_MAP_OUT="$OUT"
  make_webapp
}

# write_rules <dir>: the rules file the map follows.
write_rules() {
  mkdir -p "$1/.orchestration"
  cat >"$1/.orchestration/legacy-rules.toml" <<'EOF2'
version = 1
exempt = [".orchestration/**", "app/js/exempt.js"]

[[rules]]
kind = "JSERB"
paths = ["**/*.js.erb"]

[[rules]]
kind = "ASSETJS"
paths = ["**/app/assets/javascripts/**/*.{js,coffee}"]

[[rules]]
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

@test "writes a row per file and kind the rules file on origin/main counts" {
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

@test "the rules come from origin/main, not the working copy" {
  printf 'version = 1\n[[rules]]\nkind = "ALL"\npaths = ["**"]\n' >"$BIG_REPO/.orchestration/legacy-rules.toml"
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  not_contains "$(cat "$OUT")" 'ALL'
  printf 'version = 1\n[[rules]]\nkind = "DOCS"\npaths = ["docs/**"]\n' >"$TMP/webapp-seed/.orchestration/legacy-rules.toml"
  push_seed 'docs rule'
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  [ "$(cat "$OUT")" = "$(printf 'DOCS\tdocs/outside_scan.js')" ]
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

@test "no rules file on origin/main stops and keeps the previous map" {
  git -C "$TMP/webapp-seed" rm -q .orchestration/legacy-rules.toml
  push_seed 'no rules'
  keep_previous_map
  run "$MAP" "$BIG_REPO"
  [ "$status" -ne 0 ]
  contains "$output" "has no .orchestration/legacy-rules.toml on origin/main"
  [ "$(cat "$OUT")" = keep ]
}

@test "an invalid rules file stops with the reason and keeps the previous map" {
  printf 'version = 1\n[[rules]]\nkind = "A"\npaths = ["**"]\npattern = "("\n' >"$TMP/webapp-seed/.orchestration/legacy-rules.toml"
  push_seed 'bad rules'
  keep_previous_map
  run "$MAP" "$BIG_REPO"
  [ "$status" -ne 0 ]
  contains "$output" 'origin/main:.orchestration/legacy-rules.toml: rules[1] (A): pattern is not a valid regex'
  [ "$(cat "$OUT")" = keep ]
}

@test "a Python without tomllib says so and keeps the previous map" {
  cat >"$TMP/old-python" <<EOF2
#!/bin/sh
exec "$(command -v python3)" -c 'import runpy, sys; sys.modules["tomllib"] = None; sys.argv = sys.argv[1:]; sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0]); runpy.run_path(sys.argv[0], run_name="__main__")' "\$@"
EOF2
  chmod +x "$TMP/old-python"
  keep_previous_map
  run env ORCH_PYTHON3="$TMP/old-python" "$MAP" "$BIG_REPO"
  [ "$status" -ne 0 ]
  contains "$output" 'legacy scan needs Python 3.11+ for tomllib'
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
  printf '%s\n' 'ORCH_WATCHERS_SERVICE="1"' 'ORCH_HERDR_BIN="/bin/echo"' 'ORCH_HERDR_SOCKET="/tmp/socket"' >>"$HOME/.claude/orchestration.env"
  install_ok
  [ -z "$(find "$HOME/.claude/coordinator/service" -type f)" ]
  contains "$output" 'daily legacy map job (set ORCH_LEGACY_MAP_REPO'
  printf '%s\n' "ORCH_LEGACY_MAP_REPO=\"$BIG_REPO\"" >>"$HOME/.claude/orchestration.env"
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
      contains "$(cat "$dir/com.combinaut.coord-legacy-map.service")" 'Environment="ORCH_PYTHON3='
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
  printf '%s\n' "ORCH_LEGACY_MAP_REPO=\"$TMP/other-webapp\"" >>"$HOME/.claude/orchestration.env"
  : >"$SYSTEMCTL_CALLS"
  install_ok
  case "$(uname -s)" in
    Darwin) contains "$(cat "$HOME/Library/LaunchAgents/com.combinaut.coord-legacy-map.plist")" "<string>$TMP/other-webapp</string>" ;;
    Linux) contains "$(cat "$SYSTEMCTL_CALLS")" 'user daemon-reload' ;;
  esac
  grep -v '^ORCH_LEGACY_MAP_REPO=' "$HOME/.claude/orchestration.env" >"$TMP/env" && mv "$TMP/env" "$HOME/.claude/orchestration.env"
  install_ok
  case "$(uname -s)" in
    Darwin)
      [ ! -e "$HOME/Library/LaunchAgents/com.combinaut.coord-legacy-map.plist" ]
      not_contains "$(cat "$LAUNCHCTL_STATE")" legacy-map
      ;;
    Linux) [ ! -L "$HOME/.config/systemd/user/com.combinaut.coord-legacy-map.service" ] ;;
  esac
}

@test "the legacy map repo defaults to the watched clone of a legacy repo" {
  fresh_install
  printf '%s\n' 'ORCH_WATCHERS_SERVICE="1"' 'ORCH_HERDR_BIN="/bin/echo"' 'ORCH_HERDR_SOCKET="/tmp/socket"' \
    'ORCH_LEGACY_REPOS="acme/webapp"' "ORCH_GIT_WATCH_REPOS=\"/elsewhere/other $BIG_REPO\"" >>"$HOME/.claude/orchestration.env"
  use_service_state
  install_ok
  case "$(uname -s)" in
    Darwin) contains "$(cat "$HOME/.claude/coordinator/service/macos/com.combinaut.coord-legacy-map.plist")" "<string>$BIG_REPO</string>" ;;
    Linux) contains "$(cat "$HOME/.claude/coordinator/service/linux/com.combinaut.coord-legacy-map.service")" "\"$BIG_REPO\"" ;;
  esac
}

@test "an empty ORCH_LEGACY_REPOS turns the legacy map default off" {
  fresh_install
  printf '%s\n' 'ORCH_WATCHERS_SERVICE="1"' 'ORCH_HERDR_BIN="/bin/echo"' 'ORCH_HERDR_SOCKET="/tmp/socket"' \
    "ORCH_GIT_WATCH_REPOS=\"$BIG_REPO\"" 'ORCH_LEGACY_REPOS=""' >>"$HOME/.claude/orchestration.env"
  install_ok
  contains "$output" 'daily legacy map job (set ORCH_LEGACY_MAP_REPO'
  [ -z "$(find "$HOME/.claude/coordinator/service" -type f)" ]
}
