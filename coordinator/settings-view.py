#!/usr/bin/env python3
"""Editor for the global settings file, in a terminal or a Herdr popup."""

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "watchers"))
sys.path.insert(0, str(ROOT.parent / "lib"))
import orch_settings  # noqa: E402
import popup_terminal  # noqa: E402
import todo_popup  # noqa: E402
from popup_terminal import ESCAPES, fit, split_input  # noqa: E402
from reminder_picker import RESET, wrap  # noqa: E402

RUN = "at the next script run"
SESSION = "new agent starts use it; a running session at its next recycle"
WATCHER = (
    "after the watchers restart (install.sh restarts services when the file changed)"
)
BOTH = "next script run; watchers after they restart"
INSTALL = "after you run install.sh"
SCRIPTS_AND_SERVICES = "next script run; services after you run install.sh"
WATCHER_SESSION = "watchers after they restart; sessions at their next recycle"
ALL_READERS = "next script run; watchers after restart; sessions at next recycle"
COORDINATOR_PANE = "at the next Coordinator start or recycle (todo-pane.sh)"


def _applies(when, *keys):
    return dict.fromkeys(keys, when)


APPLIES = {
    **_applies(
        RUN,
        "heavy_slots.kill_grace",
        "heavy_slots.long_run",
        "models.pi_models",
        "watchers.closed_dir",
        "workers.ask_timeout_s",
        "pr_tabs.repos",
        "pr_tabs.terminal_app",
        "pr_tabs.org",
        "pr_tabs.default_repo",
        "pr_tabs.open_all",
    ),
    **_applies(
        SESSION,
        "providers.enabled",
        "providers.claude",
        "models.coordinator",
        "models.architect",
        "models.coordinator_effort",
        "models.architect_effort",
        "models.worker_effort",
        "models.reviewer_effort",
        "models.codex_worker",
        "models.codex_smart",
        "models.codex_smart_reasoning_effort",
        "models.pi_worker",
        "models.pi_smart",
        "models.cursor_worker",
        "models.cursor_smart",
        "models.gemini_worker",
        "models.gemini_smart",
        "remote_control.architects",
        "recycle.park_idle",
        "review.finalize_commands",
        "review.finalize_comment",
    ),
    **_applies(
        WATCHER,
        "pr.since",
        "pr.bot_reviewer_login",
        "audit.packs_max",
        "audit.worktrees_max",
        "audit.page_prs",
        "audit.page_nodes",
        "legacy.scans_max",
        "host.pressure_min_pct",
        "host.swap_max_mb",
        "host.procs_max",
        "host.files_max",
        "host.load_max",
        "alerts.escalate_ratio",
        "alerts.digest_s",
        "alerts.quiet_s",
        "hog.gb",
        "hog.git_gb",
        "hog.git_age_s",
        "workers.wait_s",
        "workers.wait_repeat_s",
        "workers.idle_s",
    ),
    **_applies(
        BOTH,
        "legacy.reads_max",
        "heavy_slots.dir",
        "recycle.worker_soft_tokens",
        "recycle.worker_hard_tokens",
        "recycle.worker_ask_timeout_s",
        "watchers.pr_scope",
        "watchers.worker_model",
        "watchers.primary_repo",
        "user_todo.status_dir",
        "user_todo.output",
        "user_todo.state",
        "user_questions.dir",
    ),
    **_applies(
        INSTALL,
        "pr.author",
        "legacy.map_repo",
        "install.repo_path",
        "install.env",
        "install.radar_path",
        "watchers.service",
        "watchers.path",
        "herdr.socket",
        "herdr.todo_popup",
        "herdr.settings_popup",
    ),
    **_applies(SCRIPTS_AND_SERVICES, "watchers.python3", "herdr.bin"),
    **_applies(ALL_READERS, "heavy_slots.max", "recycle.pct"),
    "recycle.tokens": WATCHER_SESSION,
    "herdr.todo_pane": COORDINATOR_PANE,
}

FIELD_MAX = 4096
LIST_MAX = 200
FILTER_MAX = 64
VALUE_COLUMN = 36
REFRESH_S = 3
ENV_NOTE = "Only Herdr's environment is visible here; a variable exported elsewhere is not shown."
MAIN_HINT = (
    "↑↓ select · Enter/space change · ←→ choose · u default · / filter · Esc/q close"
)
LIST_HINT = "Enter edit · a add · x delete · J/K move · s save · Esc discard"
CHECK_HINT = "↑↓ select · space tick · s save · Esc discard"
FIELD_HINT = "Enter ok · Ctrl-U clear · Esc discard"
ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}
ARROW = re.compile(r"\x1b[\[O]([ABCD])")
MOUSE = re.compile(r"\x1b\[<0;(\d+);(\d+)M")


