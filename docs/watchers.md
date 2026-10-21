# Watchers and services

The watchers write alerts to files, and one notifier turns them into a feed at `~/.claude/coordinator/watchers/prod-context/coordinator-feed.log`. The Coordinator's Monitor prints only the lines that need action (`RECYCLE`, HOST danger, PR events, `WORKER` blocked, error or dead, `HEARTBEAT STALE`).

| Watcher | Watches |
|---|---|
| audit | Your open PRs in the repos of the open workspaces (red CI, open threads, conflicts, missing reviewer, approved), blocked workers, stale heavy slots, git pile-ups in those clones |
| pulls | PR events, sent straight to the owning architect |
| context | Session token use; sends `RECYCLE` |
| host | Memory, swap, processes, open files, load |
| hog | Big processes and long or big `git` processes |
| balance | Worktree CPU and memory against the heavy-slot cap |
| workers | Blocked, errored or vanished agents |
| user-todo | What waits on you (see [The to-do pane](../README.md#the-to-do-pane)) |

**With services**: set `[watchers] service = true` (with `[herdr] bin` and `socket`) and run the installer again. It registers the watchers (audit and pulls only when `pr.author` is set in the config file, not just exported) with launchd (macOS) or `systemd --user` (Linux) to run now and at login. Back to `false` removes them. Off by default.

**Without services**: run the loops yourself in the background, and the notifier too, because it fills the Coordinator's feed. The commands are in [Run the watchers without services](../coordinator/watchers/README.md#run-the-watchers-without-services). Each producer takes `--state-dir`, and `--loop --interval <seconds>` keeps it running. A watcher without `--loop` scans once and exits, which is the quick way to test one:

```bash
ORCH_HOST_FILES_MAX=1 ~/.claude/coordinator/watchers/coord-host-watch.sh --state-dir /tmp/host-check
```

When the host is under pressure, the Coordinator follows the memory guard: on HOST danger it should stop heavy jobs by their slot pids and tell architects that hold slots. On HOST READFAIL it restores readable metrics and keeps heavy jobs paused. On host HEARTBEAT STALE it restores the producer's service and checks current metrics. HOST RECOVERED clears the pressure; it checks the guard before allowing heavy jobs again.

## Repos from the open work

The audit and pulls watchers take their repos from the open workspaces: each workspace's repo root is a clone to check for PACKS and WORKTREES, and its `origin` (`owner/name` on GitHub) a repo whose PRs to watch, deduplicated. The env adapter reports the workspaces (`env/<name>/open_work.py`). When it cannot, or a clone's `origin` cannot be read, they use the last set saved in `~/.claude/coordinator/open-work.json` and log `open work unreadable (...)`; with no saved set the github and git sources are `READFAIL`. A clone without an `origin` remote is checked for pile-ups but has no PRs; a repo root whose folder is gone is skipped.

## Focus report

`focus-check.sh [interval]` prints one line every interval (default 900 seconds):

```text
FOCUS workers=<working> focus_workers_live=<n> slots=<live> idle_no_worker=<names> no_focus_worker=<names>
```

`idle_no_worker` lists an architect that is idle (it and its workers are not working, and it holds no live heavy slot) and has open work: a worker pane in a workspace it owns (`tokens.owner`), an open PR on the branch of such a workspace, or an item under `## Waits on the user` in its status file. An idle architect without open work is resting and never listed. A branch, origin or PR lookup that fails, or a PR list at its limit of 200, counts as open work; at most 20 repos are looked up per pass. `no_focus_worker` uses each repo's `focus.pattern`.

A line `Paused: <reason>` in `~/.claude/coordinator/status/<architect>.md` leaves that architect out of both lists until the line is removed. A status file that exists but cannot be read makes the line `FOCUS READFAIL status <path>`; a missing file or folder means no waits.

Alert kinds, deadlines, feed delivery and recovery rules are in the [watcher guide](../coordinator/watchers/README.md).

Question answers are claimed with unique IDs and acknowledged after successful
reduction and durable publication. Failed passes replay pending claims without
duplicating committed answers. Closing a question shares the answer-pass lock,
removes its pending claims, and prevents late orphan answers from being published.
Claims include a persisted submission order so corrections replay after older
answers. Unreadable claims do not block other questions; recovery clears the
corresponding read-failure alert. The panel discards terminal escape sequences.
