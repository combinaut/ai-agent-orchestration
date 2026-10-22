"""A question hidden until a time: the `<slug>.remind` file next to the question, and reopening what is due."""
import os
import subprocess
import tempfile
from pathlib import Path

OPENED = 0
ALREADY_OPEN = 73


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text.strip() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def remind_path(question):
    return Path(question).with_suffix(".remind")


def write_remind(question, until):
    atomic_write(remind_path(question), repr(float(until)))


def opened(opener, slug, question):
    try:
        return opener(slug, question) in (OPENED, ALREADY_OPEN)
    except (OSError, subprocess.SubprocessError):
        return False


def reopen_due(directory, now, opener):
    """Reopen every question whose reminder time has passed; the caller holds the answers lock.
    opener(slug, question) returns the exit status of `ask-user-question <slug> <question>`.
    A reminder without its question or with an unreadable time is dropped; a failed or raising open stays for the next pass
    and never stops the others."""
    reopened = []
    for path in sorted(Path(directory).glob("*.remind")):
        question = path.with_suffix(".md")
        try:
            until = float(path.read_text())
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            continue
        if not question.is_file():
            path.unlink(missing_ok=True)
        elif until <= now and opened(opener, path.stem, question):
            path.unlink(missing_ok=True)
            reopened.append(path.stem)
    return reopened
