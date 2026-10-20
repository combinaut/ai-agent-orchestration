#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.

load test_helper

SKILL_DIR=skills/review-prs

# stub_review_tools: a herdr that logs calls and saves the prompt, and a gh that knows one reviewer.
stub_review_tools() {
  cat >"$BIN/herdr-review" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TMP/review-herdr.calls"
case "$1 $2" in
  'pane split') printf '%s\n' '{"result":{"pane":{"pane_id":"w9-p2"}}}' ;;
  'agent prompt') printf '%s' "$4" >"$TMP/review.prompt"; exit "${PROMPT_STATUS:-0}" ;;
  'agent read') printf 'APPROVE — https://github.com/acme/app/pull/7\n' ;;
esac
EOF
  cat >"$BIN/gh" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  'api user --jq .login') echo reviewer ;;
  *'/reviews'*) echo 'LASTREVIEW APPROVED 2026-01-01T00:00:00Z https://github.com/acme/app/pull/7#review' ;;
esac
EOF
  chmod +x "$BIN/herdr-review" "$BIN/gh"
  export HERDR_TEST_STUB="$BIN/herdr-review" HERDR_STUB_DIRECT=1
  export ORCH_CODEX_WORKER_MODEL=test-worker-model ORCH_CLOSED_DIR="$TMP/closed"
  mkdir -p "$TMP/app"
}

dispatch() {
  run env ORCH_CONFIG="$TMP/none.toml" bash "$ROOT/$SKILL_DIR/scripts/dispatch.sh" \
    https://github.com/acme/app/pull/7 7 'Fix it' author abc123 review rev7 "$TMP/app" acme/app
}

@test "dispatch closes the finished review pane through close-pane.sh" {
  stub_review_tools
  : >"$TMP/none.toml"
  dispatch
  [ "$status" -eq 0 ]
  [ -e "$TMP/closed/w9-p2" ]
  grep -qx 'pane close w9-p2' "$TMP/review-herdr.calls"
  contains "$output" 'CLOSED w9-p2'
}

@test "dispatch leaves the pane open when the prompt fails" {
  stub_review_tools
  : >"$TMP/none.toml"
  PROMPT_STATUS=1 dispatch
  [ "$status" -eq 0 ]
  [ ! -e "$TMP/closed/w9-p2" ]
  contains "$output" 'LEFT OPEN w9-p2'
  run grep -q 'pane close' "$TMP/review-herdr.calls"
  [ "$status" -eq 1 ]
}

@test "dispatch starts Codex on the configured worker model" {
  stub_review_tools
  : >"$TMP/none.toml"
  dispatch
  grep -q -- 'agent start rev7 --kind codex --pane w9-p2 -- --no-daemon --model test-worker-model' "$TMP/review-herdr.calls"
}

@test "dispatch refuses a placeholder worker model" {
  stub_review_tools
  : >"$TMP/none.toml"
  ORCH_CODEX_WORKER_MODEL='<codex-worker-model>' dispatch
  [ "$status" -ne 0 ]
  [ ! -e "$TMP/review-herdr.calls" ]
}

@test "review prompt ends the Codex turn once the review is posted" {
  stub_review_tools
  : >"$TMP/none.toml"
  dispatch
  contains "$(cat "$TMP/review.prompt")" 'After posting the review, reply with that one line and end your turn.'
  contains "$(cat "$TMP/review.prompt")" 'Do not poll, sleep, or wait for CI, other reviewers or replies.'
}

@test "the skill closes panes only through close-pane.sh" {
  run grep -rn 'herdr pane close' "$ROOT/$SKILL_DIR"
  [ "$status" -eq 1 ]
  grep -q 'coordinator/watchers/close-pane.sh' "$ROOT/$SKILL_DIR/SKILL.md"
}

@test "the skill holds no org, model, login or personal path literals" {
  run grep -rniE 'combinaut|sparkle|gpt-[0-9]|sleistner|/Users/|/private/tmp|31691' "$ROOT/$SKILL_DIR"
  [ "$status" -eq 1 ]
}

@test "install links the review-prs skill and backs up an old real folder" {
  mkdir -p "$HOME/.agents/skills/review-prs" "$HOME/.claude/skills"
  printf old >"$HOME/.agents/skills/review-prs/SKILL.md"
  ln -s ../../.agents/skills/review-prs "$HOME/.claude/skills/review-prs"
  fresh_install
  skill="$(cd "$CLONE" && pwd -P)/skills/review-prs"
  [ "$(readlink "$HOME/.claude/skills/review-prs")" = "$skill" ]
  [ "$(readlink "$HOME/.agents/skills/review-prs")" = "$skill" ]
  for backup in "$HOME"/.claude/backups/*/home/.agents/skills/review-prs/SKILL.md; do [ -f "$backup" ]; done
  [ "$(cat "$backup")" = old ]
  for link in "$HOME"/.claude/backups/*/skills/review-prs; do [ -L "$link" ]; done
  install_ok
  printf '%s\n' "$output" | grep -q "^already linked  *$HOME/.agents/skills/review-prs\$"
}
