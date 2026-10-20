---
name: review-prs
description: List open PRs that request your review — author, freshness, CI/conflict blockers — or watch them live and auto-dispatch a Herdr Codex pane to review or re-review each Ready PR
disable-model-invocation: true
user-invocable: true
allowed-tools: Bash Read Monitor TaskStop PushNotification
argument-hint: "[watch] [--repo owner/name | --org ORG | --all] [--actionable] [--no-dispatch]"
---

# Review PRs

Scripts: `~/.claude/skills/review-prs/scripts/` (`prs.sh` runs `prs.py` on the orchestration's Python 3.11+; `dispatch.sh`; `watch-events.sh`). State: `~/.cache/review-prs/`.

- **list** (default): open PRs that still request your review (`review-requested:@me`), bucketed by whether you can review now.
- **watch**: the list once, then a background watcher that reports each change. It stays silent while nothing changes.
- **auto-dispatch** (default on `watch`, and whenever the user asks to review/dispatch): for each **🔔 Ready for your review** PR, open a Herdr sibling pane and start Codex on `$ORCH_CODEX_WORKER_MODEL` to review or re-review it.

Read-only for the orchestrator: this skill never merges, closes, or assigns reviewers itself. Codex may post the GitHub review (that is the point of dispatch). Pass `--no-dispatch` to list/watch without launching agents.

## Settings

From `~/.claude/orchestration.toml` ([docs/settings.md](../../docs/settings.md)):

- `models.codex_worker` (`ORCH_CODEX_WORKER_MODEL`): the review model. Empty or a `<placeholder>`: dispatch nothing and tell the user to fill it in.
- `watchers.pr_scope` (`ORCH_WATCHERS_PR_SCOPE`): the scope when no scope flag is given outside a repo.

Repo settings of the PR's clone shape the prompt: `guard.big_repo` forbids broad Git reads, `legacy.scan` adds the legacy check.

## Arguments

- `watch` — start watch mode. Without it, list once.
- `--repo owner/name` — one repo. `--repo name` works together with `--org`.
- `--org ORG` — every repo in the org.
- `--all` — every PR requesting you, anywhere.
- `--actionable` — list only Ready-for-review PRs.
- `--no-dispatch` — never open Herdr/Codex panes.

With no scope flag, the script uses the GitHub repo of the current directory, else `watchers.pr_scope`, else `--all`.

## List

```bash
~/.claude/skills/review-prs/scripts/prs.sh list [scope] [--actionable]
```

It prints markdown tables. **The user cannot see tool output rendered**: copy the script's output into your reply **verbatim, as markdown**. Every table stays a table, and every PR stays its full bare `https://github.com/...` URL. Never answer "see above", never summarize the tables into prose or bullets, and never shorten a URL to `#1234`.

The buckets, first match wins:

| Bucket | Test |
|---|---|
| 📝 Drafts | `isDraft` |
| 🔴 Conflicting | `mergeable == CONFLICTING` |
| ❔ Merge state unknown | `mergeable == UNKNOWN` after one retry; never dispatched |
| 🔴 CI failing | rollup `FAILURE` / `ERROR` |
| ⏳ Waiting on CI | rollup `PENDING` / `EXPECTED` |
| 🔔 Ready for your review | none of the above |

Three short columns: PR, Title (≤60 chars), Author · status. Your own authored PRs and bot README-sync titles are hidden.

After the tables, add only what the buckets do not already say: months-old parked PRs, or which Ready PRs you just dispatched.

## Watch

1. Run `list` with the same scope (tables + snapshot).
2. Unless `--no-dispatch`, run **Auto-dispatch** below for the current Ready queue.
3. Start the watcher in the background, scope explicit, its output appended to a log in your scratchpad:

   ```bash
   ~/.claude/skills/review-prs/scripts/prs.sh watch [scope] >>"<scratchpad>/pr-watch.log" 2>&1
   ```

   Default interval 120s (`--interval` overrides). Follow it with one Monitor on `~/.claude/skills/review-prs/scripts/watch-events.sh "<scratchpad>/pr-watch.log"`: it prints only event lines, from line one on first attach and after the last line it read on a restart, so no event is skipped.

4. Tell the user: you only speak on changes; watch lasts until the session ends; "stop the PR watch" ends it. On `new:` / `ready:` events for actionable PRs, run Auto-dispatch again (skip SHAs already dispatched).

### Reporting a watch event

| Prefix | Meaning |
|---|---|
| `new:` | PR started requesting your review |
| `changed:` | draft/CI/conflict/new commits |
| `ready:` | became reviewable (or new commits while still ready) |
| `cleared:` | open, but no longer requesting you |
| `merged:` / `closed:` | left the list with real GitHub state |
| `PR fetch failing 3x in a row:` / `recovered:` | auth/network |

Relay in plain words with full bare URLs. End with `now:` counts. PushNotification for `new:` only: `Review requested: PR <n> (<short title>) by <author>`.

## Auto-dispatch (Herdr + Codex)

Requires `HERDR_ENV=1`. If not inside Herdr, say so and stop dispatch (list/watch still fine).

### Queue

```bash
~/.claude/skills/review-prs/scripts/prs.sh dispatch-queue [scope]
```

JSON fields per item: `url`, `number`, `title`, `author`, `repo`, `sha`, `kind` (`review` | `re-review`), `agent` (`rev<number>-<sha7>-<repo hash>`, unique per repo and head), `cwd` (local clone or `null`), `model`.

`cwd` comes from the clones of the open work, the current directory, or `~/Projects/<owner>/<name>`. Skip entries with `cwd: null` and tell the user no local clone was found.

Skip entries already marked for the same `sha`, pending or done (script does this). Concurrency: **up to 3 Codex reviews at once**. Queue the rest; when any in-flight review finishes, start the next queued item until 3 are running again (or the queue is empty).

### Launch one review

Run one dispatch per queue item in the background:

```bash
~/.claude/skills/review-prs/scripts/dispatch.sh "<url>" <number> "<title>" "<author>" <sha> <kind> <agent> "<cwd>" <owner/repo> [right|down]
```

It first claims the SHA (`pending`, under a lock); a head another dispatch already claimed or finished prints `TAKEN <sha>` and exits 3 before any pane, agent or worktree. Then it checks out a detached worktree at the SHA under `~/.cache/review-prs/worktrees/<agent>` (fetching `pull/<n>/head` when the clone lacks it), splits a sibling pane there in the current tab without taking focus (`down` when the pane is already narrow; `PANE=<id>` reuses an empty pane instead), starts Codex on `$ORCH_CODEX_WORKER_MODEL` in that worktree (`--cd`, also for a `PANE`) without loading the PR's `AGENTS.md` (`-c project_doc_max_bytes=0`; review rules come from the base branch), sends the review prompt and waits for the turn to end. The prompt tells Codex to post the review, reply `APPROVE|COMMENT|CHANGES_REQUESTED — <url>` and end its turn: no polling, no waiting for CI or replies.

Output lines: `WORKTREE <path>` and `PANE <id> AGENT <name> PR <url>` at start; `PROMPT_EXIT <n>` and the agent's last lines at the end. Then either `LASTREVIEW <state> <time> <url>` (your review of the dispatched SHA exists; the SHA is marked `done`), or `INCOMPLETE no review of <sha>` (the SHA stays `pending`; when the prompt failed and the agent never showed it, `RELEASED <sha>` drops the mark so the next `dispatch-queue` retries it). A release closes the pane it opened first, so the agent name is free for the retry; a pane passed in as `PANE` is never released. Then `CLOSED <pane>` and `REMOVED <worktree>` (a pane it opened, review posted, prompt finished) or `LEFT OPEN <pane>`; `DONE`. After closing a `LEFT OPEN` pane yourself, remove its worktree: `git -C <cwd> worktree remove --force <worktree>`. gh calls stop after 45 seconds.

Tell the user which pane/agent is reviewing which PR (full URL). When the review finishes you **must** announce the outcome — silence after dispatch is not enough.

### Report and close

Report in one short line with the full bare PR URL:

- `APPROVE` / `APPROVED` → **Approved** https://github.com/...
- `CHANGES_REQUESTED` → **Changes requested** https://github.com/...
- `COMMENT` / `COMMENTED` → **Commented** https://github.com/...

Take it from `LASTREVIEW` only: the agent's one-liner is a claim, the posted review is the fact. With `INCOMPLETE`, say **Review not posted** with the PR URL and a one-sentence note from the agent's lines, whatever verdict the agent printed — do not invent an approval.

Close review panes only with `~/.claude/coordinator/watchers/close-pane.sh <pane_id>`: it marks the pane closed on purpose, so the workers watcher reports it as closed, not dead. `dispatch.sh` does this itself for a pane it opened once the review is posted and the prompt finished. For `LEFT OPEN` (no review yet, timeout, `blocked`, or a pane passed in as `PANE`), report first and close only when it is done or the user says to abandon it. Never close panes you did not create for this dispatch.

### Re-review

`dispatch-queue` emits `kind: re-review` when the same URL was marked before with a **different** head SHA (new commits while still requesting you, or after CI became green again). Same launch path; prompt Mode is `re-review`.

## Rules

- **Orchestrator never merges, closes, or assigns reviewers.** Codex posts reviews only.
- **Do not dispatch Draft / CI-failing / CI-running / Conflicting** buckets — only Ready.
- **At most 3 concurrent Codex reviews** unless the user raises the limit. When one finishes, report → close pane → fill from the queue.
- **A PR missing from search is not proof it is gone** — watcher confirms `merged`/`closed` via `gh pr view`.
- **`merge state unknown` is not a pass** — `list` retries once.

## Common pitfalls

- GitHub MCP often misses private org repos — use `gh` (the script does).
- `review-requested:@me` clears after you (or Codex) submit a review. A PR you reviewed whose head moved stays in the queue without a new request (`reviewed-by:@me`), so watch mode re-reviews it after further pushes.
- GraphQL search must paginate; `mergeStateStatus` needs the `merge-info-preview` Accept header.
- Herdr `agent start` needs an empty shell pane; it does not create layout — `dispatch.sh` splits first.
- Agent names: `[a-z][a-z0-9_-]{0,31}`, unique among live agents (`dispatch-queue` makes them so).
