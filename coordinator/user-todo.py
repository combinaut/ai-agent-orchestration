#!/usr/bin/env python3
"""Build the single user-facing list from architect status files.

The module is deliberately stdlib-only.  Its small functions are also used by the
watcher and tests; the executable name retains the documented hyphen.
"""

import argparse
import codecs
import fcntl
import json
import os
import re
import select
import subprocess
import sys
import tempfile
import termios
import time
import tty
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "watchers"))
import todo_answers  # noqa: E402

NOW = "## Waits on the user"
PLACEHOLDERS = {"none", "none yet", "nothing", "nothing yet", "n/a", "-"}


def normalized(text):
    return " ".join(text.split())


def placeholder(text):
    """True when the whole item is an empty-section marker such as "(none yet)"."""
    return text.casefold().strip(" ()[]{}.,;:!") in PLACEHOLDERS


def status_items(directory, read=None):
    now, unreadable = [], []
    read = [] if read is None else read
    try:
        directory = Path(directory)
        if not directory.is_dir():
            return now, [str(directory)]
        files = sorted(path for path in directory.iterdir() if path.suffix == ".md")
    except OSError:
        return now, [str(directory)]
    for path in files:
        try:
            lines = path.read_text().splitlines()
        except (OSError, ValueError):
            unreadable.append(str(path))
            continue
        read.append(path.name)
        section = None
        fenced = False
        for line in lines:
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            heading = line.rstrip().casefold()
            if heading == NOW.casefold():
                section = now
            elif line.startswith("#"):
                section = None
            elif section is not None and line.startswith(("- ", "* ")):
                text = normalized(line[2:])
                if text and not placeholder(text):
                    section.append(
                        {
                            "source": path.name,
                            "text": text,
                            "id": path.name + "\0" + text,
                        }
                    )
    return now, unreadable


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


