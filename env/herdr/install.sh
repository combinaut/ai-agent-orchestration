# shellcheck shell=bash
# Installs Herdr, mise Node, and the pinned combinaut Radar plugin.
# Sourced by install.sh; see env/README.md for the contract.

RADAR_ID="hhdebb.herdr-radar"
RADAR_URL="https://github.com/combinaut/herdr-radar.git"

# version_at_least <version> <major> <minor>
version_at_least() {
  local major minor
  major=$(printf '%s' "$1" | cut -d. -f1)
  minor=$(printf '%s' "$1" | cut -d. -f2)
  [ "${major:-0}" -gt "$2" ] || { [ "${major:-0}" -eq "$2" ] && [ "${minor:-0}" -ge "$3" ]; }
}

install_official_script() (
  local script
  script=$(mktemp)
  trap 'rm -f "$script"' EXIT
  curl -fsSL "$1" -o "$script" || die "cannot download $1; check your network and run install.sh again"
  [ -s "$script" ] || die "empty installer from $1"
  /bin/sh "$script"
)

install_herdr() { HERDR_INSTALL_DIR="$HOME/.local/bin" install_official_script https://herdr.dev/install.sh; }
install_mise() { MISE_INSTALL_PATH="$HOME/.local/bin/mise" install_official_script https://mise.run; }

herdr_version() { "$HERDR_EXE" --version 2>/dev/null | sed -n 's/^herdr \([0-9][0-9.]*\).*/\1/p'; }

env_check() {
  command -v curl >/dev/null 2>&1 || die "curl not found. Fix: $(platform_dependency_fix curl)"
  command -v python3 >/dev/null 2>&1 || die "python3 not found. Fix: $(platform_dependency_fix python3)"
}

runtime_path_hint() {
  case "$1" in
    "$HOME/.local/bin":*) ;;
    *)
      # shellcheck disable=SC2016
      say "PATH setup" 'Add export PATH="$HOME/.local/bin:$PATH" to your shell startup file (e.g. '"$(platform_shell_profile)"'); then open a new shell for herdr and mise commands.'
      ;;
  esac
}

radar_origin_matches() {
  case "${1%/}" in
    https://github.com/combinaut/herdr-radar|https://github.com/combinaut/herdr-radar.git|git@github.com:combinaut/herdr-radar|git@github.com:combinaut/herdr-radar.git|ssh://git@github.com/combinaut/herdr-radar|ssh://git@github.com/combinaut/herdr-radar.git) return 0 ;;
    *) return 1 ;;
  esac
}

install_runtime() {
  local version="" node_version caller_path=$PATH status=installed existing managed
  existing=$(type -P herdr 2>/dev/null || true)
  [ -z "$existing" ] || existing="$(cd "$(dirname "$existing")" && pwd -P)/${existing##*/}"
  managed="$(cd "$HOME/.local/bin" 2>/dev/null && pwd -P || printf '%s' "$HOME/.local/bin")/herdr"
  export PATH="$HOME/.local/bin:$PATH"
  hash -r
  if [ -n "$existing" ] && [ "$existing" != "$managed" ]; then
    HERDR_EXE=$existing
    version=$(herdr_version)
    version_at_least "$version" 0 9 || die "Herdr at $existing reports ${version:-no readable version}; this setup needs 0.9 or newer. Fix: upgrade it the way you installed it, then run install.sh again"
    say "herdr" "already installed ($version, $existing)"
  else
    HERDR_EXE="$HOME/.local/bin/herdr"
    if [ -x "$HERDR_EXE" ]; then
      version=$(herdr_version)
      status=upgraded
    fi
    if ! version_at_least "$version" 0 9; then
      install_herdr
      version=$(herdr_version)
      version_at_least "$version" 0 9 || die "Herdr install failed; run install.sh again"
      say "herdr" "$status ($version)"
    else
      say "herdr" "already installed ($version)"
    fi
  fi
  if ! command -v mise >/dev/null 2>&1; then
    install_mise
  fi
  if [ "$HERDR_EXE" = "$HOME/.local/bin/herdr" ] || [ "$(command -v mise)" = "$HOME/.local/bin/mise" ]; then
    runtime_path_hint "$caller_path"
  fi
  node_version=$(tr -d '[:space:]' <"$ENV_DIR/node.pin")
  (cd "$ENV_DIR" && mise install "node@$node_version")
  RADAR_NODE=$(cd "$ENV_DIR" && mise which node --tool "node@$node_version")
  [ -x "$RADAR_NODE" ] || die "mise did not provide Node $node_version"
  [ "$("$RADAR_NODE" --version)" = "v$node_version" ] || die "mise Node version does not match $ENV_DIR/node.pin"
}

