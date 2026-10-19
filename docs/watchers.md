# Watchers and services

The watchers write alerts to files, and one notifier turns them into a feed at `~/.claude/coordinator/watchers/prod-context/coordinator-feed.log`. The Coordinator's Monitor prints only the lines that need action (`RECYCLE`, HOST danger, PR events, `WORKER` blocked, error or dead, `HEARTBEAT STALE`).

| Watcher | Watches |
|---|---|
| audit | Your open PRs (red CI, open threads, conflicts, missing reviewer, approved), blocked workers, stale heavy slots, git pile-ups |
| pulls | PR events, sent straight to the owning architect |
| context | Session token use; sends `RECYCLE` |
| host | Memory, swap, processes, open files, load |
| hog | Big processes and long or big `git` processes |
| balance | Worktree CPU and memory against the heavy-slot cap |
| workers | Blocked, errored or vanished agents |
| user-todo | What waits on you (see [The to-do pane](../README.md#the-to-do-pane)) |

**With services**: set `[watchers] service = true` (with `[herdr] bin` and `socket`) and run the installer again. It registers the watchers (audit and pulls only when `pr.repos` and `pr.author` are set in the config file, not just exported) with launchd (macOS) or `systemd --user` (Linux) to run now and at login. Back to `false` removes them. Off by default.

**Without services**: run the loops yourself in the background, and the notifier too, because it fills the Coordinator's feed. The commands are in [Run the watchers without services](../coordinator/watchers/README.md#run-the-watchers-without-services). Each producer takes `--state-dir`, and `--loop --interval <seconds>` keeps it running. A watcher without `--loop` scans once and exits, which is the quick way to test one:

```bash
ORCH_HOST_FILES_MAX=1 ~/.claude/coordinator/watchers/coord-host-watch.sh --state-dir /tmp/host-check
```

When the host is under pressure, the Coordinator follows the memory guard: on HOST danger it should stop heavy jobs by their slot pids and tell architects that hold slots. On HOST READFAIL it restores readable metrics and keeps heavy jobs paused. On host HEARTBEAT STALE it restores the producer's service and checks current metrics. HOST RECOVERED clears the pressure; it checks the guard before allowing heavy jobs again.

Alert kinds, deadlines, feed delivery and recovery rules are in the [watcher guide](../coordinator/watchers/README.md).
