"""Serialize question cancellation with answer publication."""
import fcntl
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path


def questions_directory():
    return Path(os.environ.get("ORCH_USER_QUESTIONS_DIR") or Path.home() / ".claude/coordinator/questions")


@contextmanager
def answers_lock():
    directory = questions_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".answers.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def close_question(slug, helper, pane):
    with answers_lock():
        if pane:
            subprocess.run([helper, pane], check=True, timeout=30)
        directory = questions_directory()
        for name in (f"{slug}.md", f"{slug}.answer", f"{slug}.answer.seen", f"{slug}.answer.processing"):
            (directory / name).unlink(missing_ok=True)
        for pattern in (f"{slug}.*.answer.processing", f"{slug}.*.answer.seen"):
            for path in directory.glob(pattern):
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    close_question(*sys.argv[1:])
