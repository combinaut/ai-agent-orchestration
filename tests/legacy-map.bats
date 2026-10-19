#!/usr/bin/env bats
# shellcheck disable=SC2154,SC2016 # bats provides status and output; fixture file bodies stay literal.
load test_helper

setup() {
  setup_fixture
  MAP="$ROOT/coordinator/legacy-map.sh"
  OUT="$TMP/out/legacy-map.tsv"
  export ORCH_LEGACY_MAP_OUT="$OUT"
  make_webapp
  cat >"$BIN/rg" <<'EOF2'
#!/usr/bin/env python3
import os, re, sys
a = sys.argv[1:]
globs, pats, paths, i = [], [], [], 0
while i < len(a):
    if a[i] == '--glob': globs.append(a[i + 1]); i += 2
    elif a[i] == '-e': pats.append(a[i + 1]); i += 2
    elif a[i] == '-l': i += 1
    else: paths.append(a[i]); i += 1
def rx(g):
    out = re.escape(g[1:]).replace(r'\*\*/', '(?:.*/)?').replace(r'\*\*', '.*').replace(r'\*', '[^/]*')
    return re.compile('^' + out + '$')
skip = [rx(g) for g in globs if g.startswith('!')]
pat = re.compile('|'.join(pats))
open(os.environ['RG_CALLS'], 'a').write(os.getcwd() + ' ' + ' '.join(paths) + '\n')
hits = []
for p in paths:
    for d, _, fs in os.walk(p):
        for f in fs:
            path = os.path.join(d, f)
            if any(s.match(path) for s in skip): continue
            if pat.search(open(path, errors='replace').read()): hits.append(path)
print('\n'.join(sorted(hits)))
sys.exit(0 if hits else 1)
EOF2
  chmod +x "$BIN/rg"
  export RG_CALLS="$TMP/rg.calls"
}

# make_webapp: BIG_REPO is a clone of a fixture origin; the working copy later diverges from origin/main.
make_webapp() {
  local seed="$TMP/webapp-seed"
  BIG_REPO="$TMP/webapp"
  mkdir -p "$seed/app/views/a" "$seed/app/assets/javascripts" "$seed/app/js" "$seed/lib" "$seed/clients/acme/app/assets/javascripts" "$seed/clients/acme/app/js" "$seed/app/tests" "$seed/docs"
  echo 'x' >"$seed/app/views/a/show.js.erb"
  echo 'x' >"$seed/app/assets/javascripts/old.js"
  echo 'x' >"$seed/clients/acme/app/assets/javascripts/legacy.coffee"
  echo '$(".a").hide()' >"$seed/app/js/uses_dollar.js"
  echo 'jQuery.ajax({})' >"$seed/lib/uses_jquery.rb"
  echo 'data-remote' >"$seed/clients/acme/app/js/remote.js"
  echo '$(".a")' >"$seed/app/tests/skipped.js"
  echo '$(".a")' >"$seed/app/js/skipped.spec.js"
  echo '$(".a")' >"$seed/app/assets/javascripts/inside.js"
  echo '$(".a")' >"$seed/docs/outside_scan.js"
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

@test "writes JSERB, ASSETJS and JQUERY rows from origin/main" {
  run "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  [ "$(cat "$OUT")" = "$(printf 'ASSETJS\tapp/assets/javascripts/inside.js\nASSETJS\tapp/assets/javascripts/old.js\nASSETJS\tclients/acme/app/assets/javascripts/legacy.coffee\nJQUERY\tapp/js/uses_dollar.js\nJQUERY\tclients/acme/app/js/remote.js\nJQUERY\tlib/uses_jquery.rb\nJSERB\tapp/views/a/show.js.erb')" ]
  contains "$output" '3 ASSETJS'
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

@test "rg scans an extracted copy of only app, lib and clients, and the copy is removed" {
  run env TMPDIR="$TMP" "$MAP" "$BIG_REPO"
  [ "$status" -eq 0 ]
  scanned=$(cut -d' ' -f1 "$RG_CALLS")
  [ "$scanned" != "$(cd "$BIG_REPO" && pwd -P)" ]
  case "$scanned" in "$(cd "$TMP" && pwd -P)"/legacy-map.*) ;; *) false ;; esac
  [ ! -e "$scanned" ]
  [ -z "$(find "$TMP" -maxdepth 1 -name 'legacy-map.*')" ]
  [ "$(cut -d' ' -f2- "$RG_CALLS")" = 'app lib clients' ]
  not_contains "$(cat "$OUT")" 'outside_scan'
}

@test "the temp dir is removed when the script fails after extracting" {
  mkdir -p "$TMP/readonly"
  chmod 555 "$TMP/readonly"
  run env TMPDIR="$TMP" ORCH_LEGACY_MAP_OUT="$TMP/readonly/map.tsv" "$MAP" "$BIG_REPO"
  chmod 755 "$TMP/readonly"
  [ "$status" -ne 0 ]
  [ -s "$RG_CALLS" ]
  [ -z "$(find "$TMP" -maxdepth 1 -name 'legacy-map.*')" ]
}

