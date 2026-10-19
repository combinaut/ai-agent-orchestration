# Coordinator watchers

This directory contains the stateful Python watcher implementation, shell entrypoints,
recorded fixtures, and tests.  Runtime state belongs under `~/.claude/coordinator/watchers/`
and is deliberately not versioned.

The production service templates are in `service/macos/` and
`service/linux/`: `com.combinaut.coord-watcher-context` produces context alerts and
`com.combinaut.coord-watcher-inbox-context` writes context, host, hog, balance, workers, audit, pulls and todo alerts to the feed with `--deliver file --configured-only --modes context host hog balance workers audit pulls todo`; `--configured-only` leaves out audit and pulls while `ORCH_PR_AUTHOR` is empty.
`com.combinaut.coord-watcher-host` collects host metrics every 60 seconds.
`com.combinaut.coord-watcher-hog` collects processes every 5 seconds.
`com.combinaut.coord-watcher-pulls` polls PR events every 150 seconds.
`com.combinaut.coord-watcher-todo` scans coordinator status files every 60 seconds, writes
`~/.claude/coordinator/user-todo.md`, and batches newly added waits-on-user items into one TODO feed line.
`user-todo.py --follow` renders that file in a pane; use a number key (or number) then Enter to jump. Clicks also jump when the terminal passes SGR mouse events; keys always work. When the items do not fit the pane, they split into pages: `n` or Page Down (or the mouse wheel down) shows the next page, `p` or Page Up the previous one, and the footer names the page. Numbers count across pages. Row counts use terminal cell widths, so wide characters wrap where the terminal wraps them; an item taller than the pane is cut to the pane. A successful jump dismisses that item from the pane and the persisted list; it returns only if its source text changes or it disappears and later reappears. Failed jumps keep the item. The rendered list has no count header: `Nothing waits on you.` is its exact empty state, while the current count is the pane's `todo=<n>` metadata token.
All eight producers write into `prod-context/`; one notifier shares their cursor,
so delivery from all producers needs no second notifier or cursor.
Host metrics use memory_pressure/sysctl on macOS and /proc on Linux.
Missing metrics skip never-active flags; active flags remain until a valid reading
clears them. HOST READFAIL lists unavailable metric names when that set changes;
HOST READFAIL cleared reports that all metrics are readable again. A scan with
no readable metric increments the read-failure count. A combined alert marks an unreadable active metric as unavailable. HOST alerts combine all active flags,
re-alert on swap steps of 1000 MB, file steps of 10,000, and whenever a flagged value crosses a step of `ORCH_ALERT_ESCALATE_RATIO` (1.5×) from its threshold, and emit
HOST RECOVERED when all flags clear. Context emits NOSENSOR per unreadable
session and RECYCLE with tokens or percentage, once per session id while it
remains in the agent list. Departed session ids are pruned.

## Run the watchers without services

The context and workers watchers read Herdr's agent list and stop with `not inside Herdr` unless `HERDR_ENV=1`. Run the block in a Herdr pane, or export `HERDR_ENV=1` and `HERDR_SOCKET_PATH=<Herdr socket>` (the services set the same from `ORCH_HERDR_SOCKET`) first. Paste the block into one shell: each command runs in the background, and `wait` keeps the shell open. The notifier is required: it turns the alerts into the feed the Coordinator's Monitor reads.

