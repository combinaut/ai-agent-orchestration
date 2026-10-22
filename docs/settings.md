# Settings

Settings come from three places. Each setting has one scope and lives in only one of them:

- **Global** settings (machine-wide: watchers, heavy slots, models, paths, Herdr) live in `~/.claude/orchestration.toml`. `ORCH_CONFIG=/path` selects another file; a selected path that is not a regular file is an error. The installer copies [`config/orchestration.toml.example`](../config/orchestration.toml.example) there on the first run.
- **Repo** settings (big-repo guard, legacy scan, focus report, merge gate checks) live at the root of each repo: `.orchestration.toml` is committed and shared, `.orchestration.local.toml` is personal. [`config/repo.orchestration.toml.example`](../config/repo.orchestration.toml.example) lists them.
- An **exported** `ORCH_*` variable wins over both files, for every setting.

A repo key in the global file, a global key in a repo file, an unknown key, a value of the wrong type, a list entry that is empty or contains its separator (whitespace, `,`, `|` or `:`) and invalid TOML are errors that name the file and the key. A script that lacks a setting or a command prints `ERROR: …` and exits with status 2. Reading TOML needs Python 3.11 or newer: the loader takes `ORCH_PYTHON3` from the environment, then `python3` on `PATH`, then the Homebrew paths.

## Print the resolved settings

`orch-settings` prints every resolved global setting as one `ORCH_NAME=value` line: the built-in default, then the global file (`ORCH_CONFIG` or `~/.claude/orchestration.toml`), then an exported variable. With no argument, a missing default file prints the defaults; a missing `ORCH_CONFIG` file, an explicit file that does not exist, an argument that starts with `-` and a value with a control character are errors (exit 2). Sessions run it at startup instead of reading the commented TOML. `orch-agents` is its companion for Herdr: one line per agent (name, pane, kind, status, workspace, owner, role, cwd), for a session that would otherwise read the `herdr agent list` JSON; it exits 2 with herdr's own message when herdr fails or answers in an unexpected shape. The installer links both into `~/.local/bin`.

## Repo files

Order, lowest first: the built-in default, `.orchestration.toml`, `.orchestration.local.toml`, an exported variable. A later file replaces a key of an earlier one; a list is replaced, not merged.

- A tool acting in a checkout (the Bash guard, the focus report for the repo an architect works in) reads the two files at the root of that checkout, found from its working directory.
- A tool acting on a PR (the audit and `legacy-scan.sh`) reads `.orchestration.toml` from the PR's base branch, then `.orchestration.local.toml` from your clone of that repo when an open workspace has one (matched by its GitHub `origin` remote).
- A linked worktree also reads the `.orchestration.local.toml` of its main checkout; its own local file wins over it.
- No file means the built-in defaults: every repo feature is off.

`.orchestration.local.toml` must not be committed. Add it to the repo's `.gitignore`, or to `.git/info/exclude` of your clone. A tracked local file is still read, with a warning that says how to untrack it.

## Global settings

