#!/usr/bin/env python3
"""Prints the action lines appended to the feed, as an agent may see them, following rotation like tail -F.
The feed is opened at its end before the heartbeat is touched, so no line between the two is missed."""

import os
import sys
import time
from pathlib import Path

import watcher_runtime as runtime
from feed_actions import action_patterns, is_action, native
from feed_attach import locked, write_record

BEAT_SECONDS = 60
POLL_SECONDS = 0.5


def rotated(path, stream):
    try:
        return os.stat(path).st_ino != os.fstat(stream.fileno()).st_ino
    except FileNotFoundError:
        return False


def attach(feed, beat):
    """Opens the feed at its end and records that position in the heartbeat, under the feed lock."""
    with locked(feed):
        stream = feed.open("rb")
        stream.seek(0, os.SEEK_END)
        write_record(
            beat, os.fstat(stream.fileno()).st_ino, stream.tell(), stream.tell()
        )
    return stream


def follow(feed, beat, out=sys.stdout.buffer):
    with runtime.deadline(runtime.ITERATION_TIMEOUT, "feed startup"):
        patterns = action_patterns()
        stream = attach(feed, beat)
    attached, previous = stream.tell(), 0
    beat_at = time.monotonic()
    for _ in runtime.iterations(0, label="feed follower"):
        if time.monotonic() - beat_at >= BEAT_SECONDS:
            try:
                os.utime(beat)
            except FileNotFoundError:
                print(
                    "feed follower: heartbeat missing, recreated",
                    file=sys.stderr,
                    flush=True,
                )
                write_record(
                    beat,
                    os.fstat(stream.fileno()).st_ino,
                    attached,
                    stream.tell(),
                    previous,
                )
            beat_at = time.monotonic()
        line = stream.readline()
        if line.endswith(b"\n"):
            text = line.decode(errors="replace").rstrip("\n")
            if is_action(text, patterns):
                out.write((native(text) + "\n").encode())
                out.flush()
            write_record(
                beat,
                os.fstat(stream.fileno()).st_ino,
                attached,
                stream.tell(),
                previous,
            )
            continue
        stream.seek(-len(line), os.SEEK_CUR)
        if not line and rotated(feed, stream):
            previous = os.fstat(stream.fileno()).st_ino
            stream.close()
            stream = feed.open("rb")
            attached = 0
            write_record(beat, os.fstat(stream.fileno()).st_ino, attached, 0, previous)
            continue
        runtime.sleep(POLL_SECONDS)


if __name__ == "__main__":
    follow(Path(sys.argv[1]), Path(sys.argv[2]))
