#!/usr/bin/env bats
# shellcheck disable=SC2153,SC2154 # bats helpers provide fixture variables, status and output after run.

load test_helper

@test "fresh one-liner installs links and watchers runtime directories" {
  fresh_install
  [ -L "$HOME/.claude/coordinator/watchers/todo_answers.py" ]
  [ -L "$HOME/.local/bin/orch-signal" ]
  [ "$HOME/.local/bin/orch-signal" -ef "$CLONE/orch-signal" ]
  [ -L "$HOME/.claude/skills/architect" ]
  [ -L "$HOME/.claude/coordinator/watchers/watchers.py" ]
  [ -L "$HOME/.claude/coordinator/legacy-scan.sh" ]
  [ -d "$HOME/.claude/coordinator/watchers/bindings" ]
}

@test "--links-only syncs links and removes stale links without touching settings or services" {
  mkdir -p "$SRC/rules"
  printf 'old shared rule\n' >"$SRC/rules/links-only-old.md"
  git -C "$SRC" add rules/links-only-old.md
  git -C "$SRC" -c user.email=test@example.invalid -c user.name=test commit -qm links-only-old
  git -C "$ORIGIN" fetch -q "$SRC" main:main
  fresh_install
  settings="$HOME/.claude/settings.json"
  cp "$settings" "$TMP/settings-before.json"
  find "$HOME" -path "$CLONE" -prune -o -path "$HOME/.claude/.orch-links" -prune -o -type f -exec cksum {} \; | LC_ALL=C sort >"$TMP/home-before.cksum"
  : >"$LAUNCHCTL_CALLS"
  : >"$SYSTEMCTL_CALLS"
  : >"$HERDR_CALLS"
  git -C "$SRC" rm -q rules/links-only-old.md
  mkdir -p "$SRC/rules"
  printf 'new shared rule\n' >"$SRC/rules/links-only.md"
  git -C "$SRC" add rules/links-only.md
  git -C "$SRC" -c user.email=test@example.invalid -c user.name=test commit -qm links-only
  git -C "$ORIGIN" fetch -q "$SRC" main:main

  run /bin/bash -c 'cd "$1"; exec /bin/bash -c "$ONE_LINER" _ --links-only --env herdr' _ "$TMP"
  [ "$status" -eq 0 ]
  [ -L "$HOME/.claude/rules/links-only.md" ]
  [ "$HOME/.claude/rules/links-only.md" -ef "$CLONE/rules/links-only.md" ]
  [ ! -e "$HOME/.claude/rules/links-only-old.md" ]
  cmp "$TMP/settings-before.json" "$settings"
  find "$HOME" -path "$CLONE" -prune -o -path "$HOME/.claude/.orch-links" -prune -o -type f -exec cksum {} \; | LC_ALL=C sort >"$TMP/home-after.cksum"
  cmp "$TMP/home-before.cksum" "$TMP/home-after.cksum"
  [ ! -s "$LAUNCHCTL_CALLS" ]
  [ ! -s "$SYSTEMCTL_CALLS" ]
  [ ! -s "$HERDR_CALLS" ]
  contains "$output" 'ignored'
  contains "$output" 'settings and services were not touched'
  contains "$output" 'Run install.sh without --links-only to apply settings and service changes'
}

@test "--links-only refuses a first install" {
  run /bin/bash -c 'cd "$1"; exec /bin/bash -c "$ONE_LINER" _ --links-only' _ "$TMP"
  [ "$status" -ne 0 ]
  contains "$output" 'run a full install first'
  [ ! -e "$HOME/.claude/settings.json" ]
}

@test "CLAUDE.md marked orchestration block is replaced without changing surrounding text" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  end='<!-- orchestration:end -->'
  printf 'before\n%s\nold managed text\n%s\nafter\n' "$start" "$end" >"$claude_md"
  chmod 640 "$claude_md"
  install_ok
  contains "$output" 'updated'
  [ "$(head -n 1 "$claude_md")" = before ]
  [ "$(tail -n 1 "$claude_md")" = after ]
  cmp "$CLONE/snippets/CLAUDE.md.snippet" <(sed -n "/$start/,/$end/p" "$claude_md")
  mode=$(stat -f '%Lp' "$claude_md" 2>/dev/null) || mode=$(stat -c '%a' "$claude_md")
  [ "$mode" = 640 ]
  for backup in "$HOME"/.claude/backups/*/CLAUDE.md; do [ -f "$backup" ]; done
  [ "$(cat "$backup")" = "$(printf 'before\n%s\nold managed text\n%s\nafter' "$start" "$end")" ]
}

@test "CLAUDE.md replacement ignores inline marker text outside the managed block" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  end='<!-- orchestration:end -->'
  printf 'quoted marker: %s\npersonal instructions\n%s\nold managed text\n%s\nafter\n' "$start" "$start" "$end" >"$claude_md"
  install_ok
  contains "$output" 'updated'
  [ "$(head -n 2 "$claude_md")" = "$(printf 'quoted marker: %s\npersonal instructions' "$start")" ]
  [ "$(tail -n 1 "$claude_md")" = after ]
}

@test "CLAUDE.md replacement uses a clean GNU stat mode fallback" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  end='<!-- orchestration:end -->'
  printf 'personal instructions\n%s\nold managed text\n%s\n' "$start" "$end" >"$claude_md"
  cat >"$BIN/stat" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = -f ]; then
  printf 'unexpected filesystem output\n'
  exit 0
fi
[ "$1" = -c ] && { printf '640\n'; exit 0; }
exit 1
EOF
  chmod +x "$BIN/stat"
  install_ok
  contains "$output" 'updated'
  rm "$BIN/stat"
  mode=$(stat -f '%Lp' "$claude_md" 2>/dev/null) || mode=$(stat -c '%a' "$claude_md")
  [ "$mode" = 640 ]
}

@test "CLAUDE.md added block on line one updates without leaving a temporary file" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  end='<!-- orchestration:end -->'
  printf '%s\nold managed text\n%s\n' "$start" "$end" >"$claude_md"
  install_ok
  contains "$output" 'updated'
  cmp "$CLONE/snippets/CLAUDE.md.snippet" "$claude_md"
  [ -z "$(find "$HOME/.claude" -name 'CLAUDE.md.tmp.*' -print)" ]
}

@test "CLAUDE.md start marker without an end marker is skipped on every run" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  printf 'top\n%s\nUSER PRECIOUS\nmore\n' "$start" >"$claude_md"
  before=$(cksum "$claude_md")
  install_ok
  contains "$output" 'skipped'
  [ "$(cksum "$claude_md")" = "$before" ]
  install_ok
  contains "$output" 'skipped'
  [ "$(cksum "$claude_md")" = "$before" ]
}

@test "CLAUDE.md CRLF markers are recognized and replaced" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  end='<!-- orchestration:end -->'
  printf 'before\r\n%s\r\nold managed text\r\n%s\r\nafter\r\n' "$start" "$end" >"$claude_md"
  install_ok
  contains "$output" 'updated'
  [ "$(head -n 1 "$claude_md")" = $'before\r' ]
  [ "$(tail -n 1 "$claude_md")" = $'after\r' ]
}

@test "misordered CLAUDE.md markers are skipped without changing the file" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  end='<!-- orchestration:end -->'
  printf '%s\ntext\n%s\n' "$end" "$start" >"$claude_md"
  before=$(cksum "$claude_md")
  install_ok
  contains "$output" 'skipped'
  [ "$(cksum "$claude_md")" = "$before" ]
}

@test "CLAUDE.md symlink stays linked while its target is updated" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  target="$TMP/real-CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  end='<!-- orchestration:end -->'
  printf '%s\nold managed text\n%s\n' "$start" "$end" >"$target"
  rm "$claude_md"
  ln -s "$target" "$claude_md"
  install_ok
  [ -L "$claude_md" ]
  cmp "$CLONE/snippets/CLAUDE.md.snippet" "$target"
}

@test "existing CLAUDE.md is backed up before the marked block is appended" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  printf 'personal instructions\n' >"$claude_md"
  install_ok
  contains "$output" 'backed up'
  for backup in "$HOME"/.claude/backups/*/CLAUDE.md; do [ -f "$backup" ]; done
  [ "$(cat "$backup")" = 'personal instructions' ]
}

@test "CLAUDE.md replacement stops safely when interrupted during its prefix copy" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  end='<!-- orchestration:end -->'
  printf 'personal instructions\n%s\nold managed text\n%s\nafter\n' "$start" "$end" >"$claude_md"
  before=$(cksum "$claude_md")
  cat >"$BIN/head" <<'EOF'
#!/usr/bin/env bash
kill -TERM "$PPID"
exit 0
EOF
  chmod +x "$BIN/head"
  run /bin/bash "$CLONE/install.sh"
  [ "$status" -ne 0 ]
  [ "$(cksum "$claude_md")" = "$before" ]
  [ -z "$(find "$HOME/.claude" -name 'CLAUDE.md.tmp.*' -print)" ]
}

@test "self-linked CLAUDE.md is skipped without hanging the installer" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  rm "$claude_md"
  ln -s CLAUDE.md "$claude_md"
  # shellcheck disable=SC2016 # Python receives this program literally.
  run python3 -c '
import subprocess
import sys

try:
    result = subprocess.run(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20)
except subprocess.TimeoutExpired as error:
    if error.stdout:
        sys.stdout.write(error.stdout)
    print("command timed out after 20 seconds", file=sys.stderr)
    raise SystemExit(124)

sys.stdout.write(result.stdout)
raise SystemExit(result.returncode)
' /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  contains "$output" 'skipped'
  contains "$output" 'cannot resolve'
  [ -L "$claude_md" ]
}

@test "CLAUDE.md marked orchestration block is a no-op when current" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  before=$(cksum "$claude_md")
  backups_before=$(find "$HOME/.claude/backups" -type f 2>/dev/null | wc -l | tr -d ' ')
  install_ok
  contains "$output" 'already set'
  [ "$(cksum "$claude_md")" = "$before" ]
  [ "$(find "$HOME/.claude/backups" -type f 2>/dev/null | wc -l | tr -d ' ')" = "$backups_before" ]
}

