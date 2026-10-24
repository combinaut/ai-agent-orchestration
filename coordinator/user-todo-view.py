#!/usr/bin/env python3
"""Live view of the user to-do list with Ignore and Reminder buttons under every item."""

import codecs
import fcntl
import importlib.util
import json
import os
import re
import select
import shutil
import signal
import sys
import tempfile
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "watchers"))
import feed_attach  # noqa: E402
import todo_answers  # noqa: E402
from reminder_picker import (  # noqa: E402
    PICK_KEYS,
    RESET,
    SGR,
    TYPE_HINT,
    button_rows,
    cells,
    edit_entry,
    entry_event,
    paint,
    picker_lines,
    remind_until,
    wrap,
)

COORDINATOR = Path.home() / ".claude/coordinator"


def configured_output():
    """ORCH_USER_TODO_OUTPUT from the environment, else from the settings file; empty when neither sets it."""
    if os.environ.get("ORCH_USER_TODO_OUTPUT"):
        return os.environ["ORCH_USER_TODO_OUTPUT"]
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import orch_settings

        return orch_settings.resolved_global(
            orch_settings.config_path(os.environ), os.environ
        )["ORCH_USER_TODO_OUTPUT"]
    except Exception:
        return ""


TODO = Path(configured_output() or COORDINATOR / "user-todo.md")
HIDDEN = TODO.with_name("user-todo-state.json")
FEED = feed_attach.FEED
HIDE_AFTER = 30
TARGET = re.compile(r"->\s*(\S.*?)\s*$")
AGE_PREFIX = re.compile(r"\[(?:now|\d+[mhd])\] ")
MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
BUTTONS = (
    ("i", "ignore", "Ignore"),
    ("r", "remind", "Reminder"),
)
CANCEL = ("r", "cancel", "Cancel reminder")
GUTTER = 2
FOOTER = "↑↓ or 1-9 select · i ignore · r reminder · s show reminders"
SHOW_HINT = "s show reminders"
CLOSE_HINT = "Esc/q close"
DEMO = [
    (
        "## Architect::Billing.md",
        "- [1h] Merge approved for 128 after reviews -> https://github.com/example/shop/pull/128",
    ),
    (
        "## Architect::Tooling.md",
        "- [3h] Which PR to open first (131 or 120) -> Architect::Frontend",
    ),
    (
        "## Architect::Setup.md",
        "- [now] Audit done: 12 fixes ranked; say which to start",
    ),
]


def parse(text):
    """Items as (owner, text) from the generated list; the age tag is left out so it never changes an item's identity."""
    owner, items = "", []
    for line in text.splitlines():
        if line.startswith("## "):
            owner = line[3:].removesuffix(".md")
        elif line.startswith("- ") and owner:
            items.append((owner, AGE_PREFIX.sub("", line[2:], count=1)))
    return items


def key(owner, text):
    return owner + "\0" + text


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def feed_line(owner, text, now):
    clean = " ".join("".join(c for c in text if c.isprintable() or c == " ").split())
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
    return f"{stamp} todo: TODO IGNORED {'_'.join(owner.split())}: {clean} (start)\n"


def key_action(action, rows, index):
    """The r key cancels the reminder on a reminder row instead of opening the picker."""
    return (
        "cancel"
        if action == "remind" and 0 <= index < len(rows) and rows[index][3]
        else action
    )


def reminders_stay(showing, rows, index):
    """The reminder list stays open only while the selected row is itself a reminder."""
    return showing and 0 <= index < len(rows) and rows[index][3] != ""


def reminders_idle(showing, last_input, now):
    return showing and now - last_input <= HIDE_AFTER


def wall():
    return time.time()


def mono():
    return time.monotonic()