```bash
~/.claude/coordinator/watchers/coord-audit-watch.sh --state-dir ~/.claude/coordinator/watchers/prod-context --loop --interval 1800 &
~/.claude/coordinator/watchers/coord-pulls-watch.sh --state-dir ~/.claude/coordinator/watchers/prod-context --loop --interval 150 &
~/.claude/coordinator/watchers/coord-user-todo-watch.sh --state-dir ~/.claude/coordinator/watchers/prod-context --loop --interval 60 &
~/.claude/coordinator/watchers/context-watch.sh --state-dir ~/.claude/coordinator/watchers/prod-context --loop --interval 300 &
~/.claude/coordinator/watchers/coord-host-watch.sh --state-dir ~/.claude/coordinator/watchers/prod-context --loop --interval 60 &
~/.claude/coordinator/watchers/coord-hog-watch.sh --state-dir ~/.claude/coordinator/watchers/prod-context --loop --interval 5 &
~/.claude/coordinator/watchers/coord-balance-watch.sh --state-dir ~/.claude/coordinator/watchers/prod-context --loop --interval 60 &
~/.claude/coordinator/watchers/coord-worker-watch.sh --state-dir ~/.claude/coordinator/watchers/prod-context --loop --interval 60 &
~/.claude/coordinator/watchers/notify-inbox.sh --state-dir ~/.claude/coordinator/watchers/prod-context --cursor ~/.claude/coordinator/watchers/prod-context/coordinator-deliver.cursor --binding ~/.claude/coordinator/watchers/bindings/coordinator.json --deliver file --loop --modes context host hog balance workers audit pulls todo &
wait
```

Operational steps:

1. From the Coordinator's own Bash environment on a fresh install, run
   `python3 bind-self.py --output ~/.claude/coordinator/watchers/bindings/coordinator.json`.
   Each architect runs it with `--output ~/.claude/coordinator/watchers/bindings/architect-$HERDR_WORKSPACE_ID.json`.
   Run it again after every recycle; the notifier reads a binding at each send.
2. Laptop sleep suspends watcher execution; heartbeats can be stale after wake.
   A stale heartbeat alone does not identify a blocked syscall. Inspect stderr
   for `watcher deadline exceeded` before attributing a gap to a hang.

## Execution deadlines

Producers use two pass deadlines. The soft deadline raises `TimeoutError` inside
`cycle()`: repeated slow passes publish the existing `<mode> read failed …
(TimeoutError)` event and touch the heartbeat, retaining the prior snapshot.
A collector that catches the first timeout still cannot commit the pass as a
success, and no further command starts after its soft deadline has expired.
Each mode in one-shot `all` gets its own pass budget. `--loop all` is rejected:
sequential slow modes would exceed the other modes' heartbeat grace. Run one loop
per mode, as the production services do.

| Producer | Soft budget | Hard backstop |
| --- | ---: | ---: |
| context, workers, hog, host | 120 s | 240 s |
| balance | 300 s | 600 s |
| prs, pulls | 600 s | 1200 s |
| audit | 1800 s | 3600 s |

The hard deadline dumps all Python thread stacks and the active command arguments,
restores stderr's original blocking flag, and exits 70. A C-level faulthandler
backstop dumps stacks and exits if the Python signal handler cannot run, 30 seconds
after the hard deadline. Notifier/reader/follower/PR watch passes and their guarded
startup reads retain a 600-second hard deadline, including the follower's action-pattern
file read before feed attachment. macOS templates have
`KeepAlive=true`; Linux units have `Restart=always`. Foreground monitor owners must
restart a monitor after an exit. The C-level fallback exits nonzero with its own
status rather than guaranteeing 70, and cannot run Python command-group cleanup.

External commands have a 45-second timeout and run in a private process group.
Nonzero command exits kill that owned group before returning the original status
and output to the caller. Timeout/exception cleanup kills only that group, reaps the direct child with
a bounded wait, and closes captured pipes without another EOF drain. Successful
commands leave their helper processes alone. Native socket sends retain their
5-second timeout. Feed-lock acquisition fails after 5 seconds; producer/cursor
locks remain nonblocking. Preview output is flushed before its cursor advances.

Between passes, sleep uses a monotonic deadline, with each sleep at most 5 seconds
and remaining time clamped to the configured interval. A separate hard deadline
(interval + 30 seconds) covers the entire sleep. The machine cannot execute watchers
while asleep; sleep/wake behavior has not been validated on hardware in this PR.

Uninterruptible kernel waits, including hung network/FUSE filesystem I/O, remain a
limit: neither the Python signal handler nor process exit can release a process
until the kernel permits it. C-level stack dumping helps diagnose a C stall but
does not make uninterruptible I/O restartable. A full diagnostic sink can also
prevent the C-level dump from completing. An external owner must investigate
stale-but-alive producers; these guards do not sample or terminate live services.