@test "CLAUDE.md is appended with marked orchestration block when absent" {
  fresh_install
  rm "$HOME/.claude/CLAUDE.md"
  install_ok
  contains "$output" 'added'
  cmp "$CLONE/snippets/CLAUDE.md.snippet" "$HOME/.claude/CLAUDE.md"
}

@test "unmarked CLAUDE.md orchestration sections are left unchanged with a marker hint" {
  fresh_install
  claude_md="$HOME/.claude/CLAUDE.md"
  printf 'personal instructions\n\n## Agent orchestration: architect, never worker\nold text\n' >"$claude_md"
  before=$(cksum "$claude_md")
  install_ok
  contains "$output" 'wrap once'
  contains "$output" 'orchestration:start'
  contains "$output" 'orchestration:end'
  [ "$(cksum "$claude_md")" = "$before" ]
}

@test "documented context watcher command emits a JSON recycle alert" {
  fresh_install
  mkdir -p "$HOME/.claude/context-usage"
  printf '%s\n' '{"result":{"agents":[{"agent":"claude","name":"Architect::fixture","pane_id":"pane-1","agent_session":{"value":"session-1"}}]}}' >"$TMP/herdr-agents.json"
  printf '1\n' >"$HOME/.claude/context-usage/session-1.tokens"
  run env HERDR_AGENTS="$TMP/herdr-agents.json" HERDR_BIN_PATH="$BIN/herdr" HERDR_ENV=1 ORCH_RECYCLE_TOKENS=1 "$HOME/.claude/coordinator/watchers/context-watch.sh" --state-dir "$TMP/context-state"
  [ "$status" -eq 0 ]
  contains "$output" 'RECYCLE pane-1 Architect::fixture'
}

@test "watcher service key is off by default" {
  fresh_install
  [ ! -e "$HOME/.claude/coordinator/watchers/service/macos/orch.coord-watcher-context.plist" ]
  [ ! -e "$HOME/.claude/coordinator/watchers/service/linux/orch.coord-watcher-context.service" ]
}

@test "second run preserves all managed links" {
  fresh_install
  before=$(find "$HOME/.claude" -type l -exec sh -c 'printf "%s -> %s\\n" "$1" "$(readlink "$1")"' sh {} \; | sort)
  sed '/^\[pr_tabs\]/,$d' "$HOME/.claude/orchestration.toml" >"$TMP/old.toml"
  printf '%s\n' '[pr_tabs]' 'org = ""' >>"$TMP/old.toml"
  cp "$TMP/old.toml" "$HOME/.claude/orchestration.toml"
  install_ok
  contains "$output" "Project features are off until you configure missing keys in $HOME/.claude/orchestration.toml: ORCH_PR_TABS_DEFAULT_REPO ORCH_PR_TABS_OPEN_ALL"
  cmp "$TMP/old.toml" "$HOME/.claude/orchestration.toml"
  config_set ORCH_PR_TABS_DEFAULT_REPO "" ORCH_PR_TABS_OPEN_ALL ""
  install_ok
  not_contains "$output" 'Project features are off until you configure missing keys'
  after=$(find "$HOME/.claude" -type l -exec sh -c 'printf "%s -> %s\\n" "$1" "$(readlink "$1")"' sh {} \; | sort)
  [ "$before" = "$after" ]
}

@test "deleted managed links are repaired" {
  fresh_install
  rm "$HOME/.claude/statusline-command.sh" "$HOME/.claude/coordinator/watchers/coord-lib.sh"
  install_ok
  [ -L "$HOME/.claude/statusline-command.sh" ]
  [ -L "$HOME/.claude/coordinator/watchers/coord-lib.sh" ]
}

@test "a real file is backed up before linking" {
  fresh_install
  rm "$HOME/.claude/coordinator/watchers/coord-audit-watch.sh"
  printf old >"$HOME/.claude/coordinator/watchers/coord-audit-watch.sh"
  install_ok
  [ -L "$HOME/.claude/coordinator/watchers/coord-audit-watch.sh" ]
  for backup in "$HOME"/.claude/backups/*/coordinator/watchers/coord-audit-watch.sh; do [ -f "$backup" ]; done
  [ "$(cat "$backup")" = old ]
  [ -z "$(find "$HOME/.claude/coordinator" -name '*.bak*')" ]
}

@test "settings merge keeps an existing hook and status line" {
  fresh_install
  printf '%s\n' '{"statusLine":{"command":"mine"},"hooks":{"Stop":[{"hooks":[{"command":"stop"}]}]}}' >"$HOME/.claude/settings.json"
  install_ok
  jq -e '.statusLine.command == "mine" and .hooks.Stop[0].hooks[0].command == "stop" and ([.hooks.SessionStart[]?.hooks[]?.command] | index("bash ~/.claude/hooks/orch-update-check.sh"))' "$HOME/.claude/settings.json" >/dev/null
}

# bind_rules <claude dir>: the two bind-self.py allow rules for that install, one per line.
bind_rules() {
  printf '%s\n' \
    'Bash(python3 ~/.claude/coordinator/watchers/bind-self.py --output ~/.claude/coordinator/watchers/bindings/*)' \
    "Bash(python3 $1/coordinator/watchers/bind-self.py --output $1/coordinator/watchers/bindings/*)"
}

@test "bind-self allow rules are added to a missing settings file" {
  fresh_install
  rm "$HOME/.claude/settings.json"
  install_ok
  [ "$(jq -c '.permissions.allow' "$HOME/.claude/settings.json")" = "$(bind_rules "$HOME/.claude" | jq -Rsc 'split("\n")[:-1]')" ]
}

@test "bind-self allow rules are added to an empty settings object" {
  fresh_install
  printf '{}\n' >"$HOME/.claude/settings.json"
  install_ok
  [ "$(jq '.permissions.allow | length' "$HOME/.claude/settings.json")" -eq 2 ]
}

@test "bind-self allow rules keep existing allow entries in order" {
  fresh_install
  printf '%s\n' '{"permissions":{"allow":["Bash(ls *)","Read(~/x)"],"deny":["Bash(rm *)"]}}' >"$HOME/.claude/settings.json"
  install_ok
  [ "$(jq -c '.permissions.allow[:2]' "$HOME/.claude/settings.json")" = '["Bash(ls *)","Read(~/x)"]' ]
  [ "$(jq -c '.permissions.deny' "$HOME/.claude/settings.json")" = '["Bash(rm *)"]' ]
  [ "$(jq '.permissions.allow | length' "$HOME/.claude/settings.json")" -eq 4 ]
}

@test "a second run adds no bind-self allow rule" {
  fresh_install
  install_ok
  before=$(cat "$HOME/.claude/settings.json")
  install_ok
  [ "$(cat "$HOME/.claude/settings.json")" = "$before" ]
  [ "$(jq '.permissions.allow | length' "$HOME/.claude/settings.json")" -eq 2 ]
}

@test "hand-added bind-self allow rules are recognized" {
  fresh_install
  jq -n --arg abs "$HOME/.claude" '{permissions: {allow: [
    "Bash(python3 ~/.claude/coordinator/watchers/bind-self.py --output ~/.claude/coordinator/watchers/bindings/*)",
    "Bash(python3 \($abs)/coordinator/watchers/bind-self.py --output \($abs)/coordinator/watchers/bindings/*)"]}}' >"$HOME/.claude/settings.json"
  before=$(jq -S . "$HOME/.claude/settings.json")
  install_ok
  [ "$(jq -S '.permissions' "$HOME/.claude/settings.json")" = "$(jq -S '.permissions' <<<"$before")" ]
  [ "$(jq '.permissions.allow | length' "$HOME/.claude/settings.json")" -eq 2 ]
}

@test "only the missing bind-self allow rule is added" {
  fresh_install
  jq -n '{permissions: {allow: ["Bash(python3 ~/.claude/coordinator/watchers/bind-self.py --output ~/.claude/coordinator/watchers/bindings/*)"]}}' >"$HOME/.claude/settings.json"
  install_ok
  [ "$(jq '.permissions.allow | length' "$HOME/.claude/settings.json")" -eq 2 ]
  [ "$(jq -r '.permissions.allow[1]' "$HOME/.claude/settings.json")" = "$(bind_rules "$HOME/.claude" | tail -n 1)" ]
}

@test "bind-self absolute allow rule follows the installed HOME" {
  other="$TMP/other-home"
  mkdir -p "$other"
  use_home "$other"
  fresh_install
  [ "$(jq -r '.permissions.allow[1]' "$other/.claude/settings.json")" = "$(bind_rules "$other/.claude" | tail -n 1)" ]
  [ "$(jq -r '.permissions.allow[]' "$other/.claude/settings.json" | grep -cF "$TMP/home/")" -eq 0 ]
}

@test "bind-self allow rules and hooks share one settings backup" {
  fresh_install
  printf '%s\n' '{"theme":"dark"}' >"$HOME/.claude/settings.json"
  install_ok
  [ "$(find "$HOME/.claude/backups" -name 'settings.json*' | wc -l)" -eq 1 ]
  [ "$(jq -c . "$(find "$HOME/.claude/backups" -name 'settings.json*')")" = '{"theme":"dark"}' ]
}

@test "bind-self allow rules alone back up settings once" {
  fresh_install
  jq '.permissions = {allow: ["Bash(ls *)"]}' "$HOME/.claude/settings.json" >"$TMP/s.json"
  cp "$TMP/s.json" "$HOME/.claude/settings.json"
  install_ok
  [ "$(find "$HOME/.claude/backups" -name 'settings.json*' | wc -l)" -eq 1 ]
}

@test "settings with an unsupported hooks object get no bind-self allow rules" {
  fresh_install
  printf '%s\n' '{"hooks":{"PreToolUse":{}}}' >"$HOME/.claude/settings.json"
  install_ok
  [ "$(jq -c . "$HOME/.claude/settings.json")" = '{"hooks":{"PreToolUse":{}}}' ]
}

@test "a failed write of the bind-self allow rules leaves settings.json byte for byte intact" {
  fresh_install
  jq '.permissions = {allow: ["Bash(ls *)"], deny: ["Bash(rm *)"]}' "$HOME/.claude/settings.json" >"$TMP/settings.before"
  cp "$TMP/settings.before" "$HOME/.claude/settings.json"
  # The replacement step fails after a partial write: cat (a copy over the live file) and mv (an atomic replace).
  real_cat=$(command -v cat)
  real_mv=$(command -v mv)
  cat >"$BIN/cat" <<EOF
#!/bin/bash
case "\$1" in
  */tmp.*) printf '{"permiss'; exit 1 ;;