class Hidden:
    """Ignored items and reminders, keyed by owner and item text."""

    def __init__(self, path, feed, clock=None):
        self.path, self.feed, self.clock = Path(path), Path(feed), clock or wall
        self.ignored, self.later = set(), {}
        self.flagged = set()
        self.reload()

    def reload(self):
        try:
            data = json.loads(self.path.read_text())
            self.ignored = set(data.get("ignored", []))
            self.later = {k: float(v) for k, v in data.get("later", {}).items()}
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def save(self):
        atomic_write(
            self.path,
            json.dumps(
                {"ignored": sorted(self.ignored), "later": self.later}, sort_keys=True
            ),
        )

    def lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.with_suffix(".lock").open("a")
        fcntl.flock(stream, fcntl.LOCK_EX)
        return stream

    def ignore(self, owner, text):
        with self.lock():
            self.reload()
            self.ignored.add(key(owner, text))
            self.later.pop(key(owner, text), None)
            self.save()
        try:
            feed_attach.append_line(self.feed, feed_line(owner, text, self.clock()))
        except (OSError, TimeoutError):
            return False
        return True

    def remind(self, owner, text, until):
        with self.lock():
            self.reload()
            self.later[key(owner, text)] = until
            self.flagged.discard(key(owner, text))
            self.save()

    def cancel(self, owner, text):
        with self.lock():
            self.reload()
            self.later.pop(key(owner, text), None)
            self.flagged.discard(key(owner, text))
            self.save()

    def visible(self, items, show_hidden=False):
        """Rows (owner, text, flagged, note); a snooze that has run out flags the item until the view restarts."""
        now, shown, expired = self.clock(), [], []
        for owner, text in items:
            k = key(owner, text)
            note = ""
            if k in self.ignored:
                continue
            if k in self.later:
                if now < self.later[k]:
                    note = "back at " + time.strftime(
                        "%H:%M", time.localtime(self.later[k])
                    )
                else:
                    expired.append(k)
                    self.flagged.add(k)
            if note and not show_hidden:
                continue
            shown.append((owner, text, k in self.flagged, note))
        if expired:
            with self.lock():
                self.reload()
                for k in expired:
                    if self.later.get(k, now + 1) <= now:
                        del self.later[k]
                self.save()
        return shown

    def open_count(self, items):
        return sum(key(*item) not in self.ignored for item in items)

    def hidden_count(self, items):
        now = self.clock()
        return sum(
            key(*item) not in self.ignored and self.later.get(key(*item), 0) > now
            for item in items
        )


def fit(line, width):
    """The line cut to width terminal cells; colour codes cost none."""
    out, used = [], 0
    for part in SGR.split(line):
        if SGR.fullmatch(part):
            out.append(part)
            continue
        for char in part:
            used += cells(char)
            if used > width:
                return "".join(out) + RESET
            out.append(char)
    return "".join(out)


def label(text, flagged, note=""):
    m = TARGET.search(text)
    body = TARGET.sub("", text).rstrip()
    tag = ""
    if m:
        short = (
            "PR on GitHub"
            if m.group(1).startswith("http")
            else m.group(1).split("::", 1)[-1]
        )
        tag = f"  [{short}]"
    mark = "reminder: " if flagged else ""
    return f"{mark}{body}{tag}" + (f"  ({note})" if note else "")


def shorten(body, rows, width):
    """The message cut to `rows` lines, the last one ending in an ellipsis."""
    if len(body) <= rows:
        return body
    body = body[:rows]
    if body:
        body[-1] = fit(body[-1], max(1, width - 1)).rstrip() + "…"
    return body


def footer_rows(width, popup=False, reminders=True):
    """The footer hint broken only at its ' · ' separators; popup mode adds the close hint, no reminders drops the s hint."""
    rows = []
    for part in [p for p in FOOTER.split(" · ") if reminders or p != SHOW_HINT] + (
        [CLOSE_HINT] if popup else []
    ):
        if rows and cells(rows[-1]) + 3 + cells(part) <= width:
            rows[-1] += " · " + part
        else:
            rows.append(part)
    return rows


def answer_lines(editor, width):
    if editor.mode == "type":
        rows = wrap(editor.prompt(), width)
        return rows[-4:], []
    entries = tuple(
        (key, "answer-" + key, value) for key, value in editor.options.items()
    ) + (("t", "answer-t", "Type answer"),)
    rows, spans = button_rows(width, False, True, entries, "answer")
    return ["Answer:"] + rows, [
        (offset + 2, first, last, action) for offset, first, last, action in spans
    ]


