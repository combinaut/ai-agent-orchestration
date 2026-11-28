"""Fail-safe completion hints from the undocumented local Codex store."""

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import discovery
from question_remind import atomic_write

TAIL_BYTES = 256 * 1024
CHILDREN_MAX = 64
DATABASE = re.compile(r"state_(\d+)\.sqlite\Z")
READ_ERRORS = (*discovery.READ_ERRORS, sqlite3.Error, UnicodeError)


def store_root():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def lifecycle(root, identifier, rollout, parent=None):
    path = Path(rollout).resolve()
    if not path.is_relative_to((root / "sessions").resolve()):
        raise ValueError("rollout outside session store")
    with path.open("rb") as stream:
        header = json.loads(stream.readline(TAIL_BYTES))
        meta = header["payload"]
        if header["type"] != "session_meta" or meta["id"] != identifier:
            raise ValueError("unrecognized session metadata")
        if (
            parent
            and meta["source"]["subagent"]["thread_spawn"]["parent_thread_id"] != parent
        ):
            raise ValueError("child does not belong to parent")
        stream.seek(0, 2)
        offset = max(0, stream.tell() - TAIL_BYTES)
        stream.seek(offset)
        if offset:
            stream.readline()
        lines = stream.read(TAIL_BYTES).splitlines()
    for line in reversed(lines):
        row = json.loads(line)
        event = row.get("payload", {})
        if row.get("type") != "event_msg":
            continue
        kind = event.get("type", "")
        if kind == "task_complete":
            when = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if (
                when.tzinfo is None
                or not isinstance(event["turn_id"], str)
                or not event["turn_id"]
            ):
                raise ValueError("unrecognized completion event")
            return event["turn_id"], when.timestamp()
        if kind == "task_started":
            return None
        if kind.startswith("task_") or kind in ("turn_aborted", "error"):
            raise ValueError("unsupported task lifecycle")
    raise ValueError("no recognized lifecycle in rollout tail")


def completions(root, database, session):
    with contextlib.closing(
        sqlite3.connect(database.as_uri() + "?mode=ro", timeout=0.1, uri=True)
    ) as db:
        db.set_progress_handler(lambda: 1, 1_000_000)
        row = db.execute(
            "SELECT rollout_path FROM threads WHERE id=?", (session,)
        ).fetchone()
        if not row:
            raise ValueError("live session absent from store")
        ended = lifecycle(root, session, row[0])
        if ended is None:
            return {}
        children = db.execute(
            """
            SELECT t.id,t.rollout_path FROM thread_spawn_edges e
            JOIN threads t ON t.id=e.child_thread_id
            WHERE e.parent_thread_id=? AND t.archived=0 AND t.updated_at>=?
            ORDER BY t.updated_at DESC LIMIT ?
        """,
            (session, int(ended[1]), CHILDREN_MAX + 1),
        ).fetchall()
        if len(children) > CHILDREN_MAX:
            raise ValueError("too many recent children")
        result = {}
        for child, rollout in children:
            done = lifecycle(root, child, rollout, session)
            if done and done[1] > ended[1]:
                result[child] = [done[0], done[1]]
        return result


def warn_once(path, session, error):
    message = f"{session}: {type(error).__name__}: {error}"
    warning = path.with_suffix(".warning")
    try:
        if warning.exists() and warning.read_text().strip() == message:
            return
        atomic_write(warning, message)
    except OSError:
        return
    print(f"orch-signal: subagent wake unavailable: {message}", file=sys.stderr)


def receipt_path(state, pane):
    return state / (hashlib.sha256(pane["pane"].encode()).hexdigest() + ".json")


def poll(work, state, session_for, send):
    root = store_root().resolve()
    databases = [
        (int(match[1]), path)
        for path in root.glob("state_*.sqlite")
        if (match := DATABASE.fullmatch(path.name))
    ]
    if not databases:
        return
    database = max(databases)[1]
    owners = {row["id"]: row.get("owner") for row in work["workspaces"]}
    live = {hashlib.sha256(pane["pane"].encode()).hexdigest() for pane in work["panes"]}
    for pattern in ("*.json", "*.warning"):
        for path in state.glob(pattern):
            if path.stem not in live:
                path.unlink(missing_ok=True)

    def receipt_age(pane):
        try:
            return receipt_path(state, pane).stat().st_mtime_ns
        except OSError:
            return 0

    for pane in sorted(work["panes"], key=receipt_age):
        if (
            pane.get("agent") != "codex"
            or pane.get("status") not in ("idle", "done")
            or not owners.get(pane["workspace"])
        ):
            continue
        path = receipt_path(state, pane)
        session = "unknown"
        attempted = False
        try:
            session = session_for(pane)
            if not session:
                continue
            saved = json.loads(path.read_text()) if path.exists() else {}
            if saved and (
                not isinstance(saved.get("seen"), dict)
                or not isinstance(saved.get("session"), str)
            ):
                raise ValueError("unrecognized wake receipt")
            seen = saved.get("seen", {}) if saved.get("session") == session else {}
            done = completions(root, database, session)
            pending = {
                child: mark for child, mark in done.items() if seen.get(child) != mark
            }
            if pending:
                if session_for(pane) != session:
                    continue
                atomic_write(
                    path,
                    json.dumps(
                        dict(
                            pane=pane["pane"],
                            session=session,
                            seen={
                                child: mark
                                for child, mark in seen.items()
                                if child in done
                            },
                        )
                    ),
                )
                attempted = True
                send(
                    session,
                    "Subagent completed while you were idle: "
                    + ", ".join(sorted(pending))
                    + ". Read the completed subagent results with list_agents/wait_agent and continue. "
                    "A wait timeout is not completion. If already handled, ignore this wake-up.",
                )
            atomic_write(
                path, json.dumps(dict(pane=pane["pane"], session=session, seen=done))
            )
            path.with_suffix(".warning").unlink(missing_ok=True)
            if pending:
                return
        except READ_ERRORS as error:
            warn_once(path, session, error)
            if attempted:
                return
