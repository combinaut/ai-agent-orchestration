#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

load test_helper

setup() {
  setup_fixture
  for tool in launchctl systemctl; do
    printf '#!/bin/sh\nexit 1\n' >"$BIN/$tool"
    chmod +x "$BIN/$tool"
  done
  # shellcheck disable=SC2016 # Expansions belong to the stubs.
  for tool in open google-chrome; do
    printf '#!/bin/sh\nprintf "%%s %%s\\n" "${0##*/}" "$*" >>"$GUI_CALLS"\n' >"$BIN/$tool"
    chmod +x "$BIN/$tool"
  done
  # shellcheck disable=SC2016 # Expansions belong to the stubs.
  for tool in pbcopy xclip wl-copy; do
    printf '#!/bin/sh\nprintf "%%s %%s\\n" "${0##*/}" "$(cat)" >>"$GUI_CALLS"\n' >"$BIN/$tool"
    chmod +x "$BIN/$tool"
  done
  export GUI_CALLS="$TMP/gui.calls" DISPLAY=:99
  unset WAYLAND_DISPLAY SSH_CONNECTION SSH_TTY
  case "$(uname -s)" in
    Darwin) CHROME_DIR="$HOME/Library/Application Support/Google/Chrome" ;;
    *) CHROME_DIR="$HOME/.config/google-chrome" ;;
  esac
  HOST_MANIFEST="$CHROME_DIR/NativeMessagingHosts/orch.pr_tabs.json"
}
teardown() { teardown_fixture; }