## Install

The linking installer must link the complete `coordinator/watchers/` tree, including shell
entrypoints (executable), Python programs, `README.md`, tests, and `fixtures/`.
It must create the runtime-only directories `bindings/` and `prod-context/` without
linking state. It must make the files under the selected `service/<platform>/`
directory available as templates, substituting installer settings and paths: `@ORCH_PYTHON3@`, `@ORCH_CLAUDE_DIR@`, `@ORCH_CONFIG@`, `@ORCH_HERDR_BIN@`,
`@ORCH_HERDR_SOCKET@`, and `@ORCH_PATH@`. Every producer and notifier service
carries the installer-selected config path as ORCH_CONFIG, including a custom
path outside ~/.claude. Relative selections become absolute so the service’s
working directory does not change which file is read. The rendered files, never the
templates, are registered as active jobs (see the top-level README, install step 7).

## Feed delivery

`notify-inbox.py --deliver file` passes every new alert through `alert_policy.py` and appends the lines it returns to `prod-context/coordinator-feed.log`; preview mode (no `--deliver`) prints the raw alert line. `notify-inbox.sh` loads `ORCH_CONFIG` (default `~/.claude/orchestration.toml`) the way every watcher wrapper does, the environment winning, then runs `notify-inbox.py`; the services start the wrapper. The alert and state files keep every event.

The feed is mode 0600 and append-only; each batch is one `O_APPEND` write of whole lines, `<local ISO time> <mode>: <text> (<reason>)`. At 1 MiB it rotates to `coordinator-feed.log.1`, replacing the previous backup, so the feed takes at most 2 MiB. A single batch over 1 MiB keeps its newest lines that fit, after `watchers: <n> feed lines over the size cap dropped (digest)`.

| Reason | When |
|---|---|
| `start` | a condition starts: the first alert for a key, or the first after its clear |
| `danger` | a HOST metric starts at, or crosses into, its second severity step (`ORCH_ALERT_ESCALATE_RATIO` × the threshold: 1.5× for most metrics; free memory at the threshold ÷ the ratio, ⅔), once per condition |
| `clear` | the condition ends; for host, balance, hog and read-failure keys only after it stays clear `ORCH_ALERT_QUIET_S` (1800) seconds, and a return inside that hold continues the condition |
| `owner` | an active PR event gets an owner, or a new one; the line goes natively to that architect and does not wake the Coordinator |
| `digest` | `Watcher digest, <n> folded: …`, at most every `ORCH_ALERT_DIGEST_S` (1800) seconds and only when something folded |

Repeats, growth and band changes write nothing. Queued batches apply in order of the time their producer wrote them. A version-2 cursor is repaired once: each active PR event whose saved owner differs from the pulls snapshot's owner gets its `(owner)` line. When the cursor holds no state of version 2 or 3 (first run, or an older notifier), the notifier rebuilds key state from the producer events the cursor already consumed, the current audit lines and read failures in `audit.json` (in place of audit history, also for a fresh cursor, which then skips that history), and each heartbeat the cursor marks stale, and writes one `start` (or `danger`) line per condition still active. Folded into the digest: `WORKER <pane> closed` (a pane closed on purpose) and a `READFAIL` source marked `,new` (a pane the workers watcher first saw under 2 minutes ago).

