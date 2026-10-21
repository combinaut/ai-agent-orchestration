#!/usr/bin/env python3
"""Live view of the user to-do list with Open, Ignore and Reminder buttons under every item."""
import fcntl
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import tty
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

COORDINATOR = Path.home() / ".claude/coordinator"
TODO = Path(os.environ.get("ORCH_USER_TODO_OUTPUT") or COORDINATOR / "user-todo.md")
HIDDEN = TODO.with_name("user-todo-state.json")
FEED = COORDINATOR / "watchers/prod-context/coordinator-feed.log"
PICKS = (("1", "pick1", "15m"), ("2", "pick2", "1h"), ("3", "pick3", "2h"), ("4", "pick4", "4h"),
         ("5", "pick5", "tomorrow 9:00"), ("t", "type", "type"))
DURATIONS = {"pick1": 900, "pick2": 3600, "pick3": 7200, "pick4": 14400}
DURATION = re.compile(r"(\d{1,4})([mh])")
HIDE_AFTER = 30
TYPE_HINT = "use minutes or hours, e.g. 30m or 3h"
TARGET = re.compile(r"->\s*(\S.*?)\s*$")
AGE_PREFIX = re.compile(r"\[(?:now|\d+[mhd])\] ")
MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
BUTTONS = (("o", "open", "Open"), ("i", "ignore", "Ignore"), ("r", "remind", "Reminder"))
CANCEL = ("r", "cancel", "Cancel reminder")
INDENT = 0
GUTTER = 2
FOOTER = "↑↓ or 1-9 select · o open · i ignore · r reminder · s show reminders"
DEMO = [("## Architect::Finder.md", "- [1h] Named merge OK for 32804 after reviews -> https://github.com/combinaut/sparkle/pull/32804"),
        ("## Architect::Tooling.md", "- [3h] Which Carol PR to open first (32780 or 32308) -> Architect::Frontend::Migration"),
        ("## Architect::Setup.md", "- [now] Audit done: 12 fixes ranked; say which to start")]


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


def append_feed(path, line):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def remind_until(choice, now):
    """Epoch seconds a pick or a typed value points at, or None when it is not a valid choice."""
    if choice in DURATIONS:
        return now + DURATIONS[choice]
    if choice == "pick5":
        tomorrow = datetime.fromtimestamp(now) + timedelta(days=1)
        return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    found = DURATION.fullmatch(choice.strip().lower())
    if not found or int(found.group(1)) == 0:
        return None
    return now + int(found.group(1)) * (60 if found.group(2) == "m" else 3600)


def key_action(action, rows, index):
    """The r key cancels the reminder on a reminder row instead of opening the picker."""
    return "cancel" if action == "remind" and 0 <= index < len(rows) and rows[index][3] else action


def reminders_stay(showing, rows, index):
    """The reminder list stays open only while the selected row is itself a reminder."""
    return showing and 0 <= index < len(rows) and rows[index][3] != ""


def reminders_idle(showing, last_input, now):
    return showing and now - last_input <= HIDE_AFTER


class Hidden:
    """Ignored items and reminders, keyed by owner and item text."""

    def __init__(self, path, feed, clock=time.time):
        self.path, self.feed, self.clock = Path(path), Path(feed), clock
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
        atomic_write(self.path, json.dumps({"ignored": sorted(self.ignored), "later": self.later}, sort_keys=True))

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
            append_feed(self.feed, feed_line(owner, text, self.clock()))
        except OSError:
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
        now, shown, changed = self.clock(), [], False
        for owner, text in items:
            k = key(owner, text)
            note = ""
            if k in self.ignored:
                continue
            if k in self.later:
                if now < self.later[k]:
                    note = "back at " + time.strftime("%H:%M", time.localtime(self.later[k]))
                else:
                    del self.later[k]
                    self.flagged.add(k)
                    changed = True
            if note and not show_hidden:
                continue
            shown.append((owner, text, k in self.flagged, note))
        if changed:
            with self.lock():
                self.save()
        return shown

    def open_count(self, items):
        return sum(key(*item) not in self.ignored for item in items)

    def hidden_count(self, items):
        now = self.clock()
        return sum(key(*item) not in self.ignored and self.later.get(key(*item), 0) > now for item in items)


