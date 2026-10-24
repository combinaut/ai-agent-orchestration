#!/usr/bin/env python3
"""Feed writer for watcher alerts. Dry-run by default; never injects pane input.
`--deliver file` appends policy lines to the Coordinator feed; native messages carry only PR events and worker recycles
to their owning architect and action lines while no feed Monitor is attached. Tokens come from mode-0600 binding files.
"""

import argparse
import fcntl
import importlib.util
import json
import os
import re
import socket
import stat
import sys
import time
from pathlib import Path

import alert_policy
import watcher_runtime as runtime
import watchers as w
from feed_actions import (
    ACTIONS_PATH,  # noqa: F401  (read as inbox.ACTIONS_PATH by the tests)
    action_patterns,
    fyi_patterns,
    is_action,
    native,
)
from feed_attach import (
    MONITOR_FRESH_SECONDS,  # noqa: F401  (read as inbox.MONITOR_FRESH_SECONDS by the tests)
    MONITOR_NAME,
    has_read,
    locked,
    read_record,
    will_read,
)

POLICY_KEY = "delivery"
PR_MODES = ("audit", "pulls")
PENDING_KEY = "pending_feed"
OUTBOX_KEY = "outbox"
OUTBOX_MAX = 200
OUTBOX_RETRY_SECONDS = 60
OUTBOX_KEEP_SECONDS = 3600
MONITOR_ACK_SECONDS = 20
FEED_NAME = "coordinator-feed.log"
FYI_NAME = "coordinator-fyi.log"
FYI = fyi_patterns()
FEED_BYTES = 1024 * 1024
TODO_OWNER = re.compile(r"^todo: TODO ANSWER owner=([A-Za-z0-9_-]+) ")
WAIT_OWNER = re.compile(
    r"^workers: WORKER WAITING .+ pane=\S+ owner=([A-Za-z0-9_-]+) \d+m session=\S+"
)
OWNER = re.compile(
    r"^(?:pulls: \S+ \S+#\d+|context: RECYCLE-WORKER(?:-SOFT|-HARD)? \S+|workers: WORKER IDLE \S+|workers: WORKER (?:DONE|QUESTION) \S+) owner=([A-Za-z0-9_-]+) "
)

spec = importlib.util.spec_from_file_location("reader", w.ROOT / "read-alerts.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def send_to_socket(path, token, message):
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("socket is not an owned socket")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(5)
        stream.connect(str(path))
        frames = [
            {"type": "auth", "token": token},
            {"type": "user", "message": {"role": "user", "content": message}},
        ]
        stream.sendall(
            ("\n".join(json.dumps(frame) for frame in frames) + "\n").encode()
        )
        stream.shutdown(socket.SHUT_WR)


def binding(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("binding must be an owned regular mode-0600 file")
    value = json.loads(path.read_text())
    pid = int(value["pid"])
    if not value["session_id"] or not value["token"]:
        raise ValueError("missing identity/auth")
    socket_path = Path(value["socket"])
    if socket_path.name != f"{pid}.sock" or socket_path.parent not in (
        Path("/tmp/cc-socks"),
        Path(f"/tmp/cc-socks-{os.getuid()}"),
    ):
        raise ValueError("not a native peer address")
    os.kill(pid, 0)
    session = json.loads((Path.home() / ".claude/sessions" / f"{pid}.json").read_text())
    if (
        value["session_id"] != session.get("sessionId")
        or value["proc_start"] != session.get("procStart")
        or value["socket"] != session.get("messagingSocketPath")
    ):
        raise ValueError("session binding changed; owner must rebind")
    return socket_path, value["token"]


def stamp(now, line):
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)) + " " + line


def dropped_lines(lines):
    """The oldest lines a batch over FEED_BYTES leaves out of the feed (see feed_data)."""
    size, kept = 0, 0
    for line in reversed(lines):
        size += len((" ".join(line.splitlines()) + "\n").encode())
        if size > FEED_BYTES - 200:
            break
        kept += 1
    total = sum(len((" ".join(line.splitlines()) + "\n").encode()) for line in lines)
    return [] if total <= FEED_BYTES else lines[: len(lines) - kept]


