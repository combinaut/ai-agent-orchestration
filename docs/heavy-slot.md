# Heavy jobs: `heavy-slot`

Many sessions share one machine. Too many test runs, browsers or builds at once can exhaust open files and memory. `heavy-slot` is a shared cap of `ORCH_HEAVY_SLOTS_MAX` jobs.

```bash
heavy-slot take acme-tests --note "first-run.bats for PR 106" -- npm test   # waits up to 1800 s for a free slot, then runs
heavy-slot count                         # slots in use
heavy-slot list                          # names, pids, ages, notes, stale records
heavy-slot stop acme-tests               # stops that slot's job
```

`--note <text>` says what the job is. It shows in `list` and in every event line (`note=-` without it). The note is cut to one printable line of 60 characters.

A slot is a file in `~/.claude/heavy-slots/` that holds the job's pid. `take` removes it when the job ends.

- If the cap stays full, `take` prints `NEED SLOT`.
- `take` exits 73 when the slot directory is not writable (for example in a sandbox). Run it again with write access to that directory.
- Never create slot files by hand. Stop a job with `heavy-slot stop`, never with `pkill`.
- The audit reports `STALESLOT` for a file whose pid is gone.
- The balance watcher uses the same cap.

## Stopping a job

`heavy-slot stop <name>` sends TERM to the pid in that slot's file, and KILL if it still runs after `ORCH_HEAVY_SLOTS_KILL_GRACE` seconds (default 10). The job's `take` then ends the rest of the job's process group and removes the slot file. `stop` prints each step. A job that `stop` ends writes a `FAIL` event.

`stop` takes no slot lock. It checks the pid and its start time just before TERM and again before KILL. A check and its signal are two steps (macOS has no pidfd), so a pid that is reused between them gets the signal. A job that ends before a signal counts as stopped.

`take` reads the start time after it releases the slot lock, and gives that step 10 seconds. When it records none, it says so on stderr and `stop` refuses the slot. `stop` exits 1 when the job's pid has ended but the slot file still holds it (its `take` is gone).

`stop` signals nothing and exits non-zero when:

- no slot has that name (exit 64);
- the pid is not running (exit 65);
- the pid does not match the start time `take` recorded in `<slot dir>/.ids/<name>`, there is no record, or the start time cannot be read in 10 seconds (exit 65). This covers a reused pid and a slot taken by a `heavy-slot` without `stop`. The command is not compared, because a job can `exec` or rename itself. `lib/process_identity.py` reads the start time without `ps`.

`install.sh` adds the allow rule `Bash(heavy-slot stop *)`, so auto mode lets any session stop any slot job, including another session's.

## Feed lines

`take` appends one line per event to `<slot dir>/.events`. The balance watcher reads that file each pass, removes it, and turns each line into a feed line (`balance: SLOT ...`). Every line names the slot, the pid, and the note.

| Event | When | Example | Feed |
| --- | --- | --- | --- |
| `WAIT` | The job found no free slot (once per job) | `SLOT WAIT acme-tests pid=40 slots=4/4 note=first-run.bats for PR 106` | FYI log |
| `FAIL` | The job exited non-zero, also a code above 128 (a kill from outside, such as the OOM killer, looks the same as `exit 137`) | `SLOT FAIL acme-tests pid=41 code=1 elapsed=93s note=...` | FYI log |
| `KILLED` | `take` received a signal and stopped the job | `SLOT KILLED acme-tests pid=41 code=143 elapsed=12s note=...` | Action line |
| `RUNLONG` | The job ran past `heavy_slots.long_run` (once per job; 0 turns it off) | `SLOT RUNLONG acme-tests pid=41 elapsed=1800s note=...` | Action line |

Only action lines reach the Coordinator's Monitor and native delivery; see [watchers](watchers.md).

What the spool guarantees. Each line is one `write(2)` on an `O_APPEND` file with a leading epoch, so lines of jobs that end together do not interleave. A job writes at most one `KILLED` or `FAIL` line, and none when it passes; a process left over after a normal exit is cleaned up without a line. A line can still be lost: when it is written at the instant the watcher renames the file, when the watcher crashes between the claim and the feed write, when the file is over 64 KB (the job then adds nothing), or when it is older than 15 minutes at the next watcher pass (it is dropped, not replayed). If the watcher cannot claim or read the file, balance reports `BALANCE READFAIL slot-events`.

The rules for workers and the Coordinator are in [Architecture](architecture.md#-heavy-slots).