def merge(directory, output, state_path, now=None):
    now = time.time() if now is None else now
    directory = Path(directory)
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix(state_path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        read = []
        current, unreadable = status_items(directory, read)
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {"first_seen": {}, "items": {}}
        cached = state.setdefault("items", {})
        dismissed = set(state.get("dismissed", []))
        unreadable_sources = {Path(path).name for path in unreadable}
        retained = (
            list(cached.values())
            if str(directory) in unreadable
            else [
                item for item in cached.values() if item["source"] in unreadable_sources
            ]
        )
        current += [dict(item) for item in retained if not item.get("later")]
        seen = state.setdefault("first_seen", {})
        for item in current:
            seen.setdefault(item["id"], now)
            item["age"] = max(0, int(now - seen[item["id"]]))
        present = {item["id"] for item in current}
        dismissed.update(todo_answers.pending_item_ids(seen) & present)
        dismissed &= present
        state["first_seen"] = {
            key: value for key, value in seen.items() if key in present
        }
        state["items"] = {
            item["id"]: {
                key: value for key, value in item.items() if key not in ("age", "later")
            }
            for item in current
        }
        state["dismissed"] = sorted(dismissed)
        atomic_write(state_path, json.dumps(state, sort_keys=True))
        visible = [item for item in current if item["id"] not in dismissed]
        render(output, visible)
        return {
            "now": visible,
            "unreadable": unreadable,
            "read": sorted(read),
            "scanned": str(directory) not in unreadable,
        }


AGE_PREFIX = re.compile(r"\[(now|\d+[mhd])\] (.*)")
AGE_SUFFIX = re.compile(r" \[(now|\d+[mhd])\]$")


def age(seconds):
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def render(output, current):
    groups = {}
    for item in current:
        groups.setdefault(item["source"], []).append(item)
    lines = []
    for source, items in groups.items():
        lines += ["", f"## {source}"]
        lines += [
            "- "
            + (
                item["display"]
                if "display" in item
                else f"[{age(item['age'])}] {item['text']}"
            )
            for item in items
        ]
    atomic_write(
        output, ("\n".join(lines) if lines else "Nothing waits on you.") + "\n"
    )


def target(text):
    return text.rsplit(" -> ", 1)[1].strip() if " -> " in text else ""


def live_agent(session, rows):
    matches = []
    for row in rows:
        tokens = row.get("tokens") or {}
        named = row.get("name") == session
        if named and tokens.get("role") not in (
            "architect",
            "coordinator",
        ):  # Workers retain their Herdr agent name.
            return row.get("name")
        if (
            named
            or row.get("terminal_title_stripped") == session
            or tokens.get("session") == session
        ):
            matches.append(row)
        elif session == "Coordinator" and tokens.get("role") == "coordinator":
            matches.append(row)
        elif (
            session.startswith("Architect::")
            and tokens.get("role") == "architect"
            and row.get("terminal_title_stripped") == session
        ):
            matches.append(row)
    if not matches:
        return None
    return max(matches, key=lambda row: row.get("revision", 0)).get("name")


def jump(item, run=subprocess.run, platform=sys.platform):
    where = target(item["text"])
    if not where:
        return "no target"
    if where.startswith(("https://", "http://")):
        argv = (
            ["open", "-a", "Google Chrome", where]
            if platform == "darwin"
            else ["xdg-open", where]
        )
        try:
            result = run(argv, check=False)
        except OSError:
            return "open failed: " + where
        return (
            "opened " + where
            if getattr(result, "returncode", 0) == 0
            else "open failed: " + where
        )
    herdr = (
        os.environ.get("HERDR_BIN_PATH") or os.environ.get("ORCH_HERDR_BIN") or "herdr"
    )
    try:
        result = run(
            [herdr, "agent", "list"], capture_output=True, text=True, check=False
        )
    except OSError:
        return "agent list failed"
    if getattr(result, "returncode", 0) != 0:
        return "agent list failed"
    try:
        agent = live_agent(
            where, json.loads(result.stdout).get("result", {}).get("agents", [])
        )
    except (ValueError, AttributeError):
        agent = None
    if not agent:
        return "not found: " + where
    try:
        focused = run([herdr, "agent", "focus", agent], check=False)
    except OSError:
        return "focus failed: " + agent
    return (
        "focused " + agent
        if getattr(focused, "returncode", 0) == 0
        else "focus failed: " + agent
    )


def report_metadata(pane, count, run=subprocess.run):
    if pane:
        herdr = (
            os.environ.get("HERDR_BIN_PATH")
            or os.environ.get("ORCH_HERDR_BIN")
            or "herdr"
        )
        try:
            tabbar = (
                ["--token", f"tabbar=To-do {count}"]
                if count
                else ["--clear-token", "tabbar"]
            )
            run(
                [
                    herdr,
                    "pane",
                    "report-metadata",
                    pane,
                    "--source",
                    "orchestrate",
                    "--token",
                    f"todo={count}",
                    *tabbar,
                ],
                check=False,
            )
        except OSError:
            pass


def number_selection(value, count):
    return int(value) - 1 if value.isdigit() and 0 < int(value) <= count else None


def load_orch_config(run=subprocess.run):
    library = Path(__file__).parent / "watchers/coord-lib.sh"
    try:
        result = run(
            [
                "/bin/bash",
                "-c",
                'source "$1"; orch_load_config; orch_export_config; env -0',
                "user-todo",
                str(library),
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    if result.returncode:
        return False
    for entry in result.stdout.split(b"\0"):
        if entry.startswith(b"ORCH_") and b"=" in entry:
            key, value = entry.split(b"=", 1)
            os.environ[key.decode()] = value.decode()
    return True


def dismissed_ids(state_path):
    try:
        return set(json.loads(Path(state_path).read_text()).get("dismissed", []))
    except (OSError, ValueError, AttributeError):
        return set()


def rendered_items(output, state_path=None):
    try:
        lines = Path(output).read_text().splitlines()
    except (OSError, ValueError):
        return []
    source, items = None, []
    for line in lines:
        if line.startswith("## "):
            source = line[3:]
        elif source and line.startswith("- "):
            text, tag = line[2:], None
            if match := AGE_PREFIX.match(text):
                tag, text = match.groups()
            elif match := AGE_SUFFIX.search(text):
                text, tag = text[: match.start()], match.group(1)
            display = f"[{tag}] {text}" if tag else text
            items.append(
                {
                    "source": source,
                    "text": text,
                    "display": display,
                    "id": source + "\0" + text,
                }
            )
    dismissed = dismissed_ids(state_path) if state_path else set()
    return [item for item in items if item["id"] not in dismissed]


def dismiss_locked(item, state_path, output, state):
    visible = rendered_items(output, state_path) if output else None
    dismissed = set(state.get("dismissed", []))
    dismissed.add(item["id"])
    state["dismissed"] = sorted(dismissed)
    atomic_write(state_path, json.dumps(state, sort_keys=True))
    if output:
        render(
            output,
            [candidate for candidate in visible if candidate["id"] != item["id"]],
        )


def dismiss_item(item, state_path, output=None):
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix(state_path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {"first_seen": {}, "items": {}}
        dismiss_locked(item, state_path, output, state)
    return True


CURRENT_GENERATION = object()
STALE_ANSWER = "stale answer: item changed; refresh and answer again"


def answer_state_path():
    return Path(
        todo_answers.setting(
            "ORCH_USER_TODO_STATE",
            Path.home() / ".claude/coordinator/user-todo.state.json",
        )
    )


def answer_generation(owner, text, state_path=None):
    try:
        state = json.loads(Path(state_path or answer_state_path()).read_text())
        return state.get("first_seen", {}).get(todo_answers.item_id(owner, text))
    except (OSError, ValueError, AttributeError):
        return None


def answer_item(item, answer, state_path, output, generation=CURRENT_GENERATION):
    owner = item["source"].removesuffix(".md")
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Queue -> state matches pruning's lock order; the producer cannot replace a generation in between.
    with todo_answers.queue_lock():
        existing = todo_answers.prune(time.time())
        with state_path.with_suffix(state_path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = json.loads(state_path.read_text())
                current = state.get("first_seen", {}).get(item["id"])
            except (OSError, ValueError, AttributeError):
                raise ValueError(STALE_ANSWER) from None
            expected = current if generation is CURRENT_GENERATION else generation
            if current is None or expected != current:
                raise ValueError(STALE_ANSWER)
            todo_answers.submit_locked(owner, item["text"], answer, expected, existing)
            dismiss_locked(item, state_path, output, state)
    return "answer queued (removed)"


def answer_from_view(owner, text, answer, output, generation=CURRENT_GENERATION):
    item = dict(source=owner + ".md", text=text, id=todo_answers.item_id(owner, text))
    return answer_item(item, answer, answer_state_path(), output, generation)


def item_display(item):
    return item.get("display", item["text"])


def cell_width(char):
    if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def item_line(index, item):
    return f"{index + 1}. {item['source']}: {item_display(item)}"


def wrapped_rows(line, width):
    width = max(1, width)
    rows, column = 1, 0
    for char in line:
        cells = cell_width(char)
        if column + cells > width:
            rows, column = rows + 1, 0
        column += cells
    return rows


def clip_rows(line, width, limit):
    width = max(1, width)
    rows, column = 1, 0
    for position, char in enumerate(line):
        cells = cell_width(char)
        if column + cells > width:
            rows, column = rows + 1, 0
            if rows > limit:
                return line[:position]
        column += cells
    return line


def answer_footer(answering, width, height):
    editor = answering["editor"]
    if answering.get("gone"):
        return [clip_rows("that item is gone; Esc cancels the answer", width, 1)]
    limit = max(1, min(4, height - 3))
    rows, column = [""], 0
    for char in editor.prompt():
        cells = cell_width(char)
        if column + cells > width:
            rows.append("")
            column = 0
        rows[-1] += char
        column += cells
    if editor.error:
        return [clip_rows(editor.error, width, 1)] + rows[-max(1, limit - 1) :]
    return rows[-limit:]


def item_row_count(index, item, width):
    return wrapped_rows(item_line(index, item), width)


def click_rows(items, width):
    rows = []
    for index, item in enumerate(items):
        rows += [index] * item_row_count(index, item, width)
    return rows


def visible_items(items, width, height, start=0):
    room = max(1, height - 3)
    visible, rows, used = [], [], 0
    for index in range(start, len(items)):
        item_rows = min(item_row_count(index, items[index], width), room)
        if used + item_rows > room:
            break
        visible.append((index, items[index]))
        rows += [index] * item_rows
        used += item_rows
    return visible, rows


def page_starts(items, width, height):
    starts, start = [], 0
    while start < len(items):
        starts.append(start)
        visible, _ = visible_items(items, width, height, start)
        start = visible[-1][0] + 1
    return starts or [0]


def mouse_events(buffer):
    events = [
        (int(button), int(row), kind)
        for button, _column, row, kind in re.findall(
            r"\x1b\[<(\d+);(\d+);(\d+)([Mm])", buffer
        )
    ]
    remainder = re.sub(r"\x1b\[<\d+;\d+;\d+[Mm]", "", buffer)
    partial = re.search(r"\x1b(?:\[[0-?]*[ -/]*)?$", remainder)
    pending = partial.group() if partial else ""
    return events, remainder[: partial.start()] if partial else remainder, pending


def jump_succeeded(result):
    return result.startswith(("focused ", "opened "))


def follow(
    args, wait=select.select, read=os.read, run_jump=jump, report=report_metadata
):
    selected, typed, footer, pending_mouse, page = (
        0,
        "",
        "keyboard: number then Enter to jump",
        "",
        0,
    )
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    answering = None
    last_count = None
    state_path = getattr(
        args, "state", Path(args.output).with_name("user-todo.state.json")
    )
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd) if os.isatty(fd) else None
    mouse = "\033[?1000h\033[?1006h"
    if saved is not None:
        tty.setcbreak(fd)
        sys.stdout.write(mouse)
        sys.stdout.flush()
    try:
        while True:
            items = rendered_items(args.output, state_path)
            count = len(items)
            if count != last_count:
                report(args.pane or os.environ.get("HERDR_PANE_ID"), count)
                last_count = count
            try:
                rendered = Path(args.output).read_text().rstrip()
            except (OSError, ValueError):
                rendered = "Nothing waits on you."
            size = (
                os.get_terminal_size() if os.isatty(fd) else os.terminal_size((80, 24))
            )
            answer_rows = (
                answer_footer(answering, max(1, size.columns - 1), size.lines)
                if answering
                else []
            )
            list_height = size.lines - max(0, len(answer_rows) - 1)
            starts = page_starts(items, size.columns, list_height)
            page = min(page, len(starts) - 1)
            visible, rows = visible_items(
                items, size.columns, list_height, starts[page]
            )
            room = max(1, list_height - 3)
            screen = [
                clip_rows(item_line(n, item), size.columns, room) for n, item in visible
            ]
            if not items:
                screen.append(rendered)
            status = (
                f"page {page + 1}/{len(starts)}: n next, p prev | " + footer
                if len(starts) > 1
                else footer
            )
            if (
                not answering
                and 0 <= selected < len(items)
                and todo_answers.choices(items[selected]["text"])
            ):
                status += " · a answer selected item"
            screen += [""] + (
                answer_rows
                if answering
                else [clip_rows(status, max(1, size.columns - 1), 1)]
            )
            print("\033[2J\033[H" + "\n".join(screen), flush=True)
            readable, _, _ = wait([fd], [], [], 1)
            if readable:
                key = read(fd, 64)
                if not key:
                    break
                key = decoder.decode(key)
                events, key, pending_mouse = mouse_events(pending_mouse + key)
                paging = re.findall(r"\x1b\[([56])~", key)
                key = re.sub(r"\x1b\[[56]~", "", key)
                step = sum(1 if code == "6" else -1 for code in paging)
                if not answering and "a" not in key:
                    step += key.count("n") - key.count("p")
                shown = items
                for button, row, kind in events:
                    if kind == "M" and button in (64, 65):
                        step += 1 if button == 65 else -1
                    elif button == 0 and kind == "M" and 1 <= row <= len(rows):
                        answering = None
                        selected = rows[row - 1]
                        footer = run_jump(shown[selected])
                        if jump_succeeded(footer) and dismiss_item(
                            shown[selected], state_path, args.output
                        ):
                            footer += " (removed)"
                            items = rendered_items(args.output, state_path)
                page = max(0, page + step)
                for char in re.findall(r"\x1b\[[0-?]*[ -/]*[@-~]|.", key, re.S):
                    if answering and char.startswith("\x1b["):
                        continue
                    if answering:
                        if answering.get("gone") or not any(
                            item["id"] == answering["item"]["id"] for item in items
                        ):
                            answering["gone"] = True
                            footer = "that item is gone; Esc cancels the answer"
                            if char == "\x1b":
                                answering, footer = None, "answer cancelled"
                            continue
                        action, answer = answering["editor"].feed(char)
                        footer = (
                            answering["editor"].error or answering["editor"].prompt()
                        )
                        if action == "cancel":
                            answering, footer = None, "answer cancelled"
                        elif action == "answer":
                            try:
                                footer = answer_item(
                                    answering["item"],
                                    answer,
                                    state_path,
                                    args.output,
                                    answering["generation"],
                                )
                                items = rendered_items(args.output, state_path)
                                answering = None
                            except (ValueError, OSError) as error:
                                answering["editor"].error = str(error)
                        continue
                    if char == "a":
                        choice = (
                            number_selection(typed, len(items)) if typed else selected
                        )
                        if choice is not None and 0 <= choice < len(items):
                            options = todo_answers.choices(items[choice]["text"])
                            if options:
                                typed = ""
                                selected = choice
                                answering = {
                                    "item": dict(items[choice]),
                                    "generation": answer_generation(
                                        items[choice]["source"].removesuffix(".md"),
                                        items[choice]["text"],
                                        state_path,
                                    ),
                                    "editor": todo_answers.AnswerInput(options),
                                }
                        continue
                    if char.isdigit():
                        typed += char
                        footer = "selected " + typed
                    elif char in ("\x7f", "\b"):
                        typed = typed[:-1]
                    elif char in ("\n", "\r"):
                        choice = (
                            number_selection(typed, len(items)) if typed else selected
                        )
                        if choice is None or not 0 <= choice < len(items):
                            footer = "not found: item " + typed
                        else:
                            selected, footer = choice, run_jump(items[choice])
                            if jump_succeeded(footer) and dismiss_item(
                                items[choice], state_path, args.output
                            ):
                                footer += " (removed)"
                                items = rendered_items(args.output, state_path)
                        typed = ""
            elif answering and pending_mouse == "\x1b":
                pending_mouse, answering, footer = "", None, "answer cancelled"
    finally:
        if saved is not None:
            sys.stdout.write("\033[?1000l\033[?1006l")
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def main():
    if not load_orch_config():
        print("Cannot load orchestration configuration", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser()
    coordinator = Path.home() / ".claude/coordinator"
    parser.add_argument(
        "--status-dir",
        type=Path,
        default=Path(
            os.environ.get("ORCH_USER_TODO_STATUS_DIR") or coordinator / "status"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            os.environ.get("ORCH_USER_TODO_OUTPUT") or coordinator / "user-todo.md"
        ),
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(
            os.environ.get("ORCH_USER_TODO_STATE")
            or coordinator / "user-todo.state.json"
        ),
    )
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--pane")
    args = parser.parse_args()
    if args.follow:
        follow(args)
    else:
        merge(args.status_dir, args.output, args.state)


if __name__ == "__main__":
    sys.exit(main())