esac
exec "$real_cat" "\$@"
EOF
  cat >"$BIN/mv" <<EOF
#!/bin/bash
case "\$1" in
  */settings.json.tmp.*) exit 1 ;;
esac
exec "$real_mv" "\$@"
EOF
  chmod +x "$BIN/cat" "$BIN/mv"
  install_fails
  cmp "$HOME/.claude/settings.json" "$TMP/settings.before"
  [ -z "$(find "$HOME/.claude" -maxdepth 1 -name 'settings.json.tmp.*')" ]
}

@test "bind-self allow rules keep the settings.json mode" {
  fresh_install
  printf '%s\n' '{"permissions":{"allow":["Bash(ls *)"]}}' >"$HOME/.claude/settings.json"
  chmod 640 "$HOME/.claude/settings.json"
  install_ok
  mode=$(python3 -c 'import os, sys; print(format(os.stat(sys.argv[1]).st_mode & 0o777, "o"))' "$HOME/.claude/settings.json")
  [ "$mode" = 640 ]
  [ "$(jq '.permissions.allow | length' "$HOME/.claude/settings.json")" -eq 3 ]
}

@test "bind-self allow rules are written through a symlinked settings.json" {
  fresh_install
  printf '%s\n' '{"permissions":{"allow":["Bash(ls *)"]}}' >"$TMP/real-settings.json"
  rm "$HOME/.claude/settings.json"
  ln -s "$TMP/real-settings.json" "$HOME/.claude/settings.json"
  install_ok
  [ -L "$HOME/.claude/settings.json" ]
  [ "$(jq '.permissions.allow | length' "$TMP/real-settings.json")" -eq 3 ]
}

@test "settings with a non-list permissions.allow are left alone" {
  fresh_install
  printf '%s\n' '{"permissions":{"allow":"nope"}}' >"$HOME/.claude/settings.json"
  install_ok
  [ "$(jq -c '.permissions' "$HOME/.claude/settings.json")" = '{"allow":"nope"}' ]
}

@test "Bash guard matcher variants retain custom config" {
  fresh_install
  custom="$TMP/selected-orchestration.toml"
  printf '%s\n' '[pr]' 'author = "me"' >"$custom"
  managed="ORCH_CONFIG=$custom python3 ~/.claude/hooks/guard-bash.py"
  legacy='python3 ~/.claude/hooks/guard-bash.py'
  while IFS='|' read -r name matcher hook preserve expected; do
    printf 'case %s\n' "$name"
    jq -n --arg matcher "$matcher" --arg hook "$hook" '{hooks: {PreToolUse: [{matcher: $matcher, hooks: [{type: "command", command: $hook}]}]}}' >"$HOME/.claude/settings.json"
    run env ORCH_CONFIG="$custom" /bin/bash "$CLONE/install.sh"
    [ "$status" -eq 0 ]
    run jq -e --arg managed "$managed" --arg hook "$hook" --arg preserve "$preserve" --argjson expected "$expected" '([.hooks.PreToolUse[]?.hooks[]?.command] | map(select(. == $managed)) | length) == $expected and (if $preserve == "yes" then ([.hooks.PreToolUse[]?.hooks[]?.command] | map(select(. == $hook)) | length) >= 1 else ([.hooks.PreToolUse[]?.hooks[]?.command] | map(select(. == $hook)) | length) == 0 end)' "$HOME/.claude/settings.json"
    [ "$status" -eq 0 ]
  done <<CASES
Bash legacy|Bash|$legacy|no|1
regex legacy|^Bash$|$legacy|no|1
mixed legacy|Read,Bash|$legacy|no|1
Read legacy|Read|$legacy|yes|1
Bash managed|Bash|$managed|yes|1
Read managed|Read|$managed|yes|2
CASES
}

@test "dirty clone is refused" {
  fresh_install
  printf dirty >>"$CLONE/README.md"
  install_fails
  contains "$output" 'REFUSED:'
}

@test "unpushed clone commit is refused" {
  fresh_install
  git -C "$CLONE" -c user.email=test@example.invalid -c user.name=test commit --allow-empty -qm local
  install_fails
  contains "$output" 'commits not on origin'
}

@test "failed one-liner download installs nothing and shows the curl error" {
  CURL_RAW_FAIL=1; export CURL_RAW_FAIL
  run /bin/bash -c "$ONE_LINER" _
  [ "$status" -ne 0 ]
  contains "$output" 'curl: (22)'
  [ ! -e "$HOME/.claude/ai-agent-orchestration" ]
}

@test "empty one-liner download installs nothing" {
  CURL_RAW_EMPTY=1; export CURL_RAW_EMPTY
  run /bin/bash -c "$ONE_LINER" _
  [ "$status" -ne 0 ]
  contains "$output" 'empty download'
  [ ! -e "$HOME/.claude/ai-agent-orchestration" ]
}

@test "a script read from a /dev/fd path bootstraps the clone and passes arguments on" {
  run /bin/bash -c 'cd "$1"; shift; exec /bin/bash <(cat "$INSTALL_SOURCE/install.sh") "$@"' _ "$TMP"
  [ "$status" -eq 0 ]
  contains "$output" 'cloned'
  contains "$output" 'env step'
  [ -L "$HOME/.claude/skills/architect" ]
}

@test "installing without gh clones over https and keeps going" {
  mv "$BIN/gh" "$BIN/gh.off"
  run env -u ORCH_REPO_URL /bin/bash -c "$ONE_LINER" _
  [ "$status" -eq 0 ]
  contains "$output" 'cloned'
  [ -L "$HOME/.claude/skills/architect" ]
}

@test "no --env and no ORCH_ENV sets up herdr without a prompt" {
  fresh_install
  run env -u ORCH_ENV /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  contains "$(cat "$HERDR_CALLS")" 'plugin link'
  not_contains "$output" 'Environment to set up'
}

@test "--env none and --no-env are refused" {
  fresh_install
  run env -u ORCH_ENV /bin/bash "$CLONE/install.sh" --env none
  [ "$status" -ne 0 ]
  contains "$output" 'unknown env: none'
  run env -u ORCH_ENV /bin/bash "$CLONE/install.sh" --no-env
  [ "$status" -ne 0 ]
  contains "$output" 'unknown option: --no-env'
}

@test "ORCH_ENV=none in the config skips the env step" {
  fresh_install
  : >"$HERDR_CALLS"
  config_set ORCH_ENV none
  run env -u ORCH_ENV /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  contains "$output" 'env step'
  not_contains "$(cat "$HERDR_CALLS")" 'plugin link'
}

@test "--env wins over ORCH_ENV=none" {
  fresh_install
  run env ORCH_ENV=none /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  contains "$(cat "$HERDR_CALLS")" 'plugin link'
}

@test "script header one-liner installs" {
  header=$(sed -n 's/^#   //p' "$ROOT/install.sh")
  run /bin/bash -c "$header" _
  [ "$status" -eq 0 ]
  [ -L "$HOME/.claude/skills/architect" ]
}

@test "herdr fresh install links the pinned Radar checkout" {
  fresh_install
  install_ok --env herdr
  RADAR="$HOME/.claude/herdr-radar"
  [ "$(git -C "$RADAR" rev-parse HEAD)" = "$RADAR_PIN" ]
  contains "$(cat "$HERDR_CALLS")" 'plugin link'
}

@test "herdr already-linked Radar starts without linking or install" {
  fresh_install
  install_ok --env herdr
  : >"$HERDR_CALLS"
  install_ok --env herdr
  not_contains "$(cat "$HERDR_CALLS")" 'plugin link'
  not_contains "$(cat "$HERDR_CALLS")" 'install'
  not_contains "$(cat "$HERDR_CALLS")" 'state-stop'
  contains "$output" 'already installed (0.9.1'
  contains "$(cat "$HERDR_CALLS")" 'state-start'
}

@test "herdr off-pin Radar returns to pin and restarts" {
  fresh_install
  install_ok --env herdr
  RADAR="$HOME/.claude/herdr-radar"
  git -C "$RADAR" checkout -q main
  : >"$HERDR_CALLS"
  install_ok --env herdr
  contains "$(cat "$HERDR_CALLS")" 'state-stop'
  contains "$(cat "$HERDR_CALLS")" 'state-start'
  [ "$(git -C "$RADAR" rev-parse HEAD)" = "$RADAR_PIN" ]
}

@test "dirty or invalid Radar checkout is refused before runtime installation" {
  fresh_install
  install_ok --env herdr
  radar="$HOME/.claude/herdr-radar"
  printf dirty >>"$radar/README.md"
  mkdir "$TMP/not-a-clone"
  hide_herdr
  mv "$BIN/mise" "$BIN/mise.off"
  for path in "$radar" "$TMP/not-a-clone"; do
    : >"$CURL_CALLS"
    : >"$MISE_CALLS"
    : >"$HERDR_CALLS"
    run env ORCH_RADAR_PATH="$path" /bin/bash "$CLONE/install.sh" --env herdr
    [ "$status" -ne 0 ]
    case "$path" in
      "$radar") contains "$output" 'Herdr Radar checkout' ;;
      *) contains "$output" 'not a git clone' ;;
    esac
    [ ! -s "$CURL_CALLS" ]
    [ ! -s "$MISE_CALLS" ]
    [ ! -s "$HERDR_CALLS" ]
    [ ! -e "$HOME/.local/bin/herdr" ]
    [ ! -e "$HOME/.local/bin/mise" ]
  done
}

@test "missing herdr is installed from its official script" {
  fresh_install
  hide_herdr
  install_ok --env herdr
  contains "$(cat "$CURL_CALLS")" https://herdr.dev/install.sh
  [ -x "$HOME/.local/bin/herdr" ]
  contains "$output" 'installed (0.9.1)'
  not_contains "$output" 'already installed'
}

