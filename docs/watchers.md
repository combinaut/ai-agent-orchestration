# Watchers and services

The watchers write alerts to files. One notifier turns them into a feed at `~/.claude/coordinator/watchers/prod-context/coordinator-feed.log`. The Coordinator's Monitor prints only the lines that need action: `RECYCLE`, HOST danger, PR events, `WORKER` blocked, error or dead, and `HEARTBEAT STALE`.

| Watcher | Watches |
|---|---|
| audit | Your open PRs in the repos of the open workspaces (red CI, open threads, a review comment, conflicts, missing reviewer, approved), blocked workers, stale heavy slots, git pile-ups in those clones |
| pulls | PR events (`APPROVED`, `RED`, `CHANGES`, `THREADS`, `COPILOT`, `COMMENTED`, `MERGED`, `CLOSED`), sent straight to the owning architect |
| context | Session token use; sends `RECYCLE` (architect, to the Coordinator) and `RECYCLE-WORKER-SOFT` / `RECYCLE-WORKER-HARD` (worker, to the owning architect) |
| host | Memory, swap, processes, open files, load |
| hog | Big processes and long or big `git` processes |
| balance | Worktree CPU and memory against the heavy-slot cap |
| workers | Blocked, errored or vanished agents; finished workers waiting on their architect; sends `WORKER IDLE` (worker idle or done for `workers.idle_s`, 2 h by default, with no open PR) to the owning architect |
| user-todo | What waits on you (see [The to-do pane](todo.md)) |

## 🔔 With services

Set `[watchers] service = true` (with `[herdr] bin` and `socket`) and run the installer again. It registers the watchers with launchd (macOS) or `systemd --user` (Linux), so they run now and at login. The audit and pulls watchers register only when `pr.author` is set in the config file, not just exported. Setting `service` back to `false` removes them. Off by default.

## ✋ Without services

Run the loops yourself in the background. Run the notifier too: it fills the Coordinator's feed. The commands are in [Run the watchers without services](../coordinator/watchers/README.md#run-the-watchers-without-services). Each producer takes `--state-dir`, and `--loop --interval <seconds>` keeps it running. A watcher without `--loop` scans once and exits, which is the quick way to test one:

```bash
ORCH_HOST_FILES_MAX=1 ~/.claude/coordinator/watchers/coord-host-watch.sh --state-dir /tmp/host-check
```

## 🔥 Host pressure

When the host is under pressure, the Coordinator follows the memory guard:

- On HOST danger it should stop heavy jobs by their slot pids and tell architects that hold slots.
- On HOST READFAIL it restores readable metrics and keeps heavy jobs paused.
- On host HEARTBEAT STALE it restores the producer's service and checks current metrics.
- HOST RECOVERED clears the pressure. The Coordinator checks the guard before it allows heavy jobs again.

## 🔭 Repos from the open work

The audit and pulls watchers take their repos from the open workspaces: each workspace's repo root is a clone to check for PACKS and WORKTREES, and its `origin` and `upstream` remotes (`owner/name` on GitHub) repos whose PRs to watch, each once, case-insensitive, `origin` first. `upstream` is the base repo of a fork, and is read only for a clone whose `origin` is a GitHub repo: every open PR of `pr.author` on it is watched, whatever its head. The env adapter reports the workspaces (`env/<name>/open_work.py`). When it cannot, or a clone's `origin` cannot be read, they use the last set saved in `~/.claude/coordinator/open-work.json` and log `open work unreadable (...)`; with no saved set the github and git sources are `READFAIL`. A clone without a GitHub `origin` remote is checked for pile-ups but has no PRs (its `upstream` is not read); a clone without an `upstream` remote is normal; an `upstream` that cannot be read logs `upstream of <clone> unreadable: <detail>; adding the repos last discovered in <path>` and adds the repos last saved to that pass, saves nothing and keeps every `origin` repo; a repo root whose folder is gone is skipped.

## 🎯 Focus report

`focus-check.sh [interval]` prints one line every interval (default 900 seconds):

```text
FOCUS workers=<working> focus_workers_live=<n> slots=<live> idle_no_worker=<names> no_focus_worker=<names>
```

