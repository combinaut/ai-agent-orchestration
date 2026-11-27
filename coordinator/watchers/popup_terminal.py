"""Terminal plumbing the popup views share: input tokens, line fitting and the screen loop."""

import codecs
import os
import re
import select
import shutil
import signal
import sys
import termios
import time
import tty

import todo_popup
from reminder_picker import RESET, SGR, cells

WRITE_TIMEOUT = 3
TOKEN = re.compile(
    r"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b[\[O]([AB])|\x1b[\[O][0-9;<]*[A-Za-z~]|(.)",
    re.S,
)
PENDING = re.compile(r"\x1b(?:[\[O][<0-9;]*)?\Z")
ESCAPES = ("\x1b", "\x1b[27u", "\x1b[27;1u", "\x1b[27;1;27~")
ENTER = "\033[?1000h\033[?1006h\033[?25l"
LEAVE = "\033[?1000l\033[?1006l\033[?25h"


def split_input(buffer):
    """The complete tokens (one key, arrow or mouse report each) and the unfinished escape tail."""
    partial = PENDING.search(buffer)
    pending = partial.group() if partial else ""
    return [
        found.group(0) for found in TOKEN.finditer(buffer[: len(buffer) - len(pending)])
    ], pending


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


class BoundedOut:
    """Terminal output that raises TimeoutError when the terminal stops draining."""

    def __init__(self, fd, timeout=WRITE_TIMEOUT):
        self.fd, self.timeout = fd, timeout

    def write(self, text):
        data = text.encode()
        deadline = time.monotonic() + self.timeout
        while data:
            try:
                data = data[os.write(self.fd, data) :]
            except BlockingIOError:
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([], [self.fd], [], left)[1]:
                    raise TimeoutError("terminal output is not draining")

    def flush(self):
        pass


def serve(make_app, popup, stop, clock=time.monotonic, scratch=None):
    """Runs make_app(out) on this terminal until it closes; scratch is a directory removed on the way out.

    The app has tick(), feed(text), lone_escape(), `buf` and `closing`.
    """
    fd = sys.stdin.fileno()
    out = BoundedOut(sys.__stdout__.fileno())
    blocking = os.get_blocking(out.fd)
    parent = os.getppid()
    previous = {
        number: signal.signal(number, stop)
        for number in (signal.SIGTERM, signal.SIGHUP)
    }
    old = None
    try:
        if popup:
            todo_popup.register()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        os.set_blocking(out.fd, False)
        try:
            out.write(ENTER)
            app = make_app(out)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
            next_pane_check = 0
            pane = os.environ.get("HERDR_PANE_ID") if popup else None
            while not app.closing and os.getppid() == parent:
                if pane and clock() >= next_pane_check:
                    if not todo_popup.pane_exists(pane):
                        break
                    next_pane_check = clock() + 3
                app.tick()
                ready, _, _ = select.select(
                    [fd], [], [], 0.3 if app.buf == "\x1b" else 1
                )
                if not ready:
                    app.lone_escape()
                    continue
                data = os.read(fd, 64)
                if not data:
                    break
                app.feed(decoder.decode(data))
        except OSError:
            pass
    finally:
        if old is not None:
            for restore in (
                lambda: out.write(LEAVE),
                lambda: termios.tcsetattr(fd, termios.TCSANOW, old),
            ):
                try:
                    restore()
                except (OSError, ValueError):
                    pass
            os.set_blocking(out.fd, blocking)
        for number, handler in previous.items():
            signal.signal(number, handler)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
