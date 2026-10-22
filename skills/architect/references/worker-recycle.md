# Worker recycle

Read this file before you act on a `RECYCLE-WORKER` message. A worker never recycles itself; you run this procedure.

**The signal.** The context watcher reads each Claude pane's `~/.claude/context-usage/<session-id>.tokens`. When a worker in a workspace tagged `owner=$HERDR_WORKSPACE_ID` reaches `$ORCH_RECYCLE_TOKENS` tokens (`$ORCH_RECYCLE_PCT` percent without that file), you get one native message per session: `context: RECYCLE-WORKER <pane> owner=<you> <name> <amount> (start). No reply needed.` It is sent through your binding `bindings/architect-$HERDR_WORKSPACE_ID.json`; before you have bound, or while the binding is dead, it waits in the outbox (retried every 60 seconds, dropped after an hour) and stays in the Coordinator feed. Codex workers have no sensor and are not watched.

## Procedure

1. **Check.** `herdr agent get <name>` gives the pane and `workspace_id`; `herdr workspace get <ws>` must show `tokens.owner` equal to `$HERDR_WORKSPACE_ID` (agent rows carry no owner tag). A worker whose PR has merged is closed (step 6), not recycled.
2. **Heavy slot.** `heavy-slot list`. While a `live` slot is the worker's, wait: never order a handoff mid-run. A slot is the worker's when its name carries the worker's name, or when its `pid` is the worker's process or a child of it (match the pid, because slot names follow no fixed rule). Check again at the worker's next status change.
3. **Order the handoff** through the worker's native message route (`SendMessage` to its verified session; the Messages rule in `SKILL.md`), never `herdr agent prompt`: workers are user-interactive and a person may have text in the composer. Run `touch <report dir>/<name>-order.marker` just before you send it: *Finish your current call and start nothing new. Overwrite (never append) `<report dir>/<name>-handoff.md`, under 4,000 characters, with the worker handoff template in `~/.claude/skills/architect/references/worker-recycle.md`. Reply with only its path, then stop.*
   - If the native route fails, repair it; do not fall back to typing into the pane. Check `herdr agent get <name>` before resending, or you will double-order.
   - Wait for the reply with `herdr agent wait <name> --timeout <ms>` (no `--until`: it matches idle, done or blocked; a Claude worker that ends its turn shows `done`, so `--until idle` can hang) and read the handoff after it returns. Step 4 guards against an early match.
   - On `agent_blocked` the worker waits on a permission or a question and takes no prompt. Do not force it: tell the user in your user-facing line which pane is blocked, and retry from step 3 when its status changes.
4. **Read the handoff.** Accept it only if `test <handoff> -nt <marker>` is true: an older file is the last recycle's handoff. Under 4,000 characters (`wc -c`) and every field filled; ask once for what is missing. Do not start the successor while the handoff, or `heavy-slot list` (step 2), shows a slot held by the worker.
5. **Start the successor** in the same workspace and worktree, on the model in the handoff's `Model:` field:
   - `herdr pane split <old pane> --direction down --cwd <worktree> --no-focus`; the new pane is `result.pane.pane_id`. Never `herdr worktree open` the worktree again: on an open path it renames the workspace.
   - `herdr agent start <name>-next --kind claude --pane <new pane> -- --model <model>` (any free name of at most 32 characters). Never `--resume` the old session: that loads its whole context again.
   - Prompt it once: *Read `<handoff path>` and only that, not the old transcript; it names your brief and rules. You own the review cycle of PR <n> from here. Write your report to `<report path>`.*
   - With "no PR yet" in the handoff, say instead: *Continue the task from the handoff; no PR is open yet.* in place of the PR sentence.
   - Wait until `herdr agent get <name>-next` shows it working from the handoff.
6. **Close the old pane** with `~/.claude/coordinator/watchers/close-pane.sh <old pane>`, then `herdr agent rename <name>-next <name>`, so slot names, reports and the Radar keep one name.
7. **Record** the new pane id in your status file and handoff. The workspace and its `owner` tag stay the same, so PR events and the next `RECYCLE-WORKER` still come to you. The feed gets `RECYCLE-WORKER <old pane> … cleared` when the old session leaves.

## Worker handoff template

Under 4,000 characters, overwritten (never appended), facts only:

- **Brief and rules:** brief path, common-rules path, report path.
- **Model:** the model id you run on.
- **Code:** repo, branch, worktree, head SHA, pushed to which remote.
- **PR:** number, state, CI, review decision; or "no PR yet".
- **Open review threads:** one line each: thread id or URL, what it asks, status (fixed in `<sha>`, replied, open).
- **Waits on:** the architect, CI, a bot review, a reviewer, or nothing.
- **Done:** one line each, with its proof (command and result).
- **Next step:** the first thing to do.
- **Decided, do not reopen:** choices made with the architect, dead ends.
- **Running:** heavy slot held (should be none), processes left running.
