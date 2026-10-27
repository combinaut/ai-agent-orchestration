# Workers, recycles and handoffs

- A **worker** does one task in its own pane and worktree, and writes a report to the path in its brief. You can watch it and type into its pane.
- Every worker carries an `owner` tag naming its architect. Herdr groups panes by repo, so without the tag a worker shows under the wrong architect. Radar groups workers under their architects from the tags.
- A **recycle** replaces a session that has used `ORCH_RECYCLE_TOKENS` tokens. An architect writes a bounded handoff file to its memory, a fresh session starts from that file, and the watchers keep sending to the new session once it runs `bind-self.py` again. A worker never recycles itself: its owning architect gets `RECYCLE-WORKER-SOFT` (300k) to ask NOW or FINISH in tokens, then `RECYCLE-WORKER-HARD` (450k, capped at 60% of a known model window) to order a handoff at the next call boundary. An unknown window leaves hard unclamped and never below soft. Silence means re-ask at idle/done; a live heavy slot delays even HARD until release. The architect orders the handoff to `<report dir>/<name>-handoff.md` and starts the successor ([`worker-recycle.md`](../skills/architect/references/worker-recycle.md)); workers do not bind. With `ORCH_PARK_IDLE="1"`, an idle architect writes its handoff and exits, and the Coordinator restarts it when work arrives.
- A worker that is done is closed by its architect with `close-worker.sh`, which leaves a recovery line (pane, task, report, branch, worktree, and the resume command of its agent: `claude --resume <id>`, `codex resume <id>`, else `session <id>`) in the architect's status file. After `workers.idle_s` seconds (2 h) idle or done with no open PR, the architect gets `WORKER IDLE <pane> owner=<workspace>` and checks the worker ([`worker-close.md`](../skills/architect/references/worker-close.md)).
- To switch models, recycle onto the new model. A live session is never switched.
- A worker runs each `review.finalize_commands` command (by default the shipped `/finalize-change-footprint` and `/finalize-code-comments`) over the complete PR diff before calling its PR done or ready, sends the architect each findings table, waits for approval, and applies only the approved findings. With commands set and `review.finalize_comment` not `false`, it then posts one PR comment with one table per configured command (every finding, approved or rejected, what changed, the commit SHA) ending with the tool's attribution trailer.

How the roles fit together: [`architecture.md`](architecture.md).

## Explicit worker signals

`orch-signal` works for Claude, Codex and pi. Identity is the caller's
`HERDR_PANE_ID`, `HERDR_WORKSPACE_ID` and the workspace's `owner` token, read
through the env adapter. It needs no Claude session ID. A shared Codex
app-server daemon can retain a dead pane environment for later tool shells.
When that stale pane ID is absent from Herdr, `orch-signal` recovers only a
single live Codex pane whose Git worktree matches the shell's current directory;
a present pane in another workspace is still refused. Identity failures name the
claimed pane, workspace, Herdr socket and pane count.

```bash
orch-signal heartbeat --new-job  # first command in every dispatched job
orch-signal ask --question "Which API?" --options "v1,v2" --timeout 600
orch-signal done --outcome succeeded --report /path/report.md --summary "Checks passed"
# In the owning architect workspace:
orch-signal list
orch-signal answer signal-<id> "v2"
orch-signal answer signal-<id> --to-user
```

A **current job** begins with `heartbeat --new-job` and ends with `done`; every
follow-up brief in a reused pane must begin a new job. Identical done calls are
idempotent and print a no-op notice to stderr. A changed report, summary or outcome
creates a new completion even without a reset. A done record suppresses WORKER WAITING
until a post-completion idle/done observation is followed by working; a heartbeat
within 300 seconds also suppresses it. Workers that send neither retain the old
alerts. WORKER IDLE remains independent. Long jobs can send heartbeat periodically;
calls less than 30 seconds apart are ignored. Ordinary heartbeats cannot undo done.

Completion and question messages go through the workers producer, shared notifier,
`bindings/architect-<owner>.json` and its existing outbox. Delivery is normally
within one workers poll (60 seconds). There is no second notifier or cursor.
Claude workers run `ask` with `run_in_background` or a Bash timeout above the
command's `--timeout`. SIGTERM/SIGINT/SIGHUP cancel the question under the answers lock;
a re-ask replaces a record whose recorded asker PID is dead. `answer` refuses
a dead asker with `asker is gone`, including escalation.
An owner may answer or escalate a question. Escalation uses the existing questions
directory, answers lock, reminders and Q panel; the answers watcher forwards the
user's answer to the blocked worker. If the asker has died, the bridge discards
the answer and emits one `ANSWERS READFAIL answer discarded:` notice instead. If opening the panel fails, a due reminder
retries. If `orch-signal` fails, a worker must finish with `DONE: <report path>`
or ask with `QUESTION: <text>` so its owner can recover the result; plain
`QUESTION:` is also the fallback when `ask` times out. `NEED SLOT:` is
unchanged. No picker opens in the worker pane.

| Resource | Bound |
|---|---|
| Pending questions | One per pane; at most 200 question records machine-wide, including deferred panel cleanup; further asks fail clearly |
| Ask wait | `workers.ask_timeout_s` / `ORCH_ASK_TIMEOUT_S`, default 1800 seconds; positive finite override `--timeout` |
| Waiting ask polling | At most once per second, last sleep capped by remaining time |
| Heartbeats | One accepted per 30 seconds; fresh for 300 seconds |
| Pane records | One per live pane; removed after a successful snapshot confirms pane closed or owner changed |
| Question/answer files | Worker consumption returns immediately; Q closure is deferred to the workers poll. Removed when consumed, replaced, closed, or at deadline + 3600 seconds; a failed Q close retains a retry record; live askers wait for cleanup, while dead askers can be replaced |
| Completion handoff | Latest 200 completions per pane for 3600 seconds; oldest dropped beyond cap; remaining events are emitted once before pruning a closed or re-tagged pane |
| Q-panel cleanup | At most one close attempt per workers poll, 2-second execution timeout with bounded process-group teardown; failed attempts rotate behind other pending panels |
| Signal dedupe state | IDs from retained live completions and unexpired pending questions only |
| Native outbox | Existing 200 entries; merge overflow by target, drop oldest beyond cap; retry every 60 seconds, expire after 3600 seconds |

Files live under `~/.claude/coordinator/worker-signals/`, never in the repo.
Escalated question files live in `~/.claude/coordinator/questions/`. Pruning uses a validated pane snapshot through the env adapter, preserves records when
the snapshot is unreadable, and does not prune records newer than the snapshot cutoff.
Pruning uses the existing answers lock, so answer publication cannot race cancellation. A
successful `ask` prints only its answer to stdout; timeout exits nonzero with one
clear line. The architect should use `list` to recover pending IDs after recycle.

Codex has no automatic watcher usage sensor; its architect checks native context readings at idle/done and before new work, and sends asks through `codex queue`. Model-window lookup and the complete decision procedure are in [`worker-recycle.md`](../skills/architect/references/worker-recycle.md).
