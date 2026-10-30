# GitHub polling

The watcher command runner, standalone `prs.py`, and focus check share `lib/github_poll.py`. Audit, pulls, workers, and the optional service PR watcher use the command runner. One-shot review dispatch and raw `gh` commands do not participate.

For an additional monitor or shell wait loop, invoke:

```sh
python3 <orch-root>/lib/github_poll.py api repos/owner/repo/pulls/123
```

Keep the command's exit status. A nonzero exit means the read failed; retain saved evidence, and report repeated failures. Prefer existing pulls/PR watcher events over another monitor. Shell pipelines need `set -o pipefail` so a failed read cannot become a successful empty result.

## Admission and retries

All participating processes for the OS user share a nonblocking lock in `~/.cache/orch-github-poll`. Contention and cooldown fail immediately without invoking `gh`. An admitted caller holds the lock through command completion and waits at most three seconds for start spacing. There is no contender queue or fairness guarantee. A clock discontinuity that exceeds that wait fails the read.

Starts are separated by at least three seconds: at most 20 participating CLI starts in any half-open 60-second interval, with one CLI process active. This bounds participating CLI calls only, not HTTP requests or raw `gh`. Pagination and CLI internal requests can multiply HTTP traffic. Existing services need their normal upgrade/restart to participate; this change does not restart them.

Each call has zero helper retries. Watch loops retry indefinitely on their existing cadence because they are continuous monitors. A rate-limit response sets a shared cooldown: `Retry-After` takes precedence, then `x-ratelimit-reset`, otherwise exponential 60, 120, 240 seconds up to 3,600 seconds. Each cooldown adds uniform jitter of 1–5 seconds. Repeated rate-limit failures retain the exponent until a successful read resets it. No participating CLI call starts during cooldown; at its expiry only one process can acquire the lock. A permission-only 403 stays a failed read without starting rate-limit cooldown. GraphQL rate-limit diagnostics also count when HTTP status is 200.

The existing per-command timeout remains 45 seconds for watcher/standalone PR reads and the shell helper, 10 seconds for idle-worker checks, and 30 seconds for focus checks. Admission spacing adds at most three seconds. Watcher pass deadlines remain in force. Corrupt or unreadable admission state fails closed.

With 10 architects and 30 workers sharing this helper, lock contention fails first, and successful starts still stay under 20/minute. There is no guaranteed polling latency under contention. At one quota point per CLI call, 20/minute is 1,200/hour; higher-cost queries and raw calls can still exhaust quotas. The helper honors rate-limit responses instead of inferring safety from its local bound.

## Measurement

`github.json` in the state directory records `callers` counters (`started`, `limited`, `last_started`) by executable, with watcher mode included. Compare counters at two timestamps for observed rates. These count CLI starts, not HTTP requests. `GITHUB_POLL_STATE_DIR` selects a different state directory for isolated tests; production pollers must share the default. Debug HTTP output is captured for response headers, then discarded; only error diagnostics reach callers.

An investigation on 2026-10-30 sampled processes for 120.46 seconds (111 samples). It observed at least six CLI processes from an ad hoc PR monitor, two from standalone `prs.py`, and one from pulls: lower bounds of 2.989, 0.996, and 0.498 calls/minute. Short processes can be missed. The ad hoc monitor's saved state had 138 reviews, 12 comments, and 92 threads, requiring five GraphQL calls per successful pass followed by a 60-second sleep. The three observed `sleep 30` loops belonged to dev-server slot wrappers.

Historical audit output confirmed rate-limit failures but lacked incident timestamps and response headers. A later healthy quota sample cannot distinguish an earlier primary-limit exhaustion from secondary limiting. Secondary limiting remains unconfirmed.

[GitHub rate-limit guidance](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) describes retry/reset headers and secondary limits.
