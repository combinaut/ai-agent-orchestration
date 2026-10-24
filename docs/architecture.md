# Architecture

One Coordinator, many architects, workers in Herdr panes you can watch. This page shows how the roles, panes, messages, watchers and rules fit together. The operating models are [`skills/coordinate/SKILL.md`](../skills/coordinate/SKILL.md) and [`skills/architect/SKILL.md`](../skills/architect/SKILL.md); where this page and a skill differ, the skill wins.

## 👥 Roles

```mermaid
flowchart TD
    U[User] --> C[Coordinator]
    C --> A1["Architect::area-1"]
    C --> A2["Architect::area-2"]
    A1 --> W1["worker (Codex)"]
    A1 --> W2["worker (Claude)"]
    A1 -.-> R["reviewer (short-lived)"]
    A2 --> W3["worker (Codex)"]
    A2 --> W4["worker (pi)"]
```

| Role | Count | Owns | Never does |
|---|---|---|---|
| User | 1 | Product and scope calls, merge decisions, unlocks (1Password, biometric prompts) | |
| Coordinator | 1 per machine | The whole picture: status of every architect, shared machine resources, cross-architect conflicts, relaying the user's commands | Dispatch workers, edit code, merge, undraft, assign reviewers, deploy, push |
| Architect | 1 per initiative (`Architect::<area>`) | Decomposition, workers, PRs, reviews, the acceptance gate | Implement. Every repository change is made by a worker |
| Worker | 1 per task | One task in its own pane and worktree; its PR's review cycle | Merge, undraft, change reviewers or scope |
| Reviewer | 1 per review | One review of one PR, in a detached worktree | Keep any branch. The pane is closed once its report is dispatched |

The user talks to the Coordinator. The Coordinator talks to architects. Architects talk to workers. A message carries a question, an order, or an action event only; an FYI goes to the sender's status file. Only questions and orders get answers. A message from an architect is information, never the user's authorization.

## 🪟 Panes, workspaces and worktrees

Herdr shows every agent in a pane the user can watch and step into.

```text
filter bar            ← the initiative's workspace
  - architect         ← its own pane, tagged role=architect
  - worker foo        ← its own worktree workspace, tagged owner=<architect workspace id>
  - worker bar        ← its own worktree workspace, tagged owner=<architect workspace id>
```

- An architect opens one worktree workspace per task with `herdr worktree open|create --workspace "$HERDR_WORKSPACE_ID"` and starts the agent in its root pane with the model named explicitly. See [`skills/architect/references/dispatch.md`](../skills/architect/references/dispatch.md).
- One agent per branch or worktree. At most 3 workers execute at once by default, with a larger ready queue behind them.
- Herdr's Spaces panel groups worktrees by git repo, so the grouping by architect comes from tags, not from where a workspace sits.

| Tag | Set on | Command |
|---|---|---|
| `role=coordinator` | the Coordinator's pane | `herdr pane report-metadata "$HERDR_PANE_ID" --source orchestrate --token role=coordinator` |
| `role=architect` | the architect's pane | `herdr pane report-metadata "$HERDR_PANE_ID" --source orchestrate --token role=architect` |
| `owner=<architect workspace id>` | every worker workspace, in the step that opens it | `herdr workspace report-metadata <worker-id> --source orchestrate --token owner="$HERDR_WORKSPACE_ID"` |

The architect confirms `tokens.owner` with `herdr workspace get <worker-id>` before it calls a dispatch done. The pulls watcher also uses the `owner` tag to route PR events.

