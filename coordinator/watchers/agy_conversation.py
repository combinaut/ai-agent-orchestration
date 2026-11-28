"""Resolve an agy conversation from its recorded workspace URI."""

import os
import sqlite3
import uuid
from pathlib import Path

STORE = Path.home() / ".gemini/antigravity-cli/conversations"


def workspace_uris(folder):
    shown = os.fspath(folder)
    path = Path(os.path.normpath(shown))
    paths = (path.absolute(), path.resolve())
    return {
        f"file://{shown}".encode(),
        *(candidate.as_uri().encode() for candidate in paths),
    }


def matches_workspace(data, uri):
    """Whether a trajectory metadata blob names exactly ``uri`` as its workspace."""
    offset = 0
    while (offset := data.find(uri, offset)) >= 0:
        end = offset + len(uri)
        if (
            end == len(data)
            or data[end : end + 1]
            not in b"/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~%-"
        ):
            return True
        offset = end
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
        uris = workspace_uris(folder)
        matches = []
        for database in Path(store).glob("*.db"):
            if database.is_symlink():
                continue
            try:
                identifier = str(uuid.UUID(database.stem))
                if any(
                    matches_workspace(data, uri)
                    for data in conversation_data(database)
                    for uri in uris
                ):
                    matches.append(identifier)
            except (OSError, sqlite3.Error, UnicodeError, ValueError):
                continue
        return matches[0] if len(matches) == 1 else None
    except OSError:
        return None
