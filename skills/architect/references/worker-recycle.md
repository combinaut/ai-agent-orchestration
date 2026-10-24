# Worker recycle

Read this file before acting on `RECYCLE-WORKER-SOFT` or `RECYCLE-WORKER-HARD`. A worker never recycles itself; its owning architect runs this procedure.

The context watcher reads Claude's `~/.claude/context-usage/<session-id>.tokens`, with `.window` and `.model` written by the status line from `context_window.context_window_size` and `model.id`. A remaining-percentage file is the fallback: soft at `recycle.pct`; convert used percentage through the model window to check effective hard (at most 60% used). Architects retain `recycle.tokens` and `recycle.pct` unchanged.

Workers use `recycle.worker_soft_tokens` (300000) and `recycle.worker_hard_tokens` (450000). Effective hard is `min(configured hard, floor(0.6 × model window))`, even when it falls below soft. Equal soft and hard sends HARD directly, preserving immediate handoff behavior. Sensor metadata takes precedence; the single fallback table is `coordinator/watchers/worker_recycle.py:MODEL_WINDOWS`. The architect-approved fallback is 200000 for every Claude model and for Codex when its cache is unavailable. For a new model, ask the architect before changing this table; never guess a larger window. The table covers Haiku (`claude-haiku-5-5`), Sonnet (`claude-sonnet-5-5`), Opus (`claude-opus-5-5`), Fable (`claude-fable-5-1`), Codex terra (`gpt-5.6-terra`) and sol (`gpt-6.1-sol`). Codex reads its exact model entry in `~/.codex/models_cache.json` before using the fallback. The architect must verify the model/window of existing workers before acting on an unclamped signal.

The message shape remains `context: RECYCLE-WORKER-SOFT <pane> owner=<you> <name> <amount> (start). No reply needed.` (or `RECYCLE-WORKER-HARD`). Messages use `bindings/architect-$HERDR_WORKSPACE_ID.json`, wait in the existing outbox when unbound, retry every 60 seconds and expire after an hour. Each stage fires once per session/owner crossing.

Codex has no token sensor in this watcher: it is not automatically watched. Its architect must obtain current used tokens and the active model window from the worker's native context display, compare them at worker idle/done and before dispatching another step, and apply these same thresholds. If those readings are unavailable, ask for them over the native route and defer new work until known. Never treat missing readings as zero. The Codex model cache's `context_window` is a window fallback, not a usage sensor; do not use cumulative billing tokens. Ask Codex through `codex queue --thread <verified-session-UUID-or-exact-name> --message <text>`; use native `SendMessage` for Claude. Verify the recipient runtime first, repair a failed route, never type into a live composer. Preserve the worker name/model tier through recycle and rebind watcher destinations with a round-trip acknowledgement.

## Soft/hard decision

1. Verify ownership and whether the PR has merged (procedure step 1). Check live slot ownership (step 2) before every ask or handoff, including HARD and NOW. A held slot always wins: record the pending stage, wait, and recheck immediately after release; never interrupt a heavy run.
2. On SOFT, ask natively: **At a clean point? Reply NOW, or FINISH <tokens needed> <what is left>**. Record the ask time and session, current reading and effective hard in your status file so a recycle preserves the pending decision. Estimates are in tokens, never time.
3. NOW runs the normal handoff procedure below. FINISH with `soft + needed < effective hard` means no action: wait for the worker to report the step done, then hand off. At or above hard, tell it to hand off at its next clean point. Recheck slots at that point.
4. Silence for `recycle.worker_ask_timeout_s` (600) is never NOW. Record a pending re-ask and ask again only when the worker next goes idle/done. Invalid replies need a valid token estimate; they do not authorize a forced recycle.
5. HARD is the only forced stop: no ask; once any live slot releases, order handoff at the next call boundary. A HARD message supersedes a pending SOFT question or FINISH allowance.

Use the same tested decision helper when choosing the next action, passing fresh sensor metadata and verified live-slot ownership:

```bash
printf '%s\n' '{"value":{"tokens":310000,"window":1000000},"answer":"FINISH 10000 checks","slot":false,"elapsed":0,"idle":false}' | python3 ~/.claude/coordinator/watchers/worker_recycle.py
```

