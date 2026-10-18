#!/usr/bin/env bash
# Shared fixture: tests never reach host service managers. Compatible with macOS bash 3.2.

setup_fixture() {
  FIXTURE_TMP=
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/ao-install-bats.XXXXXX") || return
  FIXTURE_TMP=$TMP
  HOME="$TMP/home"
  export HOME XDG_CONFIG_HOME="$HOME/.config"
  BIN="$TMP/bin"
  SRC="$TMP/source"
  ORIGIN="$TMP/origin.git"
  RADAR_ORIGIN="$TMP/radar.git"
  mkdir -p "$HOME" "$BIN" "$SRC"
  export LAUNCHCTL_CALLS="$TMP/launchctl.calls" SYSTEMCTL_CALLS="$TMP/systemctl.calls"
  export ORCH_ENV=none
  export ORCH_LAUNCHD_WAIT_TRIES=2 ORCH_LAUNCHD_WAIT_SLEEP=0
  export PATH="$BATS_SERVICE_STUB_BIN:$BIN:$PATH"
  # Apply only inside this fixture, including repositories product code creates later.
  export GIT_CONFIG_COUNT=3
  export GIT_CONFIG_KEY_0=gc.auto GIT_CONFIG_VALUE_0=0
  export GIT_CONFIG_KEY_1=maintenance.auto GIT_CONFIG_VALUE_1=false
  export GIT_CONFIG_KEY_2=core.fsmonitor GIT_CONFIG_VALUE_2=false

  (cd "$ROOT" && tar --exclude=.git -cf - .) | (cd "$SRC" && tar -xf -)
  git -C "$SRC" init -q
  git -C "$SRC" add -A
  git -C "$SRC" -c user.email=test@example.invalid -c user.name=test commit -qm fixture
  git -C "$SRC" branch -M main
  git init --bare -q "$ORIGIN"
  git -C "$ORIGIN" symbolic-ref HEAD refs/heads/main
  git -C "$SRC" remote add origin "$ORIGIN"
  git -C "$ORIGIN" fetch -q "$SRC" main:main
  git -C "$SRC" fetch -q origin
  git -C "$SRC" branch -u origin/main >/dev/null

  mkdir "$TMP/radar-source"
  git -C "$TMP/radar-source" init -q
  printf 'radar fixture\n' >"$TMP/radar-source/README.md"
  cp "$ROOT/tests/fixtures/radar-plugin.toml" "$TMP/radar-source/herdr-plugin.toml"
  git -C "$TMP/radar-source" add README.md herdr-plugin.toml
  git -C "$TMP/radar-source" -c user.email=test@example.invalid -c user.name=test commit -qm radar
  RADAR_PIN=$(git -C "$TMP/radar-source" rev-parse HEAD)
  printf 'newer radar fixture\n' >>"$TMP/radar-source/README.md"
  git -C "$TMP/radar-source" add README.md
  git -C "$TMP/radar-source" -c user.email=test@example.invalid -c user.name=test commit -qm newer-radar
  git init --bare -q "$RADAR_ORIGIN"
  git -C "$RADAR_ORIGIN" symbolic-ref HEAD refs/heads/main
  git -C "$TMP/radar-source" remote add origin "$RADAR_ORIGIN"
  git -C "$RADAR_ORIGIN" fetch -q "$TMP/radar-source" HEAD:main
  printf '%s\n' "$RADAR_PIN" >"$SRC/env/herdr/radar.pin"
  git -C "$SRC" add env/herdr/radar.pin
  git -C "$SRC" -c user.email=test@example.invalid -c user.name=test commit -qm local-radar-pin
  git -C "$ORIGIN" fetch -q "$SRC" main:main

  cat >"$BIN/gh" <<'EOF'
#!/usr/bin/env bash
case "$1 ${2:-}" in
  'auth status') [ ! -e "${GH_LOGGED_OUT:-}" ] ;;
  'api user') echo "${GH_LOGIN:-octo-login}" ;;
  *) exit 0 ;;
esac
EOF
  cat >"$BIN/herdr" <<'EOF'