def cells(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


RESET = "\033[0m"
# bright fill in the button's colour on the selected item; one faint neutral tint on every other button
COLORS = {"open": (75, 16), "ignore": (174, 16), "remind": (214, 16), "cancel": (214, 16), "show": (146, 16)}
QUIET = (238, 252)


def paint(action, text, filled):
    colour, ink = COLORS[action]
    if filled:
        return f"\033[1;48;5;{colour};38;5;{ink}m {text} {RESET}"
    return f"\033[48;5;{QUIET[0]};38;5;{QUIET[1]}m {text} {RESET}"


def wrap(text, width, indent=INDENT):
    """Rows broken at spaces; only a word wider than the row is split."""
    rows, current = [], ""
    for word in text.split(" "):
        while cells(word) > width - (indent if rows else 0):
            room = max(1, width - (indent if rows else 0) - cells(current) - (1 if current else 0))
            if current and room < 4:
                rows.append(current)
                current = ""
                continue
            cut = word[:room]
            while cells(cut) > room:
                cut = cut[:-1]
            rows.append((current + " " if current else "") + cut)
            current, word = "", word[len(cut):]
        trial = f"{current} {word}" if current else word
        if current and cells(trial) > width - (indent if rows else 0):
            rows.append(current)
            current = word
        else:
            current = trial
    rows.append(current)
    return [rows[0]] + [" " * indent + row for row in rows[1:]]


def button_rows(width, filled, hints, entries=BUTTONS, tone=None):
    """Styled button rows and the (row offset, first, last, action) of each button, columns 1-based."""
    rows, spans, line, plain = [], [], " " * INDENT, INDENT
    for hotkey, action, name in entries:
        text = f"[{hotkey}] {name}" if hints else f"  {name}  "
        size = len(text) + 2
        if plain > INDENT and plain + size > width:
            rows.append(line)
            line, plain = " " * INDENT, INDENT
        spans.append((len(rows), plain + 1, plain + size, action))
        line += paint(tone or action, text, filled) + " "
        plain += size + 1
    return rows + [line], spans


def label(text, flagged, note=""):
    m = TARGET.search(text)
    body = TARGET.sub("", text).rstrip()
    tag = ""
    if m:
        short = "PR on GitHub" if m.group(1).startswith("http") else m.group(1).split("::", 1)[-1]
        tag = f"  [{short}]"
    mark = "reminder: " if flagged else ""
    return f"{mark}{body}{tag}" + (f"  ({note})" if note else "")


def picker_lines(picker, hits, start, width, index):
    """The inline time choice under an item; its buttons are added to hits."""
    if picker["mode"] == "type":
        return [f"\033[38;5;214mRemind in (30m, 3h): {picker['entry']}▏\033[0m", "\033[2mEnter ok · Esc cancels\033[0m"]
    rows, spans = button_rows(width, False, True, PICKS, "remind")
    for offset, first, last, action in spans:
        hits.append((start + offset + 2, first + GUTTER, last + GUTTER, action, index))
    return ["\033[38;5;214mRemind me in:\033[0m"] + rows


def footer_rows(width):
    """The footer hint broken only at its ' · ' separators."""
    rows = []
    for part in FOOTER.split(" · "):
        if rows and cells(rows[-1]) + 3 + cells(part) <= width:
            rows[-1] += " · " + part
        else:
            rows.append(part)
    return rows


def layout(rows, width, hidden_count, selected=0, showing=False, picker=None, open_count=None):
    """Screen lines and mouse hits for the item rows (owner, text, flagged); hits are (line, first, last, action, index)."""
    open_count = len(rows) if open_count is None else open_count
    lines, hits = [f"\033[2m{open_count} open · {hidden_count} on reminder\033[0m", ""], []
    width = max(INDENT + GUTTER + 20, width)
    for index, (owner, text, flagged, note) in enumerate(rows):
        picked = index == selected
        gutter = f"\033[38;5;141m▌{RESET} " if picked else "  "
        start = len(lines)
        body = wrap(label(text, flagged, note), width - 1 - GUTTER - INDENT, indent=0)
        lines.append(f"\033[38;5;141m{index + 1}. {owner}{RESET}")
        lines += [" " * INDENT + (("\033[1m" + row + RESET) if picked else row) for row in body]
        entries = BUTTONS[:2] + (CANCEL,) if note else BUTTONS
        button_lines, spans = button_rows(width - GUTTER, picked, picked, entries)
        for offset, first, last, action in spans:
            hits.append((len(lines) + offset + 1, first + GUTTER, last + GUTTER, action, index))
        lines += button_lines
        if picker and picker["index"] == index:
            lines += picker_lines(picker, hits, len(lines), width - GUTTER, index)
        lines[start:] = [gutter + line for line in lines[start:]]
        lines.append("")
    if not rows:
        lines.append("Everything is snoozed." if hidden_count else "Nothing waits on you.")
    shown = f"[s] {'hide' if showing else 'show'} reminders ({hidden_count})"
    footer = footer_rows(width - 1)
    lines += [paint("show", shown, False), ""] + [f"\033[2m{row}\033[0m" for row in footer]
    hits.append((len(lines) - len(footer) - 1, 1, len(shown) + 2, "show", None))
    return lines, hits


def hit(hits, column, line):
    for row, first, last, action, index in hits:
        if row == line and first <= column <= last:
            return action, index
    return None


def mouse_clicks(buffer):
    """(column, line) of each left-button press, and the buffer's unfinished tail."""
    clicks = [(int(x), int(y)) for b, x, y, kind in MOUSE.findall(buffer) if b == "0" and kind == "M"]
    rest = MOUSE.sub("", buffer)
    partial = re.search(r"\x1b(?:\[(?:<[0-9;]*)?)?$", rest)
    return clicks, partial.group() if partial else ""


def agent_for(session_name):
    try:
        out = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=5).stdout
        agents = json.loads(out)["result"]["agents"]
    except Exception:
        return None
    hits = [a for a in agents if a.get("terminal_title_stripped") == session_name]
    if not hits:
        return None
    return max(hits, key=lambda a: a.get("state_change_seq", 0))["name"]


