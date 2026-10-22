"""Reminder time picker and the terminal drawing helpers it needs, shared by the to-do view and the Q panel."""
import re
import unicodedata
from datetime import datetime, timedelta

PICKS = (("1", "pick1", "15m"), ("2", "pick2", "1h"), ("3", "pick3", "2h"), ("4", "pick4", "4h"),
         ("5", "pick5", "tomorrow 9:00"), ("t", "type", "type"))
DURATIONS = {"pick1": 900, "pick2": 3600, "pick3": 7200, "pick4": 14400}
DURATION = re.compile(r"(\d{1,4})([mh])")
TYPE_HINT = "use minutes or hours, e.g. 30m or 3h"
INDENT = 0


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


def cells(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


RESET = "\033[0m"
SGR = re.compile(r"(\x1b\[[0-9;]*m)")
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
    width = max(1, width)
    rows, current = [], ""
    for word in text.split(" "):
        while cells(word) > width - (indent if rows else 0):
            room = max(1, width - (indent if rows else 0) - cells(current) - (1 if current else 0))
            if current and room < 4:
                rows.append(current)
                current = ""
                continue
            cut = word[:room]
            while cells(cut) > room and len(cut) > 1:
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


def button_rows(width, filled, hints, entries, tone=None):
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


def picker_lines(picker, width):
    """The inline time choice under an item and its buttons as (line within these lines, first, last, action)."""
    if picker["mode"] == "type":
        prompt = wrap(f"Remind in (30m, 3h): {picker['entry']}▏", width)
        return [f"\033[38;5;214m{row}\033[0m" for row in prompt] + ["\033[2mEnter ok · Esc cancels\033[0m"], []
    rows, spans = button_rows(width, False, True, PICKS, "remind")
    return ["\033[38;5;214mRemind me in:\033[0m"] + rows, [(offset + 2, first, last, action)
                                                          for offset, first, last, action in spans]



PICK_KEYS = {hotkey: action for hotkey, action, _ in PICKS}

def entry_event(char):
    if char in "\r\n":
        return ("submit", None)
    if char in "\x7f\b":
        return ("backspace", None)
    return ("char", char) if char.isprintable() else None


def edit_entry(entry, action, char):
    return entry[:-1] if action == "backspace" else entry + char if action == "char" else entry
