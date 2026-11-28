# Workers, recycles and handoffs

- A **worker** does one task in its own pane and worktree, and writes a report to the path in its brief. You can watch it and type into its pane.
- Every worker carries an `owner` tag naming its architect. Herdr groups panes by repo, so without the tag a worker shows under the wrong architect. Radar groups workers under their architects from the tags.
- A **recycle** replaces a session that has used `ORCH_RECYCLE_TOKENS` tokens. An architect writes a bounded handoff file to its memory, a fresh session starts from that file, and the watchers keep sending to the new session once it runs `bind-self.py` again. A worker never recycles itself: its owning architect gets `RECYCLE-WORKER-SOFT` (300k) to ask NOW or FINISH in tokens, then `RECYCLE-WORKER-HARD` (450k, capped at 60% of a known model window) to order a handoff at the next call boundary. An unknown window leaves hard unclamped and never below soft. Silence means re-ask at idle/done; a live heavy slot delays even HARD until release. The architect orders the handoff to `<report dir>/<name>-handoff.md` and starts the successor ([`worker-recycle.md`](../skills/architect/references/worker-recycle.md)); workers do not bind. With `ORCH_PARK_IDLE="1"`, an idle architect writes its handoff and exits, and the Coordinator restarts it when work arrives.
- A worker that is done is closed by its architect with `close-worker.sh`, which leaves a recovery line (pane, task, report, branch, worktree, and the resume command of its agent: `claude --resume <id>`, `codex resume <id>`, `cursor-agent --resume <id>`, `agy --conversation <id>`, else `session <id>`) in the architect's status file. After `workers.idle_s` seconds (2 h) idle or done with no open PR, the architect gets `WORKER IDLE <pane> owner=<workspace>` and checks the worker ([`worker-close.md`](../skills/architect/references/worker-close.md)).
- Claude worker and reviewer panes set `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false` before the start, so they show no prompt suggestions; architect and Coordinator sessions keep theirs.
- To switch models, recycle onto the new model. A live session is never switched.
- A worker runs each `review.finalize_commands` command (by default the shipped `/finalize-sweep`) over the complete PR diff before calling its PR done or ready, and again after every merge or rebase of the base into the PR branch. It sends the architect each findings table, waits for approval, and applies only the approved findings. With commands set, a PR, and `review.finalize_comment` not `false`, it then posts one PR comment with one table per configured command (every finding, approved or rejected, what changed, the commit SHA) ending with the tool's attribution trailer. `/finalize-sweep` writes a record with `head`, `base`, `findings`, and `validator_ids`; the worker reports its path and `orch-sweep-record check --record <path> --head <sha> [--base <sha>] --session <worker-session-id>` must pass. Its validator contract is defined in `/finalize-sweep` sections 5 (Validate) and 6 (Report, steps 4–5). The session id is `.result.agent.agent_session.value` from `herdr agent get "$HERDR_PANE_ID"`. `--no-transcripts` is CI attestation only. A worker can use `orch-sweep` for the headless script flow described in [the sweep script](sweep-script.md); its script record is checked in full, including every sweep-agent transcript even when there are no findings.

How the roles fit together: [`architecture.md`](architecture.md).

## Explicit worker signals

