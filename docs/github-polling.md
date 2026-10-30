# GitHub polling

The watcher command runner, standalone `prs.py`, and focus check share `lib/github_poll.py`. Audit, pulls, workers, and the optional service PR watcher use the command runner. One-shot review dispatch and raw `gh` commands do not participate.

For an additional monitor or shell wait loop, invoke:

```sh
python3 <orch-root>/lib/github_poll.py api repos/owner/repo/pulls/123
```

Keep the command's exit status. A nonzero exit means the read failed; retain saved evidence, and report repeated failures. Prefer existing pulls/PR watcher events over another monitor. Shell pipelines need `set -o pipefail` so a failed read cannot become a successful empty result.

## Admission and retries

All participating processes for the OS user share a lock in `~/.cache/orch-github-poll`. Contenders poll with `LOCK_NB` every 50ms for at most 15 seconds, bounded below the caller's timeout (9.75 seconds for a 10-second worker read). Exhaustion raises `Busy`; cooldown raises `Cooldown` immediately. Both remain failed reads. Busy diagnostics name the holder PID and age, with `stuck` after 60 seconds. Audit and pulls retain evidence and emit READFAIL only after three consecutive busy passes for a source. An active READFAIL stays active through contention; changed error details do not repeat the alert. Holder diagnostics use a separate delimited field, so alert source identities and exact recovery messages remain stable. Saved legacy evidence survives cooldown and other failed rechecks.

Admission sleeps only the remainder of three seconds since the last start. An idle system has no spacing delay. Shared monotonic start times bound cadence independently of wall-clock changes; a monotonic reset uses one conservative three-second wait. Wall-clock start times remain in the state for inspection. Starts are separated by at least three seconds: at most 20 participating CLI starts in a half-open 60-second interval, with one CLI active. These bounds cover participating CLI calls only, not HTTP requests or raw `gh`; pagination and CLI internal requests can multiply HTTP traffic. There is no FIFO or polling-latency guarantee.

Each call has zero helper retries. Continuous monitors retry on their existing cadence indefinitely. `Retry-After` takes precedence, then `x-ratelimit-reset` only when `x-ratelimit-remaining` is zero. Otherwise backoff grows exponentially from 60 to 3,600 seconds. Every server-provided delay is capped at 3,600 seconds before uniform 1–5 second jitter. Success resets the exponent. Stored cooldowns farther than 3,605 seconds ahead are discarded. A permission-only 403 remains a failed read without cooldown; GraphQL rate-limit diagnostics count even with HTTP 200.

Parent timers bound admission and final state reads/writes while preserving existing watcher alarms. Shell, PR, and focus captures own private groups and clean them up on interruption, including signals during process creation. Queueing, spacing, and execution share the caller's budget: 45 seconds for watcher/standalone/shell reads, 10 seconds for idle-worker reads, and 30 seconds for focus. A small supervisor owns the inherited admission descriptor and an absolute deadline, kills its `gh` child at that deadline even after wrapper death, and bounds metadata I/O with the same timer. Service SIGTERM also cleans up the active private process group. Watcher pass deadlines remain in force.

Corrupt or unreadable state is renamed to `github.json.corrupt.<timestamp>` under the lock and replaced with a 60-second recovery cooldown. Errors name the state path. Directory permissions that prevent replacement still need repair; the helper does not change permissions. Failed admission, timeout, quota, or malformed departure reads retain snapshots and never clear them. A confirmed gone PR (`resource: null` or `Could not resolve to a`) follows the five-miss path, allowing other new requests to print. Scope fallback requires confirmed local repository absence.

Registered services participate after their normal upgrade/restart. **Foreground monitors need a manual restart.** Existing processes are not stopped or restarted by this change. With 10 architects and 30 workers, demand can exceed the 20-start/minute capacity: bounded waits then fail as reads, retaining evidence. At one quota point per CLI call the cap is 1,200/hour; costly queries, pagination, and raw calls can still exhaust quota.

## Measurement

`github.json` in the state directory records `callers` counters (`started`, `limited`, `last_started`) for admitted attempts by executable, with watcher mode included. Compare counters at two timestamps for observed admission rates. A spawn failure still counts as an admitted attempt; these are not HTTP request counters. `GITHUB_POLL_STATE_DIR` selects a different state directory for isolated tests; production pollers must share the default. Debug HTTP output is captured for response headers, then discarded; only error diagnostics reach callers.

An investigation on 2026-10-30 sampled processes for 120.46 seconds (111 samples). It observed at least six CLI processes from an ad hoc PR monitor, two from standalone `prs.py`, and one from pulls: lower bounds of 2.989, 0.996, and 0.498 calls/minute. Short processes can be missed. The ad hoc monitor's saved state had 138 reviews, 12 comments, and 92 threads, requiring five GraphQL calls per successful pass followed by a 60-second sleep. The three observed `sleep 30` loops belonged to dev-server slot wrappers.

Historical audit output confirmed rate-limit failures but lacked incident timestamps and response headers. A later healthy quota sample cannot distinguish an earlier primary-limit exhaustion from secondary limiting. Secondary limiting remains unconfirmed.

[GitHub rate-limit guidance](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) describes retry/reset headers and secondary limits.
