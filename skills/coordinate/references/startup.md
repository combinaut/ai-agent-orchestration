# Required command walkthroughs

Read this file before the action named by its SKILL.md link. All source command requirements and caveats below remain in force.

1. Read `~/.claude/coordinator/status/<architect>.md` files first for open PRs, waits-on, and next step.
2. `ListAgents`. When you need Herdr pane, status, workspace or owner data, run `orch-agents` (one line per agent), never the raw `herdr agent list` JSON (about 10k tokens); it is not a startup read. Every interactive session named `Architect::…` is yours; so are any named helper sessions the user points out, for reading only. Announce your Coordinator session name once through the native channel as an FYI ending with "no reply needed"; do not request a status acknowledgement from every architect. When the user gives an architect an instruction that touches others, the architect tells you.
3. Ask only architects whose status file is missing, older than their last state change, or older than 2 hours. Build the initial overview from status files and necessary replies.
4. Tag your own pane: `herdr pane report-metadata "$HERDR_PANE_ID" --source orchestrate --token role=coordinator`.
5. Stop every Monitor you inherited. Then arm exactly one Monitor on `~/.claude/coordinator/watchers/feed-monitor.sh` (persistent). It keeps its heartbeat fresh while it runs.
6. Run `python3 ~/.claude/coordinator/watchers/bind-self.py --output ~/.claude/coordinator/watchers/bindings/coordinator.json`. Run it as one Bash call on its own (never chained with `&&`, `;` or a pipe; auto mode denies a chained call). `install.sh` installs the allow rule for exactly this command, so run it yourself and do not ask the user. If auto mode still blocks it, tell the user once.
7. Run `~/.claude/coordinator/todo-pane.sh`. It opens the to-do pane (label `To-do`) beside your pane without focus, and does nothing when a pane with that label is open, so a recycle successor keeps the existing one. Exit 69 means `HERDR_PANE_ID` is not set; tell the user.