def feed_data(lines):
    """The bytes a batch takes in the feed: one physical line per entry; over FEED_BYTES, its newest lines that fit
    after a line naming how many were dropped."""
    dropped = dropped_lines(lines)
    lines = [" ".join(line.splitlines()) for line in lines]
    data = "".join(line + "\n" for line in lines[len(dropped) :]).encode()
    if dropped:
        data = (
            f"{lines[-1].split(' ', 1)[0]} watchers: {len(dropped)} feed lines over the size cap dropped (digest)\n".encode()
            + data
        )
    return data


def feed_lines(lines):
    """The action lines of a batch; the others go to the fyi file."""
    return [line for line in lines if not is_action(line, FYI)]


def place_batch(path, data):
    """Rotates to <feed>.1 when the batch would pass FEED_BYTES; returns (inode, offset) where the batch will go."""
    try:
        if path.stat().st_size + len(data) > FEED_BYTES:
            path.replace(path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)
    finally:
        os.close(fd)
    return info.st_ino, info.st_size


def write_batch(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)


def append_feed(path, lines):
    """Appends the batch with one O_APPEND write, rotating at FEED_BYTES; returns (inode, offset) of the write."""
    data = feed_data(lines)
    inode, offset = place_batch(path, data)
    write_batch(path, data)
    return inode, offset


def partial_batch(path, inode, offset, data):
    """True when file `inode` ends, at `offset`, with a strict prefix of the batch: a write cut short."""
    try:
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_ino != inode:
                return False
            stream.seek(offset)
            tail = stream.read()
    except FileNotFoundError:
        return False
    return 0 < len(tail) < len(data) and data.startswith(tail)


def batch_written(path, inode, offset, data):
    try:
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_ino != inode:
                return False
            stream.seek(offset)
            return stream.read(len(data)) == data
    except FileNotFoundError:
        return False


def send_line(binding_path, text):
    try:
        if "TODO ANSWER " in text:
            text = w.todo_answers.filter_canceled(text)
            if not text:
                return True
        path, token = binding(binding_path)
        send_to_socket(path, token, text)
        if binding_path.name.startswith("architect-"):
            w.todo_answers.mark_delivered(
                text, binding_path.stem.removeprefix("architect-")
            )
        return True
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(
            f"native delivery to {binding_path.name} failed: {type(error).__name__}",
            file=sys.stderr,
            flush=True,
        )
        return False


def commit(args, cursor):
    w.atomic(args.cursor, json.dumps(cursor, sort_keys=True), mode=0o600)


def native_messages(args, stamped, fallback, patterns):
    """One native message per architect with a binding file (a worker recycle or a worker idle line also for an
    architect not yet bound; it waits in the outbox), and one for the Coordinator, as (binding path, text)."""
    by_owner = {}
    for line in stamped:
        body = line.split(" ", 1)[1].replace(alert_policy.WORKER_WAIT_NOTE, "")
        if WAIT_OWNER.match(body):
            continue  # WAITING start and REPEAT are fyi; ESCALATE goes to the Coordinator below
        owner = OWNER.match(body) or TODO_OWNER.match(body)
        if (
            owner
            and not line.endswith("(clear)")
            and args.bindings_dir
            and (
                any(
                    signal in line
                    for signal in (
                        " RECYCLE-WORKER ",
                        " RECYCLE-WORKER-SOFT ",
                        " RECYCLE-WORKER-HARD ",
                    )
                )
                or " WORKER IDLE " in line
                or " WORKER DONE " in line
                or " WORKER QUESTION " in line
                or " TODO ANSWER " in line
                or (args.bindings_dir / f"architect-{owner.group(1)}.json").exists()
            )
        ):
            by_owner.setdefault(owner.group(1), []).append(
                native(line.split(" ", 1)[1])
                + (
                    ". Answer with orch-signal answer <id> <text>."
                    if body.startswith("workers: WORKER QUESTION ")
                    else ". No reply needed."
                )
            )
    out = [
        (str(args.bindings_dir / f"architect-{owner}.json"), "\n".join(texts))
        for owner, texts in sorted(by_owner.items())
    ]
    actions = (
        [line for line in stamped if is_action(line, patterns)]
        if args.binding and fallback
        else []
    )
    escalations = (
        [
            line
            for line in stamped
            if WAIT_OWNER.match(line.split(" ", 1)[1])
            and line.replace(alert_policy.WORKER_WAIT_NOTE, "").endswith(
                " ESCALATE (start)"
            )
        ]
        if args.binding
        else []
    )
    for batch in (actions, escalations):
        if batch:
            out.append(
                (
                    str(args.binding),
                    "\n".join(native(line) + ". No reply needed." for line in batch),
                )
            )
    return out


