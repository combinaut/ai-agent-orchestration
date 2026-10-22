# Close a finished worker

Read this file before you close a worker, or act on a `WORKER IDLE` message. The reason is machine load: every open Claude session holds memory and runs hook processes.

**Done means all of:** the worker's report is written and you have read it; its PR is merged or closed (or it never had one); nothing is typed in its composer (`herdr pane read <pane>`); it holds no heavy slot (`heavy-slot list`).

## Close

```bash
~/.claude/coordinator/watchers/close-worker.sh <pane> --task "<what it did>" --report <report path>
```

The script reads the worker's name, worktree, branch and session id, closes the pane with `close-pane.sh`, and then adds one line to `## Closed workers` in `~/.claude/coordinator/status/<your agent name>.md`. It keeps the last 10 lines there and leaves every other section as it was. Exit 3 means it refused: Herdr found no such pane or agent, the pane is an architect, the Coordinator or your own pane, the caller is not an architect pane or its name is not a plain file name, or the worker's workspace `owner` tag is not `$HERDR_WORKSPACE_ID`. Exit 1 means the close failed and no line was written: look at the pane, do not retry in a loop. Exit 4 means the pane closed but the line could not be written: copy the printed line into your status file by hand.

The script removes no worktree and no branch. A squash or rebase merge leaves the branch tip outside the target branch, so `git branch -d` can refuse a merged branch; ask GitHub instead. After the script, run `git worktree remove <worktree>` and then `git branch -D <branch>` only when all three hold: `git -C <worktree> status --short` is empty; the PR state is `MERGED` or `CLOSED`; and the local tip is the PR head. Read both from one call, `gh pr view <fork>:<branch> --repo <base> --json state,headRefOid`, where `<base>` is the `owner/name` of the `upstream` remote of the worktree when it has one, else of `origin` (the repository that holds the PR), and `<fork>` is the owner of `origin` (the owner of the PR head; without the `<fork>:` prefix `gh` finds no PR for a fork). `headRefOid` must equal `git -C <worktree> rev-parse <branch>`. Otherwise (unpushed commits are the usual cause) leave both and say so in your status file.

## Bring a closed worker back

The recovery line ends with the resume command of the worker's agent: `claude --resume <id>`, `codex resume <id>`, or `session <id>` for another agent. Run it in the worktree from the line, in a new pane. Start it as in step 5 of [`worker-recycle.md`](worker-recycle.md), with that command in place of the fresh start. Keep the `owner=$HERDR_WORKSPACE_ID` tag on its workspace.

## WORKER IDLE

`workers: WORKER IDLE <pane> owner=<you> (start)` says the worker has been idle or done for `$ORCH_WORKER_IDLE_S` seconds (2 h by default) and its branch has no open PR. One line per idle period. Check it with `herdr agent get <pane>` and its report path:

- **Task done** (the list above holds): close it with the script.
- **Anything else** (report not written, the worker waits on a reply, text in its composer): keep it and do nothing else. You get no second line for this period.

You do not need to answer or report the message. Architects stay open; `recycle.park_idle` stays off.