@test "a missing rg stops before the map is touched" {
  rm "$BIN/rg"
  mkdir -p "$(dirname "$OUT")"
  printf 'keep\n' >"$OUT"
  run "$MAP" "$BIG_REPO"
  [ "$status" -ne 0 ]
  contains "$output" 'rg not found'
  [ "$(cat "$OUT")" = keep ]
}

@test "an rg failure stops the job and keeps the previous map" {
  printf '#!/bin/sh\nexit 2\n' >"$BIN/rg"
  mkdir -p "$(dirname "$OUT")"
  printf 'keep\n' >"$OUT"
  run "$MAP" "$BIG_REPO"
  [ "$status" -ne 0 ]
  contains "$output" 'rg failed with status 2'
  [ "$(cat "$OUT")" = keep ]
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
  ln -s /elsewhere/orch.coord-legacy-map.service "$HOME/.config/systemd/user/orch.coord-legacy-map.service"
  install_ok
  case "$(uname -s)" in
    Darwin)
      unit="$HOME/.claude/coordinator/service/macos/orch.coord-legacy-map.plist"
      [ -f "$unit" ]
      contains "$(cat "$unit")" "<string>$HOME/.claude/coordinator/legacy-map.sh</string>"
      contains "$(cat "$unit")" "<string>$BIG_REPO</string>"
      contains "$(cat "$unit")" '<key>StartCalendarInterval</key>'
      contains "$output" 'launchctl bootstrap'
      units=$(cat "$unit")
      ;;
    Linux)
      dir="$HOME/.claude/coordinator/service/linux"
      contains "$(cat "$dir/orch.coord-legacy-map.service")" "ExecStart=\"$HOME/.claude/coordinator/legacy-map.sh\" \"$BIG_REPO\""
      contains "$(cat "$dir/orch.coord-legacy-map.timer")" 'OnCalendar=*-*-* 06:00:00'
      contains "$output" 'systemctl --user link'
      units=$(cat "$dir"/orch.coord-legacy-map.service "$dir"/orch.coord-legacy-map.timer)
      contains "$output" 'systemctl --user enable --now'
      contains "$output" 'systemctl --user disable --now orch.coord-legacy-map.timer'
      ;;
  esac
  not_contains "$units" '@ORCH_'
  case "$(uname -s)" in
    Darwin)
      cmp -s "$unit" "$HOME/Library/LaunchAgents/orch.coord-legacy-map.plist"
      grep -qxF orch.coord-legacy-map "$LAUNCHCTL_STATE"
      ;;
    Linux)
      [ "$(readlink "$HOME/.config/systemd/user/orch.coord-legacy-map.service")" = "$dir/orch.coord-legacy-map.service" ]
      grep -qxF orch.coord-legacy-map.timer "$SYSTEMCTL_STATE"
      ;;
  esac
  mkdir -p "$TMP/other-webapp"
  config_set ORCH_LEGACY_MAP_REPO "$TMP/other-webapp"
  : >"$SYSTEMCTL_CALLS"
  install_ok
  case "$(uname -s)" in
    Darwin) contains "$(cat "$HOME/Library/LaunchAgents/orch.coord-legacy-map.plist")" "<string>$TMP/other-webapp</string>" ;;
    Linux) contains "$(cat "$SYSTEMCTL_CALLS")" 'user daemon-reload' ;;
  esac
  config_set ORCH_LEGACY_MAP_REPO ""
  install_ok
  case "$(uname -s)" in
    Darwin)
      [ ! -e "$HOME/Library/LaunchAgents/orch.coord-legacy-map.plist" ]
      not_contains "$(cat "$LAUNCHCTL_STATE")" legacy-map
      ;;
    Linux) [ ! -L "$HOME/.config/systemd/user/orch.coord-legacy-map.service" ] ;;
  esac
}

@test "the legacy map repo defaults to the watched clone whose repo settings turn the legacy scan on" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE 1 ORCH_HERDR_BIN /bin/echo ORCH_HERDR_SOCKET /tmp/socket ORCH_GIT_WATCH_REPOS "/elsewhere/other $BIG_REPO"
  printf '%s\n' '[legacy]' 'scan = true' >"$BIG_REPO/.orchestration.local.toml"
  use_service_state
  install_ok
  case "$(uname -s)" in
    Darwin) contains "$(cat "$HOME/.claude/coordinator/service/macos/orch.coord-legacy-map.plist")" "<string>$BIG_REPO</string>" ;;
    Linux) contains "$(cat "$HOME/.claude/coordinator/service/linux/orch.coord-legacy-map.service")" "\"$BIG_REPO\"" ;;
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