#!/usr/bin/env bash
printf 'herdr %s\n' "$*" >>"$HERDR_CALLS"
case "$1 ${2:-}" in
  '--version ') case "$0" in */.local/bin/*) echo "herdr ${HERDR_INSTALLED_VERSION:-0.9.1}" ;; *) echo "herdr ${HERDR_VERSION:-0.9.1}" ;; esac ;;
  'plugin list') [ -f "$HERDR_PLUGIN" ] && printf -- '- hhdebb.herdr-radar (Radar) enabled [local:%s%s]\n' "$(cat "$HERDR_PLUGIN")" "${HERDR_PLUGIN_DIAGNOSTIC:-}" ;;
  'plugin uninstall') rm -f "$HERDR_PLUGIN" ;;
  'plugin link') printf '%s' "$3" >"$HERDR_PLUGIN" ;;
  'plugin action')
    case "$*" in
      *state-start*)
        config="$HOME/.config/herdr/config.toml"
        if [ -n "${HERDR_RADAR_BLOCK:-}" ] && [ -f "$config" ]; then
          printf '# >>> herdr-radar theme block\n[theme]\nname = "radar"\n# <<< herdr-radar theme block\n' >>"$config"
        fi
        if [ -n "${HERDR_EDIT_TAIL:-}" ] && [ -f "$config" ]; then
          sed 's/sidebar = "left"/sidebar = "right"/' "$config" >"$config.edit" && mv "$config.edit" "$config"
        fi
        if [ -n "${HERDR_CLOBBER_CONFIG:-}" ] && [ -f "$config" ]; then
          printf '[keys]\nprefix = "ctrl-b"\n' >"$config"
        fi
        [ -z "${HERDR_FAIL_START:-}" ] || exit 1
        if [ -n "${HERDR_DAEMON_PID:-}" ]; then
          sleep 300 </dev/null >/dev/null 2>&1 &
          echo $! >"$HERDR_DAEMON_PID"
        fi
        ;;
    esac
    ;;
  'agent list') cat "$HERDR_AGENTS" ;;
esac
EOF
  cat >"$BIN/node" <<'EOF'
#!/usr/bin/env bash
echo "v${NODE_VERSION:-22.20.0}"
EOF
  mkdir -p "$TMP/managed-node"
  cp "$BIN/node" "$TMP/managed-node/node"
  chmod +x "$TMP/managed-node/node"
  cat >"$BIN/mise" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$MISE_CALLS"
printf '%s\n' "$PWD" >>"$MISE_CWDS"
if [ -f mise.toml ] || [ -f .mise.toml ] || [ -f .tool-versions ]; then
  if [ "${MISE_UNTRUSTED_COMMAND:-$1}" = "$1" ]; then echo 'untrusted mise config' >&2; exit 1; fi
fi
case "$1" in
  install) [ -z "${MISE_FAIL:-}" ] ;;
  which) echo "$TMP/managed-node/node" ;;
esac
EOF
  cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
raw=https://raw.githubusercontent.com/combinaut/ai-agent-orchestration/main
case "$*" in
  *"$raw/"*)
    [ -z "${CURL_RAW_FAIL:-}" ] || { echo 'curl: (22) The requested URL returned error: 404' >&2; exit 22; }
    [ -z "${CURL_RAW_EMPTY:-}" ] || exit 0
    path=${2#"$raw"/}
    cat "$INSTALL_SOURCE/$path"
    exit 0
    ;;
esac
printf '%s\n' "$*" >>"$CURL_CALLS"
[ -z "${CURL_FAIL:-}" ] || exit 1
[ -z "${CURL_EMPTY:-}" ] || { : >"$4"; exit 0; }
case "$2" in
  https://herdr.dev/install.sh) tool=herdr ;;
  https://mise.run) tool=mise ;;
  *) exit 1 ;;
esac
printf 'mkdir -p "$HOME/.local/bin"\ncp "$BIN/%s.off" "$HOME/.local/bin/%s"\n' "$tool" "$tool" >"$4"
EOF
  chmod +x "$BIN/mise" "$BIN/curl"
  chmod +x "$BIN/gh" "$BIN/herdr" "$BIN/node"
  export HERDR_TEST_STUB="$BIN/herdr"
  git_bin=$(command -v git) || return 1
  export REAL_GIT="$git_bin"
  cat >"$BIN/git" <<'EOF'
#!/usr/bin/env bash
args=()
for arg in "$@"; do
  if [ "$arg" = https://github.com/combinaut/herdr-radar.git ]; then arg="file://$RADAR_ORIGIN"; fi
  if [ "$arg" = https://github.com/combinaut/ai-agent-orchestration.git ]; then arg="file://$ORIGIN"; fi
  args+=("$arg")
done
case "$*" in
  *'remote get-url origin')
    url=$("$REAL_GIT" "${args[@]}") || exit
    if [ "$url" = "file://$RADAR_ORIGIN" ]; then url=https://github.com/combinaut/herdr-radar.git; fi
    echo "$url"
    ;;
  *) exec "$REAL_GIT" "${args[@]}" ;;
esac
EOF
  chmod +x "$BIN/git"
  date_bin=$(command -v date) || return 1
  ln -s "$date_bin" "$BIN/date"
  grep_bin=$(command -v grep) || return 1
  ln -s "$grep_bin" "$BIN/grep"
  jq_bin=$(command -v jq) || return 1
  ln -s "$jq_bin" "$BIN/jq"
  export REAL_UNAME
  REAL_UNAME=$(command -v uname)
  cat >"$BIN/uname" <<'EOF'
#!/bin/bash
exec "$REAL_UNAME" "$@"
EOF
  chmod +x "$BIN/uname"
  ln -s "$(command -v dirname)" "$BIN/dirname"
  export PATH="$BATS_SERVICE_STUB_BIN:$BIN:/usr/bin:/bin:/usr/sbin:/sbin"
  export TMP BIN ORIGIN RADAR_ORIGIN
  export MISE_CWDS="$TMP/mise.cwds" MISE_CALLS="$TMP/mise.calls" CURL_CALLS="$TMP/curl.calls"
  export INSTALL_SOURCE="$SRC" ORCH_REPO_URL="file://$ORIGIN"
  export HERDR_CALLS="$TMP/herdr.calls" HERDR_PLUGIN="$TMP/herdr.plugin"
  # shellcheck disable=SC2016 # Expansions belong to the downloaded script.
  export ONE_LINER='exec /bin/bash <(curl -fsSL https://raw.githubusercontent.com/combinaut/ai-agent-orchestration/main/install.sh) "$@"'
}

teardown_fixture() {
  if [ -n "${FIXTURE_TMP:-}" ] && [ -f "$FIXTURE_TMP/daemon.pid" ]; then
    kill "$(cat "$FIXTURE_TMP/daemon.pid")" 2>/dev/null || true
  fi
  if [ -e "${BATS_SERVICE_STUB_SOURCE:-}/herdr.off" ]; then
    mv "$BATS_SERVICE_STUB_SOURCE/herdr.off" "$BATS_SERVICE_STUB_SOURCE/herdr"
  fi
  [ -n "${FIXTURE_TMP:-}" ] && rm -rf "$FIXTURE_TMP"
}

fresh_install() {
  run /bin/bash -c 'cd "$1"; shift; exec /bin/bash -c "$ONE_LINER" _ "$@"' _ "$TMP"
  # shellcheck disable=SC2154 # bats assigns status and output after run.
  [ "$status" -eq 0 ]
  CLONE="$HOME/.claude/ai-agent-orchestration"
}

install_ok() {
  run /bin/bash "$CLONE/install.sh" "$@"
  # shellcheck disable=SC2154 # bats assigns status and output after run.
  [ "$status" -eq 0 ]
}

install_fails() {
  run /bin/bash "$CLONE/install.sh" "$@"
  # shellcheck disable=SC2154 # bats assigns status and output after run.
  [ "$status" -ne 0 ]
}

# use_home <dir>: a fresh HOME for the rest of the test.
use_home() {
  mkdir -p "$1"
  HOME=$1
  export HOME XDG_CONFIG_HOME="$1/.config"
}

# hide_herdr / show_herdr: no herdr on PATH, then the fixture herdr back.
hide_herdr() {
  mv "$BIN/herdr" "$BIN/herdr.off"
  mv "$BATS_SERVICE_STUB_SOURCE/herdr" "$BATS_SERVICE_STUB_SOURCE/herdr.off"
}

show_herdr() {
  mv "$BIN/herdr.off" "$BIN/herdr"
  mv "$BATS_SERVICE_STUB_SOURCE/herdr.off" "$BATS_SERVICE_STUB_SOURCE/herdr"
}

contains() { printf '%s' "$1" | grep -F "$2" >/dev/null; }

not_contains() {
  if printf '%s' "$1" | grep -F "$2" >/dev/null; then
    return 1
  fi
}

prepare_custom_service_config() {
  printf '%s\n' 'ORCH_WATCHERS_SERVICE="1"' 'ORCH_PR_AUTHOR="custom-author"' 'ORCH_PR_REPOS="owner/custom"' 'ORCH_GIT_WATCH_REPOS=""' >"$1"
  printf 'ORCH_HERDR_BIN="%s"\nORCH_HERDR_SOCKET="/tmp/socket"\nORCH_PATH="%s:/usr/bin:/bin"\n' "$BIN/herdr" "$BIN" >>"$1"
}

prepare_audit_commands() {
  python3 -c 'import json,sys; pr=json.load(open(sys.argv[1])); print(json.dumps({"data":{"search":{"nodes":[pr],"pageInfo":{"hasNextPage":False}}}}))' "$ROOT/coordinator/watchers/fixtures/audit-pr.json" >"$TMP/audit-gh.json"
  cat >"$BIN/gh" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >"$AUDIT_ARGS"
cat "$AUDIT_FIXTURE"
EOF
  cat >"$BIN/herdr" <<'EOF'
#!/bin/bash
printf '{"result":{"workspaces":[]}}'
EOF
}

# shellcheck disable=SC2154 # bats run sets status and output in the calling test.
check_service_config() {
  local adapter
  adapter=${AO_TEST_SANDBOX:?Run tests through tests/run.sh}
  run env AUDIT_ARGS="$TMP/audit-args" AUDIT_FIXTURE="$TMP/audit-gh.json" python3 "$ROOT/tests/service_config.py" "$HOME/.claude/coordinator/watchers/service/$adapter" "$1"
  [ "$status" -eq 0 ]
  contains "$output" 'audit wrapper reads its custom keys'
}

# Exercise a managed-bin-first caller while retaining logging stubs first.
use_managed_bin_fixture() {
  BIN="$HOME/.local/bin"
  cp "$BATS_SERVICE_STUB_SOURCE/launchctl" "$BATS_SERVICE_STUB_SOURCE/systemctl" "$BIN/"
  BATS_SERVICE_STUB_BIN=$BIN
  export BIN BATS_SERVICE_STUB_BIN PATH="$BIN:$PATH"
  assert_service_manager_stubs
}

# Resolve only; this assertion is safe even when a stub is missing.
assert_service_manager_stubs() {
  [ "$(command -v launchctl)" = "$BATS_SERVICE_STUB_BIN/launchctl" ] &&
    [ "$(command -v systemctl)" = "$BATS_SERVICE_STUB_BIN/systemctl" ] &&
    [ "$(command -v herdr)" = "$BATS_SERVICE_STUB_BIN/herdr" ]
}

setup() { setup_fixture; }
teardown() { teardown_fixture; }

# Makes the service-manager stubs remember what they loaded or enabled.
use_service_state() {
  export LAUNCHCTL_STATE="$TMP/launchctl.state" SYSTEMCTL_STATE="$TMP/systemctl.state"
}

# unloaded_status: install exit status when LAUNCHCTL_NOT_LOADED=1 leaves services unregistered (macOS only).
unloaded_status() { [ "$(uname -s)" = Darwin ] && echo 1 || echo 0; }

# use_stuck_service <launchd label> <systemd unit>: the service-manager stubs refuse to stop these.
use_stuck_service() {
  export LAUNCHCTL_STUCK="$1" SYSTEMCTL_STUCK="$2"
}
