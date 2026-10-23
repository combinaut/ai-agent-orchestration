"""Serialize question cancellation with answer publication."""

import fcntl
import os
import signal
import subprocess
import sys
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
def answers_lock(directory=None):
    """Holds the lock; a process the holder started with HELD in its environment does not lock again."""
    if os.environ.get(HELD):
        yield
        return
    directory = Path(directory) if directory else questions_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".answers.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


TERM_GRACE = 2


def stop_group(process):
    """SIGTERMs the process group, waits the grace for it to exit, then SIGKILLs it."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signum)
        if signum == signal.SIGTERM:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=TERM_GRACE)
    process.wait()


def run_group(argv, timeout, env=None, check=True):
    """Runs argv in its own process group, stopped when it outlives the timeout or this process is interrupted or terminated."""
    process = subprocess.Popen(argv, start_new_session=True, env=env)

    def terminate(*_):
        stop_group(process)
        raise SystemExit(TIMED_OUT)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_group(process)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
        if process.poll() is None:
            stop_group(process)
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