def jump(text):
    m = TARGET.search(text)
    if not m:
        return "no target on this item"
    target = m.group(1)
    if target.startswith("http"):
        result = subprocess.run(["open", "-a", "Google Chrome", target])
        return f"opened {target}" if result.returncode == 0 else f"could not open {target}"
    name = agent_for(target) or target
    r = subprocess.run(["herdr", "agent", "focus", name], capture_output=True, text=True)
    return f"jumped to {target}" if r.returncode == 0 else f"could not find {target}"


def load():
    if DEMO_MODE:
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
    if action == "open":
        return run_jump(text)
    if action == "ignore":
        fed = hidden.ignore(owner, text)
        return "removed; it returns only if its text changes" + ("" if fed else " (feed not written)")
    if action == "cancel":
        hidden.cancel(owner, text)
        return "reminder cancelled"
    hidden.remind(owner, text, until)
    return "will remind you at " + time.strftime("%H:%M", time.localtime(until))


def draw(rows, hidden_count, selected, status, showing, picker=None, open_count=None):
    width = os.get_terminal_size().columns or 80
    lines, hits = layout(rows, width, hidden_count, selected, showing, picker, open_count)
    out = "\033[H\033[2J" + "\r\n".join(lines)
    if status:
        out += f"\r\n\033[2m{status}\033[0m"
    sys.stdout.write(out)
    sys.stdout.flush()
    return hits


KEYS = {hotkey: action for hotkey, action, _ in BUTTONS} | {"s": "show", "\r": "open", "\n": "open"}


def key_events(text, typed=""):
    """(action, index) per key; a digit run selects an item, index None means the selected one."""
    events = []
    for char in text:
        if char.isdigit():
            typed += char
            events.append(("select", int(typed) - 1))
        elif char in KEYS:
            typed = ""
            events.append((KEYS[char], None))
    return events, typed


