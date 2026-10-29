#!/usr/bin/env python3
"""Show one user question; record its answer, dismiss it, or hide it until a later time."""

import argparse
import contextlib
import os
import select
import shutil
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "watchers"))
import feed_attach  # noqa: E402
from question_lock import answers_lock  # noqa: E402
from question_remind import atomic_write, remind_path, write_remind  # noqa: E402
from question_text import option_rows, read_question  # noqa: E402
from reminder_picker import (
    PICK_KEYS,
    TYPE_HINT,
    edit_entry,
    entry_event,
    picker_lines,
    remind_until,
)  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HINT = "1-9 answer · t type an answer · d dismiss · r remind me later"
ESCAPE = "ESC"
ESCAPE_WAIT = 0.05
SETTLE_SECONDS = 5
POLL_SECONDS = 0.2


def answer_path(question):
    return Path(question).with_suffix(".answer")


def launch(question, *args):
    """Runs ask-user-question in its own session so it outlives the pane it closes."""
    env = dict(os.environ, ORCH_USER_QUESTIONS_DIR=str(Path(question).parent))
    return subprocess.Popen(
        [str(ROOT / "ask-user-question"), *args],
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def feed_line(slug, text, now):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
    return f"{stamp} answers: {text.format(slug=slug)} (start)\n"


class Panel:
    """The screen state; every key goes through press() so it runs without a terminal."""

    def __init__(
        self,
        question,
        out,
        columns=lambda: shutil.get_terminal_size().columns,
        clock=time.time,
        launcher=launch,
        feed=feed_attach.FEED,
    ):
        self.question, self.out, self.columns, self.clock = (
            Path(question),
            out,
            columns,
            clock,
        )
        self.launcher, self.feed = launcher, Path(feed)
        self.markdown_failed = False
        self.markdown_cache = {}
        self.slug = self.question.stem
        self.status, self.picker, self.done, self.pending = "", None, False, None

    def options(self):
        """The option id -> label pairs under `## Options`, or None when the file has no such section."""
        try:
            rows = option_rows(read_question(self.question))
        except OSError:
            return None
        return (
            {match.group(1): " ".join(match.group(2).split()) for _, match in rows}
            if rows is not None
            else None
        )

    def markdown(self, text):
        if self.markdown_failed:
            return text
        columns = max(1, self.columns() - 1)
        key = (text, columns)
        if key in self.markdown_cache:
            return self.markdown_cache[key]
        executable = shutil.which("mdcat")
        if not executable and os.access("/opt/homebrew/bin/mdcat", os.X_OK):
            executable = "/opt/homebrew/bin/mdcat"
        if executable:
            source = text
            if rows := option_rows(text):
                lines = text.splitlines()
                for index, match in rows:
                    lines[index] = f"{match.group(1)}\\. {match.group(2)}  "
                source = "\n".join(lines)
            try:
                rendered = subprocess.run(
                    [
                        executable,
                        "--ansi",
                        "--local",
                        "--no-pager",
                        "--columns",
                        str(columns),
                    ],
                    input=source,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=5,
                ).stdout.rstrip()
                self.markdown_cache[key] = rendered
                return rendered
            except (OSError, subprocess.SubprocessError):
                pass
        print("question-pane: mdcat unavailable; showing plain text.", file=sys.stderr)
        self.markdown_failed = True
        return text

    def show(self, status=None):
        self.status = self.status if status is None else status
        text = (
            read_question(self.question)
            .rstrip()
            .replace("Answer in the Coordinator pane.", "")
        )
        lines = [self.markdown(text), "", HINT]
        if self.picker:
            lines += [""] + picker_lines(self.picker, max(1, self.columns() - 1))[0]
        if self.status:
            lines += ["", self.status]
        self.out.write("\033[H\033[2J" + "\n".join(lines).replace("\n", "\r\n"))
        self.out.flush()

    def record(self, text):
        """Writes the feed line first so a failed write leaves the question as it was."""
        try:
            feed_attach.append_line(self.feed, feed_line(self.slug, text, self.clock()))
        except (OSError, TimeoutError):
            self.show("The feed is not writable; nothing changed.")
            return False
        return True

    def settle(self, process, rollback=None):
        """True once the helper ran on without failing; a helper still running stays observed through poll()."""
        try:
            code = process.wait(timeout=SETTLE_SECONDS)
        except subprocess.TimeoutExpired:
            self.pending = (process, rollback)
            self.show("Closing the pane...")
            return False
        return self.finish(code, rollback)

    def finish(self, code, rollback):
        if code:
            if rollback:
                rollback()
            self.show(
                f"Closing the pane failed (exit {code}); the question is still open."
            )
        return not code

    def poll(self):
        """Ends the panel when the pending helper succeeds; reopens it with the reason when it fails."""
        if not self.pending:
            return
        process, rollback = self.pending
        code = process.poll()
        if code is not None:
            self.pending = None
            self.done = self.finish(code, rollback)

    def unremind(self):
        remind_path(self.question).unlink(missing_ok=True)

    def dismiss(self):
        if self.record("DISMISSED {slug}"):
            self.done = self.settle(self.launcher(self.question, "close", self.slug))

    def remind(self, until):
        self.picker = None
        with answers_lock(self.question.parent):
            write_remind(self.question, until)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
        if self.record("REMIND {slug} " + when):
            self.done = self.settle(
                self.launcher(self.question, "hide", self.slug), self.unremind
            )
        else:
            self.unremind()

    def pick(self, token):
        if token == ESCAPE:
            self.picker = None
        elif self.picker["mode"] == "type":
            event = entry_event(token) if len(token) == 1 else None
            if event:
                self.picker["entry"] = edit_entry(self.picker["entry"], *event)
            if event and event[0] == "submit":
                until = remind_until(self.picker["entry"], self.clock())
                if until is None:
                    self.status = TYPE_HINT
                else:
                    return self.remind(until)
        elif token in PICK_KEYS:
            if PICK_KEYS[token] == "type":
                self.picker["mode"] = "type"
            else:
                return self.remind(remind_until(PICK_KEYS[token], self.clock()))
        self.show("" if self.picker is None else None)

    def press(self, token):
        if self.pending:
            return
        if self.picker:
            return self.pick(token)
        if token in tuple("123456789"):
            options = self.options()
            if options is None:
                atomic_write(answer_path(self.question), token)
                self.show(f"Sent: {token}. The Coordinator will pick it up.")
                return
            label = options.get(token)
            if label is None:
                self.show(f"No option {token}.")
                return
            atomic_write(answer_path(self.question), f'{token} "{label}"')
            self.show(f'Sent: {token} "{label}". The Coordinator will pick it up.')
        elif token == "t":
            self.show("")
            with line_mode(sys.stdin.fileno()):
                text = input("Your answer: ")
            if text.strip():
                atomic_write(answer_path(self.question), text)
                self.show(f"Sent: {text.strip()}. The Coordinator will pick it up.")
            else:
                self.show("")
        elif token == "d":
            self.dismiss()
        elif token == "r":
            self.picker = {"mode": "pick", "entry": ""}
            self.show("")


cooked_mode = None


@contextlib.contextmanager
def cbreak(fd):
    """Holds the terminal in cbreak mode and restores the previous mode on exit; a no-op off a terminal."""
    global cooked_mode
    if not os.isatty(fd):
        yield
        return
    cooked_mode = termios.tcgetattr(fd)
    tty.setcbreak(fd, termios.TCSANOW)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, cooked_mode)
        cooked_mode = None