`idle_no_worker` lists an architect that is idle (it and its workers are not working, and it holds no live heavy slot) and has open work: a worker pane in a workspace it owns (`tokens.owner`), an open PR on the branch of such a workspace (on the clone's `origin` repo, or on its `upstream` repo when the PR's head repository belongs to the owner of `origin`), or an item under `## Waits on the user` in its status file. An idle architect without open work is resting and never listed. A branch, origin, upstream or PR lookup that fails, or a PR list at its limit of 200, counts as open work; at most 20 repos are looked up per pass. `no_focus_worker` uses each repo's `focus.pattern`.

A line `Paused: <reason>` in `~/.claude/coordinator/status/<architect>.md` leaves that architect out of both lists until the line is removed. A status file that exists but cannot be read makes the line `FOCUS READFAIL status <path>`; a missing file or folder means no waits.

Alert kinds, deadlines, feed delivery and recovery rules are in the [watcher guide](../coordinator/watchers/README.md).

Question answers are claimed with unique IDs and acknowledged after successful
reduction and durable publication. Failed passes replay pending claims without
duplicating committed answers. Closing a question shares the answer-pass lock,
finds the pane by its exact `Q: <slug>` label and closes it through `close-pane.sh`
before removing its files and pending claims. A failed pane close preserves the
files for retry. This prevents late orphan answers from being published.
A Remind on the panel leaves `<slug>.remind` (epoch seconds) next to the question; each answers pass, under the same lock, reopens the pane of every question whose time has passed and removes the file. A failed open retries on the next pass; a reminder without its question is dropped.
Claims include a persisted submission order so corrections replay after older
answers. Unreadable claims do not block other questions; recovery clears the
corresponding read-failure alert. The panel discards terminal escape sequences.
`coord-check.sh` and `coord-cycle.sh` inspect answers without claiming or
acknowledging them, so previews cannot divert submissions from production.
Queued terminal bytes are preserved; undecodable claims retry independently.
Answer previews retain the watcher deadline without consuming submissions.
Directory-read failures retain replay IDs. Failed pane setup closes the new pane
through the intentional-close helper, leaving the question file available to retry.

## 👷 Workers waiting

A session in an owner-tagged workspace that stays `done` or `idle` for more than
`workers.wait_s` seconds (default 600) emits
`WORKER WAITING <name> pane=<pane> owner=<workspace> <minutes>m session=<id>`.
Architect and Coordinator role panes, and panes whose name or stripped title is
exactly an `Architect::` name (with no whitespace), are excluded. The start
line goes only to `prod-context/coordinator-fyi.log`, with no native message to
the architect.
There is one repeat at `workers.wait_repeat_s` seconds after the initial alert
(default 1800), also only in the fyi file, then one `ESCALATE` after another such
interval. The escalation goes to the feed and natively to the Coordinator through
its binding and the existing bounded retry outbox. Each gap is at least
60 seconds, including after laptop sleep. No further repeats. The user's to-do
list is unchanged.

An observed `working` status resets the timer. Closing the session, removing its
owner tag, or changing its owner clears its old alert immediately. Clears also
cancel queued native retries for that owner and identity, preserving other
messages and later restarts. An unreadable
workspace or agent snapshot preserves state; a successful empty ownership read
clears it. Timers are keyed by session ID (or pane ID when no session is available)
and removed when that identity vanishes, so a successor using the same pane and
name starts fresh. A change in Herdr's per-agent `state_change_seq` also resets
the timer, catching state transitions between polls even when both snapshots are
done/idle. The timer starts at the first observed done/idle snapshot and survives
watcher restarts.

Before a due alert, the watcher reads the last 16 pane lines. Claude monitor/shell
footers or monitor status-line counts, and Codex's background-terminal running
line, suppress delivery without resetting the timer or advancing its stage. A
failed pane read is treated as not busy and logged once until a successful read.
Reads have a five-second per-pane timeout and a ten-second total budget, reduced
to reserve five seconds for the rest of the pass. Budget-skipped panes count as
not busy.
These text indicators are incomplete, so every waiting line retains the caveat
`(may be waiting on its own job)`, including the escalation.

Explicit completion, heartbeat and question commands: [worker signals](workers.md#explicit-worker-signals).