Keys: `host:<metric>`, `balance:<metric>`, `hog:<pid>/<process>`, `gitbig:<pid>/git`, `gitlong:<pid>/git`, `context:<pane> <label>`, `workers:<pane>`, `pulls:<kind> <repo>#<n>`, `audit:<kind> <subject>`, `heartbeat:<mode>`, `<mode>:readfail:<source>`, `<mode>:health:read`, else `<mode>:unparsed:<line shape>` (digits and bracketed details removed), with `watchers: unparsed <mode> line format: <shape> (unparsed)` written once per shape. Old hog lines without `comm=[…]` key on the basename of the first `cmd=[…]` token. `<MODE> RECOVERED` clears that mode's metric keys, a HOST or BALANCE line clears the metric keys it no longer lists, `<KIND> pid=<pid> comm=[<process>] cleared` clears a hog key, `hog LOG WRITE RECOVERED` the log-failure key, `NOSENSOR CLEARED <pane>` a missing-sensor key, `CLEARED <audit line>` an audit key, `RECYCLE <pane> <label> cleared` a context key (also emitted for the old pane and label when an alerted session moves or is renamed), `<kind> <repo>#<n> cleared` a pulls key, each source on a `<PREFIX> READFAIL a b` line is its own key and a source missing from the next such line is cleared, `READFAIL cleared` clears that mode's read-failure sources and `READ RECOVERED` its `health:read` key, `WORKER <pane> RECOVERED` and `WORKER <pane> closed` clear the pane, `HEARTBEAT RECOVERED` clears `HEARTBEAT STALE`, and `WORKER <old> MOVED <new>` moves its key. `dead/disappeared`, `MERGED` and `CLOSED` end their key at once, so the next alert on it starts again. Key state persists under `delivery` in the cursor file (an inactive or never-clearing key idle for 24 hours is dropped, and at most 1000 keys are kept, least recently active dropped first), written atomically with mode 0600. Each batch is committed as pending with the cursor before it is appended; on start the notifier writes a pending batch the feed does not already end with, so a crash neither loses nor repeats a batch. A producer is not reported stale until its heartbeat grace has passed since the cursor first saw it, or since the notifier last started.

### The Coordinator's Monitor

`feed-actions.regex` holds the action pattern, one extended regular expression per line (usable with `grep -E -f`); `feed_actions.py` reads it for the Monitor and the notifier. The Coordinator runs one Monitor on `~/.claude/coordinator/watchers/feed-monitor.sh`, which runs `feed_follow.py`: it prints each action line, with a pulls line cut to `pulls: <kind> <repo>#<n> (<reason>); read the PR on GitHub`, so no PR title or detail reaches the Coordinator. The follower opens the feed at its end before it touches `prod-context/coordinator-feed.monitor` (then every 60 seconds), and follows rotation by inode like `tail -F`, so no line written while it starts is missed. TERM stops the follower and removes the heartbeat only if this Monitor is the newest one (it records its pid in `coordinator-feed.monitor.owner` at start); a follower recreates a missing heartbeat and prints one stderr line. Action lines: `RECYCLE` start, HOST `danger`, pulls `APPROVED*`, `RED`, `CHANGES`, `THREADS`, `COPILOT` and `MERGED` starts, `TODO <n> new` and `TODO READFAIL`, `WORKER` blocked, error or dead/disappeared starts, and `HEARTBEAT STALE` starts.

The Monitor's follower writes `<inode> <attached> <consumed>` into its heartbeat: the feed file it reads, where it started reading it, and how far it has read; it attaches under `prod-context/coordinator-feed.lock`, and the notifier writes each batch under the same lock. When no live Monitor (heartbeat at most 180 seconds old) will read the batch, the notifier sends its action lines to the Coordinator binding through the native channel. When one will, the native copy waits in the outbox and is dropped once the record shows the batch was read, or sent when the Monitor goes away or has not read it within 20 seconds. Delivery to the Coordinator is at least once: a Monitor that stalls past those 20 seconds and then resumes prints an action that was already sent natively. Each pending batch records where it goes in the feed before it is written, so a restart finishes exactly that batch (a batch from an older notifier, with no recorded place, counts as written when the feed ends with its exact bytes); a write cut short is cut off and written again whole. Routine lines never go through the native channel. Native messages wait in the cursor's `outbox` until sent: a binding's queued messages go together and in order, one attempt per binding at most every 60 seconds, each dropped with a stderr note after an hour; past 200 queued messages each binding's messages merge into one before any is dropped.

### PR events