@test "watcher services use platform settings and migrate a stopped legacy service" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE '1' ORCH_HERDR_BIN '/bin/echo' ORCH_HERDR_SOCKET "\$HOME/socket&name"
  install_ok
  case "$(uname -s)" in
    Darwin)
      [ -f "$HOME/.claude/coordinator/watchers/service/macos/orch.coord-watcher-context.plist" ]
      [ ! -e "$HOME/Library/LaunchAgents/orch.coord-watcher-context.plist" ]
      contains "$(cat "$HOME/.claude/coordinator/watchers/service/macos/orch.coord-watcher-context.plist")" "<string>$HOME/socket&amp;name</string>"
      ;;
    Linux)
      CONTEXT_UNIT="$HOME/.claude/coordinator/watchers/service/linux/orch.coord-watcher-context.service"
      INBOX_UNIT="$HOME/.claude/coordinator/watchers/service/linux/orch.coord-watcher-inbox-context.service"
      [ -f "$CONTEXT_UNIT" ]
      contains "$(cat "$CONTEXT_UNIT")" 'Environment=HERDR_ENV=1'
      contains "$(cat "$INBOX_UNIT")" 'Environment=HERDR_ENV=1'
      contains "$(cat "$CONTEXT_UNIT")" "Environment=\"HERDR_SOCKET_PATH=$HOME/socket&name\""
      ;;
  esac
  rm "$HOME/.claude/orchestration.toml"
  rm -f "$HOME/.claude/coordinator/watchers/service/macos/orch.coord-watcher-context.plist" \
    "$HOME/.claude/coordinator/watchers/service/macos/orch.coord-watcher-inbox-context.plist"
  # shellcheck disable=SC2016 # The old file is sourced, so $HOME expands then.
  printf '%s\n' 'ORCH_V2_PR_SCOPE="org:example"' 'ORCH_V2_WORKER_MODEL="worker"' 'ORCH_V2_PRIMARY_REPO="owner/repo"' 'ORCH_HERDR_BIN="/bin/echo"' 'ORCH_HERDR_SOCKET="$HOME/socket&name"' >"$HOME/.claude/orchestration.env"
  mkdir -p "$HOME/.claude/coordinator/v2/bindings" "$HOME/.claude/coordinator/v2/prod-context"
  printf binding >"$HOME/.claude/coordinator/v2/bindings/coordinator.json"
  printf '{"before":{},"events":[]}' >"$HOME/.claude/coordinator/v2/prod-context/context.json"
  printf copied >"$HOME/.claude/coordinator/v2/prod-context/legacy.json"
  printf '{"context":{"seq":50}}' >"$HOME/.claude/coordinator/v2/prod-context/coordinator-deliver.cursor"
  cat >"$BIN/uname" <<'EOF'
#!/usr/bin/env bash
echo Darwin
EOF
  chmod +x "$BIN/uname"
  export LAUNCHCTL_CALLS="$TMP/launchctl.calls"
  run env LAUNCHCTL_CONTEXT_ONLY=1 PATH="$BATS_SERVICE_STUB_BIN:$BIN:/usr/bin:/bin:/usr/sbin:/sbin" /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.claude/coordinator/watchers/service/macos/orch.coord-watcher-context.plist" ]
  contains "$output" 'renamed ORCH_V2_PR_SCOPE to ORCH_WATCHERS_PR_SCOPE'
  contains "$output" "moved           $HOME/.claude/orchestration.env -> $HOME/.claude/orchestration.env.bak"
  [ "$(config_get ORCH_WATCHERS_PR_SCOPE)" = org:example ]
  contains "$output" 'set ORCH_WATCHERS_SERVICE=1 and rerun install.sh'

  config_set ORCH_WATCHERS_SERVICE '1'
  run env LAUNCHCTL_CONTEXT_ONLY=1 PATH="$BATS_SERVICE_STUB_BIN:$BIN:/usr/bin:/bin:/usr/sbin:/sbin" /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  [ -f "$HOME/.claude/coordinator/watchers/service/macos/orch.coord-watcher-context.plist" ]
  contains "$output" "$HOME/.claude/coordinator/watchers/service/macos/orch.coord-watcher-context.plist"
  contains "$output" 'stay broken until these commands run'
  case "$output" in *"launchctl bootout gui/$(id -u)/com.combinaut.coord-context-v2"*"install.sh"*"launchctl bootstrap gui/$(id -u) "*"orch.coord-watcher-context.plist"*) ;; *) false ;; esac
  not_contains "$output" 'com.combinaut.coord-inbox-v2-context'
  [ ! -e "$HOME/.claude/coordinator/watchers/bindings/coordinator.json" ]
  [ -f "$HOME/.claude/coordinator/v2/bindings/coordinator.json" ]
  run grep -Eq 'bootout|bootstrap' "$LAUNCHCTL_CALLS"
  [ "$status" -ne 0 ]

  printf new >"$HOME/.claude/coordinator/watchers/bindings/coordinator.json"
  printf '{"before":{},"events":[{"seq":1,"line":"RECYCLE pane-9"}]}' >"$HOME/.claude/coordinator/watchers/prod-context/context.json"
  : >"$LAUNCHCTL_CALLS"
  run env LAUNCHCTL_NOT_LOADED=1 PATH="$BATS_SERVICE_STUB_BIN:$BIN:/usr/bin:/bin:/usr/sbin:/sbin" /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 1 ]
  [ "$(cat "$HOME/.claude/coordinator/watchers/bindings/coordinator.json")" = new ]
  [ "$(cat "$HOME/.claude/coordinator/watchers/prod-context/legacy.json")" = copied ]
  [ ! -e "$HOME/.claude/coordinator/watchers/prod-context/coordinator-deliver.cursor" ]
  [ -f "$HOME/.claude/coordinator/v2/bindings/coordinator.json" ]
  [ "$(cat "$HOME/.claude/coordinator/v2/prod-context/legacy.json")" = copied ]
  not_contains "$output" 'MIGRATE WATCHER:'
  contains "$output" 'copied state'

  run env LAUNCHCTL_NOT_LOADED=1 PATH="$BATS_SERVICE_STUB_BIN:$BIN:/usr/bin:/bin:/usr/sbin:/sbin" /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 1 ]
  not_contains "$output" 'copied state'
}

@test "old Herdr in the managed bin is upgraded" {
  fresh_install
  hide_herdr
  mkdir -p "$HOME/.local/bin"
  printf '#!/bin/sh\necho "herdr 0.8.0"\n' >"$HOME/.local/bin/herdr"
  chmod +x "$HOME/.local/bin/herdr"
  install_ok --env herdr
  contains "$(cat "$CURL_CALLS")" https://herdr.dev/install.sh
  contains "$output" 'upgraded (0.9.1)'
}

@test "a working Herdr on PATH is used even with an old managed leftover" {
  fresh_install
  mkdir -p "$HOME/.local/bin"
  # shellcheck disable=SC2016 # Expanded when the leftover runs.
  printf '#!/bin/sh\ntouch "$TMP/leftover.used"\necho "herdr 0.8.0"\n' >"$HOME/.local/bin/herdr"
  chmod +x "$HOME/.local/bin/herdr"
  run env HERDR_STUB_DIRECT=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  contains "$output" 'already installed (0.9.1'
  [ ! -e "$TMP/leftover.used" ]
  [ ! -e "$CURL_CALLS" ]
  contains "$(cat "$HERDR_CALLS")" 'plugin link'
  contains "$(cat "$HERDR_CALLS")" 'state-start'
}

@test "a shell function named herdr is not used as the Herdr executable" {
  fresh_install
  run env 'BASH_FUNC_herdr%%=() { echo "herdr 0.9.1"; }' /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  contains "$(cat "$HERDR_CALLS")" 'plugin link'
  contains "$(cat "$HERDR_CALLS")" 'state-start'
}

@test "foreign Herdr with an unreadable version is refused without a download" {
  fresh_install
  printf '#!/bin/sh\necho "herdr dev-build"\n' >"$BIN/herdr"
  run /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'reports no readable version'
  [ ! -e "$CURL_CALLS" ]
  [ ! -e "$HOME/.local/bin/herdr" ]
}

@test "old managed Herdr found through a relative PATH entry is upgraded" {
  fresh_install
  hide_herdr
  mkdir -p "$HOME/.local/bin"
  printf '#!/bin/sh\necho "herdr 0.8.0"\n' >"$HOME/.local/bin/herdr"
  chmod +x "$HOME/.local/bin/herdr"
  cd "$HOME"
  run env PATH=".local/bin:$PATH" /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  contains "$output" 'upgraded (0.9.1)'
}

@test "old Herdr installed elsewhere is refused without a download" {
  fresh_install
  run env HERDR_VERSION=0.8.0 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'reports 0.8.0; this setup needs 0.9 or newer'
  [ ! -e "$CURL_CALLS" ]
  not_contains "$(cat "$HERDR_CALLS")" 'plugin link'
}

@test "current Herdr does not download an installer" {
  fresh_install
  install_ok --env herdr
  [ ! -e "$CURL_CALLS" ]
}

@test "missing mise is installed from its official script" {
  fresh_install
  mv "$BIN/mise" "$BIN/mise.off"
  install_ok --env herdr
  contains "$(cat "$CURL_CALLS")" https://mise.run
}

@test "Radar installs the pinned Node through mise even with system Node" {
  fresh_install
  install_ok --env herdr
  contains "$(cat "$MISE_CALLS")" 'install node@22.20.0'
  contains "$(cat "$HOME/.claude/herdr-radar-runtime/herdr-plugin.toml")" "$TMP/managed-node/node"
  [ -z "$(git -C "$HOME/.claude/herdr-radar" status --porcelain)" ]
}

@test "mise install failure stops before plugin linking" {
  fresh_install
  run env MISE_FAIL=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  not_contains "$(cat "$HERDR_CALLS")" 'plugin link'
}

@test "foreign Radar plugin is uninstalled and replaced" {
  fresh_install
  printf upstream >"$HERDR_PLUGIN"
  install_ok --env herdr
  contains "$(cat "$HERDR_CALLS")" 'plugin uninstall hhdebb.herdr-radar'
  [ "$(cat "$HERDR_PLUGIN")" = "$(cd "$HOME/.claude" && pwd -P)/herdr-radar-runtime" ]
  contains "$output" 'with combinaut/herdr-radar'
}

