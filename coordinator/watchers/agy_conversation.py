"""Resolve an agy conversation from its recorded workspace URI."""

import os
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

STORE = Path.home() / ".gemini/antigravity-cli/conversations"


def workspace_paths(folder):
    shown = os.fspath(folder)
    path = Path(os.path.normpath(shown))
    paths = (path.absolute(), path.resolve())
    return {os.path.normpath(shown), *(os.fspath(candidate) for candidate in paths)}


def protobuf_strings(data):
    """Yield length-delimited values from agy's nested protobuf metadata."""
    offset = 0
    while offset < len(data):
        key = data[offset]
        offset += 1
        if key & 7 != 2:
            return
        length = shift = 0
        while offset < len(data):
            byte = data[offset]
            offset += 1
            length |= (byte & 127) << shift
            if not byte & 128:
                break
            shift += 7
        else:
            return
        value, offset = data[offset : offset + length], offset + length
        yield value
        yield from protobuf_strings(value)


def matches_workspace(data, paths):
    """Whether agy's metadata stores exactly one of ``paths`` as a workspace URI."""
    for value in protobuf_strings(data):
        try:
            uri = urlsplit(value.decode())
        except UnicodeDecodeError:
            continue
        if uri.scheme == "file" and os.path.normpath(unquote(uri.path)) in paths:
            return True
    return False


def conversation_data(database):
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, timeout=0.1, uri=True)
    try:
        connection.set_progress_handler(lambda: 1, 1_000_000)
        return [
            row[0]
            for row in connection.execute("SELECT data FROM trajectory_metadata_blob")
        ]
    finally:
        connection.close()


def resolve(folder, store=STORE):
    """Return the sole agy conversation for ``folder``, or ``None`` when unsure."""
    try:
        paths = workspace_paths(folder)
        matches = []
        for database in Path(store).glob("*.db"):
            if database.is_symlink():
                continue
            try:
                identifier = str(uuid.UUID(database.stem))
                if any(
                    matches_workspace(data, paths)
                    for data in conversation_data(database)
                ):
                    matches.append(identifier)
            except (OSError, sqlite3.Error, UnicodeError, ValueError):
                return None
        return matches[0] if len(matches) == 1 else None
    except OSError:
        return None