`com.combinaut.coord-watcher-pulls` runs `coord-pulls-watch.sh` every 150 seconds with the audit's settings (`ORCH_PR_AUTHOR`, the repos of the open work, `ORCH_PR_SINCE`, `ORCH_BOT_REVIEWER_LOGIN`, `ORCH_AUDIT_PAGE_PRS`, `ORCH_AUDIT_PAGE_NODES`, and each repo's `audit.merge_gate_checks`, read only for approved PRs on a non-default base). Each poll makes one GraphQL search per page of `ORCH_AUDIT_PAGE_PRS` open PRs, at most 10 `gh api repos/<repo>/pulls/<n>` reads for PRs that left the open list, right after the search in their own 60-second window, fewest failed reads first (the rest wait for the next poll), then the open work from the env adapter (one `herdr workspace list` and one `herdr pane list`, shared with the repo discovery), one `git remote get-url origin` per repository root, and one `git worktree list` for each root whose remote is a watched repository; a root that fails is skipped and named by `PULLS READFAIL owners`, and only PRs of that root's repository (its `owner/name`, or every repository of that name when its remote could not be read) keep their last known owner. A failed read is `PULLS READFAIL pr:<repo>#<n>` and does not stop the others; a PR that left the search but is still open stops being tracked, its active lines cleared; each command times out after 45 seconds. Its first read emits only `PULLS BASELINE [<n> active lines]` and saves active lines and read failures silently; a read failure that persists to the next poll then emits once. Later polls emit `<kind> <repo>#<n> owner=<workspace or ?> <title> [<detail>]` when an `APPROVED`, `APPROVED-OLDHEAD`, `APPROVED-STACKED`, `RED`, `CHANGES`, `THREADS` or `COPILOT` line appears, `<kind> <repo>#<n> cleared` when it goes, and `MERGED` or `CLOSED` when the PR leaves the open list.

The owner is the `owner` tag of the open workspace whose checkout has the PR's head branch, matched by the repository's `owner/name` from its `origin` remote. The owner token always follows `<repo>#<n>`, so a PR title cannot claim one and truncation cannot drop it. The notifier sends each pulls `start` with an owner to `bindings/architect-<owner>.json` through the native channel as `pulls: <kind> <repo>#<n> (start); read the PR on GitHub` — native messages never carry PR titles or details — and writes the full line to the feed; without an owner or a binding the line stays in the feed. A failed owner lookup is `PULLS READFAIL owners`; known owners are kept and events still flow.

A PR event reaches its owner within the 150-second interval, the poll's own reads and a 5-second notifier pass. The poll normally takes seconds; its worst case is one 45-second timeout per command, with departed-PR reads starting only in its first 60 seconds.

## Hog alerts and logging

Hog runs every 5 seconds with `--loop --interval 5` in both platform services.
HOG means RSS is at least 6 GB; GITBIG means git RSS is at least 2 GB;
GITLONG means git age is at least 180 seconds. Configured thresholds override these defaults.
Alerts include RSS, age, the process name (`comm=[…]`, which keys feed delivery), a command capped at 300 characters and up to five parents.
A hit alerts once per PID, start time and kind; when that hit ends, the watcher emits
`<KIND> pid=<pid> comm=[<process>] cleared`. Every hit on every scan is appended to `<state-dir>/hog.log` and fsynced. At 1 MiB the log rotates to
`hog.log.1`, replacing the previous backup. Only `hog.log` and `hog.log.1` are bounded; the alert history is not.
Log writes happen after state and alerts are published. An OSError emits
`hog LOG WRITE FAILED` once per failure streak and `hog LOG WRITE RECOVERED` when a write works again, and never becomes a read failure;
process alerts continue while logging is unavailable. Writes are unbuffered before fsync.
Thresholds compare raw RSS in kB; only display values are rounded to one decimal GB.
Linux scans skip and count exited, inaccessible or malformed PIDs in the adapter’s
`skipped_processes` counter without discarding valid processes.
The Coordinator inspects the command and parent chain, routes the evidence to the
owning architect, and stops only an owned job PID when the memory guard requires it.
On hog HEARTBEAT STALE or repeated read failures, inspect and restore the producer.
The watcher kills nothing.