| Key | Variable | Default | Meaning |
|---|---|---|---|
| `pr.author` | `ORCH_PR_AUTHOR` | empty | GitHub login whose open PRs the audit watches in the repos of the open workspaces ("@me" works); required by the audit watcher |
| `pr.since` | `ORCH_PR_SINCE` | empty | Only PRs updated on or after this date (YYYY-MM-DD); empty means no date filter |
| `pr.bot_reviewer_login` | `ORCH_BOT_REVIEWER_LOGIN` | `"copilot-pull-request-reviewer"` | Bot whose "Needs a closer look" review raises a COPILOT line |
| `audit.packs_max` | `ORCH_PACKS_MAX` | `50` | Pack files in a clone of the open work before a PACKS line |
| `audit.worktrees_max` | `ORCH_WORKTREES_MAX` | `40` | Worktrees in a clone of the open work before a WORKTREES line |
| `audit.page_prs` | `ORCH_AUDIT_PAGE_PRS` | `80` | PR search page size (1-100) |
| `audit.page_nodes` | `ORCH_AUDIT_PAGE_NODES` | `100` | Nested GitHub query page size (1-100); a cut-off result prints TRUNCATED |
| `legacy.scans_max` | `ORCH_LEGACY_SCANS_MAX` | `10` | PRs scanned for legacy code per audit pass; the rest wait for the next pass |
| `legacy.reads_max` | `ORCH_LEGACY_READS_MAX` | `20` | File reads per PR scan for files GitHub returns without a patch |
| `legacy.map_repo` | `ORCH_LEGACY_MAP_REPO` | empty | Clone the daily legacy-map job reads; empty picks, at install time, the clone of the open work whose repo settings turn legacy.scan on |
| `heavy_slots.max` | `ORCH_HEAVY_SLOTS_MAX` | `4` | Most heavy jobs at once on the machine |
| `heavy_slots.dir` | `ORCH_HEAVY_SLOTS_DIR` | empty | Slot directory; empty means ~/.claude/heavy-slots |
| `heavy_slots.kill_grace` | `ORCH_HEAVY_SLOTS_KILL_GRACE` | `10` | Seconds a stopped job gets before it is killed |
| `models.coordinator` | `ORCH_COORDINATOR_MODEL` | `"claude-opus-5-5"` | Model of the Coordinator |
| `models.architect` | `ORCH_ARCHITECT_MODEL` | `"claude-opus-5-5"` | Model of the architects |
| `models.codex_worker` | `ORCH_CODEX_WORKER_MODEL` | `"<codex-worker-model>"` | Codex model for routine work; replace the placeholder |
| `models.codex_smart` | `ORCH_CODEX_SMART_MODEL` | `"<codex-smart-model>"` | Codex model for hard work; replace the placeholder |
| `models.pi_worker` | `ORCH_PI_WORKER_MODEL` | empty | pi model for routine work as provider/model-id; empty means pi is not used on this machine |
| `models.pi_smart` | `ORCH_PI_SMART_MODEL` | empty | pi model for hard work as provider/model-id; empty means pi is not used on this machine |
| `recycle.tokens` | `ORCH_RECYCLE_TOKENS` | `300000` | Tokens used at which RECYCLE fires |
| `recycle.pct` | `ORCH_RECYCLE_PCT` | `55` | Context percentage used when the token sensor is absent |
| `recycle.park_idle` | `ORCH_PARK_IDLE` | `false` | Park architects that have no open PR, worker or watcher |
| `install.repo_path` | `ORCH_REPO_PATH` | empty | The clone install.sh keeps up to date and links into ~/.claude; empty means ~/.claude/ai-agent-orchestration |
| `install.env` | `ORCH_ENV` | empty | Env adapter install.sh runs without --env (a directory under env/); empty means herdr, "none" skips the env step |
| `install.radar_path` | `ORCH_RADAR_PATH` | empty | Herdr Radar checkout; empty means ~/.claude/herdr-radar |
| `watchers.service` | `ORCH_WATCHERS_SERVICE` | `false` | Register the watchers as launchd or systemd user services; needs herdr.bin and herdr.socket |
| `watchers.python3` | `ORCH_PYTHON3` | `"python3"` | Interpreter for the watcher scripts (3.11 or newer); services get it, or the installer's Python 3.11+ when it is older |
| `watchers.pr_scope` | `ORCH_WATCHERS_PR_SCOPE` | empty | GitHub search scope for the PR watchers, for example "org:acme" |
| `watchers.worker_model` | `ORCH_WATCHERS_WORKER_MODEL` | empty | Model label in dispatch records; empty leaves it out |
| `watchers.primary_repo` | `ORCH_WATCHERS_PRIMARY_REPO` | empty | Primary owner/name repo in dispatch records |
| `watchers.path` | `ORCH_PATH` | `["~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]` | PATH entries given to services and to the Chrome host |
| `watchers.closed_dir` | `ORCH_CLOSED_DIR` | empty | Where close-pane.sh marks a pane as closed on purpose; empty means ~/.claude/coordinator/watchers/prod-context/closed. The workers watcher reads <state-dir>/closed, so a custom folder needs that --state-dir |
| `herdr.bin` | `ORCH_HERDR_BIN` | empty | Absolute path of the Herdr executable |
| `herdr.socket` | `ORCH_HERDR_SOCKET` | empty | Absolute path of the Herdr socket |
| `herdr.todo_popup` | `ORCH_HERDR_TODO_POPUP` | `true` | install.sh links a Herdr plugin that opens the to-do view in a popup titled "Your to-do" on prefix+t, with a marked `[[keys.command]]` block in Herdr's `config.toml`; `false` removes both. Needs the herdr env |
| `user_todo.status_dir` | `ORCH_USER_TODO_STATUS_DIR` | empty | To-do status directory; empty means ~/.claude/coordinator/status |
| `user_todo.output` | `ORCH_USER_TODO_OUTPUT` | empty | To-do output file; empty means ~/.claude/coordinator/user-todo.md |
| `user_todo.state` | `ORCH_USER_TODO_STATE` | empty | To-do state file; empty means ~/.claude/coordinator/user-todo.state.json |
| `user_questions.dir` | `ORCH_USER_QUESTIONS_DIR` | empty | Question directory; empty means ~/.claude/coordinator/questions |
| `host.pressure_min_pct` | `ORCH_HOST_PRESSURE_MIN_PCT` | `20` | Host watcher: lowest free memory percentage |
| `host.swap_max_mb` | `ORCH_HOST_SWAP_MAX_MB` | `1024` | Host watcher: most swap used, in MB |
| `host.procs_max` | `ORCH_HOST_PROCS_MAX` | `1300` | Host watcher: most processes |
| `host.files_max` | `ORCH_HOST_FILES_MAX` | `150000` | Host watcher: most open files |
| `host.load_max` | `ORCH_HOST_LOAD_MAX` | `40` | Host watcher: highest load average |
| `alerts.escalate_ratio` | `ORCH_ALERT_ESCALATE_RATIO` | `1.5` | Feed: ratio between severity steps; greater than 1 |
| `alerts.digest_s` | `ORCH_ALERT_DIGEST_S` | `1800` | Feed: seconds between digest lines |
| `alerts.quiet_s` | `ORCH_ALERT_QUIET_S` | `1800` | Feed: seconds a condition stays clear before its clear line |
| `hog.gb` | `ORCH_HOG_GB` | `6` | Hog watcher: process size in GB |
| `hog.git_gb` | `ORCH_HOG_GIT_GB` | `2` | Hog watcher: git process size in GB |
| `hog.git_age_s` | `ORCH_HOG_GIT_AGE_S` | `180` | Hog watcher: git process age in seconds |
| `workers.wait_s` | `ORCH_WAIT_S` | `600` | Seconds idle or done before WORKER WAITING |
| `workers.wait_repeat_s` | `ORCH_WAIT_REPEAT_S` | `1800` | Seconds between waiting alerts (minimum 60) |
| `pr_tabs.org` | `ORCH_PR_TABS_ORG` | empty | Organization the PR tabs extension manages; empty turns org actions off |
| `pr_tabs.default_repo` | `ORCH_PR_TABS_DEFAULT_REPO` | empty | Repo name for bare PR numbers; empty turns number jumps off |
| `pr_tabs.open_all` | `ORCH_PR_TABS_OPEN_ALL` | empty | GitHub search behind the Open all button; empty turns it off |

