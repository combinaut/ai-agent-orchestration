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
  'agent read')
    [ -z "${AGENT_READ_FAILS:-}" ] || exit 1
    [ -z "${PROMPT_DELIVERED:-}" ] || printf 'Head SHA: %s\n' "$SHA"
    printf 'APPROVE — https://github.com/acme/app/pull/7\n' ;;
  'agent wait') [ -z "${AGENT_BUSY:-}" ] || exit 1 ;;
esac
EOF
  cat >"$BIN/gh" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  'api user --jq .login') echo reviewer ;;
  *'/reviews'*) [ -n "${GH_NO_REVIEW:-}" ] || echo 'LASTREVIEW APPROVED 2026-01-01T00:00:00Z https://github.com/acme/app/pull/7#review' ;;
esac
EOF
  chmod +x "$BIN/herdr-review" "$BIN/gh"
  export HERDR_TEST_STUB="$BIN/herdr-review" HERDR_STUB_DIRECT=1
  export ORCH_CODEX_WORKER_MODEL=test-worker-model ORCH_CLOSED_DIR="$TMP/closed"
  git init -q "$TMP/app"
  git -C "$TMP/app" -c user.email=test@example.invalid -c user.name=test commit -q --allow-empty -m pr
  SHA=$(git -C "$TMP/app" rev-parse HEAD)
  export SHA
}

dispatch() {
  run env ORCH_CONFIG="$TMP/none.toml" bash "$ROOT/$SKILL_DIR/scripts/dispatch.sh" \
    https://github.com/acme/app/pull/7 7 'Fix it' author "$SHA" review rev7 "$TMP/app" acme/app
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

@test "dispatch leaves a pane passed in as PANE open" {
  stub_review_tools
  : >"$TMP/none.toml"
  PANE=w9-p5 dispatch
  [ "$status" -eq 0 ]
  contains "$output" 'LEFT OPEN w9-p5'
  [ ! -e "$TMP/closed/w9-p5" ]
}

@test "dispatch reports only submitted reviews of the dispatched head" {
  stub_review_tools
  : >"$TMP/none.toml"
  # shellcheck disable=SC2016 # The stub expands these.
  printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" "$*" >>"$TMP/gh.calls"' >"$BIN/gh"
  dispatch
  grep -qF "commit_id==\"$SHA\"" "$TMP/gh.calls"
  grep -q 'submitted_at != null' "$TMP/gh.calls"
}

@test "queue entries never reach an unknown merge state and agent names differ per repo and head" {
  # shellcheck disable=SC2016 # Python source.
  run python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
import prs
pr = {"isDraft": False, "mergeable": "UNKNOWN", "commits": {"nodes": []}}
assert prs.bucket(pr) == "unknown"
names = {prs.agent_name("acme/app", 7, "a" * 40), prs.agent_name("acme/api", 7, "a" * 40), prs.agent_name("acme/app", 7, "b" * 40)}
assert len(names) == 3, names
assert all(len(n) <= 32 for n in names), names
' "$ROOT/$SKILL_DIR/scripts"
  [ "$status" -eq 0 ]
}

@test "mark-dispatched keeps every entry when calls overlap" {
  for n in 1 2 3 4 5 6; do
    python3 "$ROOT/$SKILL_DIR/scripts/prs.py" mark-dispatched --url "https://github.com/acme/app/pull/$n" --sha "s$n" >/dev/null &
  done
  wait
  [ "$(python3 -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' "$HOME/.cache/review-prs/dispatched.json")" = 6 ]
}

@test "a PR whose merge state turns known on the same head becomes ready" {
  # shellcheck disable=SC2016 # Python source.
  run python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
import prs
pr = dict(title="t", draft=False, author="a", ci="SUCCESS", sha="s1")
url = "https://github.com/acme/app/pull/7"
unknown, known = {url: dict(pr, conflict=None)}, {url: dict(pr, conflict=False)}
assert prs.changes(unknown, unknown)[0] == [], "unknown twice"
assert prs.changes(dict(unknown), {url: dict(pr, conflict=None, sha="s2")})[0][0].startswith("changed:"), "new head, still unknown"
lines = prs.changes(unknown, known)[0]
assert lines and lines[0].startswith("ready:"), lines
' "$ROOT/$SKILL_DIR/scripts"
  echo "$output"
  [ "$status" -eq 0 ]
}

@test "dispatch leaves the pane open when the turn ends without a review of the head" {
  stub_review_tools
  : >"$TMP/none.toml"
  GH_NO_REVIEW=1 dispatch
  [ "$status" -eq 0 ]
  contains "$output" "INCOMPLETE no review of $SHA"
  contains "$output" 'LEFT OPEN w9-p2'
  [ ! -e "$TMP/closed/w9-p2" ]
}

# dispatch_state: prints whether the next dispatch-queue would dispatch PR 7 at $SHA, and the stored entry.
dispatch_state() {
  # shellcheck disable=SC2016 # Python source.
  python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1])