`WAIT_SLOT` waits for release; `WAIT_DONE` waits for the step report; `HANDOFF_CLEAN_POINT` orders the next clean point; `HANDOFF_CALL_BOUNDARY` orders the next call boundary; `HANDOFF` proceeds now; `REASK_WHEN_IDLE` records the pending re-ask; `ASK` / `ASK_VALID_REPLY` send the question. The helper never sends messages or recycles a process itself.

## Procedure

1. **Check.** `herdr agent get <name>` gives the pane and `workspace_id`; `herdr workspace get <ws>` must show `tokens.owner` equal to `$HERDR_WORKSPACE_ID` (agent rows carry no owner tag). A worker whose PR has merged is closed (step 6), not recycled.
2. **Heavy slot.** `heavy-slot list`. While a `live` slot is the worker's, wait: never order a handoff mid-run. A slot is the worker's when its name carries the worker's name, or when its `pid` is the worker's process or a child of it (match the pid, because slot names follow no fixed rule). Check again at the worker's next status change.
3. **Order the handoff after the decision above permits it** through the worker's native message route (`SendMessage` for Claude, `codex queue --thread <verified-session> --message <text>` for Codex; the Messages rule in `SKILL.md`), never `herdr agent prompt`: workers are user-interactive and a person may have text in the composer. Run `touch <report dir>/<name>-order.marker` just before you send it: *Finish your current call and start nothing new. Overwrite (never append) `<report dir>/<name>-handoff.md`, under 4,000 characters, with the worker handoff template in `~/.claude/skills/architect/references/worker-recycle.md`. Reply with only its path, then stop.*
   - If the native route fails, repair it; do not fall back to typing into the pane. Check `herdr agent get <name>` before resending, or you will double-order.
   - Wait for the reply with `herdr agent wait <name> --timeout <ms>` (no `--until`: it matches idle, done or blocked; a Claude worker that ends its turn shows `done`, so `--until idle` can hang) and read the handoff after it returns. Step 4 guards against an early match.
   - On `agent_blocked` the worker waits on a permission or a question and takes no prompt. Do not force it: tell the user in your user-facing line which pane is blocked, and retry from step 3 when its status changes.
4. **Read the handoff.** Accept it only if `test <handoff> -nt <marker>` is true: an older file is the last recycle's handoff. Under 4,000 characters (`wc -c`) and every field filled; ask once for what is missing. Do not start the successor while the handoff, or `heavy-slot list` (step 2), shows a slot held by the worker.
5. **Start the successor** in the same workspace and worktree, on the model in the handoff's `Model:` field:
   - `herdr pane split <old pane> --direction down --cwd <worktree> --no-focus`; the new pane is `result.pane.pane_id`. Never `herdr worktree open` the worktree again: on an open path it renames the workspace.
   - `herdr agent start <name>-next --kind claude --pane <new pane> -- --model <model> --strict-mcp-config` (the same choice as the first start: leave the flag out when the worker's brief needs an MCP tool) (any free name of at most 32 characters). Never `--resume` the old session: that loads its whole context again.
   - For a Codex successor, start a fresh Codex session on the saved model, preserving the configured routine/smart tier and reasoning effort; never resume the exhausted transcript. Deliver the brief through `codex queue`, then rebind native watcher destinations and verify a round-trip acknowledgement.
   - Prompt it once: *Read `<handoff path>` and only that, not the old transcript; it names your brief and rules. You own the review cycle of PR <n> from here. Write your report to `<report path>`. Do not call the `advisor` tool: each call re-reads the whole transcript uncached on the advisor model.*
   - With "no PR yet" in the handoff, say instead: *Continue the task from the handoff; no PR is open yet.* in place of the PR sentence.
   - Wait until `herdr agent get <name>-next` shows it working from the handoff.
6. **Close the old pane** with `~/.claude/coordinator/watchers/close-pane.sh <old pane>`, then `herdr agent rename <name>-next <name>`, so slot names, reports and the Radar keep one name.
7. **Record** the new pane id in your status file and handoff. The workspace and its `owner` tag stay the same, so PR events and the next worker threshold signal still come to you. The feed gets `RECYCLE-WORKER-SOFT <old pane> … cleared` (or HARD) when the old session leaves.

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
