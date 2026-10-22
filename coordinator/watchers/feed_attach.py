"""The feed lock and the Monitor's read record, shared by the notifier and the Monitor's follower.
The follower writes `<inode> <attached> <consumed> <previous> <pid>` into its heartbeat: the feed file it reads, the offset it
started reading that file from, how far it has read, the file it read to its end before this one, and its own pid. The follower attaches and the notifier appends under the feed lock."""
import contextlib
import fcntl
import os
import time
import watcher_runtime as runtime
from pathlib import Path

LOCK_NAME = "coordinator-feed.lock"
MONITOR_NAME = "coordinator-feed.monitor"
MONITOR_FRESH_SECONDS = 180
FEED = Path.home() / ".claude/coordinator/watchers/prod-context/coordinator-feed.log"


@contextlib.contextmanager
def locked(feed):
    fd = os.open(feed.with_name(LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        end = time.monotonic() + runtime.LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise TimeoutError('feed lock deadline exceeded')
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def append_line(path, line):
    path = Path(path)
    with locked(path):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            data = memoryview(line.encode())
            while data:
                written = os.write(fd, data)
                if not written:
                    raise OSError('feed write made no progress')
                data = data[written:]
        finally:
            os.close(fd)


def write_record(beat, inode, attached, consumed, previous=0):
    tmp = beat.with_name(f"{beat.name}.{os.getpid()}.tmp")
    tmp.write_text(f"{inode} {attached} {consumed} {previous} {os.getpid()}\n")
    tmp.replace(beat)


def read_record(beat, now):
    """The live Monitor's record as (inode, attached, consumed, previous), None without a live Monitor, or () for a
    live Monitor that records no position."""
    try:
        age = now - beat.stat().st_mtime
        fields = beat.read_text().split()
    except FileNotFoundError:
        return None
    if age > MONITOR_FRESH_SECONDS:
        return None
    try:
        return tuple(int(field) for field in (fields + ["0"])[:4]) if len(fields) >= 3 else ()
    except ValueError:
        return ()


def will_read(record, inode, offset):
    """A Monitor reading the current feed file `inode` from before `offset`, or an older file it will rotate out of."""
    return record == () or (record is not None and (record[0] != inode or record[1] <= offset))


def has_read(record, inode, end):
    """The Monitor has read up to `end` of file `inode`, or read that file to its end before its current one."""
    if not record:
        return False
    return record[2] >= end if record[0] == inode else record[3] == inode
