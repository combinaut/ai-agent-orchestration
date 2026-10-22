# Heavy jobs: `heavy-slot`

Many sessions share one machine. Too many test runs, browsers or builds at once can exhaust open files and memory. `heavy-slot` is a shared cap of `ORCH_HEAVY_SLOTS_MAX` jobs.

```bash
heavy-slot take acme-tests -- npm test   # waits up to 1800 s for a free slot, then runs
heavy-slot count                         # slots in use
heavy-slot list                          # names, pids, ages, stale records
```

A slot is a file in `~/.claude/heavy-slots/` that holds the job's pid. `take` removes it when the job ends.

- If the cap stays full, `take` prints `NEED SLOT`.
- `take` exits 73 when the slot directory is not writable (for example in a sandbox). Run it again with write access to that directory.
- Never create slot files by hand. Stop jobs by pid, not with `pkill`.
- The audit reports `STALESLOT` for a file whose pid is gone.
- The balance watcher uses the same cap.

The rules for workers and the Coordinator are in [Architecture](architecture.md#-heavy-slots).