def layout(
    rows,
    width,
    hidden_count,
    selected=0,
    showing=False,
    picker=None,
    open_count=None,
    height=None,
    popup=False,
):
    """Screen lines (none wider than width) and mouse hits for the item rows (owner, text, flagged, note);
    hits are (line, first, last, action, index) with 1-based lines and columns. With a height, the selected item's
    owner line, buttons and picker stay whole and its message is cut to what is left."""
    open_count = len(rows) if open_count is None else open_count
    width = max(1, width)
    lines, hits = (
        [f"\033[2m{open_count} open · {hidden_count} on reminder\033[0m", ""],
        [],
    )
    room = max(1, width - GUTTER)
    for index, (owner, text, flagged, note) in enumerate(rows):
        picked = index == selected
        gutter = f"\033[38;5;141m▌{RESET} " if picked else "  "
        head = [
            f"\033[38;5;141m{row}{RESET}"
            for row in wrap(f"{index + 1}. {owner}", room, indent=0)
        ]
        body = wrap(label(text, flagged, note), room - 1, indent=0)
        entries = (
            tuple(entry for entry in BUTTONS if entry[1] != "remind") + (CANCEL,)
            if note
            else BUTTONS
        )
        if todo_answers.choices(text):
            entries += (("a", "answer", "Answer"),)
        button_lines, spans = button_rows(room, picked, picked, entries)
        extra, extra_spans = (
            (
                answer_lines(picker["answer"], room)
                if "answer" in picker
                else picker_lines(picker, room)
            )
            if picker and picker["index"] == index
            else ([], [])
        )
        if height is not None and picked:
            free = height - 1 - len(button_lines) - len(extra)
            if free < len(head):
                head = []
            body = shorten(body, max(0, free - len(head)), room - 1)
        body = [(("\033[1m" + row + RESET) if picked else row) for row in body]
        hits += [
            (len(lines) + n + 1, GUTTER + 1, width, "focus", index)
            for n in range(len(head))
        ]
        first_row = len(lines) + len(head) + len(body)
        for offset, first, last, action in spans:
            hits.append(
                (first_row + offset + 1, first + GUTTER, last + GUTTER, action, index)
            )
        for line, first, last, action in extra_spans:
            hits.append(
                (
                    first_row + len(button_lines) + line,
                    first + GUTTER,
                    last + GUTTER,
                    action,
                    index,
                )
            )
        lines += [gutter + line for line in head + body + button_lines + extra]
        lines.append("")
    if not rows:
        lines.append(
            "Everything is snoozed." if hidden_count else "Nothing waits on you."
        )
    footer = footer_rows(width - 1, popup, hidden_count > 0)
    if any(todo_answers.choices(text) for _, text, *_ in rows):
        footer += ["a answer selected item"]
    if hidden_count:
        shown = f"[s] {'hide' if showing else 'show'} reminders ({hidden_count})"
        lines += [paint("show", shown, False), ""]
        hits.append((len(lines) - 1, 1, len(shown) + 2, "show", None))
    lines += [f"\033[2m{row}\033[0m" for row in footer]
    return [fit(line, width) for line in lines], hits


def item_range(lines, index):
    """First and last line of item `index`; items end at an empty line and the header ends at the first one."""
    blanks = [n for n, line in enumerate(lines) if line == ""]
    if index + 1 >= len(blanks):
        return 0, 0
    return blanks[index] + 1, blanks[index + 1]


def viewport(lines, hits, selected, top, height):
    """The lines that fit in `height` rows with the selected item in view, the new top, and hits moved to window rows."""
    height = max(1, height)
    if len(lines) <= height:
        top = 0
    else:
        start, end = item_range(lines, selected)
        if end - start + 1 > height or start < top:
            top = start
        elif end >= top + height:
            top = end - height + 1
        top = max(0, min(top, len(lines) - height))
    moved = [
        (row - top, first, last, action, index)
        for row, first, last, action, index in hits
        if 1 <= row - top <= height
    ]
    return top, lines[top : top + height], moved