import prs
data = prs.load_dispatched()
pr = {"url": "https://github.com/acme/app/pull/7", "isDraft": False, "mergeable": "MERGEABLE", "headRefOid": sys.argv[2],
      "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]}}
print(prs.needs_dispatch(pr, data)[0], json.dumps(data.get(pr["url"], {}).get("state")))
' "$ROOT/$SKILL_DIR/scripts" "$SHA"
}

@test "an undelivered prompt releases the head for the next dispatch-queue" {
  stub_review_tools
  : >"$TMP/none.toml"
  PROMPT_STATUS=1 GH_NO_REVIEW=1 dispatch
  contains "$output" "RELEASED $SHA"
  contains "$output" 'CLOSED w9-p2'
  [ "$(dispatch_state)" = 'True null' ]
}

@test "a delivered prompt that times out keeps the head reserved" {
  stub_review_tools
  : >"$TMP/none.toml"
  PROMPT_STATUS=1 PROMPT_DELIVERED=1 GH_NO_REVIEW=1 dispatch
  not_contains "$output" 'RELEASED'
  [ "$(dispatch_state)" = 'False "pending"' ]
}

@test "a posted review marks the head done" {
  stub_review_tools
  : >"$TMP/none.toml"
  dispatch
  [ "$(dispatch_state)" = 'False "done"' ]
}

@test "install leaves unrelated skills in the agents folder alone" {
  mkdir -p "$HOME/.agents/skills/architect" "$TMP/elsewhere/coordinate"
  printf mine >"$HOME/.agents/skills/architect/SKILL.md"
  ln -s "$TMP/elsewhere/coordinate" "$HOME/.agents/skills/coordinate"
  fresh_install
  [ ! -L "$HOME/.agents/skills/architect" ]
  [ "$(cat "$HOME/.agents/skills/architect/SKILL.md")" = mine ]
  [ "$(readlink "$HOME/.agents/skills/coordinate")" = "$TMP/elsewhere/coordinate" ]
  printf '%s\n' "$output" | grep -q "^collision  *$HOME/.agents/skills/architect "
  printf '%s\n' "$output" | grep -q "^collision  *$HOME/.agents/skills/coordinate "
}

@test "a failed prompt keeps the head reserved while the agent is busy or unreadable" {
  stub_review_tools
  : >"$TMP/none.toml"
  PROMPT_STATUS=1 GH_NO_REVIEW=1 AGENT_BUSY=1 dispatch
  not_contains "$output" 'RELEASED'
  [ "$(dispatch_state)" = 'False "pending"' ]
  rm "$HOME/.cache/review-prs/dispatched.json"
  PROMPT_STATUS=1 GH_NO_REVIEW=1 AGENT_READ_FAILS=1 dispatch
  not_contains "$output" 'RELEASED'
  [ "$(dispatch_state)" = 'False "pending"' ]
}

@test "an older head finishing never replaces a newer head's mark" {
  prs() { python3 "$ROOT/$SKILL_DIR/scripts/prs.py" "$@" >/dev/null; }
  u=https://github.com/acme/app/pull/7
  prs mark-dispatched --url "$u" --sha old --state pending
  prs mark-dispatched --url "$u" --sha new --state pending
  prs mark-dispatched --url "$u" --sha old --state "done"
  # shellcheck disable=SC2016 # Python source.
  run python3 -c '
import json, sys
e = json.load(open(sys.argv[1]))[sys.argv[2]]
print(e["sha"], e["state"], e["previous"]["sha"], e["previous"]["state"])
' "$HOME/.cache/review-prs/dispatched.json" "$u"
  [ "$output" = 'new pending old done' ]
  prs release-dispatched --url "$u" --sha new
  run python3 -c 'import json, sys; e = json.load(open(sys.argv[1]))[sys.argv[2]]; print(e["sha"], e["state"])' "$HOME/.cache/review-prs/dispatched.json" "$u"
  [ "$output" = 'old done' ]
}