def bounded(entries):
    """Over OUTBOX_MAX, each binding's messages that wait on nothing merge into one, oldest first; beyond that the
    oldest go, with a stderr note."""
    if len(entries) <= OUTBOX_MAX:
        return entries
    merged, by_target = [], {}
    for entry in entries:
        if "watch" in entry:
            merged.append(entry)
            continue
        if entry["binding"] in by_target:
            first = by_target[entry["binding"]]
            first["text"] += "\n" + entry["text"]
            first["tried"] = max(first.get("tried", 0), entry.get("tried", 0))
        else:
            by_target[entry["binding"]] = dict(entry)
            merged.append(by_target[entry["binding"]])
    for entry in merged[: max(0, len(merged) - OUTBOX_MAX)]:
        print(
            f"native delivery to {Path(entry['binding']).name} dropped: outbox full",
            file=sys.stderr,
            flush=True,
        )
    return merged[-OUTBOX_MAX:]


def flush_outbox(args, cursor, now):
    """Sends queued native messages in order, one attempt per binding at most every OUTBOX_RETRY_SECONDS. A Coordinator
    copy waiting on the Monitor is dropped once the Monitor has read its batch, and sent when the Monitor is gone or has
    not read it within MONITOR_ACK_SECONDS. A message older than OUTBOX_KEEP_SECONDS is dropped with a stderr note.
    Returns whether the outbox changed."""
    outbox = cursor.get(OUTBOX_KEY, [])
    kept = [entry for entry in outbox if now - entry["at"] <= OUTBOX_KEEP_SECONDS]
    for entry in outbox:
        if entry not in kept:
            print(
                f"native delivery to {Path(entry['binding']).name} dropped after {OUTBOX_KEEP_SECONDS}s",
                file=sys.stderr,
                flush=True,
            )
    changed = len(kept) != len(outbox)
    if any("watch" in entry for entry in kept):
        record = read_record(args.feed.with_name(MONITOR_NAME), time.time())
        for entry in [entry for entry in kept if "watch" in entry]:
            watch = entry["watch"]
            if has_read(record, watch["inode"], watch["end"]):
                kept.remove(entry)
                changed = True
            elif record is None or now - entry["at"] >= MONITOR_ACK_SECONDS:
                del entry["watch"]
                changed = True
    for target in sorted({entry["binding"] for entry in kept}):
        queued = [entry for entry in kept if entry["binding"] == target]
        ready = []
        for entry in queued:
            if "watch" in entry:
                break
            ready.append(entry)
        if (
            not ready
            or now - max(entry.get("tried", 0) for entry in queued)
            < OUTBOX_RETRY_SECONDS
        ):
            continue
        changed = True
        if send_line(Path(target), "\n".join(entry["text"] for entry in ready)):
            kept = [entry for entry in kept if entry not in ready]
        else:
            for entry in queued:
                entry["tried"] = now
    cursor[OUTBOX_KEY] = bounded(kept)
    if not cursor[OUTBOX_KEY]:
        cursor.pop(OUTBOX_KEY)
    return changed


WAIT_ID = re.compile(
    r"(?:^| )workers: WORKER WAITING .+ pane=\S+ owner=(\S+) \d+m session=(\S+)"
)


def waiting_identity(line):
    found = WAIT_ID.search(line)
    return found.groups() if found else None


