# Workers, recycles and handoffs

- A **worker** does one task in its own pane and worktree, and writes a report to the path in its brief. You can watch it and type into its pane.
- Every worker carries an `owner` tag naming its architect. Herdr groups panes by repo, so without the tag a worker shows under the wrong architect. Radar draws the Coordinator → architect → worker tree from the tags.
- A **recycle** replaces a session that has used `ORCH_RECYCLE_TOKENS` tokens. The session writes a bounded handoff file to its memory, a fresh session starts from that file, and the watchers keep sending to the new session once it runs `bind-self.py` again. With `ORCH_PARK_IDLE="1"`, an idle architect writes its handoff and exits, and the Coordinator restarts it when work arrives.
- To switch models, recycle onto the new model; a live session is never switched.

How the roles fit together: [`architecture.md`](architecture.md).