## Balance alerts

`com.combinaut.coord-watcher-balance` runs every 60 seconds on both platforms,
sharing `prod-context/` and the existing notifier. BALANCE combines load5 > 20,
slots > `ORCH_HEAVY_SLOTS_MAX` (default 4), worktrees with CPU >= 80% > 4,
and free memory < 30%. The slot boundary is configured independently; changing
`ORCH_HEAVY_SLOTS_MAX` changes both `heavy-slot` admission and the Balance
slot boundary, but not the separate heavy-worktree CPU boundary.
A missing heavy-slots directory counts as zero slots.
Slots count only files containing live positive PIDs, including permission-denied
PID probes. Stale PIDs are excluded. Audit and balance share one positive-integer
PID parser and one default heavy-slots path. Dotfiles and broken symlinks are
skipped. Invalid or unreadable slot files report
named READFAIL and preserve uncertainty unless five live slots are already known.
The audit reports stale slot PIDs and slot directories separately; each file
failure leaves valid files readable and preserves only that file’s prior conditions.
It re-alerts per load5 step of 10 and on slot count changes; RECOVERED appears
only after a pressure flag has been raised and all metrics and process mappings
are readable and all flags clear. Pending recovery is kept across unreadable
scans; a READFAIL that clears without prior pressure sends only READFAIL cleared.
BALANCE READFAIL names unavailable metrics and PIDs; valid metrics still alert,
and unreadable active flags remain remembered. A scan with no readable metric
increments the repeated read-failure counter.

The `top:` list names up to four current-user worktrees ranked by aggregate CPU, with CPU
percentage and RSS in MB. Mapping uses process cwd, since command arguments
usually contain no worktree path. Git metadata identifies the worktree root in
flat or nested repository layouts; without metadata the direct child of
`worktrees/` is used as the flat-layout fallback. Aggregation keys by root path,
with duplicate leaf names displayed as full paths for unambiguous attribution.
Unreadable own-process Git metadata reports a named PID READFAIL while the
other guards continue.
macOS selects ps by real UID and filters by effective UID before cwd mapping,
keeping its decaying ps %cpu. Linux filters /proc PID directories by owner UID.
macOS reads ps CPU/RSS and lsof cwd, rechecking unmapped PIDs to skip exited
processes while treating denied PID probes as live; zombie rows are skipped. Linux
reads loadavg, meminfo, uptime, process stat and cwd from /proc. Linux CPU uses the change in utime+stime divided by elapsed uptime between
passes. PID/start-time counter baselines are persisted in balance.json and
pruned when processes depart. Each PID's first sample uses its lifetime CPU
average (utime+stime over age); subsequent samples use counter deltas. The
Linux clock snapshot follows the process-counter reads so a newly born process
is not compared with a clock snapshot from before its birth. PID reuse
and counter/clock resets fall back to a fresh lifetime average. CPU is always
numeric for a readable process, so new PIDs cause no sampling alerts. Partial
process scans still alert when their readable worktrees prove a breach; exactly
four known heavy worktrees cannot prove that an unreadable fifth is clear.
All balance limits are defined in one place and supplied to the collectors'
partial-breach decisions. Host and balance share flag reduction while keeping
their existing event formats and recovery policies. Exited
Linux PIDs are skipped; inaccessible or malformed live PIDs report READFAIL
while readable processes remain in the list. Zombies and kernel threads are excluded.
The watcher stops no jobs. The Coordinator follows the balance-workers
procedure in `skills/coordinate/SKILL.md`.

## Workers alerts