def input_events(buffer, hits, typed="", picking=None):
    """Actions from raw terminal input, the unfinished escape tail, and the digits typed so far."""
    clicks, pending = mouse_clicks(buffer)
    plain = MOUSE.sub("", buffer)
    plain = plain[:len(plain) - len(pending)] if pending else plain
    events = [found for found in (hit(hits, x, y) for x, y in clicks) if found]
    arrows = [("move", 1 if code in "B" else -1) for code in re.findall(r"\x1b[\[O]([AB])", plain)]
    plain = re.sub(r"\x1b[\[O][0-9;<]*[A-Za-z~]", "", plain)
    if picking == "type":
        return events + [event for event in map(entry_event, plain) if event], pending, typed
    if picking:
        keys = {c: a for c, a, _ in PICKS}
        return events + [(keys[c], None) for c in plain if c in keys], pending, typed
    typed_events, typed = key_events(plain, typed)
    return events + arrows + typed_events, pending, typed


def entry_event(char):
    if char in "\r\n":
        return ("submit", None)
    if char in "\x7f\b":
        return ("backspace", None)
    return ("char", char) if char.isprintable() else None


def edit_entry(entry, action, char):
    return entry[:-1] if action == "backspace" else entry + char if action == "char" else entry


DEMO_MODE = "--demo" in sys.argv


def main():
    scratch = Path(tempfile.mkdtemp(prefix="user-todo-demo.")) if DEMO_MODE else None
    hidden = Hidden(scratch / "state.json", scratch / "feed.log") if DEMO_MODE else Hidden(HIDDEN, FEED)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write("\033[?1000h\033[?1006h\033[?25l")
    status, last, items, rows, hits, selected, buf, typed = "", 0, [], [], [], 0, "", ""
    shown_size, show_hidden, picker, last_input = None, False, None, time.time()
    try:
        while True:
            if show_hidden and not reminders_idle(show_hidden, last_input, time.time()):
                show_hidden, last = False, 0
            size = os.get_terminal_size()
            if time.time() - last > 3 or size != shown_size:
                shown_size = size
                hidden.reload()
                items, last = load(), time.time()
                rows = hidden.visible(items, show_hidden)
                selected = min(selected, max(0, len(rows) - 1))
                hits = draw(rows, hidden.hidden_count(items), selected, status, show_hidden, picker, hidden.open_count(items))
            ready, _, _ = select.select([sys.stdin], [], [], 0.3 if buf == "\x1b" else 1)
            if not ready:
                if buf == "\x1b":
                    buf, picker, last = "", None, 0
                continue
            last_input = time.time()
            events, buf, typed = input_events(buf + os.read(fd, 64).decode(errors="ignore"), hits, typed,
                                              picker and picker["mode"])
            for action, index in events:
                if picker and action in ("char", "backspace", "submit"):
                    picker["entry"] = edit_entry(picker["entry"], action, index)
                    if action == "submit":
                        until = remind_until(picker["entry"], time.time())
                        if until is None:
                            status = TYPE_HINT
                        else:
                            status = act(hidden, rows, items, "remind", picker["index"], until=until)
                            show_hidden = show_hidden and not rows[picker["index"]][3]
                            picker = None
                    continue
                if picker and action == "type":
                    picker["mode"] = "type"
                    continue
                if picker and action.startswith("pick"):
                    status = act(hidden, rows, items, "remind", picker["index"], until=remind_until(action, time.time()))
                    show_hidden = show_hidden and not rows[picker["index"]][3]
                    picker = None
                    continue
                action = key_action(action, rows, selected if index is None else index)
                if action == "remind":
                    picker = {"index": selected if index is None else index, "mode": "pick", "entry": ""}
                    selected = picker["index"]
                    continue
                picker = None
                if action == "show":
                    show_hidden = not show_hidden
                    continue
                if action == "move":
                    selected = min(max(0, selected + index), max(0, len(rows) - 1))
                    show_hidden = reminders_stay(show_hidden, rows, selected) if rows else show_hidden
                    continue
                if action == "select":
                    selected = index if 0 <= index < len(rows) else selected
                    show_hidden = reminders_stay(show_hidden, rows, selected) if rows else show_hidden
                    continue
                selected = selected if index is None else index
                status = act(hidden, rows, items, action, selected)
                show_hidden = show_hidden and not (0 <= selected < len(rows) and rows[selected][3] != "")
            if events:
                last = 0
    finally:
        sys.stdout.write("\033[?1000l\033[?1006l\033[?25h")
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
