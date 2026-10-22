"""Runs a command on a pseudo-terminal, typing PTY_ANSWERS lines (then Enter) at each "]: " prompt."""

import os
import pty
import select
import sys
import time

TIMEOUT = float(os.environ.get("PTY_TIMEOUT", 120))
TIMED_OUT = 124


def main():
    answers = [
        line + "\n"
        for line in os.environ.get("PTY_ANSWERS", "").split("\n")
        if os.environ.get("PTY_ANSWERS")
    ]
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(sys.argv[1], sys.argv[1:])
    output, deadline = b"", time.monotonic() + TIMEOUT
    prompts, timed_out = 0, False
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
            while prompts < output.count(b"]: "):
                os.write(fd, (answers.pop(0) if answers else "\n").encode())
                prompts += 1
    else:
        timed_out = True
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        print("pty_run: timeout", file=sys.stderr)
    _, status = os.waitpid(pid, 0)
    return TIMED_OUT if timed_out else os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