## Repo settings

| Key | Variable | Default | Meaning |
|---|---|---|---|
| `guard.big_repo` | `ORCH_BIG_REPO` | `false` | Broad Git reads (git grep, cat-file over all objects) are blocked in this repo |
| `audit.merge_gate_checks` | `ORCH_MERGE_GATE_CHECKS` | empty | Check names that only run on a full CI run; an approved PR on a non-default base reports "full CI ran" or "full CI not run" |
| `review.finalize_commands` | `ORCH_REVIEW_FINALIZE_COMMANDS` | empty | Slash commands workers run before calling a PR done or ready; empty skips this step |
| `legacy.scan` | `ORCH_LEGACY_SCAN` | `false` | Open PRs of this repo are scanned for added lines that legacy.rules count (LEGACY line) |
| `legacy.rules` | `ORCH_LEGACY_RULES` | empty | Legacy rules ([[legacy.rules]]: kind, paths, optional pattern and exclude); the first rule that counts a line names its kind. No rules: nothing is scanned or mapped |
| `legacy.exempt` | `ORCH_LEGACY_EXEMPT` | empty | Path globs no legacy rule counts |
| `focus.pattern` | `ORCH_FOCUS_PATTERN` | empty | Case-insensitive regex matched against the labels of the workers of an architect working in this repo; empty turns the focus check off |
| `focus.architect` | `ORCH_FOCUS_ARCHITECT` | empty | Architect name never listed as lacking a focus worker |
| `pi.deepseek` | `ORCH_PI_DEEPSEEK` | `false` | pi workers may run on the DeepSeek API in this repo; off unless set to true in the personal .orchestration.local.toml (a committed .orchestration.toml cannot turn it on); the pi start step refuses DeepSeek otherwise |

