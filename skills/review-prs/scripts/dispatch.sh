#!/usr/bin/env bash
# dispatch.sh <url> <number> <title> <author> <sha> <kind> <agent> <cwd> <owner/repo> [direction]
# Starts a Codex review of one PR in a new Herdr pane (or $PANE), reports it, and closes a pane it opened.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -u
here="$(cd "$(dirname "$0")" && pwd -P)"
orch="$(cd "$here/../../.." && pwd -P)"
. "$orch/coordinator/watchers/coord-lib.sh"
url=$1 num=$2 title=$3 author=$4 sha=$5 kind=$6 agent=$7 cwd=$8 repo=$9 dir=${10:-right}

orch_load_config
orch_load_repo_config "$cwd"
model=${ORCH_CODEX_WORKER_MODEL:-}
case "$model" in "" | "<"*">") orch_die "set models.codex_worker (ORCH_CODEX_WORKER_MODEL) in $(orch_config_path)" ;; esac

opened=""
pane=${PANE:-}
if [ -z "$pane" ]; then
  pane=$(herdr pane split --current --direction "$dir" --cwd "$cwd" --no-focus |
    python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["pane"]["pane_id"])') || exit 1
  opened=1
fi
echo "PANE $pane AGENT $agent PR $url"

close_opened() {
  [ -n "$opened" ] || return 0
  "$orch/coordinator/watchers/close-pane.sh" "$pane" && echo "CLOSED $pane"
}

for try in 1 2 3 4 5; do
  herdr agent start "$agent" --kind codex --pane "$pane" -- --no-daemon --model "$model" && break
  [ "$try" = 5 ] && { echo "START FAILED"; close_opened; exit 1; }
  sleep 3
done
herdr agent wait "$agent" --until idle --timeout 120000 >/dev/null 2>&1 || sleep 15
if herdr agent read "$agent" --source recent-unwrapped --lines 30 | grep -qi "update"; then
  echo "UPDATE BANNER"
  herdr agent send-keys "$agent" down
  herdr agent send-keys "$agent" enter
  sleep 5
fi
python3 "$here/prs.py" mark-dispatched --url "$url" --sha "$sha" --agent "$agent" --pane "$pane"

repo_rules=""
[ "${ORCH_BIG_REPO:-0}" != 1 ] || repo_rules="$repo_rules
- Never use git grep or git cat-file --batch over a commit tree in this repo."
[ "${ORCH_LEGACY_SCAN:-0}" != 1 ] || repo_rules="$repo_rules
- Legacy check: run bash ~/.claude/coordinator/legacy-scan.sh $num $repo and read every line it lists. Every added line that builds on legacy code gets an inline comment that names the modern replacement, and makes the review REQUEST_CHANGES. Exception: a clearly named bridge that links a ticket."

prompt="Review this GitHub pull request that requested my review.

PR: $url
Title: $title
Author: $author
Head SHA: $sha
Mode: $kind

Rules:
- Fetch with gh; review that head SHA via git show / gh, not a dirty local checkout of another branch.
- Review only the PR's own changes: gh pr diff, or git diff against the merge base with the PR's own base branch (gh pr view --json baseRefName; stacked PRs do not target main). On re-review, use that same diff, never a raw old-sha..new-sha range: commits that arrived through merges from the base branch are not the author's changes and get no findings.
- If the repo has a review skill (.claude/skills/*review*/SKILL.md), read and follow it. Otherwise review for correctness, security, tests, and the repo's conventions.
- On re-review, re-check every prior finding against the new head and review the fixes as new code. Post inline comments only for NEW findings. Never re-post a prior finding that is still unresolved; list those in the review body as one line \"Still open: <links to the earlier threads>\". Keep REQUEST_CHANGES while any finding is open.
- Search code with rg or grep.$repo_rules
- Do not run tests unless needed. Run any test as heavy-slot take review-$agent -- <command> (escalated if the sandbox blocks it), the narrowest spec only.
- Do NOT use Computer Use or a browser for anything in this task. Use gh and the shell only.
- Post the GitHub review directly via gh api, no confirmation needed from me (approve / comment / request changes as appropriate). End the review body with *(Generated using Codex)* on its own line.
- Never merge, close, flip ready, or assign human reviewers.
- Never edit, create or delete any file in the repository (including AGENTS.md or other instruction files); this is a read-only review.
- When finished, reply with one line: APPROVE|COMMENT|CHANGES_REQUESTED — $url
- After posting the review, reply with that one line and end your turn. Do not poll, sleep, or wait for CI, other reviewers or replies."

herdr agent prompt "$agent" "$prompt" --wait --timeout 1800000
status=$?
echo "PROMPT_EXIT $status"
herdr agent read "$agent" --source recent-unwrapped --lines 80 | tail -25
me=$(gh api user --jq .login)
gh api --paginate "repos/$repo/pulls/$num/reviews" --jq ".[] | select(.user.login==\"$me\") | \"LASTREVIEW \(.state) \(.submitted_at) \(.html_url)\"" | tail -1
if [ "$status" -eq 0 ]; then
  close_opened
else
  echo "LEFT OPEN $pane"
fi
echo "DONE $agent $pane"
