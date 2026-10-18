# Settings

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
| `ORCH_LEGACY_EXTRA_PATTERN` | empty | Regex searched in addition to the built-in jQuery/ujs patterns of the legacy scan, for project names such as `old-widget\|legacy-grid`; compiled on its own, so its flags and groups keep their meaning |
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
| `ORCH_REPO_URL` | empty | Git URL the installer clones instead of `https://github.com/combinaut/ai-agent-orchestration.git` (for forks and tests). Environment only: set it on the install command line, not in the file |
| `ORCH_ENV` | empty | Env adapter to run without `--env`; empty means `herdr`, `none` skips the env step |
| `ORCH_RADAR_PATH` | `~/.claude/herdr-radar` | Herdr Radar checkout |
| `ORCH_LAUNCHD_WAIT_TRIES`, `ORCH_LAUNCHD_WAIT_SLEEP` | `40`, `0.5` | macOS: how often and how long (seconds) the installer waits for launchd to unload a job. Environment only |
| **Bash guard and Chrome PR tabs** | | |
| `ORCH_BIG_REPO_REMOTES` | empty | `owner/repo` remotes where broad Git reads are blocked; empty turns that rule off |
| `ORCH_PR_TABS_ORG`, `ORCH_PR_TABS_DEFAULT_REPO` | empty | Organization to manage; repo for bare PR numbers |
| `ORCH_PR_TABS_OPEN_ALL` | empty | GitHub search behind the **Open all** button |
| `ORCH_PR_TABS_TOPICS` | empty | JSON array of tab-group rules |

`ORCH_V2_LAUNCHD`, `ORCH_V2_PR_SCOPE`, `ORCH_V2_WORKER_MODEL` and `ORCH_V2_PRIMARY_REPO` are old names. The installer tells you to rename them to `ORCH_WATCHERS_SERVICE`, `ORCH_WATCHERS_PR_SCOPE`, `ORCH_WATCHERS_WORKER_MODEL` and `ORCH_WATCHERS_PRIMARY_REPO`.
