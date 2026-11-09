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
- **auto-dispatch** (default on `watch`, and whenever the user asks to review/dispatch): for each **🔔 Ready for your review** PR, open a Herdr sibling pane and start Codex on `$ORCH_CODEX_WORKER_MODEL` (`$ORCH_CODEX_SMART_MODEL` when only that one is set) to review or re-review it.

Read-only for the orchestrator: this skill never merges, closes, or assigns reviewers itself. Codex may post the GitHub review (that is the point of dispatch). Pass `--no-dispatch` to list/watch without launching agents.

## Settings

Read through `orch-settings` ([docs/settings.md](../../docs/settings.md)), which prints the resolved values; never read `~/.claude/orchestration.toml` directly, since an empty `providers.enabled` there still means Codex is on:

- `models.codex_worker` (`ORCH_CODEX_WORKER_MODEL`): the review model; when it is empty or a `<placeholder>`, `models.codex_smart` serves. Applies only when `codex` is in `ORCH_PROVIDERS`; then, both empty or a `<placeholder>`: dispatch nothing and tell the user to fill one in.
- `providers.enabled` (`ORCH_PROVIDERS`): without `codex`, `dispatch-queue` plans an empty queue with a `note` and `dispatch.sh` refuses; list and watch as with `--no-dispatch`, and say once in one line that Codex reviews are off.
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

JSON fields per item: `url`, `number`, `title`, `author`, `repo`, `sha`, `kind` (`review` | `re-review` | `text-re-review` | `re-request`), `text` (hash of the PR title and body), `agent` (`rev<number>-<sha7>-<repo hash>`, plus `-<text4>` for `text-re-review`, or a round suffix for `re-request`; unique per repo and head), `cwd` (local clone or `null`), `model`.

`cwd` comes from the clones of the open work, the current directory, or `~/Projects/<owner>/<name>`; a clone matches when its `origin` is the repo, or else its `upstream` (a fork of it), and a clone whose `origin` is the repo wins. Skip entries with `cwd: null` and tell the user no local clone was found.

Skip entries whose claim (`sha`, plus `text` for a `text-re-review`) is already marked, pending, blocked, or done (script does this). Each `dispatch-queue` result includes every blocked current head with its recorded reason; a new head or an explicit `release-dispatched` clears that condition, and a title or body edit does not. It also lists in `stuck` every review pending for 30 minutes or more (`url`, `sha`, `agent`, `pane`, `minutes`); nothing expires, so release or finish it by hand. Concurrency: **up to 3 Codex reviews at once**. Queue the rest; when any in-flight review finishes, start the next queued item until 3 are running again (or the queue is empty).

### Launch one review

Run one dispatch per queue item in the background:

```bash
~/.claude/skills/review-prs/scripts/dispatch.sh "<url>" <number> "<title>" "<author>" <sha> <kind> <agent> "<cwd>" <owner/repo> right|down <text>
```

Pass the queue item's `text` as the 11th argument for every kind; `dispatch.sh` exits before claiming without it.

Set `ORCH_REVIEW_EXTRA_STEPS` when dispatching to add review-specific instructions; `dispatch.sh` includes them in the authorized review prompt.
Any follow-up sent to a review pane must repeat that it must post without asking for confirmation.

Before the prompt, `dispatch.sh` checks `orch-sweep-record gate --pr <url> --head <sha>`. It reuses a valid published record by the reviewer or PR author, including a record whose fix findings need review. Otherwise it runs `orch-sweep` as one heavy job in the review worktree; a text re-review uses `--kind text` with a full fallback. An unchanged-head re-request never runs a sweep. `review.finalize_commands = []` disables sweep and record checks on this path.

Reviews from this workflow post through `orch-sweep post-review --pr <url> --head <sha> --event APPROVE|REQUEST_CHANGES|COMMENT --input -`. The enabled approval gate checks the exact head, the merge base against GitHub's live `base.sha`, record schema, current PR text and undisputed fix findings. It accepts the reviewer's or author's record without a local cache copy. Nits and dropped findings never block. A reviewer can dispute a kept fix with a specific code reason in the review body: `Sweep dispute <sweep-comment-id>/<finding-id>: <reason>`. The comment id scopes the dispute to that sweep; the post-turn gate reads the reviewer's latest submitted review at this head.

Sweep exit 1, 6 or 75 releases the claim after cleanup for a later retry; other sweep failures block the head. Gate or review-POST tool errors exit 2: no replacement verdict and no retry of an uncertain POST. After a posted approval, gate exit 2 is retryable only after a completed turn and successful owned-pane cleanup. A failed turn, caller-owned pane or failed cleanup stays pending. A lasting post-approval gate refusal blocks the head. Recover unposted findings before releasing a pending claim; releasing a blocked retake restores its previous completed record.

Dispatch claims the head under a lock, checks the queued title/body hash before work and before prompting, checks out the requested commit in a detached worktree, and starts Codex with the configured model. It closes only panes it opened, through [`close-pane.sh`](../../coordinator/watchers/close-pane.sh); caller-owned panes remain open. A completed turn without a submitted review retains its claim and context. Failed input delivery releases the claim only after owned cleanup succeeds.

## Rules

- **Orchestrator never merges, closes, or assigns reviewers.** Codex posts reviews only.
- **Do not dispatch Draft / CI-failing / CI-running / Conflicting** buckets — only Ready.
- **At most 3 concurrent Codex reviews** unless the user raises the limit. When one finishes, report → close pane → fill from a fresh `dispatch-queue`.
- **A PR missing from search is not proof it is gone** — watcher checks up to 10 absent PRs per poll through GraphQL and keeps direct or team requests. A failed departure read retains that entry without adding a miss, reports READFAIL, and allows other PR updates to continue.
- **`merge state unknown` is not a pass** — `list` retries once.

## Common pitfalls

- GitHub MCP often misses private org repos — use `gh` (the script does).
- `review-requested:@me` clears after you (or Codex) submit a review. A PR you reviewed whose head moved stays in the queue without a new request (`reviewed-by:@me`), so watch mode re-reviews it after further pushes.
- GraphQL search must paginate; `mergeStateStatus` needs the `merge-info-preview` Accept header.
- Herdr `agent start` needs an empty shell pane; it does not create layout — `dispatch.sh` splits first.
- Agent names: `[a-z][a-z0-9_-]{0,31}`, unique among live agents (`dispatch-queue` makes them so).
