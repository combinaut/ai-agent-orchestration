# The to-do pane

Architects keep a `## Waits on the user` list in `~/.claude/coordinator/status/<architect>.md`. The user-todo watcher merges those lists into `~/.claude/coordinator/user-todo.md`. To show it in a pane:

```bash
python3 ~/.claude/coordinator/user-todo.py --follow
```

`user-todo.py` without `--follow` writes the file once. In the pane, press a number then Enter (or click) to jump to the item. `n` and `p` change page. A jump removes the item from the list. An empty list reads `Nothing waits on you.`

For buttons instead, run `python3 ~/.claude/coordinator/user-todo-view.py` (`--demo` shows sample items). Clicking an item's architect heading focuses that architect's pane. Each item has Open, Ignore and Reminder buttons (click, or `o` `i` `r` on the selected item; `↑↓` or a number selects). Open jumps to the target. Ignore removes the item for good: it returns only when the architect writes a new or changed item, and one `todo: TODO IGNORED <architect>: <text> (start)` line goes to the feed so the Coordinator can tell that architect. Reminder asks when (`1`-`5` for 15m, 1h, 2h, 4h, tomorrow 9:00; `t` types `30m` or `3h`) and hides the item until then; reminders are listed under `s` and come back marked `reminder:`; on a reminder item the button reads Cancel reminder. The top line reads `<n> open · <m> on reminder`. The `[s]` toggle and the `s show reminders` footer hint appear only while at least one reminder exists; the list closes when the last one goes. State is in `~/.claude/coordinator/user-todo-state.json` (not the generator's `user-todo.state.json`).

To open it as a Herdr popup on `prefix+t`, pass `--popup` so `Esc` or `q` closes it (the footer then shows `Esc/q close`; Herdr sets no popup marker). Without the flag nothing closes it, so the Coordinator's to-do pane stays up. `Esc` while the reminder choices show only cancels them, and `q` is typed as text in the `t` entry.

```toml
[[keys.command]]
key = "prefix+t"
type = "popup"
command = "python3 ~/.claude/coordinator/user-todo-view.py --popup"
description = "to-do list"
width = "80%"
height = "80%"
```

The Coordinator opens this view itself at start, in a pane labelled `To-do` beside its own (`~/.claude/coordinator/todo-pane.sh`). A pane with that label is kept, so a recycled Coordinator opens no second one. It runs without `--popup`, so nothing closes it.

![The to-do view in the popup, with demo items](todo-view.png)

`install.sh` (herdr env) also makes `prefix+t` open the buttons view as a popup titled "Your to-do". It links the Herdr plugin in `env/herdr/todo-plugin` and adds a marked `[[keys.command]]` block to Herdr's `config.toml`. To turn the popup off, set `todo_popup = false` under `[herdr]` in `~/.claude/orchestration.toml` and run `install.sh`; that removes both. See [docs/settings.md](settings.md).

Question panes (`ask-user-question`) stack below the pane labelled `To-do`.
