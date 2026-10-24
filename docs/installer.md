# Installer details

How `install.sh` works, what it refuses to do, and how to fix its errors. The short version is in the [README](../README.md#-1-install).

## 🛑 Local edits and "REFUSED"

The clone and the Radar checkout are never updated over local work. With uncommitted edits or commits that are not on origin, the installer prints `REFUSED: … has local changes`, lists them, and stops. It never stashes or resets.

> ⚠️ Changes go through a PR: commit them to a branch and open a PR against `main`. After it merges, run the install command again.

## ✅ Requirements

macOS or Linux, Bash 3.2 or newer; `git`, `jq`, `python3`, `curl`; `gh` for the PR watchers; `rg` (ripgrep) for the legacy scan and map. Optional: Codex CLI (architects pick Claude or Codex per task).

## 🚀 The one-line entry: get.sh

`get.sh` asks `install.sh --print-repo-path` for the clone folder (`ORCH_INSTALL_DIR` overrides it), clones the repo if the folder is missing, then runs that clone's `install.sh` with your arguments, which fast-forwards the clone. It stops with a message for an unsupported OS, Bash older than 3.2, missing `git` or `curl`, a folder that is not a git clone, a clone whose `origin` is another repo, and a clone with uncommitted edits. It never uses `sudo`. Run it as `curl -fsSL <raw get.sh URL> | bash`, with options as `... | bash -s -- --env herdr`. The script's last line is an unclosed brace until it is complete, so a cut-off download runs nothing. When the script arrives on stdin and `/dev/tty` can be opened, `install.sh` reads its answers from the terminal; a redirected stdin (`bash get.sh < answers`) or no terminal keeps stdin as it is.

## 🔧 What the installer does

1. Checks `git` and `jq` (prints the fix and stops when missing). A missing or logged-out `gh` only prints a note: the PR watchers need it.
2. Clones this repo over https into `ORCH_REPO_PATH` (default `~/.claude/ai-agent-orchestration`), or fast-forwards it. A clone with uncommitted edits or commits that are not on origin is never updated: it prints them and stops. Changes go through a PR. It never stashes or resets.
3. Runs the `install.sh` of that clone, which symlinks the files in the [file table](#-files-linked-into-claude) into `~/.claude`. Each line says what happened: `linked`, `already linked`, `backed up` (a real file in the way moves to `~/.claude/backups/<timestamp>/` under its path relative to `~/.claude` (a file elsewhere in your home under `home/`), with `.1`, `.2` … when that name is taken; nothing is deleted), `moved backup` (a `<name>.bak.<timestamp>` an earlier installer left in `~/.claude/skills`, where Claude would load it as a second skill, moves to the backups directory), `skipped`, `removed stale` (a link this installer made, listed in `~/.claude/.orch-links`, whose file left the repo). One run at a time: `~/.claude/.orch-install.lock` is a symlink to the running installer's pid; a second run stops while that pid is alive, and a lock whose process is gone is taken over. Files under `coordinator/` (including `watchers/` with its tests, fixtures and platform service templates) are linked one by one into real directories, because the watchers write state next to themselves; a symlinked directory there (or any directory link below `~/.claude` into the clone) is backed up and replaced by a real one. The watchers state directories `~/.claude/coordinator/watchers/bindings/` and `prod-context/` are created empty; state never lands in the clone. The legacy `~/.claude/commands/{architect,coordinate}.md` move to a backup. A skill in `~/.agents/skills/<name>` that this installer owns (it resolves into the clone, or `~/.claude/skills/<name>` linked to it) is backed up and replaced by the same directory link; any other entry there stays as it is and prints a `collision` line.
4. Creates `~/.claude/orchestration.toml` from the example on the first run only, or converts an existing `~/.claude/orchestration.env` (see [settings](settings.md#-moving-from-orchestrationenv)); drops `pr.repos`, `audit.git_watch_repos` and `focus.skip_architects` from it with one `dropped` line (the watchers find repos and architects from the open work); then checks the file and lists the keys still to fill and keys new in the example. It stops when no Python 3.11 or newer is found.
5. Adds to `~/.claude/settings.json` (backup first): the SessionStart hook, the PreToolUse Bash guard, and the status line when you have none. An existing guard entry counts only when it runs the guard through `python3`; a bare path to `guard-bash.py` gets a working `python3` entry added. It also appends two `permissions.allow` rules, after the entries you have, so auto mode lets a session run `bind-self.py` itself: `Bash(python3 ~/.claude/coordinator/watchers/bind-self.py --output ~/.claude/coordinator/watchers/bindings/*)` and the same rule with the absolute `~/.claude` path of this install. A rule already in the list is not added again. Nothing else in the file changes; a second run adds nothing. Then it feeds the linked guard a forbidden command and stops with an error unless the guard denies it (exit 2 with the attribution reason on stderr). When the guard fails open, it appends a line with the time, the error and the first 200 characters of the command to `~/.claude/hooks/guard-bash.log`.
6. Runs the env adapter (`--env <name>`, else `install.env` from the config, else herdr; `"none"` skips it), then asks on a terminal for the empty keys (see [Set it up](../README.md#-2-set-it-up)). See [`env/README.md`](../env/README.md).
7. With `watchers.service = true`: renders the current platform service templates beside their links (macOS plist or Linux systemd user unit) and registers every watcher so it runs now and starts at login. The daily legacy map job is rendered only when `legacy.map_repo` names a clone, or a clone of an open workspace (asked from the env adapter, else its last saved set) has `legacy.scan = true` in its repo settings; a broken repo file there stops the installer. On macOS it runs at load; on Linux its timer fires daily at 06:00 and catches up a missed run; the audit and pulls watchers only once `pr.author` is set. macOS: the plist is copied (not linked) into `~/Library/LaunchAgents`, where launchd reads it at login, then booted out and bootstrapped when the copy changed or the job is not loaded. Linux: `systemctl --user enable --now` for each unit (the legacy map's service is linked and its timer enabled). Our LaunchAgents or user units that this run did not render, such as services under a previous label prefix or a legacy map without a repo, are stopped and deleted. When the checkout's revision or the config file differs from the last registration (`coordinator/watchers/.registered-revision`), every running watcher restarts onto the new code (`launchctl kickstart -k` or `systemctl --user restart`); the revision is recorded only when every service registered or restarted, so a failure is retried on the next run. A re-run without changes touches nothing, except that a stopped watcher is started again. A changed macOS plist replaces the installed copy only after the old job is unloaded. Turning `ORCH_WATCHERS_SERVICE` off stops and removes our registered services. While old macOS watcher jobs still need the printed migration commands, nothing is registered. Off by default.
8. In the herdr env (the default): a Herdr 0.9 or newer that is already installed is kept as it is. The Herdr on your `PATH` is the one used, even when an old copy sits in `~/.local/bin`. Without Herdr, it installs it using its [official installer](https://herdr.dev/docs/install/) into `~/.local/bin`, and upgrades an old one there; an old Herdr installed elsewhere, or one whose version cannot be read, stops the run with a fix. It installs missing mise using its [official installer](https://mise.jdx.dev/getting-started.html), installs `env/herdr/node.pin` through mise, and links the pinned combinaut Radar fork. Foreign Radar installations are replaced. Herdr's `config.toml` is compared outside Radar's managed blocks before and after Radar starts; when it changed there, the previous file is saved to the backups directory and a `WARNING` line names it.

   More about step 3:
   - One run at a time: `~/.claude/.orch-install.lock` is a symlink to the running installer's pid. A second run stops while that pid is alive. A lock whose process is gone is taken over.
   - Files under `coordinator/` (including `watchers/` with its tests, fixtures and platform service templates) are linked one by one into real directories, because the watchers write state next to themselves. A symlinked directory there (or any directory link below `~/.claude` into the clone) is backed up and replaced by a real one.
   - The watcher state directories `~/.claude/coordinator/watchers/bindings/` and `prod-context/` are created empty. State never lands in the clone.
   - The legacy `~/.claude/commands/{architect,coordinate}.md` move to a backup.
   - A skill in `~/.agents/skills/<name>` that this installer owns (it resolves into the clone, or `~/.claude/skills/<name>` links to it) is backed up and replaced by the same directory link. Any other entry there stays as it is and prints a `collision` line.
4. Creates `~/.claude/orchestration.toml` from the example on the first run only, or converts an existing `~/.claude/orchestration.env` (see [settings](settings.md#-moving-from-orchestrationenv)); drops `pr.repos`, `audit.git_watch_repos` and `focus.skip_architects` from it with one `dropped` line (the watchers find repos and architects from the open work); then checks the file and lists the keys still to fill and keys new in the example. It stops when no Python 3.11 or newer is found.
5. Adds to `~/.claude/settings.json` (backup first): the SessionStart hook, the PreToolUse Bash guard, and the status line when you have none. An existing guard entry counts only when it runs the guard through `python3`; a bare path to `guard-bash.py` gets a working `python3` entry added. Nothing else in the file changes; a second run adds nothing. Then it feeds the linked guard a forbidden command and stops with an error unless the guard denies it (exit 2 with the attribution reason on stderr). When the guard fails open, it appends a line with the time, the error and the first 200 characters of the command to `~/.claude/hooks/guard-bash.log`.
6. Runs the env adapter (`--env <name>`, else `install.env` from the config, else herdr; `"none"` skips it), then asks on a terminal for the empty keys (see [Set it up](../README.md#-2-set-it-up)). See [`env/README.md`](../env/README.md).
7. With `watchers.service = true`: renders the current platform's service templates beside their links (macOS plist or Linux systemd user unit) and registers every watcher, so each runs now and starts at login. Off by default.
   - The audit and pulls watchers register only once `pr.author` is set.
   - The daily legacy map job is rendered only when `legacy.map_repo` names a clone, or when a clone of an open workspace (asked from the env adapter, else its last saved set) has `legacy.scan = true` in its repo settings. A broken repo file there stops the installer. On macOS the job runs at load. On Linux its timer fires daily at 06:00 and catches up a missed run.
   - macOS: the plist is copied (not linked) into `~/Library/LaunchAgents`, where launchd reads it at login. It is then booted out and bootstrapped when the copy changed or the job is not loaded. A changed plist replaces the installed copy only after the old job is unloaded.
   - Linux: `systemctl --user enable --now` for each unit (the legacy map's service is linked and its timer enabled).
   - Our LaunchAgents or user units that this run did not render are stopped and deleted. Examples: services under a previous label prefix, or a legacy map without a repo.
   - When the checkout's revision or the config file differs from the last registration (`coordinator/watchers/.registered-revision`), every running watcher restarts onto the new code (`launchctl kickstart -k` or `systemctl --user restart`). The revision is recorded only when every service registered or restarted, so a failure is retried on the next run.
   - A re-run without changes touches nothing, except that a stopped watcher is started again.
   - Turning `ORCH_WATCHERS_SERVICE` off stops and removes our registered services.
   - While old macOS watcher jobs still need the printed migration commands, nothing is registered.
8. In the herdr env (the default):
   - A Herdr 0.9 or newer that is already installed is kept as it is. The Herdr on your `PATH` is the one used, even when an old copy sits in `~/.local/bin`.
   - Without Herdr, the installer runs Herdr's [official installer](https://herdr.dev/docs/install/) into `~/.local/bin`, and upgrades an old copy there. An old Herdr installed elsewhere, or one whose version cannot be read, stops the run with a fix.
   - It installs missing mise with its [official installer](https://mise.jdx.dev/getting-started.html), installs `env/herdr/node.pin` through mise, and links the pinned combinaut Radar fork. Foreign Radar installations are replaced.
   - Herdr's `config.toml` is compared outside Radar's managed blocks before and after Radar starts. When it changed there, the previous file is saved to the backups directory and a `WARNING` line names it.
9. Adds the marked orchestration block from `snippets/CLAUDE.md.snippet` to `~/.claude/CLAUDE.md`, or atomically replaces the text between those markers on later runs (backing up the old file first). An older unmarked orchestration section is left unchanged and the installer tells you to add the two markers once; custom text inside them is replaced on the next run.

## 🔗 Files linked into `~/.claude`

| File | Linked to | What it does |
|---|---|---|
| `skills/coordinate/` | `~/.claude/skills/coordinate` (directory link) | `/coordinate`: the Coordinator's operating model |
| `skills/architect/` | `~/.claude/skills/architect` (directory link) | `/architect`: the architect's operating model |
| `skills/review-prs/` | `~/.claude/skills/review-prs` (directory link) | `/review-prs`: the PRs that request your review, and Codex reviews of the ready ones |
| `skills/finalize-change-footprint/` | `~/.claude/skills/finalize-change-footprint` (directory link) | `/finalize-change-footprint`: the PR diff swept for duplicated tests, leftovers and dead code |
| `skills/finalize-code-comments/` | `~/.claude/skills/finalize-code-comments` (directory link) | `/finalize-code-comments`: the PR diff's comments swept for narration and noise |
| `coordinator/watchers/coord-audit-watch.sh` | `~/.claude/coordinator/watchers/` | PR problems, blocked workers, stale heavy slots, git pile-ups |
| `coordinator/watchers/context-watch.sh` | `~/.claude/coordinator/watchers/` | Writes context alerts for the inbox notifier |
| `coordinator/watchers/coord-host-watch.sh` | `~/.claude/coordinator/watchers/` | Host pressure: memory, swap, processes, open files, load (macOS and Linux) |
| `coordinator/watchers/coord-hog-watch.sh` | `~/.claude/coordinator/watchers/` | Logs big processes and long or big `git` processes (macOS and Linux) |
| `coordinator/watchers/coord-lib.sh` | `~/.claude/coordinator/watchers/` | Config loading and loud failures, sourced by the watchers |
| `statusline-command.sh` | `~/.claude/` | Status line; the repo name and the branch are OSC 8 links to the web page of the remote the branch tracks (`origin` for the repo name when there is no upstream). The branch link exists only for GitHub (`/tree/`), GitLab (`/-/tree/`) and `bitbucket.org` (`/src/`) hosts; any other host keeps the repo link and shows the branch as plain text. In a linked worktree the link is on the worktree name. A remote the script cannot map to a web URL stays plain text. Claude Code may need `FORCE_HYPERLINK=1` to keep the links; also writes tokens used to `~/.claude/context-usage/<session-id>.tokens` and remaining context percentage to `<session-id>` |
| `heavy-slot` | `~/.local/bin/heavy-slot` | Atomic shared cap for heavy jobs: `heavy-slot take <architect>-<job> -- <command>`; `count` and `list` inspect slots |
| `orch-agents` | `~/.local/bin/orch-agents` | One line per Herdr agent (name, pane, kind, status, workspace, owner, role, cwd); a session that needs Herdr agent data runs it instead of reading the ~10k-token `herdr agent list` JSON |
| `orch-settings` | `~/.local/bin/orch-settings` | Prints every resolved global setting as one `ORCH_NAME=value` line; sessions run it at startup instead of reading the commented `~/.claude/orchestration.toml` |
| `worker-reports/machine-rules.md` | `~/.claude/worker-reports/` | Template of machine rules to paste into every worker brief |
| `hooks/orch-update-check.sh` | `~/.claude/hooks/` | SessionStart hook: says when this setup is out of date |
| `hooks/guard-bash.py` | `~/.claude/hooks/` | PreToolUse Bash guard: blocks unsafe Git commands, including every `git grep` (even path-scoped) in configured big repos |
| `coordinator/pi-start.sh` | `~/.claude/coordinator/` | Starts a pi worker in a Herdr pane from `models.pi_worker` or `models.pi_smart`; refuses DeepSeek unless the repo's `.orchestration.local.toml` sets `pi.deepseek = true` |
| `coordinator/legacy-map.sh` | `~/.claude/coordinator/` | Writes `legacy-map.tsv` (files on the configured project's `origin/main` its legacy rules count); architects run it per task for the legacy check |
| `coordinator/service/<platform>/*.template` | linked like the rest of `coordinator/`; with `watchers.service = true` and a repo to map also rendered beside the link | daily legacy map job (launchd plist, or systemd service and timer), registered for login by the installer; the repo is `legacy.map_repo`, else the first clone of an open workspace, at install time, whose repo settings set `legacy.scan = true` |
| `coordinator/watchers/` | `~/.claude/coordinator/watchers/` (file by file; `bindings/` and `prod-context/` created empty) | Coordinator watchers, see [`coordinator/watchers/README.md`](../coordinator/watchers/README.md) |
| `coordinator/watchers/service/<platform>/*.template` | linked like the rest of `coordinator/`; with `watchers.service = true` also rendered to a `.plist` or `.service` beside the link | platform watcher services, registered by the installer to run now and at login |
| `env/<name>/install.sh` | not linked | Env adapters, see [`env/README.md`](../env/README.md) |
| `config/orchestration.toml.example` | `~/.claude/orchestration.toml` (copied once) | The global settings file |
| `config/repo.orchestration.toml.example` | not installed | Repo settings to commit as `.orchestration.toml` |
| `snippets/CLAUDE.md.snippet` | managed in `~/.claude/CLAUDE.md` | Marked orchestration block the installer adds and refreshes |
| `chrome/pr-tabs/host/pr_tabs_host.py` | `~/.claude/chrome-pr-tabs/pr-tabs-host` (rendered launcher) and Chrome's `NativeMessagingHosts/orch.pr_tabs.json`, only when a Chrome profile exists | Native messaging host for the [Chrome PR tabs](pr-tabs.md) extension |

## 🩺 Troubleshooting

The installer prints the fix with each error.

| Message | Fix |
|---|---|
| `curl: (22) …` and nothing installs | The download failed. Check the network and run the command again. |
| `curl: (22)` or another curl error, nothing else | The download failed, so nothing ran. The pipeline still exits 0 (use `set -o pipefail` in scripts); run it again. |
| `git not found. Fix: …` | macOS: `xcode-select --install`. Debian/Ubuntu: `sudo apt-get install git`. |
| `jq not found. Fix: …` | macOS: `brew install jq`. Debian/Ubuntu: `sudo apt-get install jq`. |
| `curl not found. Fix: …` | macOS: `brew install curl`. Debian/Ubuntu: `sudo apt-get install curl`. |
| `python3 not found. Fix: …` | macOS: `brew install python3`. Debian/Ubuntu: `sudo apt-get install python3`. |
| `cannot download …` | Check network access to [Herdr](https://herdr.dev/install.sh) and [mise](https://mise.run), then run again. |
| `Herdr install failed` | Check the official installer output and run again. Herdr must report 0.9 or newer. |
| `mise Node version does not match …` | Install the version in `env/herdr/node.pin` with mise, then run again. |
| `REFUSED: … has local changes` | Commit to a branch and open a PR, then run again. See above. |
| `… exists but is not a git clone. Move it away or set ORCH_REPO_PATH.` | Move the folder aside, or point `ORCH_REPO_PATH` (or `ORCH_RADAR_PATH`) elsewhere. |
| `… has no upstream branch. Fix: git -C <dir> switch main` | Run the printed command. |
| `… follows <x>, not a branch on origin. Fix: git -C <dir> branch -u origin/main` | Run the printed command. |
| `another install.sh is running (pid …)` | Wait for it to end, then run again. |
| `cannot take …lock` | If no install is running, remove the lock file it names. |
| `pinned commit … not found … Fix: git -C <dir> remote set-url origin …` | Run the printed command. |
| `ERROR: …` from a watcher, exit status 2 | A setting or command is missing; fill the key in `~/.claude/orchestration.toml`. |
| `setup out of date, run install.sh` at session start | Run the install command again. |
