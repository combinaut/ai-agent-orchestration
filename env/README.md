# Env adapters

`install.sh --env <name>` runs `env/<name>/install.sh` after the shared files are linked. Without `--env` it uses `install.env` (`ORCH_ENV`) from `~/.claude/orchestration.toml`, else `herdr`. `ORCH_ENV="none"` skips it.

## Contract

`env/<name>/install.sh` is sourced by `install.sh` in a subshell, with `set -eu`. It defines two functions, which `install.sh` calls in this order:

| Function | Does |
|---|---|
| `env_check` | Checks prerequisites and changes nothing. A missing one ends the run through `die` with a fix to apply by hand. Uses the selected platform adapter for printed fixes. |
| `env_install` | Installs, updates or repairs what the env owns. Idempotent: a second run changes nothing and says so. |

Available to both:

| Name | Value |
|---|---|
| `ORCH_REPO` | The clone, e.g. `~/.claude/ai-agent-orchestration` |
| `ENV_DIR` | `$ORCH_REPO/env/<name>` |
| `CLAUDE_DIR`, `CONFIG`, `STAMP`, `BACKUP_DIR` | `~/.claude`, the personal config file, the backup timestamp, `~/.claude/backups/<timestamp>` |
| `backup_name <path>` | A free path under `BACKUP_DIR` mirroring `<path>`; its parent directory exists |
| `platform_run_detached <command…>` | Runs the command in a new session and returns its status, so what it starts outlives the installer |
| `say <status> <text>` | One output line, e.g. `say "linked" "$dest"` |
| `die <message>` | Prints `ERROR: <message>` and exits 1 |
| `config_value <KEY>` | The environment's value, else the config file's, else empty |
| `link_entry <source> <dest>` | Symlinks `dest` to `source`; anything else in the way is backed up |
| `refuse_local_changes <dir> <label>` | Exits when a git checkout has uncommitted edits or commits not on `origin` |

## Open work

`env/<name>/open_work.py` defines `open_work(command)`, which the watchers call through `coordinator/watchers/discovery.py`; watcher code never asks the env directly. `command(*argv)` runs a command and returns its output, raising on failure. It returns:

| Key | Rows |
|---|---|
| `workspaces` | `id`, `label`, `owner` (the architect's workspace id), `status`, `checkout` (the checked-out directory), `repo_root` (the main clone), `gone` (true when the directory the checkout came from no longer exists) |
| `panes` | `workspace`, `pane`, `title`, `role`, `agent`, `status`, `cwd` |

An env without the file, or `install.env = "none"`, has no open work: the watchers use the last saved set.

## herdr

- `env_check`: checks `curl` and `python3`, with fixes for macOS or Linux.
- `env_install`: keeps an installed Herdr 0.9 or newer, refuses an older or unreadable one outside `~/.local/bin` with a fix, and installs missing (or upgrades old managed) Herdr through [Herdr’s official installer](https://herdr.dev/docs/install/) and missing mise through [mise’s official installer](https://mise.jdx.dev/getting-started.html). Both support macOS and Linux. Installs the exact Node version in `env/herdr/node.pin` through mise, regardless of system Node. Both mise calls run from `env/herdr` in the managed clone, so the caller’s project configuration is not loaded.
- Clones `https://github.com/combinaut/herdr-radar` into `ORCH_RADAR_PATH` (default `~/.claude/herdr-radar`), repairs a foreign origin, and checks out `env/herdr/radar.pin`. Local edits or unpushed commits are refused.
- Creates `~/.claude/herdr-radar-runtime`, regardless of `ORCH_RADAR_PATH`, with links to the pinned checkout and a generated `herdr-plugin.toml` whose commands use the absolute mise Node executable. Runtime-root symlinks are backed up and manifest entries are replaced atomically, preserving external targets. Dangling runtime links are pruned. Manifest rendering fails if no Node command is rewritten or a bare Node command remains. The checkout stays clean. Herdr uses that interpreter for actions, startup, events, and the daemon, including in an already-running server.
- Links the runtime as `hhdebb.herdr-radar`. A plugin from another source is uninstalled and replaced, with one printed replacement line. Starts the daemon every run, in a new session through `platform_run_detached`, and stops it first when the pin or runtime manifest changes. Herdr returns from `plugin action invoke` before the action ends, so the installer waits for the old daemon to exit before starting, then asks this Herdr session's daemon endpoint for a pid that is not the old one (one retry) and fails if none answers. Tune with `ORCH_RADAR_WAIT_TRIES` and `ORCH_RADAR_WAIT_SLEEP`. Herdr's `config.toml` (`$XDG_CONFIG_HOME/herdr`, default `~/.config/herdr`) is compared outside Radar's complete `# >>> herdr-radar … block` sections (an unmatched opening marker counts as your content) before and after the start; a change there saves the previous file under `BACKUP_DIR` and prints `WARNING`.

- `open_work.py`: one `herdr workspace list` and one `herdr pane list`; a workspace without a Herdr worktree takes the checkout of its first pane's directory.

To move either pin, change `env/herdr/radar.pin` (a full commit SHA from `combinaut/herdr-radar`) or `env/herdr/node.pin` (an exact Node version) in a PR.
