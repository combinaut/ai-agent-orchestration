```text
  ▄▀█ █   ▄▀█ █▀▀ █▀▀ █▄ █ ▀█▀
  █▀█ █   █▀█ █▄█ ██▄ █ ▀█  █

  █▀█ █▀█ █▀▀ █ █ █▀▀ █▀ ▀█▀ █▀█ ▄▀█ ▀█▀ █ █▀█ █▄ █
  █▄█ █▀▄ █▄▄ █▀█ ██▄ ▄█  █  █▀▄ █▀█  █  █ █▄█ █ ▀█

  one Coordinator · many architects · workers in panes you can watch

     Coordinator ─┬─ Architect ─┬─ worker  (Claude)
                  │             └─ worker  (Codex)
                  └─ Architect ─── worker  (Claude)
```

[![Validate](https://github.com/combinaut/ai-agent-orchestration/actions/workflows/validate.yml/badge.svg)](https://github.com/combinaut/ai-agent-orchestration/actions/workflows/validate.yml)
![AI Tooling](https://img.shields.io/badge/AI-Tooling-blueviolet)
![Claude Code · Codex](https://img.shields.io/badge/Claude_Code_·_Codex-orange)
[![Herdr · Radar](https://img.shields.io/badge/Herdr_·_Radar-teal)](https://github.com/combinaut/herdr-radar)
![macOS · Linux](https://img.shields.io/badge/macOS_·_Linux-lightgrey)

Run a team of Claude Code and Codex agents you can watch: one **Coordinator**, **architects** that plan, and **workers** in visible terminal panes. You talk to the Coordinator; it relays your orders to the architects, who split the work and start workers (Claude Code or Codex) in their own git worktrees. [Radar](https://github.com/combinaut/herdr-radar) draws the Coordinator → architect → worker tree from each worker's `owner` tag. One-line install for macOS and Linux.

The installer puts the skills, watchers, hooks and status line into `~/.claude`. It starts no agent; you open those sessions yourself. The panes come from an env adapter ([`env/README.md`](env/README.md) shows how to add one). [Herdr](https://herdr.dev) is the one shipped, so the installer also sets up Herdr and Radar.

## 1. Install

You need macOS or Linux, [Claude Code](https://claude.com/claude-code) with the `ListAgents`, `SendMessage` and `Monitor` tools, `git`, `jq`, Python 3.11 or newer and `curl`. `gh` (logged in with `gh auth login`) is needed for the PR watchers. Codex CLI is optional.

```bash
s=$(curl -fsSL https://raw.githubusercontent.com/combinaut/ai-agent-orchestration/main/install.sh) && bash -c "${s:?empty download}" _
```

It clones this repo to `~/.claude/ai-agent-orchestration`, links the files into `~/.claude`, adds the hooks and status line to `~/.claude/settings.json`, and runs the env adapter, which for `herdr` installs [Herdr](https://herdr.dev) 0.9 or newer with the Radar plugin. Pick another env with `--env <name>` after the final `_`, or skip the env step with `env = "none"` under `[install]` in `~/.claude/orchestration.toml`. If it prints `PATH setup`, add the line it shows to your shell startup file and open a new shell.

The installer never edits your `~/.claude/CLAUDE.md`. When its sections are missing it tells you to paste [`snippets/CLAUDE.md.snippet`](snippets/CLAUDE.md.snippet).

## 2. Set it up

The first run copies [`config/orchestration.toml.example`](config/orchestration.toml.example) to `~/.claude/orchestration.toml`, or converts an existing `~/.claude/orchestration.env`. On a terminal it asks for the keys below while they are empty or a `<…>` placeholder, offering a default it found; Enter takes it, `-` leaves the key empty. Without a terminal it lists the keys to fill. Set at least:

| Key (prompt) | Set it to |
|---|---|
| `[pr] repos` (`ORCH_PR_REPOS`) | the `owner/name` repos to watch, for example `["acme/webapp"]` |
| `[pr] author` (`ORCH_PR_AUTHOR`) | your GitHub login (`"@me"` works) |
| `[models] codex_worker`, `codex_smart` (`ORCH_CODEX_WORKER_MODEL`, `ORCH_CODEX_SMART_MODEL`) | a Codex model name each. Fill both even without Codex: `/coordinate` and `/architect` stop while either is a placeholder |
| `[herdr] bin`, `socket` (`ORCH_HERDR_BIN`, `ORCH_HERDR_SOCKET`) | Herdr's executable and socket; needed for the watcher services |

Settings for one repo (big-repo guard, legacy scan, focus) go in that repo's `.orchestration.toml` (committed, shared), or `.orchestration.local.toml` for your own (keep it out of git). An exported `ORCH_*` variable beats both files. Restart Claude Code afterwards so it loads the hooks and skills. Every key: [`docs/settings.md`](docs/settings.md). Watcher services: [`docs/watchers.md`](docs/watchers.md).

To update, run the install command again. At session start a hook prints `ai-agent-orchestration setup out of date, run install.sh` when you are behind. Nothing updates on its own. The clone is never updated over local work: with uncommitted edits or commits that are not on origin the installer prints `REFUSED: … has local changes` and stops. It never stashes or resets. Put your changes in a PR, then run it again.

## 3. Use it

### Start the Coordinator

There is one Coordinator per machine. In a Herdr pane of its own, start Claude Code and run:

```text
/coordinate
```

It lists the `Architect::<area>` sessions, introduces itself, tags its pane `role=coordinator`, binds itself as the inbox target (`bind-self.py`, again after every recycle) and arms one Monitor on `~/.claude/coordinator/watchers/feed-monitor.sh`. You talk to the Coordinator. It relays your orders to architects and gives you one overview. It never merges, undrafts, assigns reviewers, deploys or pushes. Details: [`skills/coordinate/SKILL.md`](skills/coordinate/SKILL.md).

### Start an architect

Open a session named `Architect::<area>`, run `/architect`, and give it the initiative:

```text
/architect Move the acme/webapp billing page to the new API
```

The architect splits the work into tasks. It does not edit code. For each task it opens a Herdr workspace with its own git worktree, starts a worker (Claude or Codex, picked per task), tags the worker with `owner`, and pastes `~/.claude/worker-reports/machine-rules.md` into the brief. Edit the project-specific list at the end of that file first. The architect verifies every report, and merges only a PR you name. Details: [`skills/architect/SKILL.md`](skills/architect/SKILL.md).

Workers, recycles and handoffs: [`docs/workers.md`](docs/workers.md).

### The to-do pane

Architects keep a `## Waits on the user` list in `~/.claude/coordinator/status/<architect>.md`. The user-todo watcher merges those lists into `~/.claude/coordinator/user-todo.md`. To show it in a pane:

```bash
python3 ~/.claude/coordinator/user-todo.py --follow
```

`user-todo.py` without `--follow` writes the file once. In the pane, press a number then Enter (or click) to jump to the item. `n` and `p` change page. A jump removes the item from the list. An empty list reads `Nothing waits on you.`

### Heavy jobs

Many sessions share one machine. `heavy-slot` caps how many test runs, browsers or builds run at once (`ORCH_HEAVY_SLOTS_MAX`):

```bash
heavy-slot take acme-tests -- npm test   # waits up to 1800 s for a free slot, then runs
heavy-slot count                         # slots in use
heavy-slot list                          # names, pids, ages, stale records
```

If the cap stays full, `take` prints `NEED SLOT`. Details: [`docs/heavy-slot.md`](docs/heavy-slot.md).

## Docs

- [`docs/architecture.md`](docs/architecture.md): how the Coordinator, architects, workers and watchers fit together.
- [`docs/installer.md`](docs/installer.md): every installer step, the file table and a troubleshooting table.
- [`docs/settings.md`](docs/settings.md): every setting in `~/.claude/orchestration.toml` and in the repo files.
- [`docs/watchers.md`](docs/watchers.md): the watchers, running them with or without services, and the host-pressure response.
- [`docs/workers.md`](docs/workers.md): workers, recycles, handoffs and owner tags.
- [`docs/heavy-slot.md`](docs/heavy-slot.md): the shared cap on heavy jobs and how slots work.
- [`docs/bash-guard.md`](docs/bash-guard.md): what the Bash guard denies and where it logs.
- [`docs/pr-tabs.md`](docs/pr-tabs.md): the Chrome PR tabs extension: install, use and troubleshooting.
- [`env/README.md`](env/README.md): the env adapters (`herdr` and how to add one).
- [`docs/contributing.md`](docs/contributing.md): running the tests and sending changes.
