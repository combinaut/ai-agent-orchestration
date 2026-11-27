---
trigger: always_on
---
# Terminal sandbox (ai-agent-orchestration)

Run these with `BypassSandbox: true` on the first try; they cannot work inside the terminal sandbox:

- `orch-signal` (heartbeat, ask, done): it talks to the herdr socket.
- `git commit`, `git push`, and any git command that writes to `.git`: the sandbox keeps `.git` read-only.
- `gh` and `github-write`: the sandbox has no network.

Run everything else in the sandbox. It can read the orch checkout, your git config and the repo's git dir, and write `~/.claude/worker-reports` and the heavy-slot folder. Write reports there with your file tools.