def cancel_waiting(cursor, lines):
    """A clear removes every queued stage of that owner/identity, preserving other lines and later restarts."""
    cleared = {
        waiting_identity(line): index
        for index, line in enumerate(lines)
        if line.endswith("(clear)") and waiting_identity(line) is not None
    }
    if cleared:
        kept = []
        for entry in cursor.get(OUTBOX_KEY, []):
            text = "\n".join(
                line
                for line in entry["text"].splitlines()
                if waiting_identity(line) not in cleared
            )
            if text:
                kept.append(dict(entry, text=text))
        if kept:
            cursor[OUTBOX_KEY] = kept
        else:
            cursor.pop(OUTBOX_KEY, None)
    return {
        index
        for index, line in enumerate(lines)
        if index > cleared.get(waiting_identity(line), -1)
    }


def finish(args, cursor, now, patterns):
    """Completes the pending batch. Under the feed lock its place is committed, it is written once, and the Monitor's
    record decides the Coordinator copy: none when the Monitor will read it (kept waiting until the Monitor has read it),
    native otherwise. Its native messages then go to the outbox, all committed with the cursor."""
    pending = cursor[PENDING_KEY]
    lines = pending["lines"]
    in_feed_index = [i for i, line in enumerate(lines) if not is_action(line, FYI)]
    data = feed_data([lines[i] for i in in_feed_index])
    if not pending["fed"]:
        with locked(args.feed):
            fyi = [line for line in lines if is_action(line, FYI)]
            if fyi and not pending.get("fyi"):
                fyi_path = args.feed.with_name(FYI_NAME)
                fyi_data = feed_data(fyi)
                place_batch(fyi_path, fyi_data)
                write_batch(fyi_path, fyi_data)
                pending["fyi"] = True
                commit(args, cursor)
            if not in_feed_index:
                pending.update(fed=True, coordinator="none")
                commit(args, cursor)
            elif "inode" not in pending or not batch_written(
                args.feed, pending["inode"], pending["offset"], data
            ):
                if "inode" in pending and partial_batch(
                    args.feed, pending["inode"], pending["offset"], data
                ):
                    os.truncate(args.feed, pending["offset"])
                if (
                    "inode" in pending
                    and args.feed.exists()
                    and args.feed.stat().st_ino == pending["inode"]
                    and args.feed.stat().st_size == pending["offset"]
                ):
                    write_batch(args.feed, data)
                else:
                    pending["inode"], pending["offset"] = place_batch(args.feed, data)
                    commit(args, cursor)
                    write_batch(args.feed, data)
            if in_feed_index:
                record = read_record(args.feed.with_name(MONITOR_NAME), time.time())
                pending.update(
                    fed=True,
                    coordinator="watch"
                    if will_read(record, pending["inode"], pending["offset"])
                    else "native",
                )
                commit(args, cursor)
    coordinator = pending.get(
        "coordinator", "native" if pending.get("fallback") else "none"
    )
    active = cancel_waiting(cursor, lines)
    dropped_count = len(dropped_lines([lines[i] for i in in_feed_index]))
    in_feed = [
        lines[i]
        for position, i in enumerate(in_feed_index)
        if position >= dropped_count and i in active
    ]
    dropped = [
        lines[i]
        for position, i in enumerate(in_feed_index)
        if position < dropped_count and i in active
    ]
    for target, text in native_messages(args, in_feed, coordinator != "none", patterns):
        entry = dict(binding=target, text=text, at=now)
        # WAITING escalation is native even with a Monitor: that feed line is deliberately not an action.
        escalation = all(
            "workers: WORKER WAITING " in line
            and " ESCALATE (start)" in line.replace(alert_policy.WORKER_WAIT_NOTE, "")
            for line in text.splitlines()
        )
        if target == str(args.binding) and coordinator == "watch" and not escalation:
            entry["watch"] = dict(
                inode=pending["inode"], end=pending["offset"] + len(data)
            )
        cursor.setdefault(OUTBOX_KEY, []).append(entry)
    for target, text in native_messages(args, dropped, True, patterns):
        cursor.setdefault(OUTBOX_KEY, []).append(
            dict(binding=target, text=text, at=now)
        )
    del cursor[PENDING_KEY]
    commit(args, cursor)
    if flush_outbox(args, cursor, now):
        commit(args, cursor)