def hit(hits, column, line):
    for row, first, last, action, index in hits:
        if row == line and first <= column <= last:
            return action, index
    return None


def generator():
    spec = importlib.util.spec_from_file_location(
        "user_todo", Path(__file__).resolve().parent / "user-todo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jump(text):
    return generator().jump({"text": text})


def answer_item(owner, text, answer, generation):
    return generator().answer_from_view(owner, text, answer, TODO, generation)


def report_count(pane, count):
    generator().report_metadata(pane, count)


def load_items(demo):
    if demo:
        return parse("\n".join(line for pair in DEMO for line in pair))
    try:
        return parse(TODO.read_text())
    except OSError:
        return []


def act(hidden, rows, items, action, index, run_jump=jump, until=None):
    """Run one button on the row at index; returns the status line."""
    if action == "show":
        return ""
    if index is None or not 0 <= index < len(rows):
        return ""
    owner, text = rows[index][:2]
    if action == "focus":
        return run_jump(f"{text} -> {owner}")
    if action == "ignore":
        fed = hidden.ignore(owner, text)
        return "removed; it returns only if its text changes" + (
            "" if fed else " (feed not written)"
        )
    if action == "cancel":
        hidden.cancel(owner, text)
        return "reminder cancelled"
    hidden.remind(owner, text, until)
    return "will remind you at " + time.strftime("%H:%M", time.localtime(until))


KEYS = {hotkey: action for hotkey, action, _ in BUTTONS} | {
    "a": "answer",
    "s": "show",
    "q": "quit",
    "\r": "focus",
    "\n": "focus",
}
TOKEN = re.compile(
    r"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b[\[O]([AB])|\x1b[\[O][0-9;<]*[A-Za-z~]|(.)",
    re.S,
)
PENDING = re.compile(r"\x1b(?:[\[O][<0-9;]*)?\Z")


def split_input(buffer):
    """The complete tokens (one key, arrow or mouse report each) and the unfinished escape tail."""
    partial = PENDING.search(buffer)
    pending = partial.group() if partial else ""
    return [
        found.group(0) for found in TOKEN.finditer(buffer[: len(buffer) - len(pending)])
    ], pending


def input_events(buffer, hits, picking=None):
    """Actions in the order they were typed or clicked, and the unfinished escape tail to keep for the next read."""
    tokens, pending = split_input(buffer)
    events = []
    for found in map(TOKEN.fullmatch, tokens):
        button, column, line, kind, arrow, char = found.groups()
        if button is not None:
            clicked = (
                hit(hits, int(column), int(line))
                if button == "0" and kind == "M"
                else None
            )
            if clicked:
                events.append(clicked)
        elif arrow:
            if not picking:
                events.append(("move", 1 if arrow == "B" else -1))
        elif char is None or char == "\x1b":
            continue
        elif picking == "answer":
            events.append(("answer-key", char))
        elif picking == "type":
            event = entry_event(char)
            if event:
                events.append(event)
        elif picking:
            if char in PICK_KEYS:
                events.append((PICK_KEYS[char], None))
        elif char.isdigit():
            events.append(("digit", char))
        elif char in KEYS:
            events.append((KEYS[char], None))
    return events, pending


class App:
    """The screen state; every input and clock tick goes through here so it can run without a terminal."""

    def __init__(
        self,
        hidden,
        demo,
        out,
        size,
        clock=None,
        run_jump=jump,
        report=report_count,
        popup=False,
        run_answer=answer_item,
    ):
        self.run_answer = run_answer
        self.hidden, self.demo, self.out, self.size, self.run_jump = (
            hidden,
            demo,
            out,
            size,
            run_jump,
        )
        self.report, self.reported = report, None
        self.popup, self.closing = popup, False
        self.clock = clock or mono
        self.status, self.items, self.rows, self.hits = "", [], [], []
        self.selected, self.selected_key, self.picker, self.show = 0, None, None, False
        self.buf, self.typed, self.top = "", "", 0
        self.last_refresh, self.shown_size, self.last_input = None, None, self.clock()

    def find(self, item):
        return next(
            (n for n, row in enumerate(self.rows) if (row[0], row[1]) == item), None
        )

    def remember(self):
        row = self.rows[self.selected] if 0 <= self.selected < len(self.rows) else None
        self.selected_key = (row[0], row[1]) if row else None

    def refresh(self):
        self.hidden.reload()
        self.items = load_items(self.demo)
        self.show = self.show and self.hidden.hidden_count(self.items) > 0
        self.rows = self.hidden.visible(self.items, self.show)
        found = self.find(self.selected_key) if self.selected_key else None
        self.selected = (
            found
            if found is not None
            else min(self.selected, max(0, len(self.rows) - 1))
        )
        self.remember()
        if self.picker and self.find(self.picker["key"]) is None:
            if "answer" in self.picker:
                self.picker["gone"] = True
                self.status = "that item is gone; Esc cancels the answer"
            else:
                self.picker, self.status = None, "that item is gone"
        self.last_refresh = self.clock()
        count = self.hidden.open_count(self.items)
        if count != self.reported:
            self.report(os.environ.get("HERDR_PANE_ID"), count)
            self.reported = count
        self.render()

    def tick(self):
        now = self.clock()
        if self.show and not reminders_idle(self.show, self.last_input, now):
            self.show = False
            self.refresh()
            return
        size = self.size()
        if (
            self.last_refresh is None
            or now - self.last_refresh > 3
            or size != self.shown_size
        ):
            self.shown_size = size
            self.refresh()

    def render(self):
        size = self.size()
        columns, rows = size.columns or 80, size.lines or 24
        picker = None
        if self.picker:
            picker = dict(self.picker, index=self.find(self.picker["key"]))
        lines, hits = layout(
            self.rows,
            columns,
            self.hidden.hidden_count(self.items),
            self.selected,
            self.show,
            picker,
            self.hidden.open_count(self.items),
            rows - 1,
            self.popup,
        )
        self.top, window, self.hits = viewport(
            lines, hits, self.selected, self.top, rows - 1
        )
        status = (
            f"\033[{rows};1H\033[2m{fit(self.status, columns)}\033[0m"
            if self.status
            else ""
        )
        self.out.write("\033[H\033[2J" + "\r\n".join(window) + status)
        self.out.flush()

    def remind(self, until):
        index = self.find(self.picker["key"])
        self.picker = None
        if index is None:
            self.status = "that item is gone"
            return
        self.status = act(
            self.hidden, self.rows, self.items, "remind", index, until=until
        )
        self.show = self.show and not self.rows[index][3]

    def apply(self, action, index):
        if (
            self.picker
            and "answer" in self.picker
            and (action == "answer-key" or action.startswith("answer-"))
        ):
            if self.picker.get("gone"):
                self.status = "that item is gone; Esc cancels the answer"
                return
            editor = self.picker["answer"]
            char = index if action == "answer-key" else action.removeprefix("answer-")
            result, value = editor.feed(char)
            self.status = editor.error
            if result == "answer":
                owner, text = self.picker["key"]
                if self.find((owner, text)) is None:
                    self.status = "that item is gone"
                else:
                    try:
                        self.status = self.run_answer(
                            owner, text, value, self.picker["generation"]
                        )
                        self.picker = None
                    except (ValueError, OSError) as error:
                        self.status = str(error)
            elif result == "cancel":
                self.picker = None
            return
        if action == "answer":
            self.selected = self.selected if index is None else index
            if 0 <= self.selected < len(self.rows):
                self.remember()
                options = todo_answers.choices(self.rows[self.selected][1])
                if options:
                    self.picker = {
                        "key": self.selected_key,
                        "mode": "answer",
                        "generation": generator().answer_generation(*self.selected_key),
                        "answer": todo_answers.AnswerInput(options),
                    }
                else:
                    return
                self.typed = ""
            return
        if action == "digit":
            candidate = self.typed + index
            self.typed = candidate if 1 <= int(candidate) <= len(self.rows) else index
            if 1 <= int(self.typed) <= len(self.rows):
                self.selected = int(self.typed) - 1
                self.remember()
                self.show = (
                    reminders_stay(self.show, self.rows, self.selected)
                    if self.rows
                    else self.show
                )
            return
        self.typed = ""
        if self.picker and action in ("char", "backspace", "submit"):
            self.picker["entry"] = edit_entry(self.picker["entry"], action, index)
            if action == "submit":
                until = remind_until(self.picker["entry"], self.hidden.clock())
                if until is None:
                    self.status = TYPE_HINT
                else:
                    self.remind(until)
            return
        if self.picker and action == "type":
            self.picker["mode"] = "type"
            return
        if self.picker and action.startswith("pick"):
            self.remind(remind_until(action, self.hidden.clock()))
            return
        if action == "quit":
            self.closing = self.popup
            return
        action = key_action(
            action, self.rows, self.selected if index is None else index
        )
        if action == "remind":
            self.selected = self.selected if index is None else index
            if 0 <= self.selected < len(self.rows):
                self.remember()
                self.picker = {"key": self.selected_key, "mode": "pick", "entry": ""}
            return
        self.picker = None
        if action == "show":
            self.show = not self.show
        elif action == "move":
            self.selected = min(
                max(0, self.selected + index), max(0, len(self.rows) - 1)
            )
            self.remember()
            self.show = (
                reminders_stay(self.show, self.rows, self.selected)
                if self.rows
                else self.show
            )
        else:
            self.selected = self.selected if index is None else index
            self.remember()
            self.status = act(
                self.hidden, self.rows, self.items, action, self.selected, self.run_jump
            )
            self.show = self.show and not (
                0 <= self.selected < len(self.rows)
                and self.rows[self.selected][3] != ""
            )

    def feed(self, data):
        self.last_input = self.clock()
        tokens, self.buf = split_input(self.buf + data)
        for token in tokens:
            if token == "\x1b" and self.picker and "answer" in self.picker:
                self.picker = None
                self.status = "answer cancelled"
                self.refresh()
                continue
            events = input_events(
                token, self.hits, self.picker and self.picker["mode"]
            )[0]
            for action, index in events:
                self.apply(action, index)
                if self.closing:
                    return
            if events:
                self.refresh()

    def lone_escape(self):
        if self.buf == "\x1b":
            if self.picker and "answer" in self.picker:
                self.status = "answer cancelled"
            self.closing = self.popup and self.picker is None
            self.buf, self.picker = "", None
            self.refresh()


ENTER = "\033[?1000h\033[?1006h\033[?25l"
LEAVE = "\033[?1000l\033[?1006l\033[?25h"


def stop(*_):
    sys.exit(143)


def main():
    demo = "--demo" in sys.argv[1:]
    popup = "--popup" in sys.argv[1:]
    scratch = Path(tempfile.mkdtemp(prefix="user-todo-demo.")) if demo else None
    hidden = (
        Hidden(scratch / "state.json", scratch / "feed.log")
        if demo
        else Hidden(HIDDEN, FEED)
    )
    fd = sys.stdin.fileno()
    previous = {
        number: signal.signal(number, stop)
        for number in (signal.SIGTERM, signal.SIGHUP)
    }
    old = None
    try:
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        sys.stdout.write(ENTER)
        sys.stdout.flush()
        app = App(hidden, demo, sys.stdout, os.get_terminal_size, popup=popup)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        while not app.closing:
            app.tick()
            ready, _, _ = select.select([fd], [], [], 0.3 if app.buf == "\x1b" else 1)
            if not ready:
                app.lone_escape()
                continue
            data = os.read(fd, 64)
            if not data:
                break
            app.feed(decoder.decode(data))
    finally:
        if old is not None:
            sys.stdout.write(LEAVE)
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        for number, handler in previous.items():
            signal.signal(number, handler)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