# The extension ID Chrome derives from the manifest key: sha256 of the key, first 32 hex digits mapped to a-p.
extension_id() {
  python3 -c 'import base64, hashlib, json, sys
key = json.load(open(sys.argv[1]))["key"]
digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
print(digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop")))' "$ROOT/chrome/pr-tabs/extension/manifest.json"
}

# ask_host <launcher> <json>: sends one framed native message and prints the framed reply's JSON.
ask_host() {
  python3 -c 'import struct, sys; body = sys.argv[1].encode(); sys.stdout.buffer.write(struct.pack("=I", len(body)) + body)' "$2" |
    "$1" | python3 -c 'import struct, sys; data = sys.stdin.buffer.read(); size = struct.unpack("=I", data[:4])[0]; assert len(data) == 4 + size; print(data[4:].decode())'
}

@test "install registers the PR tabs host for the extension in the Chrome profile" {
  mkdir -p "$CHROME_DIR"
  fresh_install
  id=$(extension_id)
  jq -e --arg origin "chrome-extension://$id/" '.name == "orch.pr_tabs" and .type == "stdio" and .allowed_origins == [$origin]' "$HOST_MANIFEST" >/dev/null
  launcher=$(jq -r .path "$HOST_MANIFEST")
  [ -x "$launcher" ]
  python=$(sed -n 's/^export ORCH_PYTHON3=//p' "$launcher")
  [ "$("$python" -c 'import sys; print(sys.version_info >= (3, 11))')" = True ]
  grep -qx "exec $python .*" "$launcher"
  run ask_host "$launcher" '{"type":"ping"}'
  [ "$status" -eq 0 ]
  printf '%s' "$output" | jq -e '.ok == false and (.error | test("unknown request type"))' >/dev/null
  printf '%s\n' '[pr_tabs]' 'org = "acme"' 'default_repo = "webapp"' >"$TMP/relative config.toml"
  run /bin/bash -c 'cd "$1"; ORCH_CONFIG="relative config.toml" /bin/bash "$2"' _ "$TMP" "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  run ask_host "$launcher" '{"type":"settings"}'
  [ "$status" -eq 0 ]
  printf '%s' "$output" | jq -e '.ok and .org == "acme" and .defaultRepo == "webapp"' >/dev/null
}

@test "a second install leaves the host registration as it is" {
  mkdir -p "$CHROME_DIR"
  fresh_install
  before=$(cat "$HOST_MANIFEST" "$(jq -r .path "$HOST_MANIFEST")")
  install_ok
  after=$(cat "$HOST_MANIFEST" "$(jq -r .path "$HOST_MANIFEST")")
  [ "$before" = "$after" ]
  printf '%s\n' "$output" | grep -F "$HOST_MANIFEST" | grep -q '^already set'
  run grep -q '^backed up' <<<"$output"
  [ "$status" -eq 1 ]
  [ -z "$(find "$CHROME_DIR/NativeMessagingHosts" "$HOME/.claude/chrome-pr-tabs" -name '*.bak.*')" ]
}

@test "a second install makes a launcher that lost its executable bit runnable again" {
  mkdir -p "$CHROME_DIR"
  fresh_install
  launcher=$(jq -r .path "$HOST_MANIFEST")
  chmod -x "$launcher"
  install_ok
  [ -x "$launcher" ]
}

# done_count <output> <label>: the number before <label> in the installer's final Done line.
done_count() {
  printf '%s\n' "$1" | sed -n "s/^Done: .*[^0-9]\([0-9][0-9]*\) $2[,.].*/\1/p; s/^Done: \([0-9][0-9]*\) $2[,.].*/\1/p"
}

@test "the Done summary counts the rendered host files" {
  fresh_install
  install_ok
  without=$(done_count "$output" 'already linked')
  mkdir -p "$CHROME_DIR"
  install_ok
  [ "$(done_count "$output" linked)" -eq 2 ]
  install_ok
  [ "$(done_count "$output" 'already linked')" -eq $((without + 2)) ]
}

@test "a different host manifest in the way is backed up" {
  mkdir -p "$CHROME_DIR/NativeMessagingHosts"
  printf '{"name":"old"}\n' >"$HOST_MANIFEST"
  fresh_install
  jq -e '.name == "orch.pr_tabs"' "$HOST_MANIFEST" >/dev/null
  for backup in "$HOME"/.claude/backups/*/home/"${HOST_MANIFEST#"$HOME"/}"; do jq -e '.name == "old"' "$backup" >/dev/null; done
  [ -z "$(find "$CHROME_DIR/NativeMessagingHosts" -name '*.bak.*')" ]
}

@test "install without a Chrome profile registers nothing" {
  fresh_install
  [ ! -e "$CHROME_DIR" ]
  [ ! -e "$HOME/.claude/chrome-pr-tabs" ]
  contains "$output" 'Chrome PR tabs host (no Chrome profile'
  not_contains "$output" 'Load unpacked'
  [ ! -e "$GUI_CALLS" ]
}

# prefs <file> <extension id>: a Chrome preferences fixture whose extension settings list that ID.
prefs() {
  mkdir -p "$(dirname "$1")"
  jq -n --arg id "$2" '{extensions: {settings: {($id): {location: 4}}}}' >"$1"
}

@test "a profile without the extension gets the load steps once, the path on the clipboard and chrome://extensions open" {
  prefs "$CHROME_DIR/Default/Preferences" aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  fresh_install
  [ "$(printf '%s\n' "$output" | grep -c 'Load unpacked')" -eq 1 ]
  printf '%s\n' "$output" | grep '^load once' | grep -F 'Developer mode on → Load unpacked → ' | grep -q ' → paste → Open$'
  extension=$(cd "$CLONE/chrome/pr-tabs/extension" && pwd -P)
  grep -Eqx "(pbcopy|xclip) $extension" "$GUI_CALLS"
  grep -Eqx '(open -a Google Chrome|google-chrome) chrome://extensions' "$GUI_CALLS"
}

@test "a profile with the extension loaded stays quiet" {
  mkdir -p "$CHROME_DIR/Default"
  printf '{}\n' >"$CHROME_DIR/Default/Preferences"
  prefs "$CHROME_DIR/Profile 2/Secure Preferences" "$(extension_id)"
  fresh_install
  not_contains "$output" 'Load unpacked'
  [ ! -e "$GUI_CALLS" ]
}

@test "a stale profile without a Chrome to open stays quiet" {
  mkdir -p "$CHROME_DIR"
  printf '#!/bin/sh\nexit 1\n' >"$BIN/open"
  printf '#!/bin/sh\nexit 1\n' >"$BIN/google-chrome"
  fresh_install
  not_contains "$output" 'Load unpacked'
  [ ! -e "$GUI_CALLS" ]
}

@test "a headless session stays quiet" {
  mkdir -p "$CHROME_DIR"
  export SSH_CONNECTION="10.0.0.1 22 10.0.0.2 22"
  fresh_install
  not_contains "$output" 'Load unpacked'
  [ ! -e "$GUI_CALLS" ]
}