@test "foreign Radar origin is replaced by the fork" {
  fresh_install
  install_ok --env herdr
  git -C "$HOME/.claude/herdr-radar" remote set-url origin "$RADAR_ORIGIN"
  install_ok --env herdr
  contains "$output" 'Radar origin'
  [ "$(git -C "$HOME/.claude/herdr-radar" remote get-url origin)" = https://github.com/combinaut/herdr-radar.git ]
}

@test "failed official download never executes an installer" {
  fresh_install
  hide_herdr
  run env CURL_FAIL=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  [ ! -e "$HOME/.local/bin/herdr" ]
  contains "$output" 'cannot download'
}

@test "platform dependency fixes match the selected OS" {
  while IFS='|' read -r adapter expected; do
    run /bin/bash -c '. "$1/lib/platform/$2.sh"; platform_dependency_fix jq' _ "$ROOT" "$adapter"
    [ "$status" -eq 0 ]
    contains "$output" "$expected"
  done <<'CASES'
linux|sudo apt-get install jq
macos|brew install jq
CASES
}



@test "wrong mise Node version stops before plugin linking" {
  fresh_install
  run env NODE_VERSION=18.0.0 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'mise Node version does not match'
  not_contains "$(cat "$HERDR_CALLS")" 'plugin link'
}

@test "empty official download is refused" {
  fresh_install
  hide_herdr
  run env CURL_EMPTY=1 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'empty installer'
}

@test "runtime manifest is repaired and daemon restarted" {
  fresh_install
  install_ok --env herdr
  printf broken >"$HOME/.claude/herdr-radar-runtime/herdr-plugin.toml"
  : >"$HERDR_CALLS"
  install_ok --env herdr
  contains "$(cat "$HERDR_CALLS")" 'state-stop'
  contains "$(cat "$HOME/.claude/herdr-radar-runtime/herdr-plugin.toml")" "$TMP/managed-node/node"
}

@test "Herdr still old after install is refused" {
  fresh_install
  hide_herdr
  run env HERDR_INSTALLED_VERSION=0.8.0 /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -ne 0 ]
  contains "$output" 'Herdr install failed'
}

@test "missing mise Node executable is refused" {
  fresh_install
  rm "$TMP/managed-node/node"
  install_fails --env herdr
  contains "$output" 'mise did not provide Node'
}

@test "missing env prerequisites print platform fixes" {
  while IFS='|' read -r adapter blocked expected; do
    run /bin/bash -c '
      . "$1/lib/platform/$2.sh"
      . "$1/env/herdr/install.sh"
      blocked=$3
      command() { if [ "${2:-}" = "$blocked" ]; then return 1; fi; builtin command "$@"; }
      die() { echo "$*"; exit 1; }
      env_check
    ' _ "$ROOT" "$adapter" "$blocked"
    [ "$status" -ne 0 ]
    contains "$output" "$expected"
  done <<'CASES'
linux|curl|sudo apt-get install curl
linux|python3|sudo apt-get install python3
macos|curl|brew install curl
macos|python3|brew install python3
CASES
}



@test "bootstrap ignores platform adapters in the caller directory" {
  mkdir -p "$TMP/lib/platform"
  for adapter in macos linux; do
    printf 'echo unrelated-adapter; exit 77\n' >"$TMP/lib/platform/$adapter.sh"
  done
  fresh_install
  not_contains "$output" unrelated-adapter
  [ -L "$HOME/.claude/skills/architect" ]
}

@test "managed tools are reused without caller PATH changes" {
  fresh_install
  for tool in herdr mise; do
    if [ "$tool" = herdr ]; then hide_herdr; else mv "$BIN/$tool" "$BIN/$tool.off"; fi
    install_ok --env herdr
    : >"$CURL_CALLS"
    install_ok --env herdr
    [ ! -s "$CURL_CALLS" ]
    if [ "$tool" = herdr ]; then show_herdr; else mv "$BIN/$tool.off" "$BIN/$tool"; fi
    rm "$HOME/.local/bin/$tool"
  done
}



@test "managed tools print persistent PATH setup for the next shell" {
  fresh_install
  hide_herdr
  install_ok --env herdr
  # shellcheck disable=SC2016 # The printed command retains shell variables.
  contains "$output" 'export PATH="$HOME/.local/bin:$PATH"'
  contains "$output" 'open a new shell'
}

@test "managed bin first on caller PATH needs no profile hint" {
  fresh_install
  hide_herdr
  install_ok --env herdr
  # Keep the service stubs first while exercising the managed directory first.
  use_managed_bin_fixture
  run /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  not_contains "$output" 'open a new shell'
}

@test "replacing foreign plugin restarts its existing daemon" {
  fresh_install
  install_ok --env herdr
  printf foreign >"$HERDR_PLUGIN"
  : >"$HERDR_CALLS"
  install_ok --env herdr
  calls=$(cat "$HERDR_CALLS")
  contains "$calls" 'state-stop'
  case "$calls" in *state-stop*state-start*) ;; *) false ;; esac
}

@test "repairing redirected runtime link restarts the daemon" {
  fresh_install
  install_ok --env herdr
  runtime="$HOME/.claude/herdr-radar-runtime"
  rm "$runtime/README.md"
  printf foreign >"$TMP/foreign-readme"
  ln -s "$TMP/foreign-readme" "$runtime/README.md"
  : >"$HERDR_CALLS"
  install_ok --env herdr
  [ "$(readlink "$runtime/README.md")" = "$(cd "$HOME/.claude/herdr-radar" && pwd -P)/README.md" ]
  calls=$(cat "$HERDR_CALLS")
  case "$calls" in *state-stop*state-start*) ;; *) false ;; esac
}

