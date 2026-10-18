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

Run a **Coordinator**, several **architects** and many **workers** (Claude Code or Codex) as agents in [Herdr](https://herdr.dev) panes that you can watch. The installer puts the skills, watchers, hooks and status line into `~/.claude`. It starts no Coordinator, architect or worker; you open those sessions yourself. With `--env herdr` it also starts the Radar daemon, and with `ORCH_WATCHERS_SERVICE="1"` it starts the watcher services.

## What you need

- macOS or Linux, Bash 3.2 or newer
- [Claude Code](https://claude.com/claude-code) with the `ListAgents`, `SendMessage` and `Monitor` tools
- [Herdr](https://herdr.dev) 0.9 or newer (`install.sh --env herdr` installs it)
- `gh` (logged in with `gh auth login`), `git`, `jq`, `python3`; `curl` for `--env herdr`
- Optional: Codex CLI (architects pick Claude or Codex per task); `rg` for the legacy map

## Install and update

The installer is one command. It needs no clone. Install, update and repair are the same command; run it as often as you like.

```bash
/bin/bash -c 's=$(gh api repos/combinaut/ai-agent-orchestration/contents/install.sh -H "Accept: application/vnd.github.raw") && [ -n "$s" ] && exec /bin/bash -c "$s" install.sh "$@"; echo "Download failed. Fix: install gh (https://cli.github.com) && gh auth login" >&2; exit 1' _
```

To also install Herdr and the Herdr Radar plugin, add `--env herdr` after the final `_`.

It clones this repo to `~/.claude/ai-agent-orchestration`, links the files into `~/.claude`, and adds the hooks and status line to `~/.claude/settings.json`. If it prints `PATH setup`, add the line it shows to your shell startup file and open a new shell.

The installer never edits your `~/.claude/CLAUDE.md`. When its sections are missing it tells you to paste [`snippets/CLAUDE.md.snippet`](snippets/CLAUDE.md.snippet).

### First run

The first run copies [`config/orchestration.env.example`](config/orchestration.env.example) to `~/.claude/orchestration.env`. On a terminal it asks only for the six keys in the table below, when they are empty or still a `<…>` placeholder, and offers a default it found on the machine. Enter takes the default, `-` leaves the key empty. A key that has a value is never asked or changed, and no other key is asked. Without a terminal it asks nothing and lists the keys to fill. Set at least:

| Key | Set it to |
|---|---|
| `ORCH_PR_REPOS` | the `owner/name` repos to watch, space separated (for example `acme/webapp`) |
| `ORCH_PR_AUTHOR` | your GitHub login (`@me` works) |
| `ORCH_CODEX_WORKER_MODEL`, `ORCH_CODEX_SMART_MODEL` | a Codex model name each. Fill both even without Codex: `/coordinate` and `/architect` stop while either is still a placeholder |
| `ORCH_HERDR_BIN`, `ORCH_HERDR_SOCKET` | Herdr's executable and socket; needed for the watcher services |

### Update

Run the install command again. At session start a hook compares the clone with origin and prints `ai-agent-orchestration setup out of date, run install.sh` when you are behind. Nothing updates on its own.

The clone is never updated over local work. With uncommitted edits or commits that are not on origin the installer prints `REFUSED: … has local changes` and stops. It never stashes or resets. Put your changes in a PR, then run it again.

More: [`docs/installer.md`](docs/installer.md) has every installer step, the file table and a troubleshooting table. [`env/README.md`](env/README.md) covers the env adapters.

## Settings

Everything lives in `~/.claude/orchestration.env`, a bash file of `KEY="value"` lines. A variable exported in the environment wins over the file only for the keys the scripts keep from the environment. `heavy-slot` keeps only `ORCH_HEAVY_SLOTS_MAX`, so a file value overrides an exported `ORCH_HEAVY_SLOTS_DIR` or `ORCH_HEAVY_SLOTS_KILL_GRACE`; `ORCH_CLOSED_DIR` is not kept either, so a file value overrides it. Until the code is fixed ([#60](https://github.com/combinaut/ai-agent-orchestration/issues/60)), set these keys in the file and not in the environment. `ORCH_CONFIG=/path` selects another file; a selected path that is not a regular file is an error. A script that lacks a setting or a command prints `ERROR: …` and exits with status 2. The example file has a comment for every key.

| Setting | Default | Meaning |
|---|---|---|
| **Audit** | | |
| `ORCH_PR_REPOS` | none, **required** | `owner/name` list, space separated |
| `ORCH_PR_AUTHOR` | none, **required** | GitHub login whose open PRs are watched |
| `ORCH_PR_SINCE` | empty | Only PRs updated on or after `YYYY-MM-DD` |
| `ORCH_MERGE_GATE_CHECKS` | empty | Check names separated by `\|`; an approved PR on a non-default base reports `full CI ran` or `full CI not run` |
| `ORCH_BOT_REVIEWER_LOGIN` | `copilot-pull-request-reviewer` | Bot whose "Needs a closer look" review raises a `COPILOT` line |
| `ORCH_GIT_WATCH_REPOS` | empty | Local clone paths checked for `PACKS` and `WORKTREES` pile-ups |
| `ORCH_PACKS_MAX`, `ORCH_WORKTREES_MAX` | `50`, `40` | Pile-up limits |
| `ORCH_AUDIT_PAGE_PRS`, `ORCH_AUDIT_PAGE_NODES` | `80`, `100` | GitHub query page sizes; a cut-off result prints `TRUNCATED` |
| `ORCH_LEGACY_REPOS` | empty | Repos whose open PRs are scanned for a `LEGACY` line; empty means no scan |
| `ORCH_LEGACY_SCANS_MAX`, `ORCH_LEGACY_READS_MAX` | `10`, `20` | PRs scanned per pass; file reads per PR scan |
| `ORCH_LEGACY_EXTRA_PATTERN` | empty | Regex alternation appended to the built-in jQuery/ujs patterns of the legacy scan, for project names such as `old-widget\|legacy-grid` |
| `ORCH_LEGACY_EXEMPT_PATHS` | empty | Space-separated paths the built-in scan skips, in addition to the two ratchet guard files under `scripts/` |
| `ORCH_LEGACY_MAP_REPO` | empty | Clone the daily legacy-map job reads; empty picks the `ORCH_GIT_WATCH_REPOS` clone named like an `ORCH_LEGACY_REPOS` repo |
| `ORCH_LEGACY_MAP_OUT` | `~/.claude/coordinator/legacy-map.tsv` | Where `legacy-map.sh` writes. Environment only: the script does not read the config file, and the daily job does not use it. The skills set it for a per-task map |
| **Models and recycling** | | |
| `ORCH_COORDINATOR_MODEL`, `ORCH_ARCHITECT_MODEL` | `claude-opus-5-5` | Models for the Coordinator and architects |
| `ORCH_CODEX_WORKER_MODEL`, `ORCH_CODEX_SMART_MODEL` | placeholders | Codex models for routine and hard work; replace the placeholders |
| `ORCH_RECYCLE_TOKENS` | `300000` | Tokens used at which `RECYCLE` fires |
| `ORCH_RECYCLE_PCT` | `55` | Context percentage used when the token sensor is absent |
| `ORCH_PARK_IDLE` | `0` | `1` parks architects that have no open PR, worker or watcher |
| **Heavy jobs** | | |
| `ORCH_HEAVY_SLOTS_MAX` | `4` | Most heavy jobs at once on the machine |
| `ORCH_HEAVY_SLOTS_DIR` | `~/.claude/heavy-slots` | Slot directory; empty means the default |
| `ORCH_HEAVY_SLOTS_KILL_GRACE` | `10` | Seconds a stopped job gets before it is killed |
| `ORCH_FOCUS_SKIP_ARCHITECTS` | empty | Comma-separated architect names left out of focus reports |
| `ORCH_FOCUS_PATTERN` | empty | Case-insensitive regex matched against worker workspace labels; the `FOCUS` line counts matches (`focus_workers_live`) and lists architects with none (`no_focus_worker`). Empty turns the check off |
| `ORCH_FOCUS_ARCHITECT` | empty | Architect name never listed in `no_focus_worker` |
| **Watchers and services** | | |
| `ORCH_WATCHERS_SERVICE` | `0` | `1` registers the watchers as launchd or systemd user services; needs `ORCH_HERDR_BIN` and `ORCH_HERDR_SOCKET` |
| `ORCH_HERDR_BIN`, `ORCH_HERDR_SOCKET` | empty | Absolute path of Herdr and of its socket |
| `ORCH_PATH` | `$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` | `PATH` given to services and to the Chrome host |
| `ORCH_PYTHON3` | `python3` | Interpreter for the watcher scripts and services |
| `ORCH_WATCHERS_PR_SCOPE` | empty | GitHub search scope for the PR watchers, for example `org:acme` |
| `ORCH_WATCHERS_WORKER_MODEL`, `ORCH_WATCHERS_PRIMARY_REPO` | empty | Model label and primary `owner/name` repo in dispatch records |
| `ORCH_CLOSED_DIR` | `~/.claude/coordinator/watchers/prod-context/closed` | Where `close-pane.sh` marks a pane as closed on purpose. The workers watcher reads only `<state-dir>/closed`, so keep the default, or give the workers watcher a `--state-dir` that is the parent of this folder (`ORCH_CLOSED_DIR=/tmp/state/closed` needs `--state-dir /tmp/state`) |
| `ORCH_USER_TODO_STATUS_DIR`, `ORCH_USER_TODO_OUTPUT`, `ORCH_USER_TODO_STATE` | empty | To-do paths; empty means `~/.claude/coordinator/{status,user-todo.md,user-todo.state.json}` |
| `ORCH_HOST_PRESSURE_MIN_PCT`, `ORCH_HOST_SWAP_MAX_MB`, `ORCH_HOST_PROCS_MAX`, `ORCH_HOST_FILES_MAX`, `ORCH_HOST_LOAD_MAX` | `20`, `1024`, `1300`, `150000`, `40` | Host watcher limits |
| `ORCH_HOG_GB`, `ORCH_HOG_GIT_GB`, `ORCH_HOG_GIT_AGE_S` | `6`, `2`, `180` | Hog watcher: process size, `git` size, `git` age in seconds |
| `ORCH_ALERT_ESCALATE_RATIO`, `ORCH_ALERT_DIGEST_S`, `ORCH_ALERT_QUIET_S` | `1.5`, `1800`, `1800` | Feed: ratio between severity steps; seconds between digests; seconds a condition stays clear before its clear line |
| **Install** | | |
| `ORCH_REPO_PATH` | `~/.claude/ai-agent-orchestration` | The clone linked into `~/.claude` |
| `ORCH_REPO_URL` | empty | Git URL the installer clones instead of using `gh repo clone` (for forks and tests). Environment only: set it on the install command line, not in the file |
| `ORCH_ENV` | empty | Env adapter to run without `--env`; `none` skips |
| `ORCH_RADAR_PATH` | `~/.claude/herdr-radar` | Herdr Radar checkout |
| `ORCH_LAUNCHD_WAIT_TRIES`, `ORCH_LAUNCHD_WAIT_SLEEP` | `40`, `0.5` | macOS: how often and how long (seconds) the installer waits for launchd to unload a job. Environment only |
| **Bash guard and Chrome PR tabs** | | |
| `ORCH_BIG_REPO_REMOTES` | empty | `owner/repo` remotes where broad Git reads are blocked; empty turns that rule off |
| `ORCH_PR_TABS_ORG`, `ORCH_PR_TABS_DEFAULT_REPO` | empty | Organization to manage; repo for bare PR numbers |
| `ORCH_PR_TABS_OPEN_ALL` | empty | GitHub search behind the **Open all** button |
| `ORCH_PR_TABS_TOPICS` | empty | JSON array of tab-group rules |

`ORCH_V2_LAUNCHD`, `ORCH_V2_PR_SCOPE`, `ORCH_V2_WORKER_MODEL` and `ORCH_V2_PRIMARY_REPO` are old names. The installer tells you to rename them to `ORCH_WATCHERS_SERVICE`, `ORCH_WATCHERS_PR_SCOPE`, `ORCH_WATCHERS_WORKER_MODEL` and `ORCH_WATCHERS_PRIMARY_REPO`.

## Use it

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

### Workers, recycles and handoffs

- A **worker** does one task in its own pane and worktree, and writes a report to the path in its brief. You can watch it and type into its pane.
- Every worker carries an `owner` tag naming its architect. Herdr groups panes by repo, so without the tag a worker shows under the wrong architect. Radar draws the Coordinator → architect → worker tree from the tags.
- A **recycle** replaces a session that has used `ORCH_RECYCLE_TOKENS` tokens. The session writes a bounded handoff file to its memory, a fresh session starts from that file, and the watchers keep sending to the new session once it runs `bind-self.py` again. With `ORCH_PARK_IDLE="1"`, an idle architect writes its handoff and exits, and the Coordinator restarts it when work arrives.
- To switch models, recycle onto the new model; a live session is never switched.

How the roles fit together: [`docs/architecture.md`](docs/architecture.md).

## Watchers and services

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
| user-todo | What waits on you (see [The to-do pane](#the-to-do-pane)) |

**With services**: set `ORCH_WATCHERS_SERVICE="1"` (with `ORCH_HERDR_BIN` and `ORCH_HERDR_SOCKET`) and run the installer again. It registers the watchers (audit and pulls only when `ORCH_PR_REPOS` and `ORCH_PR_AUTHOR` are set in the config file, not just exported) with launchd (macOS) or `systemd --user` (Linux) to run now and at login. Back to `0` removes them. Off by default.

**Without services**: run the loops yourself in the background, and the notifier too, because it fills the Coordinator's feed. The commands are in [Run the watchers without services](coordinator/watchers/README.md#run-the-watchers-without-services). Each producer takes `--state-dir`, and `--loop --interval <seconds>` keeps it running. A watcher without `--loop` scans once and exits, which is the quick way to test one:

```bash
ORCH_HOST_FILES_MAX=1 ~/.claude/coordinator/watchers/coord-host-watch.sh --state-dir /tmp/host-check
```

When the host is under pressure, the Coordinator follows the memory guard: on HOST danger it should stop heavy jobs by their slot pids and tell architects that hold slots. On HOST READFAIL it restores readable metrics and keeps heavy jobs paused. On host HEARTBEAT STALE it restores the producer's service and checks current metrics. HOST RECOVERED clears the pressure; it checks the guard before allowing heavy jobs again.

Alert kinds, deadlines, feed delivery and recovery rules are in the [watcher guide](coordinator/watchers/README.md).

### The to-do pane

Architects keep a `## Waits on the user` list in `~/.claude/coordinator/status/<architect>.md`. The user-todo watcher merges those lists into `~/.claude/coordinator/user-todo.md`. To show it in a pane:

```bash
python3 ~/.claude/coordinator/user-todo.py --follow
```

`user-todo.py` without `--follow` writes the file once. In the pane, press a number then Enter (or click) to jump to the item. `n` and `p` change page. A jump removes the item from the list. An empty list reads `Nothing waits on you.`

## Heavy jobs: `heavy-slot`

Many sessions share one machine, and too many test runs, browsers or builds at once can exhaust open files and memory. `heavy-slot` is a shared cap of `ORCH_HEAVY_SLOTS_MAX` jobs.

```bash
heavy-slot take acme-tests -- npm test   # waits up to 1800 s for a free slot, then runs
heavy-slot count                         # slots in use
heavy-slot list                          # names, pids, ages, stale records
```

A slot is a file in `~/.claude/heavy-slots/` that holds the job's pid; `take` removes it when the job ends. If the cap stays full, `take` prints `NEED SLOT`. It exits 73 when the slot directory is not writable (for example in a sandbox); run it again with write access to that directory. Never create slot files by hand, and stop jobs by pid, not with `pkill`. The audit reports `STALESLOT` for a file whose pid is gone. The balance watcher uses the same cap.

## The Bash guard

`hooks/guard-bash.py` runs before every Bash tool call in Claude Code. It reads the command and never runs it. It denies:

- `git push` to `main` or `master` (including `--all`, `--mirror`, `:` and glob refspecs). Push a branch and open a PR. GitHub wiki remotes are allowed.
- AI attribution in commit messages, PR titles and PR bodies, such as `Co-Authored-By: Claude` or "Generated with Claude Code".
- Broad process kills: `pkill`, or `pgrep -f` fed into `kill`. Stop your own pid.
- Broad process kills: `killall` with a dev-server name (`node`, `ruby`, `puma`, `vite`, `rails`, `bundle`, `sidekiq`, `esbuild`, `foreman`, `overmind`, `bin/dev`, `dev-instance`) or with `-m`.
- In a repo that has linked worktrees: `git fetch` or `git pull` with `--depth`, `--deepen`, `--shallow-*` or `--unshallow`, and any `git stash` except `list` and `show`. Both change history or state the worktrees share.
- In repos listed in `ORCH_BIG_REPO_REMOTES`: every `git grep`, even with a path, and bulk `git cat-file --batch`, `--batch-check` or `--batch-all-objects` reads. Use `rg` on the directories you need.

If the guard itself breaks, it lets the command through and logs the error to `~/.claude/hooks/guard-bash.log`. The installer checks after linking that the guard denies a forbidden command.

## Chrome PR tabs

An optional Chrome extension that sorts PR tabs into named groups, jumps to a PR by number, opens your open PRs and closes tabs whose PR is merged or closed. GitHub access goes through your local `gh` login. The installer registers its native host; you load the extension once in `chrome://extensions` (Developer mode → Load unpacked → `chrome/pr-tabs/extension`). Settings and troubleshooting: [`docs/pr-tabs.md`](docs/pr-tabs.md).

## Tests

```bash
tests/run.sh
```

It runs the Bats suites, the guard tests, the watcher tests and the Chrome extension tests in a sandbox that blocks `launchctl`, `systemctl` and `herdr`, so nothing touches your services. You need `bats`, `python3` and `node`; Linux also needs `bubblewrap`. Individual test targets are not supported. Details: [`tests/README.md`](tests/README.md).

## Contributing

Changes go through a pull request against `main`; CI runs ShellCheck and `tests/run.sh`. The Herdr Radar fork is pinned to one commit in `env/herdr/radar.pin`; move the pin in a PR.