def editor_kind(setting):
    if setting.kind == "bool":
        return "toggle"
    if setting.kind == "list":
        return "checklist" if setting.choices else "list"
    if setting.kind in ("int", "number"):
        return "number"
    return "cycle" if setting.choices else "text"


def clean(text):
    return "".join(c if c.isprintable() else "�" for c in str(text))


def typed_value(setting, text):
    """The value a field's text stands for."""
    if setting.kind == "str":
        return text
    text = text.strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text)
    if setting.kind == "int":
        raise ValueError(f"{setting.key}: not a whole number")
    if re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", text):
        return float(text)
    raise ValueError(f"{setting.key}: not a number")


class Field:
    def __init__(self, text, done):
        self.text, self.done, self.error = text, done, ""


class Editor:
    """The entries of one list setting being changed; nothing is written before save."""

    def __init__(self, setting, original):
        self.setting, self.original = setting, original
        self.checks = setting.choices is not None
        self.items = list(original)
        self.index, self.error = 0, ""

    def rows(self):
        return self.setting.choices if self.checks else self.items

    def move(self, step):
        self.index = min(max(0, self.index + step), max(0, len(self.rows()) - 1))


class App:
    """Screen state, driven by feed(), lone_escape() and tick(); needs no terminal."""

    def __init__(self, environ, out, size):
        self.environ, self.out, self.size = environ, out, size
        self.closing = False
        self.buf, self.status, self.error = "", "", ""
        self.filter, self.filtering = "", False
        self.field, self.editor = None, None
        self.selected, self.top, self.hits = 0, 0, {}
        self.values, self.path, self.create = {}, "", False
        self.last_refresh, self.shown_size = None, None
        self.settings = orch_settings.listed("global")
        self.rows = list(self.settings)

    # --- the file ---

    def load(self):
        self.values, self.error, self.create = {}, "", False
        try:
            self.path = orch_settings.config_path(self.environ)
            data = orch_settings.read_bytes(self.path)
            self.create = data is None
            if data is not None:
                self.values = orch_settings.read_file(self.path, "global")
                orch_settings.check_resolves(self.path, self.path)
        except (orch_settings.ConfigError, OSError) as error:
            self.error = f"ERROR: {error}"
        self.last_refresh = time.monotonic()

    def value(self, setting):
        return self.values.get(setting, setting.default)

    def blocked(self, setting):
        if self.error:
            return self.error
        if setting.env in self.environ:
            return f"env {setting.env} wins; nothing written"
        return ""

    def save(self, setting, value, original):
        """Writes one value; returns the reason it was not written, else an empty string."""
        self.load()
        reason = self.blocked(setting)
        if not reason and self.value(setting) != original:
            reason = "changed in the file while you edited; Esc and open it again"
        if reason:
            return reason
        try:
            orch_settings.edit_setting(
                self.path, setting, value, self.environ, create=self.create
            )
        except (orch_settings.ConfigError, OSError) as error:
            return str(error).removeprefix(f"{self.path}: ")
        self.load()
        self.status = "saved" + self.missing_models(setting)
        return ""

    def missing_models(self, setting):
        if setting.table not in ("providers", "models"):
            return ""
        try:
            values = orch_settings.resolved_global(self.path, self.environ)
            missing = [
                name
                for name in orch_settings.required_models(values)
                if not orch_settings.filled(values.get(name, ""))
            ]
        except orch_settings.ConfigError as error:
            return f"; {error}"
        return f"; still to set: {' '.join(missing)}" if missing else ""

    # --- input ---

    def current(self):
        return self.rows[self.selected] if self.selected < len(self.rows) else None

    def apply_filter(self):
        chosen = self.current()
        wanted = self.filter.lower()
        self.rows = [x for x in self.settings if wanted in x.key.lower()]
        self.selected = self.rows.index(chosen) if chosen in self.rows else 0

    def change(self, action):
        """Enter, space, an arrow or u on the selected setting."""
        setting = self.current()
        if setting is None:
            return
        reason = self.blocked(setting)
        if reason:
            self.status = reason
            return
        kind, value = editor_kind(setting), self.value(setting)
        if action == "default":
            self.status = self.save(setting, setting.default, value) or self.status
        elif kind == "toggle" and action in ("enter", "space"):
            self.status = self.save(setting, not value, value) or self.status
        elif kind == "cycle":
            choices = ["", *setting.choices]
            step = -1 if action == "left" else 1
            chosen = choices[(choices.index(value) + step) % len(choices)]
            self.status = self.save(setting, chosen, value) or self.status
        elif action != "enter":
            return
        elif kind in ("list", "checklist"):
            self.editor = Editor(setting, value)
        elif kind in ("number", "text"):

            def done(text):
                try:
                    new = typed_value(setting, text)
                except ValueError as error:
                    return str(error)
                return self.save(setting, new, value)

            self.field = Field(str(value), done)

    def field_key(self, token):
        field = self.field
        if token in "\r\n":
            field.error = field.done(field.text)
            if not field.error:
                self.field = None
        elif token in ("\x7f", "\b"):
            field.text = field.text[:-1]
        elif token == "\x15":
            field.text = ""
        elif len(token) == 1 and token.isprintable() and len(field.text) < FIELD_MAX:
            field.text += token

    def entry_field(self, position):
        """The field for the list entry at position; None adds one at the end."""
        editor = self.editor

        def done(text):
            if not text:
                return "an entry cannot be empty"
            if position is None:
                editor.items.append(text)
                editor.index = len(editor.items) - 1
            else:
                editor.items[position] = text
            return ""

        self.field = Field("" if position is None else editor.items[position], done)

    def editor_key(self, token):
        editor = self.editor
        if token in ("up", "down"):
            editor.move(-1 if token == "up" else 1)
        elif token == "s":
            items = editor.items
            if editor.checks:
                items = [x for x in editor.setting.choices if x in items]
            editor.error = self.save(editor.setting, items, editor.original)
            if not editor.error:
                self.editor = None
        elif editor.checks:
            if token == " ":
                choice = editor.setting.choices[editor.index]
                if choice in editor.items:
                    editor.items.remove(choice)
                else:
                    editor.items.append(choice)
        elif token == "a":
            if len(editor.items) < LIST_MAX:
                self.entry_field(None)
            else:
                editor.error = f"at most {LIST_MAX} entries"
        elif not editor.items:
            return
        elif token in "\r\n":
            self.entry_field(editor.index)
        elif token == "x":
            del editor.items[editor.index]
            editor.move(0)
        elif token in ("J", "K"):
            old = editor.index
            editor.move(1 if token == "J" else -1)
            items = editor.items
            items[old], items[editor.index] = items[editor.index], items[old]

    def key(self, token):
        arrow = ARROW.fullmatch(token)
        if arrow:
            token = ARROWS[arrow[1]]
        elif len(token) != 1:
            return
        if self.field:
            if len(token) == 1:
                self.field_key(token)
        elif self.editor:
            self.editor_key(token)
        elif self.filtering:
            if token in ("\r", "\n"):
                self.filtering = False
            elif token in ("\x7f", "\b"):
                self.filter = self.filter[:-1]
            elif len(token) == 1 and token.isprintable():
                self.filter = (self.filter + token)[:FILTER_MAX]
            self.apply_filter()
        elif token in ("up", "down"):
            step = -1 if token == "up" else 1
            self.selected = min(
                max(0, self.selected + step), max(0, len(self.rows) - 1)
            )
            self.status = ""
        elif token == "/":
            self.filtering = True
        elif token == "q":
            self.closing = True
        else:
            action = {
                "\r": "enter",
                "\n": "enter",
                " ": "space",
                "u": "default",
                "left": "left",
                "right": "right",
            }.get(token)
            if action:
                self.change(action)

    def escape(self):
        if self.field:
            self.field = None
        elif self.editor:
            self.editor, self.status = None, "list changes discarded"
        elif self.filtering or self.filter:
            self.filtering, self.filter = False, ""
            self.apply_filter()
        else:
            self.closing = True

    def click(self, column, line):
        if self.field or self.editor or line not in self.hits:
            return
        self.selected, self.status = self.hits[line], ""
        if column >= VALUE_COLUMN:
            kind = editor_kind(self.current())
            self.change("right" if kind == "cycle" else "enter")

    def feed(self, data):
        tokens, self.buf = split_input(self.buf + data)
        for token in tokens:
            clicked = MOUSE.fullmatch(token)
            if token in ESCAPES:
                self.escape()
            elif clicked:
                self.click(int(clicked[1]), int(clicked[2]))
            else:
                self.key(token)
            if self.closing:
                return
            self.render()

    def lone_escape(self):
        if self.buf == "\x1b":
            self.buf = ""
            self.escape()
            if not self.closing:
                self.render()

    def tick(self):
        size = self.size()
        stale = (
            self.last_refresh is None
            or time.monotonic() - self.last_refresh > REFRESH_S
        )
        if stale:
            self.load()
        if stale or size != self.shown_size:
            self.shown_size = size
            self.render()

    # --- screen ---

    def shown(self, setting):
        kind = editor_kind(setting)
        if setting.env in self.environ:
            return f"{clean(self.environ[setting.env])}  env {setting.env} wins"
        value = self.value(setting)
        if kind == "toggle":
            text = "[x] on" if value else "[ ] off"
        elif kind == "cycle":
            text = f"< {value or 'empty'} >"
        elif kind == "checklist":
            text = f"{', '.join(value) or 'automatic'} >"
        elif kind == "list":
            text = f"{len(value)} {'entry' if len(value) == 1 else 'entries'} >"
        elif kind == "number":
            text = str(value)
        else:
            text = f'"{value}"'
        return clean(text) + ("" if setting in self.values else "  default")

    def body(self):
        """The scrolling lines, the line to keep visible and {line: row index}."""
        lines, rows, focus = [], {}, 0
        if self.editor:
            editor = self.editor
            lines.append(editor.setting.key)
            for index, item in enumerate(editor.rows()):
                tick = ""
                if editor.checks:
                    tick = "[x] " if item in editor.items else "[ ] "
                mark = ">" if index == editor.index else " "
                if index == editor.index:
                    focus = len(lines)
                lines.append(f" {mark} {tick}{clean(item)}")
            if not editor.rows():
                lines.append("   (no entries)")
            return lines, focus, rows
        table = None
        for index, setting in enumerate(self.rows):
            if setting.table != table:
                table = setting.table
                lines.append(f"\033[1m[{table}]{RESET}")
            picked = index == self.selected
            if picked:
                focus = len(lines)
            rows[len(lines)] = index
            name = f"{'>' if picked else ' '} {setting.name} "
            text = f"{name:<{VALUE_COLUMN - 1}}{self.shown(setting)}"
            lines.append(f"\033[7m{text}{RESET}" if picked else text)
        if not self.rows:
            lines.append("no setting matches")
        return lines, focus, rows

    def footer(self, width):
        lines = ["─" * width]
        if self.field:
            room = max(1, width - 8)
            lines.append(f"edit: {clean(self.field.text)[-room:]}▏")
        error = (self.field and self.field.error) or (self.editor and self.editor.error)
        if error:
            lines += wrap(clean(error), width)[:3]
        setting = self.editor.setting if self.editor else self.current()
        if setting:
            lines += wrap(clean(f"{setting.key}: {setting.doc}"), width)[:3]
            lines += wrap(f"Applies: {APPLIES[setting.key]}", width)[:2]
        lines += wrap(ENV_NOTE, width)[:2]
        if self.field:
            hint = FIELD_HINT
        elif self.editor:
            hint = CHECK_HINT if self.editor.checks else LIST_HINT
        else:
            hint = MAIN_HINT
        return lines + [f"\033[2m{row}{RESET}" for row in wrap(hint, width)[:2]]

    def render(self):
        size = self.size()
        width, height = size.columns or 80, size.lines or 24
        head = [f"Settings  {clean(self.path)}"]
        if self.error:
            head += wrap(clean(self.error), width)[:4]
        if self.filtering or self.filter:
            head.append(
                f"/ filter: {clean(self.filter)}{'▏' if self.filtering else ''}"
            )
        body, focus, rows = self.body()
        footer = self.footer(width)
        room = max(1, height - 1 - len(head))
        footer = footer[-max(0, room - 3) :] if room > 3 else []
        room = max(1, room - len(footer))
        if focus < self.top + 1:
            self.top = max(0, focus - 1)
        elif focus >= self.top + room:
            self.top = focus - room + 1
        self.top = max(0, min(self.top, len(body) - room))
        window = body[self.top : self.top + room]
        self.hits = {
            len(head) + line - self.top + 1: index
            for line, index in rows.items()
            if 0 <= line - self.top < len(window)
        }
        lines = [fit(line, width) for line in head + window + footer]
        status = (
            f"\033[{height};1H\033[2m{fit(clean(self.status), width)}{RESET}"
            if self.status
            else ""
        )
        self.out.write("\033[H\033[2J" + "\r\n".join(lines) + status)
        self.out.flush()


def stop(*_):
    sys.exit(143)


def main():
    todo_popup.configure(
        __file__,
        "combinaut.settings",
        "ORCH_SETTINGS_POPUP_TOKEN",
        "settings-popup",
        "settings popup",
    )
    if "--open-popup" in sys.argv[1:]:
        return todo_popup.open_popup()
    popup = "--popup" in sys.argv[1:]
    popup_terminal.serve(
        lambda out: App(os.environ, out, os.get_terminal_size),
        popup,
        stop,
    )


if __name__ == "__main__":
    sys.exit(main())