def recover(args, cursor, now, patterns):
    """A batch a crash left pending is completed on start. A batch from an older notifier, without a recorded place, is
    taken as written when the feed ends with its exact bytes."""
    if isinstance(cursor.get(PENDING_KEY), list):
        cursor[PENDING_KEY] = {"lines": cursor[PENDING_KEY], "fed": False}
    pending = cursor.get(PENDING_KEY)
    if pending and not pending["fed"] and "inode" not in pending:
        data = feed_data(feed_lines(pending["lines"]))
        try:
            info = args.feed.stat()
            with args.feed.open("rb") as stream:
                stream.seek(max(0, info.st_size - len(data)))
                if info.st_size >= len(data) and stream.read() == data:
                    pending.update(inode=info.st_ino, offset=info.st_size - len(data))
        except FileNotFoundError:
            pass
    if cursor.get(PENDING_KEY):
        finish(args, cursor, now, patterns)


def deliver(args, cursor, lines, now, patterns):
    """The batch is committed as pending with the cursor before it is written, so a crash neither loses nor repeats it."""
    cursor[PENDING_KEY] = {"lines": [stamp(now, line) for line in lines], "fed": False}
    commit(args, cursor)
    finish(args, cursor, now, patterns)


def audit_snapshot(base):
    """Current audit lines and read failures; audit history keeps only additions of its latest 100 batches."""
    try:
        before = json.loads((base / "audit.json").read_text())["before"] or {}
    except (OSError, ValueError, KeyError, TypeError):
        return []
    lines = sorted(
        {line for source in before.get("sources", {}).values() for line in source}
    )
    lines += sorted(
        entry["line"]
        for entry in before.get("legacy", {}).values()
        if entry.get("line")
    )
    return lines + sorted(before.get("unreadable", {}).values())


def snapshot_lines(mode, before):
    """Lines that describe the conditions a producer's saved state holds now."""
    if mode in ("hog", "context"):
        return list(before.values())
    if mode == "workers":
        lines = [
            f"WORKER {alert['pane']} {alert['state']}"
            for alert in w.worker_alerts(before).values()
        ]
        lines += [
            w.waiting_line(key, entry, entry.get("sent", entry["since"]), suffix)
            for key, entry in before.get("waiting", {}).items()
            for suffix in ("", "REPEAT", "ESCALATE")[: entry["stage"]]
        ]
        lines += [
            w.idle_line(pane, entry["owner"])
            for pane, entry in sorted(before.get("idle", {}).items())
            if entry.get("state") == "fired"
        ]
        return lines + (
            ["WORKER READFAIL " + " ".join(before["unreadable"])]
            if before.get("unreadable")
            else []
        )
    if mode in w.PRESSURE_SETTINGS:
        labels = w.PRESSURE_SETTINGS[mode]["labels"]
        flags = before.get("flags", {})
        lines = (
            [
                mode.upper()
                + " "
                + " ".join(f"{labels[key]}(0)" for key in labels if key in flags)
            ]
            if flags
            else []
        )
        return lines + (
            [mode.upper() + " READFAIL " + " ".join(before["unreadable"])]
            if before.get("unreadable")
            else []
        )
    if mode == "pulls":
        return [
            w.pull_line(line, key, pr.get("owner"))
            for key, pr in before.get("prs", {}).items()
            for line in pr["lines"].values()
        ] + ["PULLS READFAIL " + " ".join(before["unreadable"])] * bool(
            before.get("unreadable")
        )
    return None


def snapshot_alert(base, mode):
    """The current conditions of a capped-history mode as one snapshot alert, or None without a readable state."""
    if mode == "audit":
        return {"mode": "audit", "events": audit_snapshot(base), "snapshot": True}
    try:
        state = json.loads((base / (mode + ".json")).read_text())
        lines = snapshot_lines(mode, state["before"] or {})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return (
        {"mode": mode, "events": lines, "snapshot": True} if lines is not None else None
    )


def resolve_gaps(base, alerts):
    """A reader that fell behind a capped history gets that mode's snapshot in place of the lost batches."""
    out = []
    for alert in alerts:
        if alert.get("gap"):
            snapshot = snapshot_alert(base, alert["mode"])
            if snapshot:
                out.append(snapshot)
        else:
            out.append(alert)
    return out