@contextlib.contextmanager
def line_mode(fd):
    """Gives a full-line prompt the mode the terminal had before cbreak."""
    if cooked_mode is None:
        yield
        return
    termios.tcsetattr(fd, termios.TCSADRAIN, cooked_mode)
    try:
        yield
    finally:
        tty.setcbreak(fd, termios.TCSANOW)


def read_key(wait=None):
    """One key as a character; ESCAPE for an Esc that no sequence follows; None when `wait` seconds pass without one."""
    fd = sys.stdin.fileno()
    if wait is not None and not select.select([fd], [], [], wait)[0]:
        return None
    key = os.read(fd, 1).decode("latin1")
    if key == "\x1b" and not select.select([fd], [], [], ESCAPE_WAIT)[0]:
        return ESCAPE
    return key


def follow(question):
    with cbreak(sys.stdin.fileno()):
        run(question)


def run(question):
    panel = Panel(question, sys.stdout)
    panel.show()
    escape = ""
    while not panel.done:
        key = read_key(POLL_SECONDS if panel.pending else None)
        if key is None:
            panel.poll()
            continue
        if not key:
            return
        if key == ESCAPE:
            panel.press(key)
            continue
        if key == "\x1b":
            escape = "start"
            continue
        if escape:
            if escape == "start":
                escape = "sequence" if key in "[O" else ""
            elif "@" <= key <= "~":
                escape = ""
            continue
        panel.press(key)
        time.sleep(0.05)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("question", type=Path)
    args = parser.parse_args(argv)
    if not args.question.is_file():
        parser.error(f"question is not a regular file: {args.question}")
    follow(args.question)


if __name__ == "__main__":
    main()
