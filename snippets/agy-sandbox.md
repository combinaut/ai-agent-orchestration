---
trigger: always_on
---
# Terminal sandbox (ai-agent-orchestration)

Run these with `BypassSandbox: true` on the first try; they cannot work inside the terminal sandbox:

- `orch-signal` (heartbeat, ask, done): it talks to the herdr socket.
- `git commit`, `git push`, and any git command that writes to `.git`: the sandbox keeps `.git` read-only.
- `gh`, `github-write`, `git fetch`, `git ls-remote` and anything else that needs the network: the sandbox has none.
- `herdr` and anything else that needs a Unix socket: the sandbox cannot open one.

Run everything else in the sandbox. It can read the orch checkout, your git config, the repo's git dir and `~/.claude/worker-reports`, and write the heavy-slot folder and your own report folder: the subfolder of `~/.claude/worker-reports` that your brief names for the report. Write reports there with your file tools.
