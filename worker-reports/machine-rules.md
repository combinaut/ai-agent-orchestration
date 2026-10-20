MACHINE RULES (template: copy this into every worker brief; edit the project-specific list at the end).
Many sessions share one machine, and running out of open file handles can crash it.

- Before ANY heavy job, take a shared slot yourself: `heavy-slot take <architect>-<job> -- <command> [args...]`. Do not hand-write slot files or wait for an architect. It atomically enforces the machine cap, waits up to 1800 seconds, and prints `NEED SLOT` when it expires. Heavy = test runners (except one file with one worker), browser specs, Playwright (local or remote), screenshot runs, Storybook, dev servers, local front-end builds. Adjust this list to the project. Codex workers must run `heavy-slot take` escalated because its slot directory is outside the sandbox; `heavy-slot: cannot write <dir> (sandbox?); run this command escalated` means re-run escalated, not wait.
- Inspect current capacity with `heavy-slot count` (or `heavy-slot list` for names, pids, ages, and stale records). Slots remain files in `~/.claude/heavy-slots`; the helper writes the child pid and removes its own file on every normal exit or handled signal.
- Run the narrowest command (a single spec file), 1 worker, one run at a time.
- In a repo with `guard.big_repo = true`, search with `rg` scoped to directories; never `git grep` or whole-tree `git cat-file` batch reads (`--batch`, `--batch-check` or `--batch-all-objects`); read another ref with `git show <ref>:<path>`.
- Kill only by pidfile, never `pkill` or a match on process names.
- Check `sysctl kern.num_files` before each run and note the number. Stop if your run adds far more on top of what you measured just before.
- Never change shared config to get past a block: the repo's `.git/config`, global gitconfig, `CLAUDE.md`, settings, credentials. A blocked worker stops and reports `BLOCKED: …`.
- Never open an interactive question box (Codex's picker, any prompt that waits for a keypress). End your turn with a plain-text `NEED SLOT: …` or `QUESTION: …` line and stop. `SLOT: go` from the architect means run now.
- Codex workers: a sandbox denial of a write to .git is not a block; ask for an escalated run of git add, commit, push and gh pr create through your normal approval flow; stop only if that approval is denied or signing fails.
- Codex workers: the sandbox has no socket to the 1Password desktop app, so `op` fails inside it. Run every command that reads 1Password as an escalated command through the same approval flow. `BLOCKED: op` only if op fails outside the sandbox too.
- When done, make sure nothing you started is still running, and your slot file is gone.
- 1Password: every `op` call from a new process can prompt the user (biometric unlock). Read each secret AT MOST ONCE per job, at the start, into environment variables, and reuse them. Never an extra `op read` to "prime" op, never per test, per page load or per retry. Prefer one `op run --env-file` / `op inject` for all secrets over many `op read`s. If a run needs secrets again, reuse the exported env, do not call op again.
- Never drive the user's browser or desktop: no AppleScript/osascript UI scripting, no computer-use tools, no pasting into web pages, no opening pages on your own. PR text and images go through gh (`gh pr edit --body-file`; images committed to the branch or attached via the API). Browser checks use headless Playwright in your own worktree. The architect may grant a named exception in the brief; without one, the ban holds.
- When the repo sets `review.finalize_commands`, run each command on your branch before calling the PR done or ready, send the architect each findings table and wait, then apply only the approved findings.

PROJECT-SPECIFIC RULES (the architect fills this in per project; delete what does not apply):
- Dev servers: <the project's launcher, e.g. a script that leases an instance; never a global dev-stack bring-up>.
- Worktrees: <where they live; remove a worktree when its PR merges>.
- PR rules: <screenshots, reviewers, anything else the project requires>.
