"""Serialize question cancellation with answer publication."""

import fcntl
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path


def questions_directory():
    return Path(
        os.environ.get("ORCH_USER_QUESTIONS_DIR")
        or Path.home() / ".claude/coordinator/questions"
    )


HELD = "ORCH_ANSWERS_LOCK_HELD"
CLOSE_TIMEOUT = 30
ACTION_TIMEOUT = 60
TIMED_OUT = 124


@contextmanager
def answers_lock(directory=None, timeout=None):
    """Holds the lock; a process the holder started with HELD in its environment does not lock again."""
    if os.environ.get(HELD):
        yield
        return
    directory = Path(directory) if directory else questions_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".answers.lock").open("a") as lock:
        if timeout is None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("question lock wait timed out") from None
                    time.sleep(min(0.05, remaining))
        yield


TERM_GRACE = 2
OUTER_GRACE = 3 * TERM_GRACE
KILL_WAIT = 5


def group_gone(process):
    process.poll()
    try:
        os.killpg(process.pid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    return False


def wait_group_gone(process, seconds):
    deadline = time.monotonic() + seconds
    while not group_gone(process):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def stop_group(process, grace):
    """SIGTERMs the process group, waits up to the grace for every member to exit, then SIGKILLs it."""
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    if not wait_group_gone(process, grace):
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        wait_group_gone(process, KILL_WAIT)
    process.wait()


def run_group(argv, timeout, env=None, check=True, quiet=False):
    """Runs argv in its own process group, stopped when it outlives the timeout or this process is interrupted or terminated."""
    process = subprocess.Popen(
        argv,
        start_new_session=True,
        env=env,
        **(
            {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if quiet
            else {}
        ),
    )

    grace = TERM_GRACE if os.environ.get(HELD) else OUTER_GRACE

    def terminate(*_):
        raise SystemExit(TIMED_OUT)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        code = process.wait(timeout=timeout)
    except BaseException:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        stop_group(process, grace)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
    if code and check:
        raise subprocess.CalledProcessError(code, argv)
    return code


def run_locked(argv):
    """Runs argv under the answers lock unless the caller already holds it, and bounds it there; returns its exit status."""
    held = dict(os.environ, **{HELD: "1"})
    if os.environ.get(HELD):
        return subprocess.run(argv, env=held).returncode
    with answers_lock():
        try:
            return run_group(argv, ACTION_TIMEOUT, env=held, check=False)
        except subprocess.TimeoutExpired:
            return TIMED_OUT


def close_question(slug, helper, pane):
    with answers_lock():
        if pane:
            run_group([helper, pane], CLOSE_TIMEOUT)
        directory = questions_directory()
        for name in (
            f"{slug}.md",
            f"{slug}.remind",
            f"{slug}.asker-pane",
            f"{slug}.answer",
            f"{slug}.answer.seen",
            f"{slug}.answer.processing",
        ):
            (directory / name).unlink(missing_ok=True)
        for pattern in (f"{slug}.*.answer.processing", f"{slug}.*.answer.seen"):
            for path in directory.glob(pattern):
                path.unlink(missing_ok=True)


def hide_question(helper, pane):
    with answers_lock():
        if pane:
            run_group([helper, pane], CLOSE_TIMEOUT)


if __name__ == "__main__":
    if sys.argv[1] == "--run":
        sys.exit(run_locked(sys.argv[2:]))
    elif sys.argv[1] == "--hide":
        hide_question(*sys.argv[2:])
    else:
        close_question(*sys.argv[1:])