@test "install leaves an agents skills folder that links into the clone alone" {
  mkdir -p "$HOME/.agents"
  ln -s "$HOME/.claude/ai-agent-orchestration/skills" "$HOME/.agents/skills"
  fresh_install
  [ -d "$CLONE/skills/review-prs" ] && [ ! -L "$CLONE/skills/review-prs" ]
  [ -f "$CLONE/skills/review-prs/SKILL.md" ]
  [ -z "$(git -C "$CLONE" status --porcelain)" ]
}

@test "prs.sh runs prs.py on the configured Python, not the first python3 on PATH" {
  mkdir -p "$TMP/oldpy"
  printf '%s\n' '#!/bin/sh' 'echo old python >&2; exit 3' >"$TMP/oldpy/python3"
  chmod +x "$TMP/oldpy/python3"
  : >"$TMP/none.toml"
  run env PATH="$TMP/oldpy:$PATH" ORCH_PYTHON3="$(command -v python3)" ORCH_PYTHON_FALLBACKS= ORCH_CONFIG="$TMP/none.toml" \
    bash "$ROOT/$SKILL_DIR/scripts/prs.sh" --help
  [ "$status" -eq 0 ]
  contains "$output" 'usage: prs.py'
}

@test "watch-events forwards change lines and the now: counts from line one" {
  log="$TMP/pr-watch.log"
  printf '%s\n' 'noise' 'ready: https://github.com/acme/app/pull/7 — t: CI green' 'now: 1 actionable, 1 total' >"$log"
  set -m
  bash "$ROOT/$SKILL_DIR/scripts/watch-events.sh" "$log" >"$TMP/events" &
  pid=$!
  set +m
  for _ in $(seq 1 100); do [ "$(wc -l <"$TMP/events")" -ge 2 ] && break; sleep 0.05; done
  kill -TERM -- "-$pid"
  wait "$pid" 2>/dev/null || true
  [ "$(cat "$TMP/events")" = "$(printf '%s\n' 'ready: https://github.com/acme/app/pull/7 — t: CI green' 'now: 1 actionable, 1 total')" ]
  [ "$(cat "$log.pos")" = 3 ]
}

@test "dispatch reviews in a detached worktree at the head and removes it with the pane" {
  stub_review_tools
  : >"$TMP/none.toml"
  dispatch
  wt="$HOME/.cache/review-prs/worktrees/rev7"
  grep -qF -- "--cwd $wt" "$TMP/review-herdr.calls"
  contains "$output" "REMOVED $wt"
  [ ! -e "$wt" ]
  GH_NO_REVIEW=1 dispatch
  [ "$(git -C "$wt" rev-parse HEAD)" = "$SHA" ]
  contains "$output" "WORKTREE $wt"
}

@test "an undelivered prompt in a caller's pane keeps the head reserved" {
  stub_review_tools
  : >"$TMP/none.toml"
  PANE=w9-p5 PROMPT_STATUS=1 GH_NO_REVIEW=1 dispatch
  not_contains "$output" 'RELEASED'
  contains "$output" 'LEFT OPEN w9-p5'
  [ "$(dispatch_state)" = 'False "pending"' ]
}

@test "a head still pending behind a newer mark is not offered again" {
  stub_review_tools
  prs() { python3 "$ROOT/$SKILL_DIR/scripts/prs.py" "$@" >/dev/null; }
  u=https://github.com/acme/app/pull/7
  prs mark-dispatched --url "$u" --sha "$SHA" --state pending
  prs mark-dispatched --url "$u" --sha older --state pending
  [ "$(dispatch_state)" = 'False "pending"' ]
}

@test "dispatch-queue finds the clone from a subdirectory of it" {
  stub_review_tools
  git -C "$TMP/app" remote add origin https://github.com/acme/app.git
  mkdir -p "$TMP/app/sub/dir"
  # shellcheck disable=SC2016 # Python source.
  run bash -c 'cd "$1/sub/dir" && python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import prs; print(prs.resolve_cwd(\"acme/app\"))" "$2"' _ "$TMP/app" "$ROOT/$SKILL_DIR/scripts"
  [ "$output" = "$(cd "$TMP/app" && pwd -P)" ]
}