`orch-signal` works for Claude, Codex, pi and cursor; for gemini it is untested until a live agy worker runs (see the Gemini workers section). The Herdr env adapter
verifies the caller's process ancestry against live pane foreground processes,
then reads the pane's workspace and its `owner` token. Inherited pane IDs only
prioritize the lookup: cached shell environments and session IDs can belong to
another terminal after a restart or concurrent session resume. A corrected pane
is logged and preserved as `recovered_from` in the signal record. Neither a
matching working directory nor a live inherited pane proves caller identity.
All commands, including architect `answer` and `list`, require a verified caller;
an unverifiable caller fails without writing a signal. If the sandbox blocks
process inspection, run the command escalated. Tool shells must descend from
the pane's foreground process (Codex workers start with `--no-daemon`). A command
started with `&` or a `( ... &)` subshell (also with `nohup`) is
detached from its pane, so `orch-signal ask` fails with `cannot verify caller
pane from live foreground processes`; run it in the foreground (Claude Code: the
Bash tool's `run_in_background`).

`orch-signal` writes `~/.claude/coordinator`, outside the Codex sandbox. Codex
workers run every `orch-signal` command as an escalated command, like
`heavy-slot`. When a write or lock is denied, it exits 73 with
`orch-signal: cannot write <dir> (sandbox?); run this command escalated`.

```bash
orch-signal heartbeat --new-job  # first command in every dispatched job
orch-signal ask --question "Which API?" --options "v1,v2" --timeout 600
orch-signal done --outcome succeeded --report /path/report.md --summary "Checks passed"
# In the owning architect workspace:
orch-signal list
orch-signal answer signal-<id> "v2"
```

An architect-owned worker question never opens the Q panel. The owning architect asks
the user from its own pane, then returns the answer with `orch-signal answer <id>
"<text>"`. `orch-signal answer <id> --to-user` is unsupported for worker questions; ask the user in the architect's pane instead.

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
Codex workers must keep polling a running `exec_command` session with `write_stdin`
and empty input until `ask` exits; never end the turn while it runs. The tool
yielding a session ID does not mean the command completed.
Claude workers run `ask` with `run_in_background` or a Bash timeout above the
command's `--timeout`. SIGTERM/SIGINT/SIGHUP cancel the question under the answers lock;
a re-ask replaces a record whose recorded asker PID is dead. `answer` refuses a dead asker with `asker is gone`.
For Codex answers, a bounded receipt survives CLI consumption until the workers
poll observes the model working or sends one native wake-up to its verified live
session while idle/done. The wake-up names the ask ID and tells the worker to read
its running session output; it includes the answer for recovery if the tool
session is no longer readable after a restart. A worker that already read the
answer ignores the wake-up. No pane input is injected. Failed native sends are
reported on stderr and retried by the next workers poll; receipt retention ends
at the question deadline. At most one wake-up is attempted per workers poll;
failed attempts rotate behind other pending receipts. The CLI remains the normal
answer channel.
Codex subagent waits are also bounded: `No agents completed yet` or a wait
timeout does not establish completion. Check `list_agents` and keep calling
`wait_agent` until the required children finish; never end the turn with pending
children. As a fallback, the workers poll reads only a verified idle worker's own
session metadata and child completion events, then sends a native wake-up for
new child completions after the parent's idle turn ended. Active parents and old
completions do not trigger it. Receipts are scoped to the live parent session,
bounded to 64 recent children, and pruned when the pane closes.
The Codex SQLite/rollout store is undocumented and may change. Unknown schemas or
rollout shapes disable this fallback and log once per failure episode;
`test_codex_store_shape_wakes_idle_parent_once` in `test_codex_subagents.py` pins
the supported shape. Subagent answers remain in their native threads.

An owner answers a question directly.
If `orch-signal` fails, a worker must finish with `DONE: <report path>`
or ask with `QUESTION: <text>` so its owner can recover the result; plain
`QUESTION:` is also the fallback when `ask` times out. `NEED SLOT:` is
unchanged. No picker opens in the worker pane.

| Resource | Bound |
|---|---|
| Pending questions | One per pane; at most 200 question records machine-wide; further asks fail clearly |
| Ask wait | `workers.ask_timeout_s` / `ORCH_ASK_TIMEOUT_S`, default 1800 seconds; positive finite override `--timeout` |
| Waiting ask polling | At most once per second, last sleep capped by remaining time |
| Heartbeats | One accepted per 30 seconds; fresh for 300 seconds |
| Pane records | One per live pane; removed after a successful snapshot confirms pane closed or owner changed |
| Question/answer files | Worker consumption returns immediately. Removed when consumed (Codex receipts remain until wake-up, observed working, or deadline), replaced, closed, or at deadline + 3600 seconds; live askers wait for cleanup, while dead askers can be replaced |
| Completion handoff | Latest 200 completions per pane for 3600 seconds; oldest dropped beyond cap; remaining events are emitted once before pruning a closed or re-tagged pane |
| Q-panel cleanup | At most one close attempt per workers poll, 2-second execution timeout with bounded process-group teardown; failed attempts rotate behind other pending panels |
| Signal dedupe state | IDs from retained live completions and unexpired pending questions |
| Native outbox | Existing 200 entries; merge overflow by target, drop oldest beyond cap; retry every 60 seconds, expire after 3600 seconds |

Files live under `~/.claude/coordinator/worker-signals/`, never in the repo.
Pruning uses a validated pane snapshot through the env adapter, preserves records when
the snapshot is unreadable, and does not prune records newer than the snapshot cutoff.
Pruning uses the existing answers lock, so answer publication cannot race cancellation. A
successful `ask` prints only its answer to stdout; timeout exits nonzero with one
clear line. The architect should use `list` to recover pending IDs after recycle.

Codex has no automatic watcher usage sensor; its architect checks native context readings at idle/done and before new work, and sends asks through `codex queue`. Model-window lookup and the complete decision procedure are in [`worker-recycle.md`](../skills/architect/references/worker-recycle.md).

## Gemini workers (agy)

A gemini worker runs the `agy` CLI through `gemini-start.sh`, which starts `herdr agent start --kind agy` with `--model` from settings and `--mode auto`, and takes no extra arguments. It never passes `--dangerously-skip-permissions` (no gate at all), `--remote-agent` or `--remote-control`. agy sends the code a worker reads and the prompts it gets to Google; see docs/settings.md.

`--mode auto` is the safest mode in which a worker can still edit, test and commit. Per agy's docs (not run here), `--mode auto` auto-approves workspace file edits and runs terminal commands inside the terminal sandbox without prompting; a command that would leave the sandbox is automatically evaluated by agy itself, which lets safe, reversible ones through and prompts for irreversible or high-risk actions (destructive Git and file commands), unless the command matches an `allow` rule, which switches off that check. `--mode accept-edits` approves edits but still asks before every terminal command, so a worker cannot run tests or commits unattended; `--sandbox` alone (in the default mode) still asks before every edit. Auto mode turns the terminal sandbox on automatically, so `gemini-start.sh` passes no `--sandbox`. Auto mode needs the terminal sandbox and is not available for business or enterprise accounts; without it agy silently starts in default mode and asks before every edit, so a worker stalls — a gemini worker needs a personal account and a sandbox-capable host.

Merge this `permissions` object into `~/.gemini/antigravity-cli/settings.json`, keeping its other keys (`model`, `trustedWorkspaces`) and any `allow` rule you already set (the block adds none) (`command(prefix)` matches any command whose leading tokens are that prefix):

```json
{
  "permissions": {
    "deny": [
      "command(git push --force)",
      "command(git push -f)",
      "command(git reset --hard)",
      "command(git clean)",
      "command(gh pr merge)"
    ]
  }
}
```

An `allow` rule switches off Auto's check for every command it matches, and matching is by token prefix, so a flag cannot be excluded: `git fetch --upload-pack`, `git log|diff|show --output`, and `gh pr … --body-file` each run any program, write any path, or post a local file. No `allow` rule is shipped, so Auto's check sees every command that leaves the sandbox, including every `orch-signal` call; do not add `command(orch-signal)`, because orch-signal runs the program the caller's environment names (`ORCH_LOADER_PYTHON`, `ORCH_PYTHON3`, `PATH`). If a prompt stalls on an `orch-signal` call, the user approves that one call in the pane; no rule fixes it safely. Add a rule only after a prompt stalls a worker, knowing it removes the check for every form of that command. Do not edit agy's own settings files; a worker asks the user for the rules it needs. For `close_worker.py`'s recovery line to carry the agy conversation id instead of `unknown`, run `herdr integration install antigravity-cli`.

agy reads global skills and rules only from `~/.gemini/config/`, not from `~/.claude` or `~/.agents/skills`. `install.sh` builds the plugin `~/.gemini/config/plugins/ai-agent-orchestration/` with the orch skills, your `~/.claude/CLAUDE.md` as `rules/AGENTS.md`, and each `~/.claude/rules/<name>.md` without `paths:` as an always-on include (see the [file table](installer.md#-files-linked-into-claude)), so a gemini worker gets the orch skills and your global rules in every repo. The plugin's `hooks.json` runs the [Bash guard](bash-guard.md) on every agy shell command and on text sent to a running command, so its rules apply to gemini workers too; agy 1.5.18 runs it from this plugin layout (a live run). `gemini-start.sh` refuses to start while `plugin.json`, `rules/AGENTS.md` or `hooks.json` is missing or dangling; run `install.sh`.

Before the start, `gemini-start.sh` adds the resolved path of the pane's folder to `trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json` (agy matches the exact path), so agy shows no trust prompt. It changes no other value, and refuses to start when that file is not a JSON object or `trustedWorkspaces` is not a list.

Two things are untested and stay so until a live agy worker runs: `orch-signal` from inside agy's terminal sandbox is untested, so the first gemini worker runs `orch-signal heartbeat --new-job` and `orch-signal done` as its first check; and agy loading the plugin's skills and rules (it follows the links and expands the includes) is untested, so the first gemini worker lists its skills and quotes one line of each rule file.