def current_keys(base, modes):
    """Keys each producer's saved state holds now, by mode, with the current text for pulls lines;
    a mode without a readable snapshot is left out."""
    out = {}
    for mode in modes:
        try:
            state = json.loads((base / (mode + ".json")).read_text())
            lines = snapshot_lines(mode, state["before"] or {})
            if lines is not None and state.get("log_failed"):
                lines.append(f"{mode} LOG WRITE FAILED")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if lines is not None:
            out[mode] = {
                item["key"]: item["text"] if mode == "pulls" else None
                for item in alert_policy.facts({"mode": mode, "events": lines})
                if item["kind"] == "alert"
            }
    return out


def unread_terminal(state, seen):
    """MERGED and CLOSED pulls lines in retained batches after `seen`; a snapshot no longer holds them."""
    return reader.terminal_alerts(
        [event for event in state["events"] if event["seq"] > seen]
    )


def history(base, cursor, modes, now, late=None):
    """Alerts the cursor already consumed, in order per mode; a mode new to the cursor takes all its events and the
    cursor moves past them. A capped-history mode comes from its snapshot instead, and the cursor moves past its history."""
    alerts = []
    for mode in modes:
        existing = mode in cursor
        seen = cursor.get(mode, {}).get("seq", 0)
        if mode in w.CAPPED_HISTORY:
            try:
                state = json.loads((base / (mode + ".json")).read_text())
                events = state["events"]
            except (OSError, ValueError, KeyError):
                continue
            reset = (
                state.get("reset")
                and cursor.get(mode, {}).get("reset") != state["reset"]
            )
            if reset:
                cursor.setdefault(
                    mode, {"seq": 0, "stale": False, "since": now}
                ).update(seq=0, reset=state["reset"])
                seen = 0
                if state.get("reset_line"):
                    alerts.append(
                        {
                            "mode": mode,
                            "events": [state["reset_line"]],
                            "notify_reset": True,
                        }
                    )
            snapshot = snapshot_alert(base, mode) or {"mode": mode, "events": []}
            if late is not None and existing:
                late += unread_terminal(state, 0 if reset else seen)
            health = (
                [f"{mode} read failed {state['fails']} times (saved state)"]
                if state.get("fails", 0) >= w.READ_FAILURES
                else []
            )
            alerts.append({"mode": mode, "events": snapshot["events"] + health})
            cursor.setdefault(mode, {"seq": 0, "stale": False, "since": now})["seq"] = (
                max([seen] + [event["seq"] for event in events])
            )
            continue
        try:
            state = json.loads((base / (mode + ".json")).read_text())
            events = state["events"]
        except (OSError, ValueError, KeyError):
            events = []
            state = {}
        fresh = mode not in cursor
        unseen_reset = False
        if state.get("reset") and cursor.get(mode, {}).get("reset") != state["reset"]:
            if fresh:
                seen = max([0] + [event["seq"] for event in events])
                cursor[mode] = {
                    "seq": seen,
                    "stale": False,
                    "since": now,
                    "reset": state["reset"],
                }
                unseen_reset = True
            else:
                cursor.setdefault(
                    mode, {"seq": 0, "stale": False, "since": now}
                ).update(seq=0, reset=state["reset"])
                seen = 0
        if mode in ("workers", "todo") and late is not None:
            prefixes = ("WORKER DONE ", "WORKER QUESTION ", "TODO ANSWER ")
            unread = [
                event for event in events if event["seq"] > (0 if fresh else seen)
            ]
            has_signals = any(
                any(
                    line.startswith(prefixes)
                    for line in json.loads(event["line"]).get("events", [])
                )
                and now - event.get("at", now) <= OUTBOX_KEEP_SECONDS
                for event in unread
            )
            if has_signals:
                for event in unread:
                    alert = json.loads(event["line"])
                    lines = [
                        line
                        for line in alert.get("events", [])
                        if (
                            line.startswith(prefixes)
                            and now - event.get("at", now) <= OUTBOX_KEEP_SECONDS
                        )
                        or (not fresh and not line.startswith(prefixes))
                    ]
                    if lines:
                        late.append(dict(alert, events=lines))
                cursor.setdefault(mode, {"seq": 0, "stale": False, "since": now})[
                    "seq"
                ] = max(event["seq"] for event in events)
                if fresh:
                    seen = max(event["seq"] for event in events)
        if mode not in cursor and events:
            seen = max(event["seq"] for event in events)
            cursor[mode] = {"seq": seen, "stale": False, "since": now}
        for event in events:
            if event["seq"] <= seen:
                alert = json.loads(event["line"])
                if mode in ("workers", "todo"):
                    alert["events"] = [
                        line
                        for line in alert.get("events", [])
                        if not line.startswith(
                            ("WORKER DONE ", "WORKER QUESTION ", "TODO ANSWER ")
                        )
                    ]
                if unseen_reset and any(
                    line.startswith("STATE RESET ") for line in alert.get("events", [])
                ):
                    alert["notify_reset"] = True
                alerts.append(alert)
    return alerts + [
        {"mode": mode, "events": ["HEARTBEAT STALE"]}
        for mode in modes
        if cursor.get(mode, {}).get("stale")
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--cursor", type=Path, required=True)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--bindings-dir", type=Path)
    parser.add_argument("--feed", type=Path)
    parser.add_argument("--deliver", choices=("file",))
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--modes", nargs="+", choices=w.MODES, default=w.MODES)
    parser.add_argument(
        "--configured-only",
        action="store_true",
        help="leave out audit and pulls while ORCH_PR_AUTHOR is empty",
    )
    args = parser.parse_args()
    if args.configured_only and not os.environ.get("ORCH_PR_AUTHOR"):
        args.modes = [mode for mode in args.modes if mode not in PR_MODES]
    with runtime.deadline(runtime.ITERATION_TIMEOUT, "notifier startup"):
        args.cursor.parent.mkdir(parents=True, exist_ok=True)
        lock = args.cursor.with_suffix(".lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cursor = json.loads(args.cursor.read_text()) if args.cursor.exists() else {}
        args.feed = args.feed or args.state_dir / FEED_NAME
        if args.binding and not args.bindings_dir:
            args.bindings_dir = args.binding.parent
        patterns = action_patterns()
        started_at = time.time()
        for mode in args.modes:
            if mode in cursor and not cursor[mode].get("stale"):
                # Services start together, so a producer is not stale before its grace from this start has passed.
                cursor[mode]["since"] = started_at
        if args.deliver:
            recover(args, cursor, started_at, patterns)
        if args.deliver and cursor.get(POLICY_KEY, {}).get("version") == 2:
            now = time.time()
            current = (
                current_keys(args.state_dir, ["pulls"]).get("pulls", {})
                if "pulls" in args.modes
                else {}
            )
            lines = alert_policy.repair_owners(cursor[POLICY_KEY], current)
            if lines:
                deliver(args, cursor, lines, now, patterns)
            commit(args, cursor)
        if (
            args.deliver
            and cursor.get(POLICY_KEY, {}).get("version") != alert_policy.STATE_VERSION
        ):
            now = time.time()
            late = []
            cursor[POLICY_KEY], lines = alert_policy.rebuild(
                history(args.state_dir, cursor, args.modes, now, late),
                now,
                current=current_keys(args.state_dir, args.modes),
            )
            lines += alert_policy.apply(cursor[POLICY_KEY], late, now)
            if lines:
                deliver(args, cursor, lines, now, patterns)
            commit(args, cursor)
    for _ in runtime.iterations(5, args.loop, "notifier"):
        now = time.time()
        known = set(cursor)
        line = reader.read_once(args.state_dir, cursor, now, 900, args.modes)
        started_modes = set(cursor) != known
        if args.deliver:
            policy = cursor.setdefault(POLICY_KEY, {})
            started = not policy
            messages = alert_policy.apply(
                policy,
                resolve_gaps(args.state_dir, json.loads(line)["alerts"])
                if line
                else [],
                now,
            )
            if messages:
                deliver(args, cursor, messages, now, patterns)
            elif line or started or started_modes:
                commit(args, cursor)
            if cursor.get(OUTBOX_KEY) and flush_outbox(args, cursor, now):
                commit(args, cursor)
        elif line:
            print(line, flush=True)
        # Preview cursor is separate from the delivery cursor by caller convention.
        if (line or started_modes) and not args.deliver:
            w.atomic(args.cursor, json.dumps(cursor, sort_keys=True))


if __name__ == "__main__":
    main()