prune_runtime_links() {
  local entry
  for entry in "$RADAR_PLUGIN"/* "$RADAR_PLUGIN"/.[!.]* "$RADAR_PLUGIN"/..?*; do
    if [ -L "$entry" ] && [ ! -e "$entry" ]; then
      rm "$entry"
      moved=1
      say "removed stale" "$entry"
    fi
  done
}

prepare_runtime_directory() {
  local backup
  RADAR_PLUGIN="$CLAUDE_DIR/herdr-radar-runtime"
  if [ -L "$RADAR_PLUGIN" ]; then
    backup=$(backup_name "$RADAR_PLUGIN")
    mv "$RADAR_PLUGIN" "$backup"
    moved=1
    say "backed up" "$RADAR_PLUGIN -> $backup"
  fi
  mkdir -p "$RADAR_PLUGIN"
  RADAR_PLUGIN=$(cd "$RADAR_PLUGIN" && pwd -P)
}

replace_runtime_manifest() {
  python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$1" "$2"
}

prepare_plugin() {
  local path=$1 entry tmp
  prepare_runtime_directory
  prune_runtime_links
  for entry in "$path"/*; do
    [ "${entry##*/}" = herdr-plugin.toml ] && continue
    if [ "$(readlink "$RADAR_PLUGIN/${entry##*/}" 2>/dev/null || true)" != "$entry" ]; then moved=1; fi
    link_entry "$entry" "$RADAR_PLUGIN/${entry##*/}"
  done
  tmp=$(mktemp "$RADAR_PLUGIN/.herdr-plugin.XXXXXX")
  if ! python3 "$ENV_DIR/pin-runtime.py" "$path/herdr-plugin.toml" "$RADAR_NODE" >"$tmp"; then
    rm -f "$tmp"
    die "cannot pin the Radar runtime manifest"
  fi
  if [ -L "$RADAR_PLUGIN/herdr-plugin.toml" ] || ! cmp -s "$tmp" "$RADAR_PLUGIN/herdr-plugin.toml"; then
    if ! replace_runtime_manifest "$tmp" "$RADAR_PLUGIN/herdr-plugin.toml"; then
      rm -f "$tmp"
      die "cannot replace the Radar runtime manifest"
    fi
    moved=1
  else
    rm -f "$tmp"
  fi
}

# radar_daemon <checkout>: pid of the daemon answering this Herdr session's control endpoint, empty when none does.
radar_daemon() {
  "$RADAR_NODE" -e 'require(process.argv[1] + "/lib/control").request({ cmd: "ping" }).then((r) => { if (r && r.ok && r.pid) console.log(r.pid); })' "$1" 2>/dev/null || true
}

# radar_daemon_wait <stop|start> <checkout> <pid before>: succeeds once this session's daemon is not the pid before (stop) or is a running pid other than it (start).
radar_daemon_wait() {
  local mode=$1 tries=${ORCH_RADAR_WAIT_TRIES:-40} now
  while :; do
    now=$(radar_daemon "$2")
    if [ "$mode" = stop ]; then
      [ "$now" != "$3" ] && return 0
    else
      [ -n "$now" ] && [ "$now" != "$3" ] && return 0
    fi
    tries=$((tries - 1))
    [ "$tries" -gt 0 ] || return 1
    sleep "${ORCH_RADAR_WAIT_SLEEP:-0.5}"
  done
}

radar_start() {
  local status=0
  herdr_config_snapshot
  platform_run_detached "$HERDR_EXE" plugin action invoke "$RADAR_ID.state-start" >/dev/null || status=$?
  herdr_config_check
  [ "$status" = 0 ] || die "Radar state-start failed (status $status); run install.sh again"
}

env_install() {
  local pin path url head moved=0 source origin known=""
  pin=$(tr -d '[:space:]' <"$ENV_DIR/radar.pin")
  path=$(config_value ORCH_RADAR_PATH)
  if [ -z "$path" ]; then
    path="$CLAUDE_DIR/herdr-radar"
    say "note" "ORCH_RADAR_PATH is empty; using $path (set it in $CONFIG)"
  fi
  url=$RADAR_URL

  if [ ! -e "$path" ]; then
    mkdir -p "$(dirname "$path")"
    git clone -q "$url" "$path"
    say "cloned" "$path"
  else
    [ -e "$path/.git" ] || die "$path exists but is not a git clone. Move it away or set ORCH_RADAR_PATH."
    refuse_local_changes "$path" "the Herdr Radar checkout"
    origin=$(git -C "$path" remote get-url origin 2>/dev/null || true)
    if ! radar_origin_matches "$origin"; then
      git -C "$path" remote set-url origin "$url"
      git -C "$path" fetch -q origin
      say "replaced" "Radar origin $origin -> $url"
    fi
  fi
  path=$(cd "$path" && pwd -P)

  head=$(git -C "$path" rev-parse HEAD)
  if [ "$head" = "$pin" ]; then
    say "radar" "already at pin ${pin:0:7} ($path)"
  else
    git -C "$path" cat-file -e "$pin^{commit}" 2>/dev/null || git -C "$path" fetch -q origin
    git -C "$path" cat-file -e "$pin^{commit}" 2>/dev/null || die "pinned commit $pin not found in $path (origin $(git -C "$path" remote get-url origin 2>/dev/null)). Fix: git -C $path remote set-url origin $RADAR_URL"
    git -C "$path" checkout -q --detach "$pin"
    say "checked out" "$path at pin ${pin:0:7} (was ${head:0:7})"
    moved=1
  fi

  install_runtime
  prepare_plugin "$path"
  source=$("$HERDR_EXE" plugin list | sed -n "s/^- $RADAR_ID .*\[\(.*\)\]\$/\1/p")
  source=$(printf '%s' "$source" | sed 's/; [0-9][0-9]* warning(s)$//')
  if [ "$source" != "local:$RADAR_PLUGIN" ]; then
    if [ -n "$source" ]; then
      "$HERDR_EXE" plugin uninstall "$RADAR_ID" >/dev/null
      say "replaced" "Herdr plugin $RADAR_ID from $source with combinaut/herdr-radar"
    fi
    "$HERDR_EXE" plugin link "$RADAR_PLUGIN" >/dev/null
    moved=1
    say "linked" "Herdr plugin $RADAR_ID -> $RADAR_PLUGIN"
  else
    say "already linked" "Herdr plugin $RADAR_ID -> $RADAR_PLUGIN"
  fi
  if [ "$moved" = 1 ]; then
    known=$(radar_daemon "$path")
    "$HERDR_EXE" plugin action invoke "$RADAR_ID.state-stop" >/dev/null
    [ -z "$known" ] || radar_daemon_wait stop "$path" "$known" || say "note" "the Radar daemon did not exit after state-stop; starting anyway"
  fi
  radar_start
  if ! radar_daemon_wait start "$path" "$known"; then
    radar_start
    radar_daemon_wait start "$path" "$known" || die "Radar daemon is not running after state-start; run install.sh again, or see: $HERDR_EXE plugin log"
  fi
  say "running" "Radar daemon (mise Node $RADAR_NODE)"
  install_todo_popup
}

TODO_ID="combinaut.todo"
TODO_KEY_START="# >>> ai-agent-orchestration to-do key (managed by install.sh; edits inside are replaced)"
TODO_KEY_END="# <<< ai-agent-orchestration to-do key"

todo_key_block() {
  printf '%s\n[[keys.command]]\nkey = "prefix+t"\ntype = "plugin_action"\ncommand = "%s.open"\ndescription = "to-do list"\n%s\n' "$TODO_KEY_START" "$TODO_ID" "$TODO_KEY_END"
}

# todo_key_markers_ok <file>: succeeds when the file has no key block markers or exactly one start followed by one end.
# Marker lines match with trailing whitespace or a carriage return.
todo_key_markers_ok() {
  awk -v start="$TODO_KEY_START" -v end="$TODO_KEY_END" '
    { line = $0; sub(/[ \t\r]+$/, "", line) }
    line == start { starts++ }
    line == end { ends++; if (!starts) end_first = 1 }
    END { exit !((!starts && !ends) || (starts == 1 && ends == 1 && !end_first)) }
  ' "$1"
}

# todo_config_check: ends the install before anything changes when the key block markers in Herdr's config.toml are unpaired.
todo_config_check() {
  local target
  target=$(resolve_link_path "$(herdr_config_file)") || die "cannot resolve $(herdr_config_file)"
  [ ! -f "$target" ] || todo_key_markers_ok "$target" || die "$target has an unpaired '$TODO_KEY_START' or '$TODO_KEY_END' marker; fix or remove it, then run install.sh again (the file is unchanged)"
}

# todo_config_without_key <file>: the file without our key block and the blank line before it; the markers must be paired.
# Every other line keeps its bytes.
todo_config_without_key() {
  awk -v start="$TODO_KEY_START" -v end="$TODO_KEY_END" '
    { line = $0; sub(/[ \t\r]+$/, "", line) }
    line == start { skip = 1; held = ""; next }
    skip { if (line == end) skip = 0; next }
    line == "" { printf "%s", held; held = $0 "\n"; next }
    { printf "%s", held; held = ""; print }
    END { printf "%s", held }
  ' "$1"
}

# todo_config_want <enabled> <target> <snap> <want>: builds <want> from <snap>, a copy of <target>; CRLF files get a CRLF block.
todo_config_want() {
  todo_config_without_key "$3" >"$4" || return 1
  [ "$1" = 1 ] || return 0
  if head -n 1 "$3" | grep -q "$(printf '\r')\$"; then
    { printf '\n'; todo_key_block; } | sed "s/\$/$(printf '\r')/" >>"$4"
  else
    { printf '\n'; todo_key_block; } >>"$4"
  fi
}

# todo_config_sync <enabled>: the prefix+t block is in Herdr's config.toml when enabled and absent otherwise.
# Returns 0 when the file changed, 1 when it did not; any failure ends the install and leaves the file as it was.
# Another writer changing the file before the rename restarts the edit from the fresh content, up to three times.
todo_config_sync() {
  local target snap want tmp backup mode attempt
  target=$(resolve_link_path "$(herdr_config_file)") || die "cannot resolve $(herdr_config_file)"
  if [ ! -f "$target" ]; then
    [ "$1" = 1 ] || return 1
    mkdir -p "$(dirname "$target")" || die "cannot create $(dirname "$target")"
    : >"$target" || die "cannot create $target"
  fi
  for attempt in 1 2 3; do
    snap=$(mktemp "${TMPDIR:-/tmp}/orch-todo-key.XXXXXX") || die "cannot create a temporary file for $target"
    want=$(mktemp "${TMPDIR:-/tmp}/orch-todo-key.XXXXXX") || { rm -f "$snap"; die "cannot create a temporary file for $target"; }
    cp "$target" "$snap" || { rm -f "$snap" "$want"; die "cannot read $target"; }
    todo_key_markers_ok "$snap" || { rm -f "$snap" "$want"; die "$target has an unpaired '$TODO_KEY_START' or '$TODO_KEY_END' marker; fix or remove it, then run install.sh again (the file is unchanged)"; }
    todo_config_want "$1" "$target" "$snap" "$want" || { rm -f "$snap" "$want"; die "cannot prepare the new $target"; }
    if cmp -s "$want" "$snap"; then
      rm -f "$snap" "$want"
      say "already set" "$target: to-do key block"
      return 1
    fi
    mode=$(claude_md_mode "$target") || { rm -f "$snap" "$want"; die "cannot read the mode of $target"; }
    tmp=$(mktemp "$target.XXXXXX") || { rm -f "$snap" "$want"; die "cannot create a temporary file beside $target"; }
    if ! { cat "$want" >"$tmp" && chmod "$mode" "$tmp"; }; then
      rm -f "$snap" "$want" "$tmp"
      die "cannot write $target"
    fi
    if cmp -s "$snap" "$target"; then
      backup=$(backup_name "$target") || { rm -f "$snap" "$want" "$tmp"; die "cannot name a backup of $target"; }
      cp -p "$target" "$backup" || { rm -f "$snap" "$want" "$tmp"; die "cannot back up $target"; }
      say "backed up" "$target -> $backup"
      mv "$tmp" "$target" || { rm -f "$snap" "$want" "$tmp"; die "cannot write $target (backup: $backup)"; }
      rm -f "$snap" "$want"
      if [ "$1" = 1 ]; then say "added" "$target: prefix+t opens the to-do popup"; else say "removed" "$target: to-do key block"; fi
      return 0
    fi
    rm -f "$snap" "$want" "$tmp"
    say "note" "$target changed while it was being edited; trying again ($attempt of 3)"
  done
  die "$target keeps changing; run install.sh again (the file is unchanged)"
}

# install_todo_popup: the to-do plugin and the prefix+t block are installed unless herdr.todo_popup is false, which removes both.
install_todo_popup() {
  local enabled plugin source changed=0
  enabled=$(config_value ORCH_HERDR_TODO_POPUP)
  [ "$enabled" = 0 ] || enabled=1
  todo_config_check
  plugin="$ORCH_REPO/env/herdr/todo-plugin"
  source=$("$HERDR_EXE" plugin list | sed -n "s/^- $TODO_ID .*\[\(.*\)\]\$/\1/p")
  source=$(printf '%s' "$source" | sed 's/; [0-9][0-9]* warning(s)$//')
  if [ "$enabled" = 1 ]; then
    if [ "$source" != "local:$plugin" ]; then
      [ -z "$source" ] || "$HERDR_EXE" plugin unlink "$TODO_ID" >/dev/null
      "$HERDR_EXE" plugin link "$plugin" >/dev/null
      say "linked" "Herdr plugin $TODO_ID -> $plugin"
    else
      say "already linked" "Herdr plugin $TODO_ID -> $plugin"
    fi
  elif [ -n "$source" ]; then
    "$HERDR_EXE" plugin unlink "$TODO_ID" >/dev/null
    say "unlinked" "Herdr plugin $TODO_ID"
  fi
  if todo_config_sync "$enabled"; then changed=1; fi
  [ "$changed" = 0 ] || "$HERDR_EXE" server reload-config >/dev/null 2>&1 || say "note" "Herdr did not reload its config; run: $HERDR_EXE server reload-config"
}

herdr_config_file() { printf '%s/herdr/config.toml' "${XDG_CONFIG_HOME:-$HOME/.config}"; }

# herdr_config_foreign <file>: the file without Radar's complete managed blocks; an unmatched opening marker stays.
herdr_config_foreign() {
  awk '
    /^# >>> herdr-radar .* block$/ { printf "%s", held; held = $0 "\n"; open = 1; next }
    open { held = held $0 "\n"; if ($0 ~ /^# <<< herdr-radar .* block$/) { held = ""; open = 0 } next }
    { print }
    END { printf "%s", held }
  ' "$1"
}

herdr_config_snapshot() {
  local herdr_config
  herdr_config=$(herdr_config_file)
  HERDR_CONFIG_COPY=""
  [ -f "$herdr_config" ] || return 0
  HERDR_CONFIG_COPY=$(mktemp)
  cp -p "$herdr_config" "$HERDR_CONFIG_COPY"
}

herdr_config_check() {
  local herdr_config backup
  herdr_config=$(herdr_config_file)
  [ -n "$HERDR_CONFIG_COPY" ] || return 0
  if [ -f "$herdr_config" ] && [ "$(herdr_config_foreign "$herdr_config"; echo .)" = "$(herdr_config_foreign "$HERDR_CONFIG_COPY"; echo .)" ]; then
    rm -f "$HERDR_CONFIG_COPY"
    say "kept" "$herdr_config (your blocks unchanged)"
    return 0
  fi
  backup=$(backup_name "$herdr_config")
  mv "$HERDR_CONFIG_COPY" "$backup"
  say "WARNING" "$herdr_config changed outside Radar's blocks; your previous file is $backup"
}
