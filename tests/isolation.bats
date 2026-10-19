#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output.
load test_helper

@test "service managers resolve to shared stubs" {
  run command -v launchctl
  [ "$status" -eq 0 ]
  [ "$output" = "$BATS_SERVICE_STUB_BIN/launchctl" ]
  assert_service_manager_stubs
}

@test "resolution guard detects a missing stub without executing a manager" {
  mv "$BATS_SERVICE_STUB_BIN/launchctl" "$BATS_SERVICE_STUB_BIN/launchctl.off"
  run assert_service_manager_stubs
  [ "$status" -ne 0 ]
  mv "$BATS_SERVICE_STUB_BIN/launchctl.off" "$BATS_SERVICE_STUB_BIN/launchctl"
}

@test "fixture Git environment disables background workers for plain repositories" {
  local plain="$TMP/plain" clone="$TMP/plain-clone"
  git init -q "$plain"
  git clone -q "$ORIGIN" "$clone"
  for repository in "$plain" "$clone"; do
    [ "$(git -C "$repository" config --get gc.auto)" = 0 ]
    [ "$(git -C "$repository" config --get maintenance.auto)" = false ]
    [ "$(git -C "$repository" config --get core.fsmonitor)" = false ]
  done
}

@test "mutant direct service calls in installer adapters only change stub logs" {
  for adapter in macos linux; do
    printf '\nlaunchctl bootout gui/123/orch.fixture\nsystemctl --user stop fixture.service\n' >>"$SRC/lib/platform/$adapter.sh"
  done
  fresh_install
  contains "$(cat "$LAUNCHCTL_CALLS")" 'bootout gui/123/orch.fixture'
  contains "$(cat "$SYSTEMCTL_CALLS")" 'stop fixture.service'
  [ -L "$HOME/.claude/coordinator/watchers/watchers.py" ]
}
