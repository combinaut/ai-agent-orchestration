# Workers, recycles and handoffs

- A **worker** does one task in its own pane and worktree, and writes a report to the path in its brief. You can watch it and type into its pane.
- Every worker carries an `owner` tag naming its architect. Herdr groups panes by repo, so without the tag a worker shows under the wrong architect. Radar groups workers under their architects from the tags.
- A **recycle** replaces a session that has used `ORCH_RECYCLE_TOKENS` tokens. An architect writes a bounded handoff file to its memory, a fresh session starts from that file, and the watchers keep sending to the new session once it runs `bind-self.py` again. A worker never recycles itself: its owning architect gets `RECYCLE-WORKER`, orders the handoff to `<report dir>/<name>-handoff.md` and starts the successor ([`worker-recycle.md`](../skills/architect/references/worker-recycle.md)); workers do not bind. With `ORCH_PARK_IDLE="1"`, an idle architect writes its handoff and exits, and the Coordinator restarts it when work arrives.
- A worker that is done is closed by its architect with `close-worker.sh`, which leaves a recovery line (pane, task, report, branch, worktree, and the resume command of its agent: `claude --resume <id>`, `codex resume <id>`, else `session <id>`) in the architect's status file. After `workers.idle_s` seconds (2 h) idle or done with no open PR, the architect gets `WORKER IDLE <pane> owner=<workspace>` and checks the worker ([`worker-close.md`](../skills/architect/references/worker-close.md)).
- To switch models, recycle onto the new model. A live session is never switched.
- When a repo sets `review.finalize_commands`, a worker runs each command before calling its PR done or ready, sends the architect each findings table, waits for approval, and applies only the approved findings.

How the roles fit together: [`architecture.md`](architecture.md).

## Explicit worker signals

`orch-signal` works for Claude, Codex and pi. Identity is the caller's
`HERDR_PANE_ID`, `HERDR_WORKSPACE_ID` and the workspace's `owner` token, read
through the env adapter. It needs no Claude session ID.

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
follow-up brief in a reused pane must begin a new job. Repeated done calls are
idempotent until that reset. A done record suppresses WORKER WAITING; a heartbeat
within 300 seconds also suppresses it. Workers that send neither retain the old
alerts. WORKER IDLE remains independent. Long jobs can send heartbeat periodically;
calls less than 30 seconds apart are ignored. Ordinary heartbeats cannot undo done.

Completion and question messages go through the workers producer, shared notifier,
`bindings/architect-<owner>.json` and its existing outbox. Delivery is normally
within one workers poll (60 seconds). There is no second notifier or cursor.
An owner may answer or escalate a question. Escalation uses the existing questions
directory, answers lock, reminders and Q panel; the answers watcher forwards the
user's answer to the blocked worker. If opening the panel fails, a due reminder
retries. Plain `QUESTION:` is the fallback when the command fails or times out;
`NEED SLOT:` is unchanged. No picker opens in the worker pane.

| Resource | Bound |
|---|---|
| Pending questions | One per pane; a second pending ask fails |
| Ask wait | `workers.ask_timeout_s` / `ORCH_ASK_TIMEOUT_S`, default 1800 seconds; positive finite override `--timeout` |
| Waiting ask polling | At most once per second, last sleep capped by remaining time |
| Heartbeats | One accepted per 30 seconds; fresh for 300 seconds |
| Pane records | One per live pane; removed after a successful snapshot confirms pane closed or owner changed |
| Question/answer files | One question and answer per pane; removed when consumed, replaced, closed, or at deadline + 3600 seconds by workers poll |
| Completion handoff | Latest 200 completions per pane for 3600 seconds; oldest dropped beyond cap, so rapid new-job resets cannot erase a completion before the poll |
| Signal dedupe state | IDs from retained live completions and unexpired pending questions only |
| Native outbox | Existing 200 entries; merge overflow by target, drop oldest beyond cap; retry every 60 seconds, expire after 3600 seconds |

Files live under `~/.claude/coordinator/worker-signals/`, never in the repo.
Escalated question files live in `~/.claude/coordinator/questions/`. Pruning uses
the existing answers lock, so answer publication cannot race cancellation. A
successful `ask` prints only its answer to stdout; timeout exits nonzero with one
clear line. The architect should use `list` to recover pending IDs after recycle.