@test "all manifest sections use the pinned Node interpreter" {
  fresh_install
  install_ok --env herdr
  manifest="$HOME/.claude/herdr-radar-runtime/herdr-plugin.toml"
  [ "$(grep -Fc "command = [\"$TMP/managed-node/node\"" "$manifest")" -eq 5 ]
  not_contains "$(cat "$manifest")" 'command = ["node"'
  for section in actions build panes startup events; do
    contains "$(cat "$manifest")" "[[$section]]"
  done
}

@test "heavy-slot installation prints PATH guidance when the managed bin is absent" {
  fresh_install
  install_ok --env herdr
  contains "$output" 'open a new shell'
}

@test "the installer links the startup helpers into the managed bin" {
  fresh_install
  install_ok --env herdr
  for helper in orch-agents orch-settings; do
    [ -L "$HOME/.local/bin/$helper" ]
    [ "$HOME/.local/bin/$helper" -ef "$CLONE/$helper" ]
  done
}

@test "equivalent fork origins preserve their authentication transport" {
  fresh_install
  install_ok --env herdr
  radar="$HOME/.claude/herdr-radar"
  for origin in git@github.com:combinaut/herdr-radar.git https://github.com/combinaut/herdr-radar ssh://git@github.com/combinaut/herdr-radar.git; do
    git -C "$radar" remote set-url origin "$origin"
    install_ok --env herdr
    [ "$(git -C "$radar" remote get-url origin)" = "$origin" ]
    not_contains "$output" 'Radar origin'
  done
}

@test "plugin warning diagnostics do not trigger replacement or restart" {
  fresh_install
  install_ok --env herdr
  : >"$HERDR_CALLS"
  run env HERDR_PLUGIN_DIAGNOSTIC='; 10 warning(s)' /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  calls=$(cat "$HERDR_CALLS")
  not_contains "$calls" 'plugin uninstall'
  not_contains "$calls" 'plugin link'
  not_contains "$calls" 'state-stop'
  contains "$calls" 'state-start'
}

@test "mise calls ignore untrusted configuration in caller directory" {
  fresh_install
  project="$TMP/untrusted-project"
  mkdir "$project"
  printf '[tools]\nnode = "24"\n' >"$project/mise.toml"
  for phase in install which; do
    : >"$MISE_CWDS"
    # shellcheck disable=SC2016 # The child shell receives the arguments.
    run env MISE_UNTRUSTED_COMMAND="$phase" /bin/bash -c 'cd "$1"; exec /bin/bash "$2/install.sh" --env herdr' _ "$project" "$CLONE"
    [ "$status" -eq 0 ]
    [ "$(sort -u "$MISE_CWDS")" = "$(cd "$CLONE/env/herdr" && pwd -P)" ]
  done
}

@test "custom Radar checkout keeps runtime inside Claude directory" {
  fresh_install
  checkout="$TMP/projects/radar"
  run env ORCH_RADAR_PATH="$checkout" /bin/bash "$CLONE/install.sh" --env herdr
  [ "$status" -eq 0 ]
  [ -f "$HOME/.claude/herdr-radar-runtime/herdr-plugin.toml" ]
  [ ! -e "$checkout-runtime" ]
  [ "$(cat "$HERDR_PLUGIN")" = "$(cd "$HOME/.claude/herdr-radar-runtime" && pwd -P)" ]
}

@test "runtime pruning removes dangling links and restarts the daemon" {
  fresh_install
  install_ok --env herdr
  runtime="$HOME/.claude/herdr-radar-runtime"
  ln -s "$TMP/removed-source" "$runtime/removed-entry"
  ln -s "$TMP/removed-source" "$runtime/.removed-entry"
  ln -s "$TMP/removed-source" "$runtime/..removed-entry"
  ln -s "$TMP" "$runtime/.valid-entry"
  printf keep >"$runtime/personal-file"
  : >"$HERDR_CALLS"
  install_ok --env herdr
  [ ! -L "$runtime/removed-entry" ]
  [ ! -L "$runtime/.removed-entry" ]
  [ ! -L "$runtime/..removed-entry" ]
  [ -L "$runtime/.valid-entry" ]
  [ "$(cat "$runtime/personal-file")" = keep ]
  [ -L "$runtime/README.md" ]
  contains "$(cat "$HERDR_CALLS")" state-stop
}

@test "manifest pinning rejects zero Node substitutions" {
  printf 'command = ["python3", "script.py"]\n' >"$TMP/manifest.toml"
  run python3 "$ROOT/env/herdr/pin-runtime.py" "$TMP/manifest.toml" "$TMP/managed-node/node"
  [ "$status" -ne 0 ]
  contains "$output" 'no Node commands'
}

@test "manifest pinning rejects remaining bare Node commands" {
  cp "$ROOT/tests/fixtures/radar-plugin.toml" "$TMP/manifest.toml"
  printf "\n[[startup]]\ncommand = [ 'node', 'extra.js' ]\n" >>"$TMP/manifest.toml"
  run python3 "$ROOT/env/herdr/pin-runtime.py" "$TMP/manifest.toml" "$TMP/managed-node/node"
  [ "$status" -ne 0 ]
  contains "$output" 'unpinned Node command'
}

@test "redirected runtime root is backed up without touching its target" {
  fresh_install
  external="$TMP/external-runtime"
  mkdir "$external"
  printf keep >"$external/herdr-plugin.toml"
  ln -s "$external" "$HOME/.claude/herdr-radar-runtime"
  install_ok --env herdr
  [ ! -L "$HOME/.claude/herdr-radar-runtime" ]
  [ "$(cat "$external/herdr-plugin.toml")" = keep ]
  [ ! -e "$external/README.md" ]
  for backup in "$HOME"/.claude/backups/*/herdr-radar-runtime; do
    [ -L "$backup" ]
    [ "$(readlink "$backup")" = "$external" ]
  done
}

@test "redirected manifest is replaced without touching external targets" {
  fresh_install
  install_ok --env herdr
  manifest="$HOME/.claude/herdr-radar-runtime/herdr-plugin.toml"
  for kind in foreign matching directory; do
    target="$TMP/manifest-target-$kind"
    case "$kind" in
      foreign) printf keep >"$target" ;;
      matching) cp "$manifest" "$target" ;;
      directory) mkdir "$target"; printf keep >"$target/marker" ;;
    esac
    if [ "$kind" != directory ]; then cp "$target" "$TMP/before"; fi
    rm "$manifest"
    ln -s "$target" "$manifest"
    : >"$HERDR_CALLS"
    install_ok --env herdr
    [ ! -L "$manifest" ]
    [ -f "$manifest" ]
    if [ "$kind" = directory ]; then
      [ "$(cat "$target/marker")" = keep ]
      [ "$(find "$target" -type f | wc -l | tr -d ' ')" -eq 1 ]
    else
      cmp -s "$TMP/before" "$target"
    fi
    contains "$(cat "$HERDR_CALLS")" state-stop
  done
}

@test "manifest substitution ignores comments and unrelated keys" {
  printf '# command = ["node", "comment.js"]\nprecommand = ["node", "unrelated.js"]\n' >"$TMP/noncommands.toml"
  run python3 "$ROOT/env/herdr/pin-runtime.py" "$TMP/noncommands.toml" "$TMP/managed-node/node"
  [ "$status" -ne 0 ]
  contains "$output" 'no Node commands'
  cat "$ROOT/tests/fixtures/radar-plugin.toml" "$TMP/noncommands.toml" >"$TMP/manifest.toml"
  run python3 "$ROOT/env/herdr/pin-runtime.py" "$TMP/manifest.toml" "$TMP/managed-node/node"
  [ "$status" -eq 0 ]
  contains "$output" '# command = ["node", "comment.js"]'
  contains "$output" 'precommand = ["node", "unrelated.js"]'
}

@test "manifest rendering failure removes its private temporary file" {
  mkdir "$TMP/invalid-radar"
  printf 'command = ["python3", "script.py"]\n' >"$TMP/invalid-radar/herdr-plugin.toml"
  # shellcheck disable=SC2016 # The child shell receives the arguments.
  run /bin/bash -eu -c '
    . "$1/env/herdr/install.sh"
    ENV_DIR="$1/env/herdr" CLAUDE_DIR="$HOME/.claude" RADAR_NODE="$3" moved=0
    say() { :; }
    die() { echo "$*"; exit 1; }
    link_entry() { ln -s "$1" "$2"; }
    prepare_plugin "$2"
  ' _ "$ROOT" "$TMP/invalid-radar" "$TMP/managed-node/node"
  [ "$status" -ne 0 ]
  contains "$output" 'cannot pin the Radar runtime manifest'
  [ -z "$(find "$HOME/.claude/herdr-radar-runtime" -name '.herdr-plugin.*' -type f)" ]
  [ ! -e "$HOME/.claude/herdr-radar-runtime/herdr-plugin.toml" ]
}

@test "manifest publication failure removes its private temporary file" {
  fresh_install
  install_ok --env herdr
  runtime="$HOME/.claude/herdr-radar-runtime"
  rm "$runtime/herdr-plugin.toml"
  mkdir "$runtime/herdr-plugin.toml"
  printf keep >"$runtime/herdr-plugin.toml/marker"
  install_fails --env herdr
  contains "$output" 'cannot replace the Radar runtime manifest'
  [ "$(cat "$runtime/herdr-plugin.toml/marker")" = keep ]
  [ -z "$(find "$runtime" -name '.herdr-plugin.*' -type f)" ]
}

@test "host services render with shared inbox and print lifecycle commands" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE '1' ORCH_HERDR_BIN '/bin/echo' ORCH_HERDR_SOCKET '/tmp/socket'
  install_ok
  case "$(uname -s)" in
    Darwin) adapter=macos; suffix=plist; start='launchctl bootstrap'; stop='launchctl bootout' ;;
    Linux) adapter=linux; suffix=service; start='systemctl --user enable --now'; stop='systemctl --user disable --now' ;;
  esac
  services="$HOME/.claude/coordinator/watchers/service/$adapter"
  host="$services/orch.coord-watcher-host.$suffix"
  [ -f "$host" ]
  contains "$(cat "$host")" 'coord-host-watch.sh'
  contains "$(cat "$host")" '/prod-context'
  contains "$(cat "$services/orch.coord-watcher-inbox-context.$suffix")" 'host'
  not_contains "$(cat "$host")" '@ORCH_'
  contains "$output" "$start"
  contains "$output" "$stop"
}

@test "removed monitor link is cleaned through the installer manifest" {
  fresh_install
  old="$HOME/.claude/coordinator/monitors/retired-monitor.sh"
  mkdir -p "$(dirname "$old")"
  ln -s "$(cd "$CLONE" && pwd -P)/coordinator/monitors/retired-monitor.sh" "$old"
  printf '%s\n' "$old" >>"$HOME/.claude/.orch-links"
  install_ok
  [ ! -L "$old" ]
  contains "$output" "removed stale"
}

@test "coordinator documentation covers host alerts and memory guard response" {
  for doc in "$ROOT/docs/watchers.md" "$ROOT/skills/coordinate/SKILL.md"; do
    contains "$(cat "$doc")" 'HOST READFAIL'
    contains "$(cat "$doc")" 'HOST RECOVERED'
    contains "$(cat "$doc")" 'host HEARTBEAT STALE'
    contains "$(cat "$doc")" 'stop heavy jobs'
    contains "$(cat "$doc")" 'tell architects'
  done
}

@test "hog services render at five seconds and share the inbox" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE '1' ORCH_HERDR_BIN '/bin/echo' ORCH_HERDR_SOCKET '/tmp/socket'
  install_ok
  case "$(uname -s)" in
    Darwin) adapter=macos; suffix=plist; start='launchctl bootstrap'; stop='launchctl bootout' ;;
    Linux) adapter=linux; suffix=service; start='systemctl --user enable --now'; stop='systemctl --user disable --now' ;;
  esac
  services="$HOME/.claude/coordinator/watchers/service/$adapter"
  hog="$services/orch.coord-watcher-hog.$suffix"
  [ -f "$hog" ]
  contains "$(cat "$hog")" 'coord-hog-watch.sh'
  contains "$(cat "$hog")" '/prod-context'
  case "$adapter" in
    macos) contains "$(cat "$hog")" '<string>5</string>' ;;
    linux) contains "$(cat "$hog")" '"--interval" "5"' ;;
  esac
  contains "$(cat "$services/orch.coord-watcher-inbox-context.$suffix")" 'hog'
  not_contains "$(cat "$hog")" '@ORCH_'
  contains "$output" "$start"
  contains "$output" "$stop"
}

@test "balance services render at sixty seconds and share the inbox" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE '1' ORCH_HERDR_BIN '/bin/echo' ORCH_HERDR_SOCKET '/tmp/socket'
  install_ok
  case "$(uname -s)" in
    Darwin) adapter=macos; suffix=plist; start='launchctl bootstrap'; stop='launchctl bootout' ;;
    Linux) adapter=linux; suffix=service; start='systemctl --user enable --now'; stop='systemctl --user disable --now' ;;
  esac
  services="$HOME/.claude/coordinator/watchers/service/$adapter"
  balance="$services/orch.coord-watcher-balance.$suffix"
  [ -f "$balance" ]
  contains "$(cat "$balance")" 'coord-balance-watch.sh'
  contains "$(cat "$balance")" '/prod-context'
  case "$adapter" in
    macos) contains "$(cat "$balance")" '<string>60</string>' ;;
    linux) contains "$(cat "$balance")" '"--interval" "60"' ;;
  esac
  contains "$(cat "$services/orch.coord-watcher-inbox-context.$suffix")" 'balance'
  not_contains "$(cat "$balance")" '@ORCH_'
  contains "$output" "$start"
  contains "$output" "$stop"
}

@test "workers services render at sixty seconds and share the inbox" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE '1' ORCH_HERDR_BIN '/bin/echo' ORCH_HERDR_SOCKET '/tmp/socket'
  install_ok
  case "$(uname -s)" in
    Darwin) adapter=macos; suffix=plist; start='launchctl bootstrap'; stop='launchctl bootout' ;;
    Linux) adapter=linux; suffix=service; start='systemctl --user enable --now'; stop='systemctl --user disable --now' ;;
  esac
  services="$HOME/.claude/coordinator/watchers/service/$adapter"
  workers="$services/orch.coord-watcher-workers.$suffix"
  [ -f "$workers" ]
  contains "$(cat "$workers")" 'coord-worker-watch.sh'
  contains "$(cat "$workers")" '/prod-context'
  case "$adapter" in
    macos) contains "$(cat "$workers")" '<string>60</string>' ;;
    linux) contains "$(cat "$workers")" '"--interval" "60"' ;;
  esac
  contains "$(cat "$services/orch.coord-watcher-inbox-context.$suffix")" 'workers'
  not_contains "$(cat "$workers")" '@ORCH_'
  contains "$output" "$start"
  contains "$output" "$stop"
}

@test "install drops the settings the open work replaced, in one line" {
  fresh_install
  cfg="$HOME/.claude/orchestration.toml"
  python3 - "$cfg" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
for header, line in (('[pr]', 'repos = [\n  "acme/web",\n]'), ('[audit]', 'git_watch_repos = ["/tmp/x"]'),
                     ('[focus]', '# Architect names left out of focus reports\nskip_architects = ["A"]')):
    text = text.replace(header + '\n', header + '\n' + line + '\n', 1) if header + '\n' in text else text + f'\n{header}\n{line}\n'
path.write_text(text)
PY
  install_ok
  [ "$(printf '%s\n' "$output" | grep -c '^dropped ')" = 1 ]
  contains "$output" "dropped         $cfg: pr.repos audit.git_watch_repos focus.skip_architects (repos and architects come from the open work)"
  not_contains "$(cat "$cfg")" 'acme/web'
  not_contains "$(cat "$cfg")" 'git_watch_repos'
  not_contains "$(cat "$cfg")" 'skip_architects'
  not_contains "$(cat "$cfg")" 'Architect names left out'
  contains "$(cat "$cfg")" 'author = '
  install_ok
  not_contains "$output" 'dropped '
}

@test "a key install cannot drop is named and install goes on" {
  fresh_install
  cfg="$HOME/.claude/orchestration.toml"
  printf '%s\n' 'pr = { repos = ["acme/web"], author = "me" }' >"$cfg"
  install_ok
  contains "$output" "cannot drop pr.repos in place; edit it by hand"
  [ "$(cat "$cfg")" = 'pr = { repos = ["acme/web"], author = "me" }' ]
  run grep 'fill in.*WARNING' <<<"$output"
  [ "$status" -eq 1 ]
}

@test "PR watchers are not registered until their settings are filled" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE '1' ORCH_HERDR_BIN '/bin/echo' ORCH_HERDR_SOCKET '/tmp/socket'
  install_ok
  contains "$output" 'orch.coord-watcher-audit watcher (set ORCH_PR_AUTHOR in'
  contains "$output" 'orch.coord-watcher-pulls watcher (set ORCH_PR_AUTHOR in'
  [ -z "$(find "$HOME/.claude/coordinator/watchers/service" \( -name 'orch.coord-watcher-audit.*' -o -name 'orch.coord-watcher-pulls.*' \) ! -name '*.template')" ]
  [ -n "$(find "$HOME/.claude/coordinator/watchers/service" -name 'orch.coord-watcher-host.*' ! -name '*.template')" ]
  run env ORCH_PR_AUTHOR=me /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  contains "$output" 'orch.coord-watcher-pulls watcher (set ORCH_PR_AUTHOR in'
}

@test "audit services render at thirty minutes and share the inbox" {
  fresh_install
  config_set ORCH_WATCHERS_SERVICE '1' ORCH_HERDR_BIN '/bin/echo' ORCH_HERDR_SOCKET '/tmp/socket' ORCH_PR_AUTHOR 'me'
  install_ok
  case "$(uname -s)" in
    Darwin) adapter=macos; suffix=plist ;;
    Linux) adapter=linux; suffix=service ;;
  esac
  services="$HOME/.claude/coordinator/watchers/service/$adapter"
  audit="$services/orch.coord-watcher-audit.$suffix"
  [ -f "$audit" ]
  contains "$(cat "$audit")" 'coord-audit-watch.sh'
  contains "$(cat "$audit")" '/prod-context'
  case "$adapter" in
    macos) contains "$(cat "$audit")" '<string>1800</string>' ;;
    linux) contains "$(cat "$audit")" '"--interval" "1800"' ;;
  esac
  contains "$(cat "$services/orch.coord-watcher-inbox-context.$suffix")" 'audit'
  not_contains "$(cat "$audit")" '@ORCH_'
  contains "$output" 'coord-watcher-audit'
}

@test "installed audit entrypoint exports own-PR config to the collector" {
  fresh_install
  config_set ORCH_PR_AUTHOR 'author'
  git init -q "$TMP/product"
  git -C "$TMP/product" remote add origin git@github.com:owner/product.git
  python3 -c 'import json,sys; pr=json.load(open(sys.argv[1])); pr["mergeable"]="CONFLICTING"; print(json.dumps({"data":{"search":{"nodes":[pr],"pageInfo":{"hasNextPage":False}}}}))' "$ROOT/coordinator/watchers/fixtures/audit-pr.json" >"$TMP/audit-gh.json"
  cat >"$BIN/gh" <<'EOF'
#!/bin/bash
case "$*" in *"/contents/"*) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;; esac
printf '%s\n' "$*" >"$AUDIT_ARGS"
cat "$AUDIT_FIXTURE"
EOF
  cat >"$BIN/herdr" <<EOF
#!/bin/bash
case "\$1" in
  workspace) printf '{"result":{"workspaces":[{"workspace_id":"w1","label":"x","worktree":{"checkout_path":"$TMP/product","repo_root":"$TMP/product"}}]}}' ;;
  *) printf '{"result":{"panes":[]}}' ;;
esac
EOF
  run env ORCH_ENV=herdr ORCH_HERDR_BIN="$BIN/herdr" AUDIT_ARGS="$TMP/audit-args" AUDIT_FIXTURE="$TMP/audit-gh.json" "$HOME/.claude/coordinator/watchers/coord-audit-watch.sh" --state-dir "$TMP/audit-state"
  [ "$status" -eq 0 ]
  contains "$output" 'AUDIT BASELINE github [1 active lines'
  contains "$(cat "$TMP/audit-args")" 'author:author repo:owner/product'
  contains "$(cat "$TMP/audit-state/audit.json")" 'CONFLICT owner/product#7 Fix menu'
  run env ORCH_ENV=herdr ORCH_HERDR_BIN="$BIN/herdr" AUDIT_ARGS="$TMP/audit-args" AUDIT_FIXTURE="$TMP/audit-gh.json" "$HOME/.claude/coordinator/watchers/coord-audit-watch.sh" --state-dir "$TMP/audit-state"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "coord-check runs all eight Coordinator modes once" {
  fresh_install
  cat >"$TMP/python" <<'EOF'
#!/bin/bash
case "$1" in -c | -I) exec python3 "$@" ;; esac
printf '%s\n' "$2" >>"$WATCHER_MODES"
EOF
  chmod +x "$TMP/python"
  run env ORCH_PYTHON3="$TMP/python" WATCHER_MODES="$TMP/modes" "$HOME/.claude/coordinator/watchers/coord-check.sh" --state-dir "$TMP/state"
  [ "$status" -eq 0 ]
  [ "$(cat "$TMP/modes")" = "$(printf '%s\n' context workers hog balance host audit todo answers)" ]
}

@test "coord-check continues after a mode command fails" {
  fresh_install
  cat >"$TMP/python" <<'EOF'
#!/bin/bash
case "$1" in -c | -I) exec python3 "$@" ;; esac
printf '%s\n' "$2" >>"$WATCHER_MODES"
[ "$2" != hog ]
EOF
  chmod +x "$TMP/python"
  run env ORCH_PYTHON3="$TMP/python" WATCHER_MODES="$TMP/modes" "$HOME/.claude/coordinator/watchers/coord-check.sh" --state-dir "$TMP/state"
  [ "$status" -eq 1 ]
  [ "$(cat "$TMP/modes")" = "$(printf '%s\n' context workers hog balance host audit todo answers)" ]
  contains "$output" 'hog check failed'
}

@test "coord-cycle previews every Coordinator mode including answers" {
  fresh_install
  cat >"$TMP/python" <<'EOF'
#!/bin/bash
case "$1" in -c | -I) exec python3 "$@" ;; esac
printf '%s\n' "$*" >>"$WATCHER_ARGS"
EOF
  chmod +x "$TMP/python"
  run env ORCH_PYTHON3="$TMP/python" WATCHER_ARGS="$TMP/args" "$HOME/.claude/coordinator/watchers/coord-cycle.sh" "$TMP/state"
  [ "$status" -eq 0 ]
  grep -F -- '--modes context workers hog balance host audit todo answers' "$TMP/args"
}

@test "unused watcher wrapper is absent and its managed link is removed" {
  fresh_install
  old="$HOME/.claude/coordinator/watchers/coord-run.sh"
  [ ! -L "$old" ]
  ln -s "$(cd "$CLONE" && pwd -P)/coordinator/watchers/coord-run.sh" "$old"
  printf '%s\n' "$old" >>"$HOME/.claude/.orch-links"
  install_ok
  [ ! -L "$old" ]
  contains "$output" 'removed stale'
}

@test "every rendered service carries a custom config and the audit wrapper reads it" {
  fresh_install
  custom="$TMP/custom config & <audit>.toml"
  prepare_custom_service_config "$custom"
  run env ORCH_CONFIG="$custom" /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  prepare_audit_commands
  check_service_config "$custom"
}

@test "relative custom config remains usable from the service working directory" {
  fresh_install
  prepare_custom_service_config "$TMP/relative config.toml"
  run /bin/bash -c 'cd "$1"; ORCH_CONFIG="relative config.toml" /bin/bash "$2"' _ "$TMP" "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  prepare_audit_commands
  check_service_config "$(cd "$TMP" && pwd -P)/relative config.toml"
}

@test "every rendered service carries the default config selected by the installer" {
  fresh_install
  prepare_custom_service_config "$HOME/.claude/orchestration.toml"
  run env -u ORCH_CONFIG /bin/bash "$CLONE/install.sh"
  [ "$status" -eq 0 ]
  prepare_audit_commands
  check_service_config "$HOME/.claude/orchestration.toml"
}

@test "configured watcher settings reach the watcher environment" {
  printf '%s\n' '[legacy]' 'scans_max = 3' 'reads_max = 7' '[heavy_slots]' 'max = 7' >"$BATS_TEST_TMPDIR/orchestration.toml"
  # shellcheck disable=SC2016 # the inner script expands its own variables
  run env -i PATH="$PATH" ORCH_CONFIG="$BATS_TEST_TMPDIR/orchestration.toml" bash -c '. "$1/coordinator/watchers/coord-lib.sh"; orch_load_config; orch_export_config; env' _ "$BATS_TEST_DIRNAME/.."
  [ "$status" -eq 0 ]
  [[ "$output" == *"ORCH_LEGACY_SCANS_MAX=3"* ]]
  [[ "$output" == *"ORCH_LEGACY_READS_MAX=7"* ]]
  [[ "$output" == *"ORCH_HEAVY_SLOTS_MAX=7"* ]]
}

@test "explicit missing or non-file configuration fails before installation" {
  for config in "$TMP/missing.env" "$TMP"; do
    run env ORCH_CONFIG="$config" /bin/bash "$BATS_TEST_DIRNAME/../install.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"ORCH_CONFIG is not a regular file"* ]]
  done
  [ ! -e "$TMP/missing.env" ]
}

@test "shared config reader rejects invalid selected paths even without errexit" {
  for config in "$TMP/missing.env" "$TMP"; do
    # shellcheck disable=SC2016 # The child Bash expands its positional argument.
    run env ORCH_CONFIG="$config" ORCH_PR_TABS_ORG=acme bash -c '. "$1/coordinator/watchers/coord-lib.sh"; orch_load_config; echo incorrectly-continued' _ "$BATS_TEST_DIRNAME/.."
    [ "$status" -ne 0 ]
    [[ "$output" == *"ORCH_CONFIG is not a regular file"* ]]
    [[ "$output" != *"incorrectly-continued"* ]]
  done
}

@test "update hook rejects invalid selected paths" {
  for config in "$TMP/missing.env" "$TMP"; do
    run env ORCH_CONFIG="$config" ORCH_RADAR_PATH="$TMP" bash "$BATS_TEST_DIRNAME/../hooks/orch-update-check.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"ORCH_CONFIG is not a regular file"* ]]
  done
}

@test "install converts an old orchestration.env once and keeps it as .bak" {
  fresh_install
  rm "$HOME/.claude/orchestration.toml"
  printf '%s\n' '# who I am' 'ORCH_PR_AUTHOR="me"' 'ORCH_HEAVY_SLOTS_MAX="6"' 'ORCH_BIG_REPO_REMOTES="acme/webapp"' >"$HOME/.claude/orchestration.env"
  install_ok
  contains "$output" "migrated        converted $HOME/.claude/orchestration.env -> $HOME/.claude/orchestration.toml"
  contains "$output" "migrated        wrote $HOME/.claude/orchestration-repo-snippets/acme/webapp.toml"
  contains "$output" "moved           $HOME/.claude/orchestration.env -> $HOME/.claude/orchestration.env.bak"
  [ ! -e "$HOME/.claude/orchestration.env" ]
  [ -f "$HOME/.claude/orchestration.env.bak" ]
  [ "$(config_get ORCH_PR_AUTHOR)" = me ]
  [ "$(config_get ORCH_HEAVY_SLOTS_MAX)" = 6 ]
  grep -qx '# who I am' "$HOME/.claude/orchestration.toml"
  grep -qx 'big_repo = true' "$HOME/.claude/orchestration-repo-snippets/acme/webapp.toml"
  install_ok
  not_contains "$output" 'migrated'
}

@test "with both config files the TOML file wins and install warns" {
  fresh_install
  printf '%s\n' 'ORCH_PR_AUTHOR="old"' >"$HOME/.claude/orchestration.env"
  config_set ORCH_PR_AUTHOR new
  install_ok
  contains "$output" "$HOME/.claude/orchestration.env is not read any more; $HOME/.claude/orchestration.toml wins"
  [ -f "$HOME/.claude/orchestration.env" ]
  [ "$(config_get ORCH_PR_AUTHOR)" = new ]
}

@test "an invalid config stops install with the file and key" {
  fresh_install
  printf '%s\n' '[guard]' 'big_repo = true' >>"$HOME/.claude/orchestration.toml"
  run /bin/bash "$CLONE/install.sh"
  [ "$status" -ne 0 ]
  contains "$output" "ERROR: $HOME/.claude/orchestration.toml: guard.big_repo: is a repo setting"
}

@test "install stops when no Python 3.11 or newer is found" {
  fresh_install
  mkdir "$TMP/oldpy"
  for name in python3 python3.11 python3.12 python3.13 python3.14; do
    printf '#!/bin/sh\nexit 1\n' >"$TMP/oldpy/$name"
    chmod +x "$TMP/oldpy/$name"
  done
  run env PATH="$TMP/oldpy:$PATH" ORCH_PYTHON_FALLBACKS="" /bin/bash "$CLONE/install.sh"
  [ "$status" -ne 0 ]
  contains "$output" "Python 3.11 or newer not found (the TOML config needs it)"
}

@test "the one-liner reads install.repo_path with a compatible Python when python3 on PATH is older" {
  mkdir -p "$TMP/oldpy/site" "$HOME/.claude"
  printf 'import sys\nsys.modules["tomllib"] = None\n' >"$TMP/oldpy/site/sitecustomize.py"
  # A Python 3.10 stand-in: it runs code, says it is older than 3.11 and has no tomllib.
  cat >"$TMP/oldpy/python3" <<EOF
#!/bin/sh
case "\$2" in *"version_info >= (3, 11)"*) echo False; exit 0 ;; esac
PYTHONPATH="$TMP/oldpy/site" exec "$BIN/python3" "\$@"
EOF
  chmod +x "$TMP/oldpy/python3"
  printf '%s\n' '[install]' "repo_path = \"$TMP/custom-clone\"" >"$HOME/.claude/orchestration.toml"
  # shellcheck disable=SC2016 # The child Bash expands these.
  run env PATH="$TMP/oldpy:$PATH" ORCH_PYTHON3="$BIN/python3" /bin/bash -c 'cd "$1"; exec /bin/bash -c "$ONE_LINER"' _ "$TMP"
  [ "$status" -eq 0 ]
  [ -e "$TMP/custom-clone/.git" ]
  [ ! -e "$HOME/.claude/ai-agent-orchestration" ]
}

@test "update hook reports a config the loader rejects" {
  printf '%s\n' '[pr]' 'nope = 1' >"$TMP/bad.toml"
  run env ORCH_CONFIG="$TMP/bad.toml" bash "$BATS_TEST_DIRNAME/../hooks/orch-update-check.sh"
  [ "$status" -ne 0 ]
  contains "$output" "orch-update-check: $TMP/bad.toml: pr.nope: unknown key"
}

@test "installer drops retired multiline topic rules once and preserves personal settings" {
  fresh_install
  awk '{ print } /^\[pr_tabs\]$/ { print "topics = ["; print "  { name = \"Old\", color = \"blue\", repo = [\"webapp\"] },"; print "]" }' "$HOME/.claude/orchestration.toml" >"$TMP/global.toml"
  mv "$TMP/global.toml" "$HOME/.claude/orchestration.toml"
  install_ok
  contains "$output" 'removed pr_tabs.topics (architect grouping is automatic)'
  run grep '^topics' "$HOME/.claude/orchestration.toml"
  [ "$status" -eq 1 ]
  install_ok
  not_contains "$output" 'removed pr_tabs.topics'
}

@test "install retires discovered-work settings and PR topic rules together" {
  fresh_install
  cat >"$HOME/.claude/orchestration.toml" <<'TOML'
[pr]
author = "me"
repos = ["acme/web"]
[focus]
skip_architects = ["Old"]
[pr_tabs]
org = "acme"
topics = []
TOML
  install_ok
  contains "$output" 'repos and architects come from the open work'
  contains "$output" 'removed pr_tabs.topics (architect grouping is automatic)'
  run python3 -c 'import sys,tomllib; c=tomllib.load(open(sys.argv[1], "rb")); assert c["pr"] == {"author":"me"}; assert c["pr_tabs"] == {"org":"acme"}; assert not c.get("focus")' "$HOME/.claude/orchestration.toml"
  [ "$status" -eq 0 ]
  install_ok
  not_contains "$output" 'removed pr_tabs.topics'
  not_contains "$output" 'repos and architects come from the open work'
}

@test "--print-repo-path prints the configured clone folder and does nothing else" {
  mkdir -p "$HOME/.claude"
  printf '[install]\nrepo_path = "%s/custom-clone"\n' "$TMP" >"$HOME/.claude/orchestration.toml"
  run /bin/bash "$SRC/install.sh" --print-repo-path
  [ "$status" -eq 0 ]
  [ "$output" = "$TMP/custom-clone" ]
  [ ! -e "$HOME/.claude/.orch-links" ]
  run env -u ORCH_REPO_PATH /bin/bash "$SRC/install.sh" --print-repo-path
  [ "$output" = "$TMP/custom-clone" ]
}

@test "--print-repo-path defaults to ~/.claude/ai-agent-orchestration" {
  run /bin/bash "$SRC/install.sh" --print-repo-path
  [ "$status" -eq 0 ]
  [ "$output" = "$HOME/.claude/ai-agent-orchestration" ]
}

@test "install links both finalize skills for Claude and the shared agents folder" {
  fresh_install
  for name in finalize-change-footprint finalize-code-comments; do
    skill="$(cd "$CLONE" && pwd -P)/skills/$name"
    [ -f "$skill/SKILL.md" ]
    [ "$(readlink "$HOME/.claude/skills/$name")" = "$skill" ]
    grep -qx "name: $name" "$HOME/.claude/skills/$name/SKILL.md"
  done
}

@test "a foreign finalize skill in the shared agents folder stays" {
  mkdir -p "$HOME/.agents/skills/finalize-change-footprint"
  printf 'foreign\n' >"$HOME/.agents/skills/finalize-change-footprint/SKILL.md"
  fresh_install
  [ ! -L "$HOME/.agents/skills/finalize-change-footprint" ]
  [ "$(cat "$HOME/.agents/skills/finalize-change-footprint/SKILL.md")" = foreign ]
  printf '%s\n' "$output" | grep -q "^collision  *$HOME/.agents/skills/finalize-change-footprint "
}

@test "the finalize skills carry no repo-specific names" {
  run grep -rniE 'sparkle|combinaut|sleistner|stagehand|rubocop|mise run|\.ai/' "$ROOT/skills/finalize-change-footprint" "$ROOT/skills/finalize-code-comments"
  [ "$status" -eq 1 ]
}