`com.combinaut.coord-watcher-workers` runs `coord-worker-watch.sh` every 60 seconds
on both platforms. This gives prompt blocker detection with modest agent-list polling.
It shares `prod-context/`, the binding and the single inbox notifier with the other producers.
Every listed agent is included, regardless of role tags. WORKER lines name the
pane; agent list does not supply workspace labels. Blocked and error alert once
per agent session and state; working, idle, done and waiting are silent.
An active agent (working, waiting, blocked or error) that leaves the list raises
dead/disappeared once, or `WORKER <pane> closed` when `<state-dir>/closed/<pane id>` exists.
`close-pane.sh <pane id>` writes that marker, closes the pane, and removes the marker when the close fails; the watcher deletes a marker once neither its scan nor its saved state lists the pane. Its key is then dropped, so persisted worker state is
bounded by live agents. A new session on an occupied pane silently replaces the
old key without reporting the old session as disappeared. An old session still
listed on another pane keeps its own state.

Session identity falls back to the pane id when no session id exists. A fallback
key is promoted when a session id becomes available on the same pane. Temporary
missing metadata preserves the last known identity unless that session is listed
elsewhere in the same scan. Only a row keyed by
its own pane id can be promoted, and fallback state never overwrites an existing
session key.
`WORKER <pane> RECOVERED <state>` clears a live agent's previous blocked/error
state when it changes. A blocked-to-error transition clears blocked and raises
error. Disappearance emits no RECOVERED line. Working/idle changes produce no
alerts. Dedupe survives service restarts.

Unknown or missing statuses skip only that agent. WORKER READFAIL names the
unreadable panes and statuses once whenever the sorted set changes; a pane the watcher first saw
under 2 minutes ago shows as `<pane>(<status>,new)`; READFAIL
cleared marks a fully readable list. A previously active alert and last known
lifecycle state remain remembered through unknown-status scans, preventing false
recovery. A failed Herdr command or malformed list uses the shared three-failure
warning and READ RECOVERED path. HEARTBEAT STALE/RECOVERED covers producer liveness.
The Coordinator responses are in `skills/coordinate/SKILL.md`: inspect panes,
route blockers and errors to owners, verify disappeared sessions and arrange
recovery, clear recovered warnings, and restore unreadable agents or producers.
The audit reports workspace-level blocked lines.

## Audit alerts

`com.combinaut.coord-watcher-audit` runs `coord-audit-watch.sh` every 1800 seconds
on macOS and Linux, sharing `prod-context/` and the inbox notifier. Thirty minutes
keeps GitHub cost modest; the workers service detects agent blockers every minute.
Watcher default intervals and per-mode heartbeat grace share WATCH_TIMING in
watchers.py. Audit heartbeat staleness starts at 4500 seconds, allowing a full 1800-second
soft pass budget plus its 1800-second interval and 15 minutes of slack. Other modes retain their 900-second threshold.

The audit uses ORCH_PR_AUTHOR, the repos of the open work (see docs/watchers.md), optional ORCH_PR_SINCE,
ORCH_BOT_REVIEWER_LOGIN and existing page/limit settings, and each repo's `audit.merge_gate_checks`.
Each pass uses one GraphQL search, paged as needed, with all PR fields bundled.
The PR list itself needs no per-PR calls; only the LEGACY scan below makes them. Nested checks/threads exceeding ORCH_AUDIT_PAGE_NODES
emit the existing TRUNCATED line, so incomplete evidence remains visible.
The latest run of each check name determines RED. A missing/empty head rollup
emits NOCHECKS for that PR and does not block other PRs.

