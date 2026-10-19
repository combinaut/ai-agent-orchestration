#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

load test_helper

OLD=com.combinaut.coord-

setup() {
  setup_fixture
  fresh_install
  mkdir -p "$TMP/webapp"
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  config_set ORCH_CODEX_WORKER_MODEL 'w' ORCH_CODEX_SMART_MODEL 's' ORCH_LEGACY_MAP_REPO "$TMP/webapp"
  use_service_state
  case "$(uname -s)" in
    Darwin) DIR="$HOME/Library/LaunchAgents" SUFFIX=plist CALLS=$LAUNCHCTL_CALLS STATE=$LAUNCHCTL_STATE PLATFORM_DIR=macos ;;
    Linux) DIR="$HOME/.config/systemd/user" SUFFIX=service CALLS=$SYSTEMCTL_CALLS STATE=$SYSTEMCTL_STATE PLATFORM_DIR=linux ;;
  esac
  mkdir -p "$DIR"
}
teardown() { teardown_fixture; }

# unit_state_name <file name>: the name the service manager lists a unit under.
unit_state_name() { case "$(uname -s)" in Darwin) printf '%s' "${1%.plist}" ;; *) printf '%s' "$1" ;; esac; }

# seed_old <unit file name>...: an old machine's unit files, loaded.
seed_old() {
  local name
  for name in "$@"; do
    printf 'old\n' >"$DIR/$name"
    printf '%s\n' "$(unit_state_name "$name")" >>"$STATE"
  done
}

old_watchers() {
  find "$ROOT/coordinator/watchers/service/$PLATFORM_DIR" -name '*.template' | sed "s|.*/orch\.coord-|$OLD|; s|\.template\$||" | LC_ALL=C sort
}

old_legacy_map() {
  case "$(uname -s)" in
    Darwin) printf '%s\n' "${OLD}legacy-map.plist" ;;
    Linux) printf '%s\n' "${OLD}legacy-map.service" "${OLD}legacy-map.timer" ;;
  esac
}

no_old_left() {
  [ -z "$(find "$DIR" -name "${OLD}*")" ]
  ! grep -q "^${OLD}" "$STATE"
}

new_registered() {
  local name
  for name in "$@"; do
    grep -qxF "$(unit_state_name "${name/#$OLD/orch.coord-}")" "$STATE"
  done
}

@test "an update stops and removes the whole old generation and registers the new one" {
  # shellcheck disable=SC2046
  seed_old $(old_watchers) $(old_legacy_map)
  install_ok
  no_old_left
  contains "$output" "removed         $DIR/${OLD}watcher-hog.$SUFFIX"
  # shellcheck disable=SC2046
  new_registered $(old_watchers)
  case "$(uname -s)" in
    Darwin) new_registered "${OLD}legacy-map.plist" ;;
    Linux) new_registered "${OLD}legacy-map.timer" ;;
  esac
  contains "$(cat "$CALLS")" "${OLD}watcher-hog"
}

@test "an old service that will not stop keeps its file and nothing new starts" {
  # shellcheck disable=SC2046
  seed_old $(old_watchers)
  case "$(uname -s)" in
    Darwin) use_stuck_service "${OLD}watcher-hog" "" ;;
    Linux) use_stuck_service "" "${OLD}watcher-hog.service" ;;
  esac
  install_fails
  [ "$status" -eq 1 ]
  contains "$output" 'not removed'
  [ -f "$DIR/${OLD}watcher-hog.$SUFFIX" ]
  not_contains "$(cat "$CALLS")" 'bootstrap gui'
  not_contains "$(cat "$CALLS")" 'enable --now'
  run grep -qxF "$(unit_state_name "orch.coord-watcher-hog.$SUFFIX")" "$STATE"
  [ "$status" -ne 0 ]
}

@test "a second install after the migration removes and starts nothing" {
  # shellcheck disable=SC2046
  seed_old $(old_watchers) $(old_legacy_map)
  install_ok
  before=$(wc -l <"$CALLS")
  install_ok
  no_old_left
  not_contains "$output" 'removed  '
  not_contains "$(sed "1,${before}d" "$CALLS")" 'bootstrap'
  not_contains "$(sed "1,${before}d" "$CALLS")" 'bootout'
  not_contains "$(sed "1,${before}d" "$CALLS")" 'enable --now'
  not_contains "$(sed "1,${before}d" "$CALLS")" 'disable --now'
}

@test "turning services off stops and removes the old generation" {
  # shellcheck disable=SC2046
  seed_old $(old_watchers) $(old_legacy_map)
  config_set ORCH_WATCHERS_SERVICE 0
  install_ok
  no_old_left
}

@test "the old legacy map job is replaced by the new one" {
  # shellcheck disable=SC2046
  seed_old $(old_legacy_map)
  install_ok
  no_old_left
  case "$(uname -s)" in
    Darwin) new_registered "${OLD}legacy-map.plist" ;;
    Linux) new_registered "${OLD}legacy-map.timer" ;;
  esac
}