### pi workers

`models.pi_worker` and `models.pi_smart` are `provider/model-id`, for example `lmstudio/qwen/qwen3.6-35b-a3b`; pi's `--model` accepts that form (`provider/id`, the model id may itself contain slashes). Check ids with `pi --list-models <provider>`. Both default to empty, and empty means pi is not used on this machine; `install.sh` does not ask for them and the architect does not stop on them.

DeepSeek sends the code to a remote API, so it is off for every repo. A repo opts in with `[pi] deepseek = true` in its personal `.orchestration.local.toml` (the main checkout's file also applies to its linked worktrees). The key in a committed `.orchestration.toml` is ignored with a warning, because a committed file would opt in every clone. The same key in a git-tracked `.orchestration.local.toml` is ignored too, and so is it when git cannot say whether the file is tracked (a broken index or a timeout), with a warning. DeepSeek counts as named when `deepseek` appears, in any case, in the provider or in the model id (`openrouter/deepseek/…`); every other provider, LM Studio included, needs no opt-in. `coordinator/pi-start.sh` enforces this when it starts a pi worker: it refuses an empty provider, ignores an exported `ORCH_PI_DEEPSEEK` and `GIT_*` variables, and its refusal tells the session to ask the user rather than edit the file. The Bash guard denies the ways around it; `docs/bash-guard.md` lists what stays open.

## Legacy rules

Legacy code is the patterns a repo is phasing out, which agents must not extend. The tool ships no rules: each repo lists its own under `[legacy]`, and with none the scan and the map do nothing and say so in one line.

Each `[[legacy.rules]]` has a `kind` (one word), `paths` (globs: `*`, `?`, `**`, `{a,b}`; a trailing `/` means everything below), an optional `pattern` (a [ripgrep](https://github.com/BurntSushi/ripgrep) regex, matched in time linear in the line, so no look-around or backreferences; searched in the first 2000 characters of a line; `(?i)` for case-insensitive) and optional `exclude` globs. A line counts under the first rule whose `paths` cover its file, whose `exclude` does not, and whose `pattern` matches it; a rule without a `pattern` counts every line it covers. `legacy.exempt` lists files no rule counts. The scan reads the rules of the PR's base branch and your clone's `.orchestration.local.toml`, whose rules replace the committed ones; `legacy-map.sh` reads them from `origin/main` and the clone.

Example for a Rails app moving off jQuery:

```toml
[legacy]
scan = true
exempt = ["scripts/legacy-guard.mjs"]

[[legacy.rules]]
kind = "JSERB"
paths = ["**/*.js.erb"]

[[legacy.rules]]
kind = "LEGACY"
paths = ["app/assets/javascripts/**"]
exclude = ["**/*.md"]

[[legacy.rules]]
kind = "JQUERY"
pattern = '\$\(|jQuery|\$\.ajax|rails-ujs|data-remote|remote: *true|select2'
paths = ["**/*.{js,coffee,erb,haml,rb}"]
exclude = ["spec/**"]
```

## Environment only

These are read from the environment and never from a file.

| Variable | Default | Meaning |
|---|---|---|
| `ORCH_CONFIG` | `~/.claude/orchestration.toml` | The global file |
| `ORCH_LEGACY_MAP_OUT` | `~/.claude/coordinator/legacy-map.tsv` | Where `legacy-map.sh` writes. The skills set it for a per-task map |
| `ORCH_REPO_URL` | empty | Git URL the installer clones instead of `https://github.com/combinaut/ai-agent-orchestration.git` (for forks and tests) |
| `ORCH_PYTHON_FALLBACKS` | `/opt/homebrew/bin/python3 /usr/local/bin/python3` | Absolute interpreters the loader and the installer try after `python3` on `PATH`; empty tries none |
| `ORCH_LAUNCHD_WAIT_TRIES`, `ORCH_LAUNCHD_WAIT_SLEEP` | `40`, `0.5` | macOS: how often and how long (seconds) the installer waits for launchd to unload a job |
| `ORCH_RADAR_WAIT_TRIES`, `ORCH_RADAR_WAIT_SLEEP` | `40`, `0.5` | How often and how long (seconds) the herdr env waits for the old Radar daemon to exit and the new one to answer |

## Moving from `orchestration.env`

When `~/.claude/orchestration.toml` is missing and `~/.claude/orchestration.env` exists, `install.sh` converts the old file once, keeps its comments where they sit above a key, and renames it to `orchestration.env.bak`. When both exist, the TOML file wins and the installer warns. The old `ORCH_V2_*` names are converted to their `ORCH_WATCHERS_*` names.

Repo values in the old file are not written to the global file. `ORCH_BIG_REPO_REMOTES` becomes `guard.big_repo = true` and `ORCH_LEGACY_REPOS` becomes `legacy.scan = true` for each listed repo, `ORCH_LEGACY_EXTRA_PATTERN` one `LEGACY` rule over every path and `ORCH_LEGACY_EXEMPT_PATHS` `legacy.exempt`; `ORCH_MERGE_GATE_CHECKS` goes to each `ORCH_PR_REPOS` repo; `ORCH_FOCUS_PATTERN` and `ORCH_FOCUS_ARCHITECT` go to `ORCH_WATCHERS_PRIMARY_REPO`. For a repo with a clone in `ORCH_GIT_WATCH_REPOS` they are written to that clone's `.orchestration.local.toml`, and the file is added to the clone's `.git/info/exclude` unless Git already ignores it. An existing local file is never overwritten. Every other repo gets a snippet in `~/.claude/orchestration-repo-snippets/<owner>/<repo>.toml` to commit as `.orchestration.toml` or keep as `.orchestration.local.toml`.

The removed `legacy.extra_pattern` and `legacy.exempt_paths` keys are errors. `install.sh` rewrites them, before it drops `audit.git_watch_repos`, in the `.orchestration.local.toml` of each clone of the open work, of the `legacy.map_repo` clone, of each clone `audit.git_watch_repos` still lists, of their main checkouts, and in the snippets, keeping every other line: `extra_pattern` becomes one `LEGACY` rule over every path, `exempt_paths` becomes `exempt`. The built-in jQuery rules are gone, so add the rules the repo needs.

## Repos and architects from the open work

The audit and pulls watchers watch the open PRs of `pr.author` in the GitHub repos of the open workspaces (the `origin` of each workspace's repo root), and check those clones for PACKS and WORKTREES. The env adapter reports the workspaces (`env/<name>/open_work.py`, see [env/README.md](../env/README.md)). When it cannot, the watchers use the last set saved in `~/.claude/coordinator/open-work.json` and say so in their log.

`pr.repos`, `audit.git_watch_repos` and `focus.skip_architects` are not settings. `install.sh` drops them from an existing file and prints one `dropped` line; one it cannot remove in place (an inline table) gets a `warning` line to edit it by hand. Until then the readers skip them with a warning. To leave an architect out of the focus report, see the pause line in [watchers.md](watchers.md#focus-report).