Each source emits one AUDIT BASELINE summary on its first successful read,
including a first read after startup failure. No existing condition lines are
sent during that source baseline. Read
`audit.json` → `before.sources` for the active lines, including approvals and
existing problems. Later scans emit newly active or changed lines with the
original audit text: RED, THREADS, COPILOT, CONFLICT, CHANGES, NOREVIEWER,
APPROVED, APPROVED-OLDHEAD, APPROVED-STACKED, LEGACY, BLOCKED worker, STALESLOT,
SLOTDIR, PACKS and WORKTREES. A condition that leaves a readable source, or a legacy line whose PR closed or whose new head has no hits, emits `CLEARED <its last line>`; a changed detail, such as a thread count, is the same condition. Pack steps use ORCH_PACKS_MAX; worktree steps
use 20, with the configured thresholds deciding when to alert.
`LEGACY <repo>#<n> <title> [<count> lines: <kind> a, <kind> b, ...]`
lists lines an open PR adds that the repo's legacy rules count (patterns the repo is phasing out, which
agents must not extend), one count per rule kind in rule order. The rules are `[[legacy.rules]]` in the repo
settings ([docs/settings.md](../../docs/settings.md#legacy-rules)); the tool ships none. A repo with `legacy.scan` on and no rules gets one
`NORULES LEGACY <repo>@<base> [...]` line per base branch and no scan. The scan runs for PRs of repos whose repo settings turn `legacy.scan` on (off by default; read from the `.orchestration.toml` of the PR's base branch, then the `.orchestration.local.toml` of a watched clone) when a PR is first seen, when its head SHA changes, when its base branch changes, or when its rules or `legacy.exempt` change, at most ORCH_LEGACY_SCANS_MAX PRs per
pass (default 10; the rest wait for the next pass), and a head with a count above
zero emits one line. Results are kept in `audit.json` → `before.legacy`, and the
first read emits `AUDIT BASELINE legacy`. Each scan costs one `gh pr diff` call; every pass also makes one `.orchestration.toml` read per repository base branch of the open PRs. A PR over 300 files makes `gh pr diff`
fail with `too_large`; the scan then reads the `pulls/<n>/files` patches, 100 files per page,
and classifies them identically. GitHub leaves `patch` out of big file entries; the scan then reads
the file at the PR head with the contents API (`added`), or the head and merge-base versions
(`modified`, `renamed`, found with one compare call) and diffs them with a bounded shortest-edit (Myers) diff, whose added-line count equals Git's (over 5,000 lines or 1,000 changed lines fails the scan); `removed`
files need no read. The added-line count must equal the entry's `additions`, or the scan fails. At most
`ORCH_LEGACY_READS_MAX` reads per PR (default 20; more fails the scan), each under the 45s
command timeout, files over 1,000,000 bytes fail the scan, and binary files (NUL byte or not UTF-8)
are skipped with a `legacy-scan skipped binary file <path>` line on stderr. A rules or scan failure becomes that PR’s `READFAIL LEGACY <repo>#<n> <title>: <reason>` entry, retried on later passes; other PR scans and own-PR review/check lines keep reporting. The classifier lives only in
`legacy_rules.py`; `coordinator/legacy-scan.sh <pr> [repo]` uses `watchers.primary_repo` when no repo is supplied. It is a
thin CLI over it that prints the matching lines.

Invalid rules (an unknown key, a `kind` that is not one word, missing `paths`, a bad glob or regex) are a scan failure that names the rule
(`READFAIL LEGACY <repo>#<n> <title>: legacy.rules[2] (JQUERY): pattern is not a valid rg regex: ...`).

Only the current search's OPEN PR lines are remembered; departed PRs are pruned
without follow-up reads. Each source failing to read keeps its last known lines
and emits READFAIL naming github, herdr, slots, individual slot files or the git path; other sources
continue. READFAIL is deduplicated by source and error text; `<source> READFAIL cleared` marks each source recovery, even while others fail.
Audit event history contains the latest 100 batches with
monotonic sequence numbers, bounding persisted history as well as PR snapshots.
A reader offline for more than 100 batches must inspect the current sources.

`coord-check.sh --state-dir <directory>` runs seven Coordinator producers once
(context, workers, hog, balance, host, audit, todo). A held lock reports the mode as
`running` without touching its state; other modes still run. Command failures are
reported per mode, and the check exits nonzero after attempting every mode.
It excludes prs, which belongs to
PR::Reviewer. `coord-cycle.sh <directory>` adds a dry-run inbox preview of those
same modes. These are stateful checks; they do not inspect phone links, router
reachability or pane tails. Those remain Coordinator actions when needed.
Service managers own process lifetime; foreground Monitors own their children.
Coordinator responses for each audit line are in `skills/coordinate/SKILL.md`.

Rules reject bridge markers matching the empty string and pattern sets with no patterns.
