# The Bash guard

`hooks/guard-bash.py` runs before every Bash tool call in Claude Code. It reads the command and never runs it. It denies:

- `git push` to `main` or `master` (including `--all`, `--mirror`, `:` and glob refspecs). Push a branch and open a PR. GitHub wiki remotes are allowed.
- AI attribution in commit messages, PR titles and PR bodies, such as `Co-Authored-By: Claude` or "Generated with Claude Code".
- Broad process kills: `pkill`, or `pgrep -f` fed into `kill`. Stop your own pid.
- Broad process kills: `killall` with a dev-server name (`node`, `ruby`, `puma`, `vite`, `rails`, `bundle`, `sidekiq`, `esbuild`, `foreman`, `overmind`, `bin/dev`, `dev-instance`) or with `-m`.
- In a repo that has linked worktrees: `git fetch` or `git pull` with `--depth`, `--deepen`, `--shallow-*` or `--unshallow`, and any `git stash` except `list` and `show`. Both change history or state the worktrees share.
- In repos whose `.orchestration.toml` or `.orchestration.local.toml` sets `guard.big_repo = true`: every `git grep`, even with a path, and bulk `git cat-file --batch`, `--batch-check` or `--batch-all-objects` reads. Use `rg` on the directories you need.

If the guard itself breaks, it lets the command through and logs the error to `~/.claude/hooks/guard-bash.log`. The installer checks after linking that the guard denies a forbidden command.