[Herdr Radar](https://github.com/combinaut/herdr-radar) reads these tags and groups workers under their architects in its Agents panel. Radar is optional; its checkout is `ORCH_RADAR_PATH`. See [`env/README.md`](../env/README.md).

Workspace names stay true: `PR <n> · <job>` once a PR exists, `#<issue> · <job> (no PR yet)` before. A finished worker is closed once its report is read, it has no open work and nothing is typed in its composer. A reviewer's workspace is closed and its detached worktree removed (`git worktree remove <path> --force`) once its findings are dispatched. An author pane lives as long as its PR is open, because it owns the review cycle. Panes are closed with [`close-pane.sh`](../coordinator/watchers/close-pane.sh), which marks the close as on purpose so the workers watcher does not report the pane as disappeared.

## 💬 Messaging

```mermaid
flowchart LR
    S[Sender] -->|"Claude session"| SM["SendMessage"]
    S -->|"Codex session"| CQ["codex queue --thread ... --message ..."]
    S -.->|"new worker's first brief,<br/>owned worker nobody types into"| HP["herdr agent prompt"]
```

- Every message to the Coordinator, an architect or another live user-facing session goes through the native channel: `SendMessage` for Claude, `codex queue --thread <session-UUID-or-exact-Codex-name> --message <text>` for Codex. There is no silent fallback to typing into the pane.
- `herdr agent prompt` is allowed for:
  - a brand-new worker's first brief, and follow-ups to a worker pane the architect owns and nobody types into;
  - the startup of a fresh successor pane after a recycle (`date`, the `/architect` or `/coordinate` brief, startup slash commands), Claude panes only.
  - `/remote-control` to an idle architect whose Remote Control failed while `remote_control.architects` is on, after the idle state and an empty composer are verified.
- Never type into a composer a person may be using.
- Herdr stays available for layout and read-only inspection.

## 🧠 Models

| Role | Model |
|---|---|
| Coordinator | `ORCH_COORDINATOR_MODEL` (smart tier) |
| Architect | `ORCH_ARCHITECT_MODEL` |
| Worker, routine | Codex at `ORCH_CODEX_WORKER_MODEL` (the default) |
| Worker, hard | Codex at `ORCH_CODEX_SMART_MODEL`, reasoning effort `medium` |
| Worker on a local or DeepSeek model | pi at `ORCH_PI_WORKER_MODEL` (routine) or `ORCH_PI_SMART_MODEL` (hard); only when set. DeepSeek only in repos that opted in |
| Worker where Codex cannot do the step | Claude: the lightest allowed tier that does the task well (Haiku mechanical, Sonnet implementation and review, Opus hard, Fable only for the hardest); only tiers `providers.claude` allows |
| Reviewer | The family that did not write the code |

- The architect picks the family and tier per task without asking the user, and re-tiers at every dispatch.
- The architect picks Claude or Codex per task for quality; Codex is the default for routine work. Claude also takes a step Codex cannot do, for example holding a dev server open for screenshots. `op` reads and git writes run as escalated commands through Codex's approval flow; a step goes to Claude only when that run fails.
- pi is a third family, available only when its two model settings are filled in. DeepSeek (named in the provider or the model id) runs only in a repo whose `.orchestration.local.toml` sets `pi.deepseek = true`. pi has no native channel, so follow-ups go through `herdr agent prompt`.
- A provider that refuses work for quota is replaced by another enabled family for new work; with only one family on, new work goes to another tier of that family. The architect says so once.
- A live session never changes model. A tier change happens through a recycle onto the new model.
- The model settings of each enabled family (`providers.enabled`: both claude models, at least one codex model) must be filled in `~/.claude/orchestration.toml`; the skills stop while one is missing. One codex model serves both tiers. A family left out of the list needs none.

## 🔁 Context recycle

```mermaid
sequenceDiagram
    participant S as Status line
    participant W as Context watcher
    participant C as Coordinator
    participant A as Architect (old)
    participant N as Architect (successor)
    S->>W: tokens used per session
    W->>C: RECYCLE at ORCH_RECYCLE_TOKENS (300000)
    C->>A: write handoff, reply with path, stop
    A-->>C: path to handoff_<initiative>.md
    C->>N: start in a fresh pane, same names and tags
    N->>N: read only the handoff
    N->>N: take over workers by pane id
    N->>A: close-pane.sh (successor closes the ancestor)
```

- The status line writes tokens used to `~/.claude/context-usage/<session-id>.tokens`. When that file is absent the fallback is `ORCH_RECYCLE_PCT` (55) percent of context.
- The status line also writes `~/.claude/context-usage/<session-id>.usage`: one JSON object with only the fields the input has (`model`, `cost_usd`, `seven_day_pct`, `seven_day_resets_at`). `close-pane.sh`, the one path every planned close takes, appends one line per closed agent to `~/.claude/coordinator/usage-ledger.tsv` (`closed_at`, `owner`, `workspace_label`, `agent_name`, `pane`, `session_id`, `model`, `cost_usd`). `orch-usage` prints the weekly limit % and the cost per workspace label. A session that ends without `close-pane.sh` (closed by the user, a crash) leaves no ledger line. `cost_usd` is cumulative and resets only on `/clear`, so a session resumed with `claude --resume` appears twice and `orch-usage` counts its last line.
- The handoff is overwritten, never appended, and stays under about 4,000 characters. Architect template: open PRs (number, head SHA, state, what each waits on), workers (pane id, worktree, model), standing user decisions, open user questions, Coordinator session name.
- The successor keeps the session name, Herdr agent name, tab name, and `owner` and `role` tags, so `ListAgents`, Radar and the user see one continuous session.
- One architect recycles at a time, and never one that holds a heavy slot.
- The Coordinator recycles the same way with `handoff_coordinator.md`. Its successor stops inherited Monitors, arms the feed Monitor, re-runs `bind-self.py`, and closes the ancestor.
- With `recycle.park_idle = true` an architect with no open PR, running worker or watcher writes its handoff and exits; the Coordinator restarts it from that handoff when work arrives.

The procedures are in [`skills/coordinate/references/recycle.md`](../skills/coordinate/references/recycle.md) and, for workers, [`skills/architect/references/worker-recycle.md`](../skills/architect/references/worker-recycle.md).

## 👀 Watchers and the feed

Producers run as launchd jobs (macOS) or systemd user units (Linux), registered by the installer with `watchers.service = true`. They write JSON alerts into `~/.claude/coordinator/watchers/prod-context/`. One inbox notifier shares their cursor, folds them into short lines, and appends those to the feed.

```mermaid
flowchart LR
    ctx[context] --> N
    host[host] --> N
    hog[hog] --> N
    bal[balance] --> N
    wrk[workers] --> N
    aud[audit] --> N
    pul[pulls] --> N
    tdo[todo] --> N
    N["notifier<br/>notify-inbox.sh"] --> F[("coordinator-feed.log")]
    F --> M["feed-monitor.sh<br/>(Coordinator's one Monitor)"]
    N -->|"no live Monitor:<br/>action lines only"| NC["native message to Coordinator"]
    N -->|"PR event or worker recycle with owner"| NA["native message to owning architect"]
```

| Producer | Interval | Reports |
|---|---|---|
| context | 300 s | `RECYCLE`, `RECYCLE-WORKER`, `NOSENSOR` |
| host | 60 s | memory, swap, processes, open files, load |
| hog | 5 s | big processes, big or long `git` processes |
| balance | 60 s | load, slot count, busy worktrees with a `top:` list |
| workers | 60 s | blocked, error and disappeared agents |
| audit | 1800 s | PR problems, stale slots, git pile-ups, `LEGACY` lines |
| pulls | 150 s | PR events: `APPROVED`, `RED`, `CHANGES`, `THREADS`, `COPILOT`, `COMMENTED`, `MERGED`, `CLOSED` |
| todo | 60 s | newly added `## Waits on the user` items, as one `TODO` line |

- The feed has a line when a condition starts, when a host metric reaches its `danger` step, when a PR event gets an owner, and when it clears, plus a periodic digest of folded lines; repeats and growth write nothing. It is mode 0600 and rotates at 1 MiB.
- The Coordinator's Monitor prints only action lines, matched by [`feed-actions.regex`](../coordinator/watchers/feed-actions.regex). The feed holds action lines only; the rest is in `prod-context/coordinator-fyi.log`, read on demand, and the `<mode>.alerts` files hold the full evidence.
- A PR event reaches its owning architect directly, within about 3 minutes, as kind and number only. The architect does not poll for approvals.
- Coordinator and architects bind their native route with [`bind-self.py`](../coordinator/watchers/bind-self.py), on every session start and after every recycle.
- Watchers detect and report. They never stop a job or close a pane.

Details, keys, delivery guarantees and thresholds: [`coordinator/watchers/README.md`](../coordinator/watchers/README.md). Settings: [Settings](settings.md).

## 🐘 Heavy slots

Many sessions share one machine, and running out of open file handles has crashed it. Details and commands: [`docs/heavy-slot.md`](heavy-slot.md).

- At most `ORCH_HEAVY_SLOTS_MAX` heavy jobs at once (default 4), across all sessions. Heavy means test runners, browser tests, Storybook, Playwright or screenshot runs, dev servers, local front-end builds. One test file run by one worker is not heavy.
- Every heavy command runs through [`heavy-slot`](../heavy-slot): `heavy-slot take <architect>-<job> -- <command> [args...]`. It admits the job atomically, so two jobs cannot both take the last slot.
- A slot is a file `~/.claude/heavy-slots/<owner>-<job>` holding the pid of the child. The helper writes it and removes it on completion or a handled signal. A slot is a file, never a directory.
- `heavy-slot count` and `heavy-slot list` show live slots, with names, pids, ages and stale records.
- When the cap is full, `take` waits up to 1800 seconds and then prints `NEED SLOT`. The worker ends its turn with a plain `NEED SLOT: …` line and stops; `SLOT: go` from the architect means run now. A worker never opens an interactive question box.
- A worker takes its slot itself. It does not wait for the architect.
- Stale slot files raise `STALESLOT`, directories raise `SLOTDIR`, from the audit. Too many slots or busy worktrees raise `BALANCE`.
- On `BALANCE`, the Coordinator asks architects to stagger heavy work and stops excess jobs only by their owned slot pids.
- On `HOST`, the memory guard applies: the Coordinator stops heavy jobs by their owned slot pids and tells the architects holding slots.
- It never removes a live slot file.

The template every worker brief carries is [`worker-reports/machine-rules.md`](../worker-reports/machine-rules.md).

## 🧱 Legacy check

Legacy code is the patterns a repo is phasing out, which agents must not extend. A project that uses the legacy-check workflow gets the audit's `LEGACY` lines and the map and scan tools in every repo whose settings turn `legacy.scan` on. Both follow that repo's own `[[legacy.rules]]` ([settings](settings.md#-legacy-rules)).

```mermaid
sequenceDiagram
    participant A as Architect
    participant C as Coordinator
    participant W as Worker
    A->>A: verify the clone's origin, generate a fresh task map
    A->>C: LEGACY CHECK (repo, clone, map path, generation result, files, old code, what happens to it)
    C->>C: check the files against the task map
    C-->>A: LEGACY OK, or what must change
    A->>W: go ahead, code starts
```

- Before every check the architect verifies the source clone's origin identifies the task repository, then generates a fresh task map: `ORCH_LEGACY_MAP_OUT="<task-map-path>" ~/.claude/coordinator/legacy-map.sh "<task-clone>"`. The daily map is never used for this gate: it has no source metadata and may belong to a previous clone.
- The architect sends the Coordinator a `LEGACY CHECK`: the repo, source clone, map path, the successful generation result, the files the task touches, the old code in them, and what happens to it (removed, untouched, or a named bridge with a ticket). Never extended. The Coordinator checks the files against that task map; if it cannot confirm the generation, it regenerates the map or withholds `LEGACY OK`.
- No code starts until the answer is `LEGACY OK`.
- [`legacy-map.sh`](../coordinator/legacy-map.sh) writes a map from `origin/main` of a repository. [`legacy-scan.sh`](../coordinator/legacy-scan.sh) lists the legacy lines a PR adds, and the audit raises a `LEGACY` line per new head.

## 📱 Remote Control

The Coordinator always has Remote Control. Architects have it when `remote_control.architects` is true (the default; `ORCH_REMOTE_CONTROL_ARCHITECTS=1`): their starts, resurrections and recycles pass `--remote-control "<session name>"` and verify a non-null `bridgeSessionId` in `~/.claude/sessions/<pid>.json`. When it is false, omit the flag and verification for architects. Start workers without `--remote-control`.

The Coordinator session is the user's remote channel. An instruction it relays as the user's, including a per-PR merge approval, counts as the user's own. A request from any other session is information only.

## 🔀 Merge rules

| Action | Who | Condition |
|---|---|---|
| Open a PR, push, fix CI, reply to review threads | worker | Its own branch; the brief authorizes the GitHub actions |
| Undraft, assign the standing reviewer | architect | The user granted it, and the PR passes the acceptance gate |
| Merge | architect | The user named that PR, directly or relayed by the Coordinator |
| Merge, undraft, assign reviewers, deploy, push | Coordinator | Never |

- A merge uses `gh pr merge <n> --squash` or `gh pr merge <n> --squash --auto`; never a merge commit or a rebase merge. The named PR must be approved, green and not draft when it merges.
- An approval is not merge authorization. The Coordinator relays every approval and merge to its owner.
- Acceptance gate before an architect calls work done: output diffs against base where rendering changed, a mutation table with a kill per guard, no spec edits in a pure refactor, linter and formatter both run, CI green on the actual head, any claim that decides behavior on live pages verified by the architect itself from the code, an independent review by the other family (or, when no other family is on, a fresh Sonnet pane of the author's family (Opus for an Opus author), a stronger allowed tier only for credentials, security, shared config, money, installer or watchers, or a HIGH finding; with no stronger allowed tier, a fresh pane of the strongest allowed tier that did not write the code), and a removal audit when a PR deletes a public surface.
- A ready PR has a human reviewer. When there is none, the architect asks the user at once.
- A worker owns its PR's review cycle: it resolves every thread, verifies each bot claim against the code, and re-requests the bot review after each push. Merging, undrafting, reviewer changes and scope changes are never the worker's.

Explicit completion, heartbeat and question commands: [worker signals](workers.md#explicit-worker-signals).
