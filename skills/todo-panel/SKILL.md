---
name: todo-panel
description: Publish the user's to-do list as a private claude.ai Artifact panel, apply the answers the user submitted there, and republish. Use as the Coordinator when the user asks for the to-do panel, when a turn carries a comment sent to Claude on the panel URL, and once after each Coordinator recycle.
user-invocable: true
---

# To-do panel

The panel is a private claude.ai Artifact. It shows the open items of the to-do list, takes one answer per item, and has one Submit button. Submit sends every item that has an answer, and those items leave the panel once you apply them. Dismiss stays in the terminal view (`user-todo-view.py`); the panel only answers.

Run every step below in order, every time. Each step is safe to run again.

Data side: `python3 ~/.claude/coordinator/todo-panel.py` (`render`, `apply`). State lives in `~/.claude/coordinator/todo-panel/`. Page files: `~/.claude/coordinator/todo-panel.html` and `todo-panel.js`. Plain items (no `[n: label]` choices) take free text, and the queue accepts it.

## 1. Find the panel

Read `~/.claude/coordinator/todo-panel/panel.json`. It holds `{"url": "<artifact url>"}` after the first run. With no file or no URL, this is the first run: skip to step 3 and publish without `url`.

With a URL:

1. Read the artifact with the Artifact tool, `action: "read"`, `url: <url>`. A publish to an artifact from an earlier session is refused until the session has read it.
2. Read its answers file: `action: "read"`, `url: <url>`, `path: "answers.json"`. The tool saves the file to disk and prints where. If the artifact has no `answers.json`, there are no new answers: go to step 3.

## 2. Apply

If step 1 saved an answers file, run one Bash call on its own (no `&&`, `;` or pipe):

```
python3 ~/.claude/coordinator/todo-panel.py apply <saved answers.json path>
```

It prints one line per answer: `queued`, `failed <reason>` or `skipped` (an id among the latest 1,000 applied; it is not queued again). The reasons are kept and shown on the item at the next render. Apply uses the item's generation from the last render, so an item that changed since then fails with a stale reason instead of sending a wrong answer. The changed item has a new key, so the next render shows it without that reason; tell the user in your answer.

## 3. Render and publish

Render into the scratchpad directory so the Artifact tool may read the files (replace `<scratchpad>` with your scratchpad path):

```
python3 ~/.claude/coordinator/todo-panel.py render --stage <scratchpad>/todo-panel
```

Publish with the Artifact tool:

- `file_path`: `<scratchpad>/todo-panel/todo-panel.html`
- `root`: `<scratchpad>/todo-panel`
- `files`: `{"todo-panel.js": "todo-panel.js", "items.json": "items.json", "answers.json": null}`. The `null` clears the answers you just applied.
- With a URL: pass `url`. First publish only: pass `capabilities: {"artifact": {}, "comments": {}}`, `icon: "checklist"` and a one-sentence `description`. Never pass `capabilities` or `icon` on a later publish.

If the publish is refused because a newer version exists, the user's Submit landed after your read, and publishing now would clear answers you have not applied. Go back to step 1, read the new `answers.json`, apply it, render and publish again. Step 2 skips ids among the latest 1,000 it applied.

## 4. Save

Write the artifact URL to `~/.claude/coordinator/todo-panel/panel.json` as `{"url": "<url>"}`. Reply to the user with the link and one line: how many items are in the panel and how many answers you queued.

## 5. When to run it again

- On a turn that carries a comment sent to Claude on the panel URL. Submit publishes `answers.json`, then the page sends the comment "N answers submitted on the to-do panel. Run /todo-panel." A publish alone starts no turn. The platform posts the first reply in the thread itself, so do not post another. Run the steps, and when the republish is done resolve the thread with the `ArtifactComments` tool (`action: "resolve"`). Put `Queued N. Failed M: <reason>.` in your answer to the user, not in the thread.
- Once after each Coordinator recycle. A new session must read and publish to the panel to watch it: it reads the URL from `panel.json` and runs these steps.
- When the user asks to refresh the panel. The list only changes when you render.

If the user's Submit and your publish cross, the page reloads to the newest version and their typed answers come back from that tab's session storage; they press Submit again.
