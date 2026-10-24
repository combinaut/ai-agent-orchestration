#!/usr/bin/env python3
"""Event reducers. Never sends messages or mutates source state."""

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic as _monotonic

import alert_policy
import audit
import discovery
import question_remind
import todo_answers
import watcher_runtime as runtime
import worker_recycle as recycle_policy
import worker_signals
from question_lock import HELD, answers_lock, questions_directory
from watcher_platform import (
    MissingMetric,
    balance_metrics,
    herdr_bin,
    host_metrics,
    process_rows,
)

ROOT = Path(__file__).resolve().parent
MODES = (
    "context",
    "workers",
    "hog",
    "balance",
    "host",
    "prs",
    "audit",
    "pulls",
    "todo",
    "answers",
)

WATCH_TIMING = {
    "context": {"soft_budget": 120, "interval": 300, "heartbeat_grace": 900},
    "workers": {"soft_budget": 120, "interval": 60, "heartbeat_grace": 900},
    "hog": {"soft_budget": 120, "interval": 5, "heartbeat_grace": 900},
    "balance": {"soft_budget": 300, "interval": 60, "heartbeat_grace": 900},
    "host": {"soft_budget": 120, "interval": 60, "heartbeat_grace": 900},
    "prs": {"soft_budget": 600, "interval": 60, "heartbeat_grace": 900},
    "audit": {"soft_budget": 1800, "interval": 1800, "heartbeat_grace": 4500},
    "pulls": {"soft_budget": 600, "interval": 150, "heartbeat_grace": 900},
    "todo": {"soft_budget": 120, "interval": 60, "heartbeat_grace": 900},
    "answers": {"soft_budget": 120, "interval": 60, "heartbeat_grace": 900},
}
YOUNG_PANE_SECONDS = 120
HISTORY_BATCHES = 100
CAPPED_HISTORY = ("audit", "pulls")
READ_FAILURES = 3
PULLS_DEPARTED_MAX = 10
PULLS_DEPARTED_SECONDS = 60
# Pulls needs stable identities; audit retains its existing presentation-only query.
PULLS_SELECTION = audit.SELECTION.replace(
    "nodes{isResolved}", "nodes{id isResolved}"
).replace(
    "reviews(last:6){nodes{author{login} body}}",
    "reviews(last:100){pageInfo{hasPreviousPage} nodes{id author{login}}} "
    "recentReviews:reviews(last:6){nodes{id author{login} body}}",
)
GITHUB_REMOTE = discovery.GITHUB_REMOTE

BALANCE_LIMITS = {"free": 30, "slots": 4, "heavy": 4, "load5": 20, "cpu": 80}


def balance_limits():
    limits = dict(BALANCE_LIMITS)
    limits["slots"] = int(os.environ.get("ORCH_HEAVY_SLOTS_MAX", limits["slots"]))
    return limits


def command(*argv, timeout=None):
    result = (
        runtime.run(argv) if timeout is None else runtime.run(argv, timeout=timeout)
    )
    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, argv, output=result.stdout, stderr=result.stderr
        )
    if not result.stdout.strip():
        raise MissingMetric(f"{argv[0]} empty response")
    return result.stdout


def atomic(path, data, mode=None):
    tmp = path.with_suffix(path.suffix + ".tmp")
    if mode is not None:
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode))
        os.chmod(tmp, mode)
    with tmp.open("w") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def pressure_limits(mode):
    def configured(name, default):
        return float(os.environ.get(name, default))

    return {
        "host": {
            "free": (configured("ORCH_HOST_PRESSURE_MIN_PCT", 20), False),
            "swap": (configured("ORCH_HOST_SWAP_MAX_MB", 1024), True),
            "procs": (configured("ORCH_HOST_PROCS_MAX", 1300), True),
            "files": (configured("ORCH_HOST_FILES_MAX", 150000), True),
            "load": (configured("ORCH_HOST_LOAD_MAX", 40), True),
        },
        "balance": {
            key: (limit, key != "free")
            for key, limit in balance_limits().items()
            if key != "cpu"
        },
    }[mode]


def thresholds(mode, values):
    return {
        key: f"{key}={values[key]}"
        for key, (limit, above) in pressure_limits(mode).items()
        if key in values
        and (float(values[key]) > limit if above else float(values[key]) < limit)
    }


def agents(raw):
    rows = raw["result"]["agents"]
    if not isinstance(rows, list):
        raise ValueError("agents not a list")
    return rows


def identity(agent):
    return (agent.get("agent_session") or {}).get("value") or agent["pane_id"]


def workspace_owners(raw):
    """The `owner` tag of each workspace by id; none when the workspace list was not read."""
    rows = ((raw.get("workspaces") or {}).get("result") or {}).get("workspaces") or []
    return {
        row.get("workspace_id"): row["tokens"].get("owner")
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("tokens"), dict)
    }


def architect_like(agent):
    """An architect or the Coordinator, by role tag or by name; never a worker."""
    label = agent.get("name") or agent.get("terminal_title_stripped") or ""
    return (
        label.startswith("Architect::")
        or (agent.get("tokens") or {}).get("role") in ("architect", "coordinator")
        or "Architect::" in (agent.get("terminal_title_stripped") or "")
    )


def waiting_owners(raw):
    """Validate the workspace envelope before treating missing owners as authoritative."""
    listing = raw.get("workspaces")
    if listing is None:
        return None
    try:
        rows = [] if listing == {} else listing["result"]["workspaces"]
        if not isinstance(rows, list) or not all(
            isinstance(row, dict)
            and "workspace_id" in row
            and (row.get("tokens") is None or isinstance(row["tokens"], dict))
            for row in rows
        ):
            return None
        return workspace_owners(raw)
    except (KeyError, TypeError):
        return None


def recycle_amount(value):
    """The context used, as `<n>k` or `<n>%`, at or over the recycle threshold; None under it or without a reading."""
    if value.get("tokens") is not None:
        tokens = int(value["tokens"])
        return (
            f"{tokens // 1000}k"
            if tokens >= int(os.environ.get("ORCH_RECYCLE_TOKENS", 300000))
            else None
        )
    if value.get("remaining") is None:
        return None
    used = 100 - float(value["remaining"])
    return (
        f"{used:.0f}%"
        if used >= float(os.environ.get("ORCH_RECYCLE_PCT", 55))
        else None
    )


def worker_recycle(agent, value, owners):
    """`RECYCLE-WORKER-{SOFT,HARD} <pane> owner=<workspace> [<name>] <amount>` for a worker over the threshold whose workspace has
    an owner tag. Only the agent name labels it: a terminal title changes while the worker runs."""
    owner = owners.get(agent.get("workspace_id"))
    stage = recycle_policy.stage(value) if owner else None
    amount = (
        (
            f"{int(value['tokens']) // 1000}k"
            if value.get("tokens") is not None
            else f"{100 - float(value['remaining']):.0f}%"
        )
        if stage
        else None
    )
    if not amount:
        return None
    return " ".join(
        part
        for part in (
            f"RECYCLE-WORKER-{stage}",
            agent["pane_id"],
            f"owner={owner}",
            agent.get("name"),
            amount,
        )
        if part
    )


def conditions(mode, raw):
    if mode in PRESSURE_SETTINGS:
        return thresholds(
            mode, {key: value for key, value in raw.items() if value is not None}
        )
    if mode == "context":
        out = {}
        owners = workspace_owners(raw)
        for agent in agents(raw["herdr"]):
            if agent.get("agent") != "claude":
                continue
            label = agent.get("name") or agent.get("terminal_title_stripped") or ""
            sid = (agent.get("agent_session") or {}).get("value")
            value = raw["usage"].get(sid, {})
            if not architect_like(agent):
                line = worker_recycle(agent, value, owners)
                if line:
                    out[sid] = line
                continue
            if (
                not sid
                or not value
                or (value.get("tokens") is None and value.get("remaining") is None)
            ):
                out[f"nosensor:{sid or agent['pane_id']}"] = (
                    f"NOSENSOR {agent['pane_id']} {label}"
                )
                continue
            amount = recycle_amount(value)
            if amount:
                out[sid] = f"RECYCLE {agent['pane_id']} {label} {amount}"
        return out
    if mode == "hog":
        out = {}
        for proc in raw:
            rss, age = float(proc["rss"]), int(proc["age"])
            kind = (
                "HOG"
                if rss >= float(os.environ.get("ORCH_HOG_GB", 6)) * 1048576
                else None
            )
            if Path(proc["comm"]).name == "git":
                kind = (
                    "GITBIG"
                    if rss >= float(os.environ.get("ORCH_HOG_GIT_GB", 2)) * 1048576
                    else kind
                )
                kind = kind or (
                    "GITLONG"
                    if age >= int(os.environ.get("ORCH_HOG_GIT_AGE_S", 180))
                    else None
                )
            if kind:
                out[f"{proc['pid']}:{proc['start']}:{kind}"] = hog_detail(
                    kind, proc, raw
                )
        return out
    raise ValueError(mode)


def pr_snapshot(rows):
    out = {}
    for pr in rows:
        sha, state = pr["headRefOid"], pr["state"]
        if not sha or state not in ("OPEN", "MERGED", "CLOSED"):
            raise ValueError("unknown PR state/head")
        if len(pr.get("latestReviews", {}).get("nodes", [])) >= 100:
            raise ValueError("latest reviewer page may be truncated")
        threads = pr["reviewThreads"]
        if threads["pageInfo"]["hasNextPage"]:
            raise ValueError("unread thread page")
        ci = (
            (pr["commits"]["nodes"] or [{}])[0]
            .get("commit", {})
            .get("statusCheckRollup")
            or {}
        ).get("state")
        if state == "OPEN" and ci not in (
            "SUCCESS",
            "PENDING",
            "EXPECTED",
            "FAILURE",
            "ERROR",
        ):
            raise ValueError("unknown/empty head checks")
        out[pr["url"]] = {
            "id": pr["id"],
            "title": pr["title"],
            "sha": sha,
            "state": state,
            "decision": pr["reviewDecision"],
            "ci": ci,
            "threads": sorted(t["id"] for t in threads["nodes"] if not t["isResolved"]),
            "requests": sorted(
                review["id"]
                for review in pr.get("latestReviews", {}).get("nodes", [])
                if review.get("state") == "CHANGES_REQUESTED"
            ),
        }
    return out


HOST_FLAGS = {
    "free": "PRESSURE",
    "swap": "SWAP",
    "procs": "PROCS",
    "files": "FILES",
    "load": "LOAD",
}


def host_detail(key, value):
    if value is None:
        return "unavailable"
    if key == "free":
        return f"{value:.0f}% free"
    if key == "swap":
        return f"{value:.2f}MB"
    if key == "load":
        return f"{value:.2f}"
    return str(value)


BALANCE_FLAGS = {
    "load5": "LOAD5",
    "slots": "SLOTS",
    "heavy": "HEAVYWT",
    "free": "MEMFREE",
}


def balance_detail(key, value):
    if value is None:
        return "unavailable"
    if key == "load5":
        return f"{value:.2f}"
    if key == "free":
        return f"{value:g}%"
    return str(value)


def balance_top(raw):
    return " top: " + " ".join(
        f"{row['name']}({row['cpu']:.0f}%,{row['mb']:.0f}MB)"
        for row in raw.get("top", [])[:4]
    )


PRESSURE_SETTINGS = {
    "host": dict(
        labels=HOST_FLAGS,
        steps={"swap": 1000, "files": 10000},
        counts=(),
        detail=host_detail,
        suffix=None,
        defer_recovery=False,
        state_fields=(),
        read_grace=False,
    ),
    "balance": dict(
        labels=BALANCE_FLAGS,
        steps={"load5": 10},
        counts=("slots",),
        detail=balance_detail,
        suffix=balance_top,
        defer_recovery=True,
        state_fields=("cpu_sample",),
        read_grace=True,
    ),
}


def ratio_step(key, raw, limits, detail):
    return alert_policy.step(
        alert_policy.number(detail(key, float(raw[key]))),
        limits[key][0],
        not limits[key][1],
    )


def pressure_keys(mode, flags, raw, after, settings):
    steps, limits = settings["steps"], pressure_limits(mode)
    keys = {
        key: raw[key]
        if key in settings["counts"]
        else f"{int(float(raw[key]) // steps[key])}/{ratio_step(key, raw, limits, settings['detail'])}"
        if key in steps
        else ratio_step(key, raw, limits, settings["detail"])
        for key in after
    }
    keys.update({key: value for key, value in flags.items() if raw.get(key) is None})
    return keys


def readfail_changes(prefix, previous, unreadable):
    if unreadable == previous:
        return []
    return [prefix + " READFAIL " + (" ".join(unreadable) or "cleared")]


def reduce_pressure(mode, before, raw, after):
    previous = before or {}
    flags = previous.get("flags", previous)
    settings = PRESSURE_SETTINGS[mode]
    labels = settings["labels"]
    prefix = mode.upper()
    unreadable = [key for key in labels if raw.get(key) is None] + raw.get(
        "unreadable", []
    )
    state_extra = {}
    reported = unreadable
    read = raw  # what this scan read; a kept value prints as unavailable
    if settings["read_grace"]:
        # A source is reported only after READ_FAILURES unreadable passes in a row; until then
        # a metric keeps its last good value.
        # A state saved before the grace has no counts; a source it already reported stays reported.
        passes = {
            source: previous.get("unreadable_passes", {}).get(
                source,
                READ_FAILURES - 1 if source in previous.get("unreadable", []) else 0,
            )
            + 1
            for source in unreadable
        }
        good = previous.get("last_good", {})
        raw = dict(
            raw,
            **{
                key: good[key]
                for key in labels
                if raw.get(key) is None and passes[key] < READ_FAILURES and key in good
            },
        )
        reported = [source for source in unreadable if passes[source] >= READ_FAILURES]
        state_extra = dict(
            unreadable_passes=passes,
            last_good={key: raw[key] for key in labels if raw.get(key) is not None},
        )
        after = conditions(mode, raw)
    keys = pressure_keys(mode, flags, raw, after, settings)
    pending = previous.get("pending_recovery", False) or bool(flags) or bool(keys)
    events = readfail_changes(prefix, previous.get("unreadable", []), reported)
    if keys != flags and keys:
        line = (
            prefix
            + " "
            + " ".join(
                f"{label}({settings['detail'](key, read.get(key))})"
                for key, label in labels.items()
                if key in keys
            )
        )
        if settings["suffix"]:
            line += settings["suffix"](raw)
        events.append(line)
    if not keys and pending and (not settings["defer_recovery"] or not unreadable):
        events.append(prefix + " RECOVERED")
        pending = False
    state = dict(
        flags=keys, unreadable=reported, pending_recovery=pending, **state_extra
    )
    state.update({key: raw.get(key) for key in settings["state_fields"]})
    return state, events


def context_step(line):
    found = alert_policy.context_facts(line) if isinstance(line, str) else None
    return (
        alert_policy.step(found[0]["value"], found[0]["limit"], False) if found else 0
    )


def recycle_cleared(line):
    return re.sub(r"( \d+(?:k|%))?$", " cleared", line, count=1)


def nosensor_cleared(line):
    return "NOSENSOR CLEARED " + line.split(" ")[1]


def subject_cleared(line):
    return (
        nosensor_cleared(line)
        if line.startswith("NOSENSOR ")
        else recycle_cleared(line)
    )


RECYCLE_LINES = (
    "RECYCLE ",
    "RECYCLE-WORKER ",
    "RECYCLE-WORKER-SOFT ",
    "RECYCLE-WORKER-HARD ",
)
WORKSPACE_FAILS = "workspaces:unreadable"


def workspace_failures(previous, raw):
    """The passes in a row that `herdr workspace list` was unreadable, kept in the saved state as a string."""
    return (
        int(previous.get(WORKSPACE_FAILS, 0)) + 1
        if raw.get("workspaces") is None
        else 0
    )


def worker_untagged(key, line, raw):
    """True when a successful workspace read shows this worker owner removed or changed."""
    if (
        not line.startswith(
            ("RECYCLE-WORKER ", "RECYCLE-WORKER-SOFT ", "RECYCLE-WORKER-HARD ")
        )
        or raw.get("workspaces") is None
    ):
        return False
    owners = workspace_owners(raw)
    return any(
        owners.get(row.get("workspace_id")) != line.split()[2].removeprefix("owner=")
        for row in agents(raw["herdr"])
        if (row.get("agent_session") or {}).get("value") == key
    )


def reduce_context(before, after, raw):
    previous = before or {}
    after = dict(after)
    for key, line in after.items():
        old = previous.get(key, "")
        if (
            old.startswith("RECYCLE-WORKER-HARD ")
            and line.startswith("RECYCLE-WORKER-SOFT ")
            and old.split()[2] == line.split()[2]
        ):
            # A forced handoff can be waiting on a heavy slot. Keep it pending
            # for this session/owner even if a later sensor reading decreases.
            after[key] = line.replace("RECYCLE-WORKER-SOFT ", "RECYCLE-WORKER-HARD ", 1)
    moved = {
        key
        for key in after
        if key in previous
        and previous[key].startswith(RECYCLE_LINES + ("NOSENSOR ",))
        and subject_cleared(previous[key]) != subject_cleared(after[key])
    }
    present = {
        (a.get("agent_session") or {}).get("value") for a in agents(raw["herdr"])
    }
    remembered = {
        key: value
        for key, value in previous.items()
        if not key.startswith("nosensor:")
        and key in present
        and not (key not in after and worker_untagged(key, value, raw))
    }
    # A departed session clears before any start: a successor in the same pane shares the policy key.
    events = [subject_cleared(previous[key]) for key in sorted(moved)]
    events += [
        nosensor_cleared(value)
        for key, value in sorted(previous.items())
        if key.startswith("nosensor:") and key not in after
    ]
    events += [
        recycle_cleared(value)
        for key, value in sorted(previous.items())
        if key not in remembered
        and key not in after
        and value.startswith(RECYCLE_LINES)
    ]
    events += [
        after[key]
        for key in sorted(after)
        if key not in previous
        or key in moved
        or (
            not after[key].startswith("RECYCLE-WORKER")
            and context_step(after[key]) > context_step(previous[key])
        )
    ]
    remembered.update(after)
    fails = workspace_failures(previous, raw)
    if fails:
        remembered[WORKSPACE_FAILS] = str(fails)
    if fails == READ_FAILURES:
        events.append(f"context read failed {READ_FAILURES} times (WorkspaceList)")
    elif not fails and int(previous.get(WORKSPACE_FAILS, 0)) >= READ_FAILURES:
        events.append("context READ RECOVERED")
    return remembered, events


WORKER_STATUSES = ("working", "idle", "done", "blocked", "error", "waiting")
ACTIVE_WORKER_STATUSES = ("working", "waiting", "blocked", "error")
IDLE_STATUSES = ("idle", "done")


def idle_seconds():
    return int(os.environ.get("ORCH_WORKER_IDLE_S", "7200"))


def idle_line(pane, owner, cleared=False):
    return f"WORKER IDLE {pane} owner={owner}" + (" cleared" if cleared else "")


def idle_due(previous, rows, owners, now):
    """The workers in `rows` that have been idle or done for more than ORCH_WORKER_IDLE_S in this idle period, have no
    line yet, and are due an open-PR check: the first one, or the next one, ORCH_WORKER_IDLE_S after the last."""
    due = []
    for row in rows:
        entry = (previous or {}).get(row["pane_id"])
        owner = owners.get(row.get("workspace_id"))
        if (
            entry
            and owner
            and entry["owner"] == owner
            and entry.get("state") != "fired"
            and row.get("agent_status") in IDLE_STATUSES
            and now - entry["since"] > idle_seconds()
            and entry.get("retry", 0) <= now
        ):
            due.append(row)
    return due


def reduce_idle(previous, raw, present):
    """The idle periods of owned workers, and a `WORKER IDLE` line the first time a period passes the threshold with
    no open PR. A period ends when the worker works again, leaves or changes owner; the line then clears."""
    now = raw.get("observed_at")
    if now is None or raw.get("workspaces") is None:
        return previous, []
    owners = workspace_owners(raw)
    checks, found, fired = raw.get("pr_open") or {}, {}, []
    for row in present.values():
        pane, owner = row["pane_id"], owners.get(row.get("workspace_id"))
        if (
            not owner
            or architect_like(row)
            or row.get("agent_status") not in IDLE_STATUSES
        ):
            continue
        old = previous.get(pane)
        session = (row.get("agent_session") or {}).get("value")
        same = (
            old
            and old["owner"] == owner
            and not (session and old.get("session") and session != old["session"])
        )
        entry = dict(old) if same else {"owner": owner, "since": now}
        if session:
            entry["session"] = session
        if (
            entry.get("state") != "fired"
            and now - entry["since"] > idle_seconds()
            and pane in checks
            and entry.get("retry", 0) <= now
        ):
            if checks[pane] is None or checks[pane] is True:
                if checks[pane]:
                    entry["state"] = "open_pr"
                entry["retry"] = now + idle_seconds()
            else:
                entry["state"] = "fired"
                fired.append(idle_line(pane, owner))
        found[pane] = entry
    cleared = [
        idle_line(pane, entry["owner"], cleared=True)
        for pane, entry in sorted(previous.items())
        if entry.get("state") == "fired" and found.get(pane, {}).get("state") != "fired"
    ]
    return found, cleared + fired


def worker_snapshot(raw, previous, bad):
    by_pane = {row["pane_id"]: key for key, row in previous.items()}
    rows = agents(raw)
    live_keys = {identity(row) for row in rows}
    present = {}
    for row in rows:
        key = identity(row)
        old_key = by_pane.get(row["pane_id"])
        if old_key is not None and old_key != key:
            old = previous[old_key]
            new_sid = (row.get("agent_session") or {}).get("value")
            if not new_sid and old_key not in live_keys:
                key = old_key
                row = dict(row, agent_session=old.get("agent_session"))
            elif old_key == row["pane_id"] and key not in previous:
                previous[key] = previous.pop(old_key)
                if old_key in bad:
                    bad[key] = bad.pop(old_key)
            elif old_key not in live_keys:
                previous.pop(old_key)
                bad.pop(old_key, None)
        present[key] = row
    return present


def worker_line(alert, recovered=False):
    action = "RECOVERED " if recovered else ""
    return f"WORKER {alert['pane']} {action}{alert['state']}"


def worker_alerts(previous):
    alerts = {}
    for key, value in previous.get("bad", {}).items():
        if isinstance(value, str):
            row = previous.get("present", {}).get(key, {})
            if row.get("agent_status") not in ("blocked", "error"):
                continue
            value = dict(pane=row["pane_id"], state=row["agent_status"])
        alerts[key] = value
    return alerts


def waiting_line(key, entry, now, suffix=""):
    row = entry["row"]
    tail = (
        f" pane={row['pane_id']} owner={entry['owner']} "
        f"{int((now - entry['since']) // 60)}m session={key}"
        + (f" {suffix}" if suffix else "")
        + alert_policy.WORKER_WAIT_NOTE
    )
    budget = max(1, alert_policy.LINE_LIMIT - len("WORKER WAITING ") - len(tail))
    name = (
        " ".join(
            (row.get("name") or row.get("terminal_title_stripped") or "worker").split()
        )
        or "worker"
    )[: min(24, budget)]
    return f"WORKER WAITING {name}" + tail


def reduce_waiting(previous, raw):
    """Timers use session IDs, falling back to panes. Unreadable metadata freezes existing state."""
    old = previous.get("waiting", {})
    now = raw.get("observed_at", time.time())
    rows = {identity(row): row for row in agents(raw)}
    gaps = {}
    old_panes = {entry["row"]["pane_id"]: key for key, entry in old.items()}
    for row in agents(raw):
        key = old_panes.get(row["pane_id"])
        if (
            not (row.get("agent_session") or {}).get("value")
            and key
            and key != identity(row)
            and key not in rows
        ):
            rows.pop(identity(row), None)
            rows[key] = row
            gaps[key] = True
    owners = waiting_owners(raw)
    current, events = {}, []
    for key, row in rows.items():
        entry = old.get(key)
        if worker_signals.suppressed(row, raw):
            continue
        if architect_like(row):
            continue
        if row.get("agent_status") == "working" and key not in gaps:
            continue
        seq = row.get("state_change_seq")
        old_seq = entry["row"].get("state_change_seq") if entry else None
        if seq is not None and old_seq is not None and seq != old_seq:
            if entry["stage"]:
                events.append(waiting_line(key, entry, now, "cleared"))
            entry = None
        owner = owners.get(row.get("workspace_id")) if owners is not None else None
        if owners is not None and not owner:
            continue
        if (
            row.get("agent_status") not in WORKER_STATUSES
            or owners is None
            or key in gaps
        ):
            if entry and owners is not None and owner != entry["owner"]:
                continue
            if entry:
                saved_row = dict(entry["row"], pane_id=row["pane_id"])
                if seq is not None:
                    saved_row["state_change_seq"] = seq
                current[key] = dict(entry, row=saved_row)
            continue
        if row.get("agent_status") not in IDLE_STATUSES:
            continue
        if entry and entry["owner"] != owner:
            if entry["stage"]:
                events.append(waiting_line(key, entry, now, "cleared"))
            entry = None
        if seq is None and old_seq is not None:
            row = dict(row, state_change_seq=old_seq)
        entry = dict(entry or dict(since=now, stage=0, owner=owner), row=dict(row))
        stage = entry["stage"]
        delay = (
            float(os.environ.get("ORCH_WAIT_S", 600))
            if stage == 0
            else float(os.environ.get("ORCH_WAIT_REPEAT_S", 1800))
        )
        since = entry["since"] if stage == 0 else entry["sent"]
        if stage:
            delay = max(60, delay)
        if (
            row["pane_id"] not in raw.get("waiting_busy", [])
            and stage < 3
            and (now - since > delay if stage == 0 else now - since >= delay)
        ):
            events.append(
                waiting_line(key, entry, now, ("", "REPEAT", "ESCALATE")[stage])
            )
            entry.update(stage=stage + 1, sent=now)
        current[key] = entry
    for key, entry in old.items():
        if key not in current and entry["stage"]:
            line = waiting_line(key, entry, now, "cleared")
            if line not in events:
                events.append(line)
    return current, events


def reduce_workers(before, raw):
    previous = before or {}
    old_bad = worker_alerts(previous)
    old_present = dict(previous.get("present", {}))
    present = worker_snapshot(raw, old_present, old_bad)
    now, closed = raw.get("observed_at"), set(raw.get("closed", []))
    panes = {row["pane_id"] for row in present.values()}
    seen = {
        pane: since for pane, since in previous.get("seen", {}).items() if pane in panes
    }
    if now is not None:
        seen.update({pane: now for pane in panes if pane not in seen})
    bad, unreadable = {}, []
    for key, row in present.items():
        status = row.get("agent_status")
        if status not in WORKER_STATUSES:
            young = now is not None and now - seen[row["pane_id"]] < YOUNG_PANE_SECONDS
            unreadable.append(f"{row['pane_id']}({status}{',new' if young else ''})")
            if key in old_bad:
                bad[key] = dict(old_bad[key], pane=row["pane_id"])
            # Retain the last known lifecycle state while this agent is unreadable.
            if key in old_present:
                present[key] = dict(
                    row, agent_status=old_present[key].get("agent_status")
                )
        elif status in ("blocked", "error"):
            bad[key] = dict(pane=row["pane_id"], state=status)
    for key, row in old_present.items():
        if key not in present and row.get("agent_status") in ACTIVE_WORKER_STATUSES:
            bad[key] = dict(
                pane=row["pane_id"],
                state="closed" if row["pane_id"] in closed else "dead/disappeared",
            )
    unreadable.sort()
    events = readfail_changes("WORKER", previous.get("unreadable", []), unreadable)
    idle, idle_events = reduce_idle(previous.get("idle", {}), raw, present)
    for key in sorted(set(old_bad) | set(bad)):
        old, new = old_bad.get(key, {}), bad.get(key, {})
        pane = new.get("pane") or (present[key]["pane_id"] if key in present else None)
        if old and pane and pane != old["pane"]:
            events.append(f"WORKER {old['pane']} MOVED {pane}")
        if old.get("state") == new.get("state"):
            continue
        if old and key in present:
            events.append(
                worker_line(dict(old, pane=present[key]["pane_id"]), recovered=True)
            )
        if new:
            events.append(worker_line(new))
    active_done = {
        record.get("id")
        for record in raw.get("worker_signals", {}).values()
        if record["kind"] == "done"
    }
    resumed = set(previous.get("resumed_done_ids", []))
    idle_done = set(previous.get("idle_done_ids", []))
    if "worker_signals" in raw:
        resumed &= active_done
        idle_done &= active_done
    for row in agents(raw):
        record = raw.get("worker_signals", {}).get(row["pane_id"])
        if record and record["kind"] == "done" and now > record["at"]:
            if row.get("agent_status") in IDLE_STATUSES:
                idle_done.add(record.get("id"))
            elif row.get("agent_status") == "working" and record.get("id") in idle_done:
                resumed.add(record.get("id"))
    raw = dict(raw, resumed_done_ids=list(resumed))
    waiting, waiting_events = reduce_waiting(previous, raw)
    events += waiting_events
    signal_events = raw.get("signal_events", [])
    signal_ids = (
        [record["id"] for record in signal_events]
        if "signal_events" in raw
        else previous.get("signal_ids", [])
    )
    events += [
        worker_signals.event_line(record)
        for record in signal_events
        if record["id"] not in previous.get("signal_ids", [])
    ]
    result = dict(
        bad={key: value for key, value in bad.items() if key in present},
        present=present,
        unreadable=unreadable,
        seen=seen,
        waiting=waiting,
        waiting_read_failures=raw.get(
            "waiting_read_failures", previous.get("waiting_read_failures", [])
        ),
        idle=idle,
    )
    if idle_done:
        result["idle_done_ids"] = sorted(idle_done)
    if resumed:
        result["resumed_done_ids"] = sorted(resumed)
    if signal_ids:
        result["signal_ids"] = signal_ids
    return result, events + idle_events


def pull_owners(command, prs, failed=None, work=None):
    """Owner tag of the open workspace whose checkout has each PR's head branch, by <repo>#<n>.
    work is the open work already read (discovery.open_work), or the Unavailable it raised.
    Each root is identified by its `origin` remote and its `upstream` remote, the base repo of a fork; only roots with
    a watched repository have their worktrees listed. A PR takes the owner of the checkout of its head branch when the
    head repository's owner is the owner of the root's origin, so a same-named branch of another fork has none.
    A root that fails is skipped and listed in `failed` by the `owner/name` of its origin and upstream, or as `*/<name>`
    when its origin could not be read. An unreadable upstream does not skip the root: its origin is still handled, and
    `*` is listed, since the base repo it names is unknown."""
    work = discovery.open_work(command) if work is None else work
    if isinstance(work, discovery.Unavailable):
        raise work
    rows = work["workspaces"]
    by_path = {
        row["checkout"]: row for row in rows if row.get("checkout") and row.get("owner")
    }
    watched = {pr["repository"]["nameWithOwner"].lower() for pr in prs}
    branches = {}
    for root in sorted({row["repo_root"] for row in rows if row.get("repo_root")}):
        identities = ["*/" + Path(root).name.lower()]
        try:
            remote = GITHUB_REMOTE.search(
                command("git", "-C", root, "remote", "get-url", "origin").strip()
            )
            if not remote:
                continue
            origin = remote.group(1).lower()
            identities = ["*/" + origin.split("/")[1]]
            bases = {origin}
            try:
                bases.add(
                    (discovery.github_repo(command, root, "upstream") or origin).lower()
                )
            except discovery.Unavailable:
                if failed is not None:
                    failed.append("*")
            identities = sorted(bases)
            if not bases & watched:
                continue
            listing = command("git", "-C", root, "worktree", "list", "--porcelain")
        except (*audit.READ_ERRORS, discovery.Unavailable) as error:
            if isinstance(
                error, subprocess.CalledProcessError
            ) and audit.orch_settings.NO_ORIGIN.search(str(error.stderr or "")):
                continue
            if failed is not None:
                failed.extend(identities)
            continue
        path = None
        for line in listing.splitlines():
            if line.startswith("worktree "):
                path = line[9:]
            elif line.startswith("branch refs/heads/") and path in by_path:
                for base in bases:
                    branches[(base, origin.split("/")[0], line[18:])] = path
    owners = {}
    for pr in prs:
        repo = pr["repository"]["nameWithOwner"]
        head = (
            ((pr.get("headRepository") or {}).get("nameWithOwner") or "/")
            .split("/")[0]
            .lower()
        )
        owner = (
            by_path.get(branches.get((repo.lower(), head, pr.get("headRefName")))) or {}
        ).get("owner")
        if owner:
            owners[f"{repo}#{pr['number']}"] = owner
    return owners


def pull_check_evidence(pr):
    """Visible latest check outcomes; unseen names remain unknown on truncated connections."""
    commits = pr["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    contexts = (
        rollup["contexts"]
        if rollup
        else {"nodes": [], "pageInfo": {"hasNextPage": False}}
    )
    checks = {}
    for check in contexts["nodes"]:
        name = check.get("name") or check["context"]
        if name not in checks or (check.get("startedAt") or "") >= (
            checks[name].get("startedAt") or ""
        ):
            checks[name] = check
    failing = {
        name
        for name, check in checks.items()
        if check.get("conclusion") in ("FAILURE", "TIMED_OUT")
        or check.get("state") in ("FAILURE", "ERROR")
    }
    return set(checks), failing, contexts["pageInfo"]["hasNextPage"]


def collect_pulls(command, before, clock=time.monotonic):
    """Departed-PR reads run right after the search with their own reserved window, before the owner lookup."""
    known = (before or {}).get("prs", {})
    page_nodes = audit.positive_setting("ORCH_AUDIT_PAGE_NODES", 100, 100)
    try:
        work = discovery.open_work(command)
    except discovery.Unavailable as error:
        work = error
    found = discovery.repos(command, work)
    prs = audit.fetch_prs(
        command, page_nodes, found["repos"], selection=PULLS_SELECTION
    )
    searched = {f"{pr['repository']['nameWithOwner']}#{pr['number']}" for pr in prs}
    watched = {repo.lower() for repo in found["repos"]}
    departed = {
        key: "UNWATCHED"
        for key in set(known) - searched
        if key.rsplit("#", 1)[0].lower() not in watched
    }
    failed, attempted = [], set()
    pending = sorted(
        set(known) - searched,
        key=lambda key: (
            known[key].get("departure_reads", known[key].get("misses", 0)),
            key,
        ),
    )
    deadline = clock() + PULLS_DEPARTED_SECONDS
    for key in pending[:PULLS_DEPARTED_MAX]:
        if clock() >= deadline:
            break
        attempted.add(key)
        repo, number = key.rsplit("#", 1)
        try:
            row = json.loads(command("gh", "api", f"repos/{repo}/pulls/{number}"))
            state = "MERGED" if row.get("merged") else row["state"].upper()
            # Unwatched PRs get one bounded chance to report a terminal event.
            # Open/unreadable ones retain the drop marker, never saved evidence.
            if departed.get(key) != "UNWATCHED" or state in ("MERGED", "CLOSED"):
                departed[key] = state
        except audit.READ_ERRORS:
            if departed.get(key) != "UNWATCHED":
                failed.append(key)
    waiting = [
        key
        for key in pending
        if key not in attempted
        and departed.get(key) != "UNWATCHED"
        and known[key].get("misses", 0)
    ]
    unreadable, roots = [], []
    try:
        owners = pull_owners(command, prs, roots, work)
    except (*audit.READ_ERRORS, discovery.Unavailable):
        owners, unreadable = {}, ["owners"]
    if roots:
        unreadable = ["owners"]
    current, settings, settings_failed = {}, {"clones": found["clones"]}, []
    for pr in prs:
        key = f"{pr['repository']['nameWithOwner']}#{pr['number']}"

        gate_checks = None

        def repo_settings(pr=pr, key=key):
            nonlocal gate_checks
            try:
                values = audit.repo_settings(
                    command,
                    pr["repository"]["nameWithOwner"],
                    pr["baseRefName"],
                    settings,
                )
                gate_checks = sorted(
                    set(
                        filter(
                            None, values.get("ORCH_MERGE_GATE_CHECKS", "").split("|")
                        )
                    )
                )
                return values
            except audit.READ_ERRORS:
                gate_checks = old.get("gate_checks")
                settings_failed.append(f"settings:{key}")
                raise

        old = dict(known.get(key, {}))
        old["identities"] = pull_saved_identities(old)
        head, base = pr.get("headRefOid"), pr.get("baseRefName")
        same_scope = old.get("head") == head and old.get("base") == base
        try:
            # The 100-review connection is metadata for dedupe; bodies/classification keep the six-review window.
            display_pr = dict(pr, reviews=pr.get("recentReviews", pr["reviews"]))
            visible_bot_reviews = [
                review["id"]
                for review in pr["reviews"]["nodes"]
                if (review.get("author") or {}).get("login")
                == os.environ.get(
                    "ORCH_BOT_REVIEWER_LOGIN", "copilot-pull-request-reviewer"
                )
            ]
            lines = {
                line.split(" ", 1)[0]: line
                for line in audit.pr_lines(display_pr, page_nodes, repo_settings)
                if line.split(" ", 1)[0] in alert_policy.PULL_KINDS
            }
            visible, failing, checks_truncated = pull_check_evidence(pr)
            check_failures = sorted(failing)
            # A bounded connection is not proof that a previous condition has gone.
            incomplete = []
            if checks_truncated:
                if old.get("check_failures") is not None:
                    failing |= set(old["check_failures"]) - visible
                    check_failures = sorted(failing)
                    if failing:
                        lines["RED"] = (
                            f"RED {key} {pr['title']} [{','.join(check_failures)}]"
                        )
                    else:
                        lines.pop("RED", None)
                elif "RED" in old.get("lines", {}) and not failing:
                    # Legacy snapshots lack per-check evidence; retain that unknown condition until a full read.
                    incomplete.append("RED")
                    check_failures = None
                stacked = lines.get("APPROVED-STACKED", "")
                if stacked.endswith(", full CI not run]"):
                    lines["APPROVED-STACKED"] = (
                        stacked.removesuffix("full CI not run]") + "full CI unknown]"
                    )
                    incomplete.append("APPROVED-STACKED")
            identities = pull_identities(display_pr, lines)
            if pr["reviewThreads"]["pageInfo"]["hasNextPage"]:
                incomplete.append("THREADS")
            same_ci_scope = (
                same_scope
                and gate_checks is not None
                and gate_checks == old.get("gate_checks")
            )
            for kind in incomplete:
                if kind == "APPROVED-STACKED" and not same_ci_scope:
                    continue
                if kind == "THREADS" and kind in old.get("identities", {}):
                    # A partial page can prove a visible resolution/new thread, never an unseen resolution.
                    threads = pr["reviewThreads"]["nodes"]
                    unseen = set(old["identities"][kind]) - {
                        thread["id"] for thread in threads
                    }
                    active = sorted(
                        unseen
                        | {
                            thread["id"]
                            for thread in threads
                            if not thread["isResolved"]
                        }
                    )
                    if active:
                        identities[kind] = active
                        lines[kind] = (
                            f"THREADS {key} {pr['title']} [{len(active)} unresolved]"
                        )
                    else:
                        lines.pop(kind, None)
                        identities.pop(kind, None)
                    continue
                if kind in old.get("lines", {}):
                    lines[kind] = old["lines"][kind]
                if kind in old.get("identities", {}):
                    identities[kind] = old["identities"][kind]
            if (
                same_scope
                and f"settings:{key}" in settings_failed
                and "APPROVED-STACKED" in old.get("lines", {})
            ):
                lines["APPROVED-STACKED"] = old["lines"]["APPROVED-STACKED"]
                if "APPROVED-STACKED" in old.get("identities", {}):
                    identities["APPROVED-STACKED"] = old["identities"][
                        "APPROVED-STACKED"
                    ]
            if incomplete or checks_truncated:
                unreadable.append(f"pr:{key}")
        except audit.READ_ERRORS:
            # Partial per-PR data must not discard readable siblings or saved evidence.
            lines, identities = old.get("lines", {}), old.get("identities", {})
            check_failures = old.get("check_failures")
            visible_bot_reviews = None
            gate_checks = old.get("gate_checks")
            head, base = old.get("head"), old.get("base")
            unreadable.append(f"pr:{key}")
        owner = owners[key] if key in owners else old.get("owner")
        current[key] = dict(
            title=pr["title"],
            owner=owner,
            lines=lines,
            identities=identities,
            seen_reviews=old.get("seen_reviews", []),
            visible_bot_reviews=visible_bot_reviews,
            check_failures=check_failures,
            head=head,
            base=base,
            gate_checks=gate_checks,
        )
    return dict(
        prs=current,
        departed=departed,
        failed=failed,
        unreadable=unreadable
        + [f"pr:{key}" for key in sorted(failed + waiting)]
        + sorted(set(settings_failed)),
    )


def pull_identities(pr, lines):
    """JSON-stable event identities, independent of display text and pane/session IDs."""
    label = f"{pr['repository']['nameWithOwner']}#{pr['number']} {pr['title']}"
    identities = {
        kind: line[len(kind) + 1 + len(label) :] for kind, line in lines.items()
    }
    if "THREADS" in lines:
        identities["THREADS"] = sorted(
            thread["id"]
            for thread in pr["reviewThreads"]["nodes"]
            if not thread["isResolved"]
        )
    if "APPROVED-OLDHEAD" in lines:
        identities["APPROVED-OLDHEAD"] = [
            pr["headRefOid"],
            sorted(
                (review.get("commit") or {}).get("oid") or ""
                for review in pr["latestOpinionatedReviews"]["nodes"]
                if review["state"] == "APPROVED"
            ),
        ]
    bot = os.environ.get("ORCH_BOT_REVIEWER_LOGIN", "copilot-pull-request-reviewer")
    reviews = [
        row
        for row in pr["reviews"]["nodes"]
        if (row.get("author") or {}).get("login") == bot
    ]
    if "COPILOT" in lines:
        identities["COPILOT"] = reviews[-1]["id"]
    return identities


def pull_saved_identities(pr):
    """Read identities from the previous head-keyed schema without alerting on the format change."""
    identities = dict(pr.get("identities", {}))
    for kind, value in identities.items():
        if (
            kind == "THREADS"
            and isinstance(value, list)
            and len(value) == 2
            and isinstance(value[1], list)
        ):
            identities[kind] = value[1]
        elif (
            kind not in ("THREADS", "COPILOT", "APPROVED-OLDHEAD")
            and isinstance(value, list)
            and len(value) == 2
        ):
            identities[kind] = value[1]
    return identities


def pull_line(line, key, owner):
    """`<kind> <repo>#<n> owner=<workspace or ?> <rest>`: the owner sits before any PR text."""
    kind, _, rest = line.partition(" ")
    return f"{kind} {key} owner={owner or '?'}" + rest[len(key) :]


def reduce_pulls(before, raw):
    current = {key: dict(pr) for key, pr in raw["prs"].items()}
    events = (
        []
        if before is None
        else readfail_changes("PULLS", before.get("unreadable", []), raw["unreadable"])
    )
    for key, pr in sorted(current.items()):
        previous = dict(((before or {}).get("prs", {})).get(key, {}))
        previous["identities"] = pull_saved_identities(previous)
        old = previous.get("lines", {})
        identities = pr.get("identities", {})
        seen = set(previous.get("seen_reviews", []))
        visible_reviews = pr.pop("visible_bot_reviews", None)
        legacy_reviews = (
            key in (before or {}).get("prs", {}) and "seen_reviews" not in previous
        )
        if before is None or legacy_reviews:
            seen.update(visible_reviews or [])
        rerouted = pr["owner"] and pr["owner"] != previous.get("owner")
        for kind, line in sorted(pr["lines"].items()):
            identity = identities.get(kind)
            changed = kind not in old or (
                kind in previous.get("identities", {})
                and identity != previous["identities"][kind]
            )
            if (
                kind == "THREADS"
                and kind in previous["identities"]
                and identity is not None
            ):
                changed = bool(set(identity) - set(previous["identities"][kind]))
            if kind == "COPILOT" and identity is not None:
                # Seed legacy active state silently on its first identity-aware read.
                changed = identity not in seen and not legacy_reviews
                seen.add(identity)
            if before is not None and (changed or rerouted):
                # End the previous identity before starting its replacement, including in the feed policy.
                if kind in old and changed:
                    events.append(f"{kind} {key} cleared")
                events.append(pull_line(line, key, pr["owner"]))
        seen.update(visible_reviews or [])
        if legacy_reviews and visible_reviews is None:
            pr.pop(
                "seen_reviews", None
            )  # Defer migration until the reviews are readable.
        else:
            pr["seen_reviews"] = sorted(seen)
        if before is not None:
            events += [
                f"{kind} {key} cleared" for kind in sorted(set(old) - set(pr["lines"]))
            ]
    if before is None:
        active = sum(len(pr["lines"]) for pr in current.values())
        return dict(prs=current, unreadable=[]), [
            f"PULLS BASELINE [{active} active lines]"
        ]
    after = dict(current)
    for key, old in sorted(before["prs"].items()):
        if key in current:
            continue
        state = raw["departed"].get(key)
        if state == "UNWATCHED":
            events += [f"{kind} {key} cleared" for kind in sorted(old["lines"])]
            continue
        if state not in ("MERGED", "CLOSED"):
            after[key] = dict(old)
            if state is not None or key in raw.get("failed", []):
                after[key]["departure_reads"] = (
                    old.get("departure_reads", old.get("misses", 0)) + 1
                )
                after[key]["misses"] = (
                    old.get("misses", 0) + 1 if key in raw.get("failed", []) else 0
                )
            continue
        events += [f"{kind} {key} cleared" for kind in sorted(old["lines"])]
        events.append(pull_line(f"{state} {key} {old['title']}", key, old.get("owner")))
    return dict(prs=after, unreadable=raw["unreadable"]), events


_TODO_MODULE = None


def todo_module():
    global _TODO_MODULE
    if _TODO_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "user_todo", ROOT.parent / "user-todo.py"
        )
        _TODO_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_TODO_MODULE)
    return _TODO_MODULE


def collect_todo(before):
    module = todo_module()
    directory = Path(
        os.environ.get("ORCH_USER_TODO_STATUS_DIR")
        or Path.home() / ".claude/coordinator/status"
    )
    coordinator = Path.home() / ".claude/coordinator"
    output = Path(
        os.environ.get("ORCH_USER_TODO_OUTPUT") or coordinator / "user-todo.md"
    )
    state = Path(
        os.environ.get("ORCH_USER_TODO_STATE") or coordinator / "user-todo.state.json"
    )
    raw = module.merge(directory, output, state)
    records, failed = todo_answers.collect(command)
    if todo_answers.directory().exists():
        raw["todo_answers"] = records
        raw["todo_pending_ids"] = list(todo_answers.pending_ids())
        raw["todo_answer_notices"] = [
            notice for notice in failed if notice.startswith("answer discarded:")
        ]
        raw["unreadable"] += [
            notice for notice in failed if not notice.startswith("answer discarded:")
        ]
    return raw


def reduce_todo_status(before, raw):
    current = {item["id"]: item for item in raw["now"]}
    directory_down = any(Path(path).suffix != ".md" for path in raw["unreadable"])
    if directory_down and before:
        raw = dict(
            raw,
            unreadable=sorted(
                set(raw["unreadable"]) | set(before.get("unreadable", []))
            ),
        )
    unreadable_names = {Path(path).name for path in raw["unreadable"]}
    kept = set((before or {}).get("read", []))
    scanned = bool(raw.get("scanned")) or bool((before or {}).get("scanned"))
    read = sorted(
        set(raw.get("read", [])) | (kept if directory_down else kept & unreadable_names)
    )
    if before is None:
        return {
            "now": current,
            "unreadable": raw["unreadable"],
            "read": read,
            "scanned": scanned,
        }, [f"TODO READFAIL {path}" for path in sorted(raw["unreadable"])]
    events = []
    for path in sorted(set(raw["unreadable"]) - set(before.get("unreadable", []))):
        events.append(f"TODO READFAIL {path}")
    for path in sorted(set(before.get("unreadable", [])) - set(raw["unreadable"])):
        events.append(f"TODO READ RECOVERED {path}")
    recovered = set(before.get("unreadable", [])) - set(raw["unreadable"])
    known_sources = {item["source"] for item in before.get("now", {}).values()} | set(
        before.get("read", [])
    )
    baseline_sources = {
        Path(path).name for path in recovered if Path(path).name not in known_sources
    }
    new = [
        current[key]
        for key in sorted(set(current) - set(before.get("now", {})))
        if current[key]["source"] not in baseline_sources
    ]
    if (
        any(Path(path).suffix != ".md" for path in recovered)
        and not before.get("now")
        and not before.get("read")
        and not before.get("scanned")
    ):
        new = []
    if new:
        events.append(
            "TODO "
            + str(len(new))
            + " new: "
            + "; ".join(f"{item['source']}: {item['text']}" for item in new)
        )
    return {
        "now": current,
        "unreadable": raw["unreadable"],
        "read": read,
        "scanned": scanned,
    }, events


def reduce_todo(before, raw):
    after, events = reduce_todo_status(before, raw)
    events += [
        "TODO READFAIL " + notice for notice in raw.get("todo_answer_notices", [])
    ]
    records = raw.get("todo_answers", [])
    previous = set((before or {}).get("todo_answer_ids", []))
    now = raw.get("observed_at", time.time())
    retries = (before or {}).get("todo_answer_retry_at", {})
    emitted = {
        record["id"]
        for record in records
        if record["id"] not in previous
        or now - retries.get(record["id"], now) >= todo_answers.KEEP_SECONDS + 60
    }
    events += [
        todo_answers.event_line(record) for record in records if record["id"] in emitted
    ]
    pending = set(raw.get("todo_pending_ids", [record["id"] for record in records]))
    seen = (previous & pending) | {record["id"] for record in records}
    if seen:
        after["todo_answer_ids"] = sorted(seen)
        after["todo_answer_retry_at"] = {
            identifier: now if identifier in emitted else retries.get(identifier, now)
            for identifier in seen
        }
    return after, events


def answer_order(path):
    claim = path.name.split(".", 2)[1]
    if re.fullmatch(r"[0-9]{20}-[0-9a-f]{32}", claim):
        return int(claim.split("-", 1)[0]), path.name
    try:
        return path.stat().st_mtime_ns, path.name
    except OSError:
        return 0, path.name


def open_question(slug, question, timeout=question_remind.OPEN_TIMEOUT):
    if slug == "close":
        from question_lock import run_group

        return run_group(
            [str(ROOT.parents[1] / "ask-user-question"), slug, str(question)],
            timeout,
            env=dict(os.environ, **{HELD: "1"}),
            check=False,
            quiet=True,
        )
    argv = [
        "/usr/bin/env",
        f"{HELD}=1",
        str(ROOT.parents[1] / "ask-user-question"),
        slug,
        str(question),
    ]
    return runtime.run(argv, timeout=timeout).returncode


def collect_answers(before, consume=True):
    directory = questions_directory()
    answers, unreadable, blocked = [], [], set()
    try:
        files = list(directory.iterdir())
        claimed = sorted(
            (path for path in files if path.name.endswith(".answer.processing")),
            key=answer_order,
        )
        last_order = max((answer_order(path)[0] for path in claimed), default=0)
        paths = claimed + sorted(
            (path for path in files if path.name.endswith(".answer")), key=answer_order
        )
    except FileNotFoundError:
        return {"answers": [], "unreadable": []}
    except OSError:
        return {"answers": [], "unreadable": [str(directory)]}
    for path in paths:
        processing = path
        try:
            slug = path.name.split(".", 1)[0]
            if slug in blocked:
                continue
            if not (directory / f"{slug}.md").is_file():
                if consume:
                    path.unlink(missing_ok=True)
                continue
            if consume and path.suffix != ".processing":
                last_order = max(time.time_ns(), last_order + 1)
                processing = path.with_name(
                    f"{slug}.{last_order:020d}-{uuid.uuid4().hex}.answer.processing"
                )
            if path != processing:
                path.replace(processing)
            text = " ".join(processing.read_text().split())
            if not text:
                if consume:
                    processing.unlink()
                answers.append((slug, "", "", "blank"))
                continue
            claim = processing.name.split(".", 2)[1]
            answers.append((slug, text, str(processing), claim))
        except (OSError, UnicodeError):
            blocked.add(slug)
            unreadable.append(str(processing))
    if consume:
        try:
            question_remind.reopen_due(directory, time.time(), open_question)
        except (OSError, subprocess.SubprocessError):
            pass
    return {"answers": answers, "unreadable": unreadable}


def reduce_answers(before, raw):
    committed = set((before or {}).get("committed", []))
    try:
        pending = {
            path.name.split(".", 2)[1]
            for path in questions_directory().iterdir()
            if path.name.endswith(".answer.processing")
        }
    except OSError:
        pending = committed.copy()
    pending.update(key for _, text, _, key in raw["answers"] if text)
    committed &= pending
    events = [
        f"ANSWER {slug}: {text}"
        for slug, text, _, key in raw["answers"]
        if text and key not in committed
    ]
    events += [
        f"ANSWERS blank {slug}" for slug, text, _, _ in raw["answers"] if not text
    ]
    events += [f"ANSWERS READFAIL {path}" for path in raw["unreadable"]]
    events += [
        f"ANSWERS READ RECOVERED {path}"
        for path in sorted(
            set((before or {}).get("unreadable", [])) - set(raw["unreadable"])
        )
    ]
    committed.update(key for _, text, _, key in raw["answers"] if text)
    return {"unreadable": raw["unreadable"], "committed": sorted(committed)}, events


def acknowledge_answers(raw):
    notices = []
    for slug, text, path, _ in raw.get("answers", []):
        if not path:
            continue
        notice = worker_signals.bridge_user_answer(slug, text)
        if notice:
            notices.append(notice)
        processing = Path(path)
        processing.replace(
            processing.with_name(processing.name.removesuffix(".processing") + ".seen")
        )
    return notices


def reduce(mode, before, raw):
    if mode == "audit":
        return audit.reduce(before, raw)
    if mode == "pulls":
        return reduce_pulls(before, raw)
    if mode == "todo":
        return reduce_todo(before, raw)
    if mode == "answers":
        return reduce_answers(before, raw)
    if mode == "prs":
        after = pr_snapshot(raw)
        missing = set(before or {}) - set(after)
        if missing:
            raise ValueError("tracked PR disappeared without a state read")
        events = []
        for url, now in sorted(after.items()):
            was = (before or {}).get(url, {})
            label = f"{now['title']} ({url})"
            for thread in sorted(set(now["threads"]) - set(was.get("threads", []))):
                events.append(f"{label}: unresolved thread {thread}")
            if (
                now["decision"] == "CHANGES_REQUESTED"
                and was.get("decision") != now["decision"]
            ) or (set(now["requests"]) - set(was.get("requests", []))):
                events.append(f"{label}: CHANGES_REQUESTED")
            if now["ci"] in ("FAILURE", "ERROR") and (
                was.get("ci") not in ("FAILURE", "ERROR")
                or was.get("sha") != now["sha"]
            ):
                events.append(f"{label}: red checks on {now['sha'][:9]}")
            if now["state"] in ("MERGED", "CLOSED") and now["state"] != was.get(
                "state"
            ):
                events.append(f"{label}: {now['state']}")
        return after, events
    if mode == "workers":
        return reduce_workers(before, raw)
    after = conditions(mode, raw)
    if mode in PRESSURE_SETTINGS:
        return reduce_pressure(mode, before, raw, after)
    if mode == "context":
        return reduce_context(before, after, raw)
    return reduce_hog(before, after)


def hog_key(key):
    """Hog keys are pid:start:kind; a trailing severity step from older state is dropped."""
    head, _, tail = key.rpartition(":")
    return head if tail.isdigit() else key


def hog_cleared(line):
    match = re.match(r"(\S+) pid=(\d+) .*? comm=\[([^\]]*)\]", line) or re.match(
        r"(\S+) pid=(\d+) .*? cmd=\[(?:[^\] ]*/)?([^\] /]+)", line
    )
    return (
        f"{match.group(1)} pid={match.group(2)} comm=[{match.group(3)}] cleared"
        if match
        else None
    )


def reduce_hog(before, after):
    previous = {hog_key(key): line for key, line in (before or {}).items()}
    renamed = sorted(
        key
        for key in set(after) & set(previous)
        if hog_cleared(after[key]) != hog_cleared(previous[key])
    )
    events = [after[key] for key in sorted(set(after) - set(previous))]
    live = {hog_cleared(line) for line in after.values()}
    events += [
        line
        for key in renamed
        for line in (hog_cleared(previous[key]), after[key])
        if line
    ]
    events += [
        line
        for key in sorted(set(previous) - set(after))
        if (line := hog_cleared(previous[key])) and line not in live
    ]
    return after, events


HOG_LOG_BYTES = 1024 * 1024


def clean_command(value, limit):
    return " ".join(value.split())[:limit]


def hog_detail(kind, proc, rows):
    parents = []
    by_pid = {str(row["pid"]): row for row in rows}
    pid = str(proc.get("ppid", "0"))
    seen = {str(proc["pid"])}
    while pid not in ("0", "1") and pid not in seen and len(parents) < 5:
        parent = by_pid.get(pid)
        if parent is None:
            break
        seen.add(pid)
        parents.append(
            f"{pid} {clean_command(parent.get('command', parent['comm']), 90)}"
        )
        pid = str(parent.get("ppid", "0"))
    cmd = clean_command(proc.get("command", proc["comm"]), 300)
    return (
        f"{kind} pid={proc['pid']} rss={float(proc['rss']) / 1048576:.1f}GB "
        f"age={proc['age']}s comm=[{clean_command(Path(proc['comm']).name, 60)}] cmd=[{cmd}] parents:"
        + "".join(" <- " + p for p in parents)
    )


def append_hog_log(base, lines):
    path = base / "hog.log"
    for line in lines:
        entry = (time.strftime("%Y-%m-%d %H:%M:%S") + " " + line + "\n").encode()
        if path.exists() and path.stat().st_size + len(entry) > HOG_LOG_BYTES:
            path.replace(base / "hog.log.1")
        with path.open("ab", buffering=0) as stream:
            stream.write(entry)
            os.fsync(stream.fileno())


def failed_read(state, mode, failures, error_name):
    state["fails"] += 1
    if state["fails"] == failures:
        return [f"{mode} read failed {failures} times ({error_name})"]
    return []


def publish_events(base, mode, state, events):
    line = (
        json.dumps({"mode": mode, "events": events}, sort_keys=True) if events else None
    )
    if line:
        state["events"].append(
            {
                "seq": state["events"][-1]["seq"] + 1 if state["events"] else 1,
                "line": line,
                "at": time.time(),
            }
        )
        if mode in CAPPED_HISTORY:
            state["events"] = state["events"][-HISTORY_BATCHES:]
    atomic(base / (mode + ".json"), json.dumps(state, sort_keys=True))
    atomic(
        base / (mode + ".alerts"), "".join(e["line"] + "\n" for e in state["events"])
    )
    return line


def log_hog_hits(base, state, hits):
    if not hits:
        return []
    try:
        append_hog_log(base, hits.values())
        failed = False
    except OSError:
        failed = True
    was_failed = state.get("log_failed", False)
    events = (
        ["hog LOG WRITE FAILED"]
        if failed and not was_failed
        else ["hog LOG WRITE RECOVERED"]
        if was_failed and not failed
        else []
    )
    if failed != was_failed:
        state["log_failed"] = failed
        publish_events(base, "hog", state, events)
    return events


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def reset_corrupt_state(state_path, reason):
    stamp = utc_stamp()
    moved = state_path.with_name(state_path.name + ".corrupt-" + stamp)
    suffix = 1
    while moved.exists():
        moved = state_path.with_name(state_path.name + f".corrupt-{stamp}.{suffix}")
        suffix += 1
    moved.write_bytes(state_path.read_bytes())
    backups = sorted(state_path.parent.glob(state_path.name + ".corrupt-*"))
    for backup in backups[:-3]:
        backup.unlink()
    reset_line = f"STATE RESET {state_path.stem}: {reason}; moved to {moved.name}"
    state = {
        "before": None,
        "fails": 0,
        "events": [],
        "reset": moved.name,
        "reset_line": reset_line,
        "baseline_pending": True,
    }
    publish_events(state_path.parent, state_path.stem, state, [reset_line])
    return state, reset_line


def load_state(state_path, mode):
    if not state_path.exists():
        return {"before": None, "fails": 0, "events": []}, None
    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return reset_corrupt_state(state_path, "invalid JSON")
    if not isinstance(state, dict):
        return reset_corrupt_state(state_path, "state is not an object")
    if "before" not in state:
        return reset_corrupt_state(state_path, "missing before")

    def valid_event(event):
        try:
            return (
                isinstance(event, dict)
                and isinstance(event.get("seq"), int)
                and event["seq"] > 0
                and isinstance(event.get("line"), str)
                and isinstance(json.loads(event["line"]), dict)
            )
        except (TypeError, ValueError, KeyError):
            return False

    if (
        not isinstance(state["before"], (dict, type(None)))
        or not isinstance(state.get("fails"), int)
        or state["fails"] < 0
        or not isinstance(state.get("events"), list)
        or any(not valid_event(event) for event in state["events"])
    ):
        return reset_corrupt_state(state_path, "invalid state shape")
    if (
        mode == "pulls"
        and state["before"] is not None
        and (
            not isinstance(state["before"].get("prs"), dict)
            or not isinstance(state["before"].get("unreadable"), list)
        )
    ):
        return reset_corrupt_state(state_path, "invalid state shape")
    return state, None


def cycle(base, mode, collector, failures=READ_FAILURES, budget=None):
    budget = WATCH_TIMING[mode]["soft_budget"] if budget is None else budget
    with runtime.deadline(2 * budget, mode):
        if mode == "answers":
            with answers_lock():
                return cycle_pass(base, mode, collector, failures, budget)
        return cycle_pass(base, mode, collector, failures, budget)


def cycle_pass(base, mode, collector, failures, budget):
    base.mkdir(parents=True, exist_ok=True)
    with (base / (mode + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return json.dumps({"mode": mode, "events": [f"{mode} running"]})
        state_path = base / (mode + ".json")
        state, reset_event = load_state(state_path, mode)
        events = []
        hog_hits = None
        reduced = False
        try:
            with runtime.deadline(budget, mode, soft=True):
                raw = collector(state["before"])
                after, reduced_events = reduce(mode, state["before"], raw)
            reduced = True
            if not state.get("baseline_pending") or mode == "answers":
                events += reduced_events
            else:
                events += [
                    line
                    for line in reduced_events
                    if line.startswith(
                        ("WORKER DONE ", "WORKER QUESTION ", "TODO ANSWER ")
                    )
                ]
            if mode == "hog":
                hog_hits = after
            if mode in PRESSURE_SETTINGS and not any(
                raw.get(key) is not None for key in PRESSURE_SETTINGS[mode]["labels"]
            ):
                events += failed_read(state, mode, failures, "MissingMetric")
                state["before"] = after
            else:
                if state["fails"] >= failures:
                    events.append(f"{mode} READ RECOVERED")
                state.update(before=after, fails=0)
                state.pop("baseline_pending", None)
        except (
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            MissingMetric,
            AttributeError,
            IndexError,
        ) as error:
            events += failed_read(state, mode, failures, type(error).__name__)
        line = publish_events(base, mode, state, events)
        if mode == "answers" and reduced:
            notices = acknowledge_answers(raw)
            if notices:
                line = publish_events(base, mode, state, notices)
        if mode == "hog":
            log_events = log_hog_hits(base, state, hog_hits)
            if log_events:
                line = json.dumps(
                    {"mode": mode, "events": events + log_events}, sort_keys=True
                )
        (base / (mode + ".heartbeat")).touch()
        if reset_event:
            return json.dumps(
                {
                    "mode": mode,
                    "events": [reset_event]
                    + (json.loads(line)["events"] if line else []),
                },
                sort_keys=True,
            )
        return line


def workspace_list():
    """`herdr workspace list`, for the owner tags of worker workspaces; None, with a stderr note, when it cannot be
    read, so that architect alerts still go out."""
    try:
        listing = json.loads(command(herdr_bin(), "workspace", "list"))
        if not isinstance(listing["result"]["workspaces"], list):
            raise ValueError("workspaces not a list")
        return listing
    except discovery.READ_ERRORS as error:
        print(
            f"context: herdr workspace list unreadable ({type(error).__name__}); worker recycle alerts wait",
            file=sys.stderr,
            flush=True,
        )
        return None


def session_usage(sid):
    path = Path.home() / ".claude/context-usage" / sid
    try:
        try:
            value = {"tokens": int(Path(str(path) + ".tokens").read_text().strip())}
        except FileNotFoundError:
            value = {"remaining": float(path.read_text().strip())}
        for field in ("window", "model"):
            try:
                text = Path(str(path) + "." + field).read_text().strip()
                value[field] = int(text) if field == "window" else text
            except (OSError, ValueError):
                pass
        return value
    except Exception:
        return {}


def closed_panes(base, present, before):
    """Pane ids a recycle closed on purpose. A marker is consumed once neither this scan nor the saved state lists its pane."""
    directory = base / "closed"
    try:
        names = sorted(path.name for path in directory.iterdir())
    except FileNotFoundError:
        return []
    saved = {
        row.get("pane_id") for row in ((before or {}).get("present") or {}).values()
    }
    for name in names:
        if name not in present and name not in saved:
            (directory / name).unlink(missing_ok=True)
    return names


BACKGROUND_JOB = re.compile(
    r"^\s*[1-9]\d* (?:monitors?|shells?) still running(?:\s.*)?$"
    r"|[·,] [1-9]\d* (?:monitors?|shells?)(?=\s|,|$)"
    r"|^\s*(?:[•*] Working[^\n]* · )?[1-9]\d* background terminals? running · /ps to view · /stop to close\s*$",
    re.MULTILINE,
)


def collect_waiting_jobs(raw, before):
    """Read only panes with a due alert. Failed reads allow delivery and log once until a successful read."""
    previous = before or {}
    old = previous.get("waiting", {})
    # Reuse the pure reducer to find due sends without duplicating identity and eligibility rules.
    due, _ = reduce_waiting(previous, raw)
    live = {row["pane_id"] for row in agents(raw)}
    failed = set(previous.get("waiting_read_failures", [])) & live
    busy = []
    remaining = signal.getitimer(signal.ITIMER_REAL)[0]
    budget = min(10, max(0, remaining - 5)) if remaining else 10
    read_until = time.monotonic() + budget
    for key, entry in due.items():
        if entry["stage"] <= old.get(key, {}).get("stage", 0):
            continue
        timeout = min(5, read_until - time.monotonic())
        if timeout <= 0:
            break
        pane = entry["row"]["pane_id"]
        try:
            text = command(
                herdr_bin(),
                "pane",
                "read",
                pane,
                "--source",
                "recent-unwrapped",
                "--lines",
                "16",
                timeout=timeout,
            )
        except discovery.READ_ERRORS as error:
            if pane not in failed:
                print(
                    f"workers: pane {pane} background-job read failed ({type(error).__name__}); treating as not busy",
                    file=sys.stderr,
                    flush=True,
                )
            failed.add(pane)
            continue
        failed.discard(pane)
        if BACKGROUND_JOB.search(text):
            busy.append(pane)
    raw.update(waiting_busy=busy, waiting_read_failures=sorted(failed))


OPEN_PR_CHECKS_MAX = 3
IDLE_CHECK_SECONDS = 30
GH_TIMEOUT = 10
CALLS_PER_CHECK = (
    6  # branch, operation markers, two remote URLs, one gh list per remote
)
REMOTES = ("origin", "upstream")
STOPPED_OPERATIONS = (
    "rebase-merge",
    "rebase-apply",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
)
NO_CHECKOUT = re.compile(
    r"not a git repository|No such file or directory", re.IGNORECASE
)


@contextlib.contextmanager
def short_commands():
    """Every git and gh call inside gets GH_TIMEOUT seconds, not the 45 s default, so the pass stays in its budget."""
    saved = runtime.COMMAND_TIMEOUT
    runtime.COMMAND_TIMEOUT = GH_TIMEOUT
    try:
        yield
    finally:
        runtime.COMMAND_TIMEOUT = saved


def stopped_operation(cwd):
    """Whether the checkout is stopped in a rebase, merge, cherry-pick, revert or bisect. HEAD can be attached (merge,
    cherry-pick, revert) or detached (rebase, bisect), and the branch is not gone either way."""
    paths = command(
        "git",
        "-C",
        cwd,
        "rev-parse",
        *(arg for name in STOPPED_OPERATIONS for arg in ("--git-path", name)),
    )
    return any((Path(cwd) / line).exists() for line in paths.splitlines())


def github_remotes(cwd):
    """owner/name of the checkout's GitHub `origin` and `upstream`, each once, `origin` first; empty without a
    GitHub remote."""
    found = []
    for name in REMOTES:
        try:
            url = command("git", "-C", cwd, "remote", "get-url", name).strip()
        except subprocess.CalledProcessError as error:
            if discovery.NO_REMOTE.search(str(error.stderr or "")):
                continue
            raise
        match = GITHUB_REMOTE.search(url)
        if match and match[1].lower() not in [repo.lower() for repo in found]:
            found.append(match[1])
    return found


def open_pr(row):
    """Whether the worker's branch has an open PR on any GitHub remote of its checkout (a PR from a fork is on
    `upstream`) whose head is in the repository of the checkout's `origin`. A plain detached HEAD, a checkout that is
    not a git repository and a checkout without a GitHub remote cannot have one: False. None when a git or gh call
    fails, when the checkout is stopped in a rebase, merge, cherry-pick, revert or bisect (also with an attached
    HEAD), or when a PR's head owner cannot be read. The pass deadline is not a failed call: it passes up."""
    cwd = row.get("cwd")
    if not cwd:
        return None
    try:
        with short_commands():
            try:
                branch = command(
                    "git", "-C", cwd, "symbolic-ref", "--quiet", "--short", "HEAD"
                ).strip()
            except subprocess.CalledProcessError as error:
                if error.returncode != 1:
                    if NO_CHECKOUT.search(str(error.stderr or "")):
                        return False
                    raise
                branch = ""
            if stopped_operation(cwd):
                return None
            if not branch:
                return False
            repos = github_remotes(cwd)
            head_owner = repos[0].split("/")[0].lower() if repos else ""
            unknown = False
            for repo in repos:
                try:
                    for pr in json.loads(
                        command(
                            "gh",
                            "pr",
                            "list",
                            "--repo",
                            repo,
                            "--head",
                            branch,
                            "--state",
                            "open",
                            "--json",
                            "number,headRepositoryOwner",
                        )
                    ):
                        owner = (
                            (pr.get("headRepositoryOwner") or {}).get("login") or ""
                        ).lower()
                        if owner == head_owner:
                            return True
                        unknown = unknown or not owner
                except TimeoutError:
                    raise
                except (*discovery.READ_ERRORS, subprocess.CalledProcessError):
                    unknown = True
            return None if unknown else False
    except TimeoutError:
        raise
    except (*discovery.READ_ERRORS, subprocess.CalledProcessError):
        return None


def collect_idle(raw, before, started):
    """For the workers that pass the idle line in this pass, their open-PR checks: at most OPEN_PR_CHECKS_MAX, and none
    started IDLE_CHECK_SECONDS after the pass began (`started`, a monotonic time taken before the first Herdr call);
    the rest wait for a later pass. Needs the workspace list `collect` read: without it nothing runs."""
    if raw.get("workspaces") is None:
        return
    rows = [
        row
        for row in agents(raw)
        if not architect_like(row)
        and (
            row.get("agent_status") in IDLE_STATUSES
            or row.get("agent_status") not in WORKER_STATUSES
        )
    ]
    if not rows:
        return
    due = idle_due(
        (before or {}).get("idle", {}), rows, workspace_owners(raw), raw["observed_at"]
    )
    checks = {}
    for row in due[:OPEN_PR_CHECKS_MAX]:
        if _monotonic() - started >= IDLE_CHECK_SECONDS:
            break
        checks[row["pane_id"]] = open_pr(row)
    if checks:
        raw["pr_open"] = checks


def collect(mode, before, scope="", base=None):
    if mode == "audit":
        return audit.collect(command, before=before)
    if mode == "pulls":
        return collect_pulls(command, before)
    if mode == "todo":
        return collect_todo(before)
    if mode == "answers":
        return collect_answers(before)
    if mode in ("context", "workers"):
        if os.environ.get("HERDR_ENV") != "1":
            raise RuntimeError("not inside Herdr")
        started = _monotonic()
        raw = json.loads(command(herdr_bin(), "agent", "list"))
        agents(raw)
        if mode == "workers":
            raw["observed_at"] = time.time()
            try:
                raw["workspaces"] = json.loads(
                    command(herdr_bin(), "workspace", "list")
                )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
                subprocess.SubprocessError,
                MissingMetric,
            ):
                raw["workspaces"] = None
            owners = waiting_owners(raw)
            if owners is not None and worker_signals.directory().exists():
                signals, events = worker_signals.collect_live(
                    command, raw["observed_at"]
                )
                raw["worker_signals"] = signals
                raw["signal_events"] = events
                raw["observed_at"] = time.time()
            collect_waiting_jobs(raw, before)
            if base is not None:
                raw["closed"] = closed_panes(
                    base, {row["pane_id"] for row in agents(raw)}, before
                )
            collect_idle(raw, before, started)
            return raw
        usage = {}
        for agent in agents(raw):
            sid = (agent.get("agent_session") or {}).get("value")
            if not sid:
                continue
            value = session_usage(sid)
            if value:
                usage[sid] = value
        return {"herdr": raw, "usage": usage, "workspaces": workspace_list()}
    if mode == "hog":
        return process_rows(command)
    if mode == "host":
        return host_metrics(command)
    if mode == "balance":
        return balance_metrics(command, balance_limits(), before=before)
    if mode == "prs":
        spec = importlib.util.spec_from_file_location("prs_copy", ROOT / "prs-copy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.gh = lambda *args: command("gh", *args)
        module.QUERY = (
            module.QUERY.replace("url number title", "id state url number title")
            .replace(
                "reviewThreads(first: 100) { nodes { isResolved } }",
                "reviewThreads(first: 100) { pageInfo { hasNextPage endCursor } nodes { id isResolved } }",
            )
            .replace(
                "latestReviews(first: 20) { nodes { author",
                "latestReviews(first: 100) { nodes { id author",
            )
        )
        rows = module.fetch(scope)
        known = {pr["url"] for pr in rows}
        selection = module.QUERY.split("... on PullRequest {", 1)[1].rsplit("} }", 1)[0]
        for url, old in (before or {}).items():
            if url not in known and old["state"] == "OPEN":
                query = (
                    "query($id:ID!){node(id:$id){... on PullRequest {"
                    + selection
                    + "}}}"
                )
                response = json.loads(
                    command(
                        "gh",
                        "api",
                        "graphql",
                        "-f",
                        "query=" + query,
                        "-f",
                        "id=" + old["id"],
                    )
                )
                if response.get("errors") or not response.get("data", {}).get("node"):
                    raise ValueError("tracked PR read failed")
                rows.append(response["data"]["node"])
            elif url not in known:
                # Terminal records are retained; no repeated reads needed.
                rows.append(
                    {
                        "id": old["id"],
                        "url": url,
                        "title": old["title"],
                        "headRefOid": old["sha"],
                        "state": old["state"],
                        "reviewDecision": old["decision"],
                        "reviewThreads": {
                            "nodes": [
                                {"id": t, "isResolved": False} for t in old["threads"]
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "latestReviews": {
                            "nodes": [
                                {"id": rid, "state": "CHANGES_REQUESTED"}
                                for rid in old.get("requests", [])
                            ]
                        },
                        "commits": {
                            "nodes": [
                                {"commit": {"statusCheckRollup": {"state": old["ci"]}}}
                            ]
                        },
                    }
                )
        for pr in rows:
            connection = pr["reviewThreads"]
            while connection["pageInfo"]["hasNextPage"]:
                query = "query($id:ID!,$cursor:String!){node(id:$id){... on PullRequest {reviewThreads(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id isResolved}}}}}"
                response = json.loads(
                    command(
                        "gh",
                        "api",
                        "graphql",
                        "-f",
                        "query=" + query,
                        "-f",
                        "id=" + pr["id"],
                        "-f",
                        "cursor=" + connection["pageInfo"]["endCursor"],
                    )
                )
                if response.get("errors"):
                    raise ValueError("thread page failed")
                page = response["data"]["node"]["reviewThreads"]
                connection["nodes"] += page["nodes"]
                connection["pageInfo"] = page["pageInfo"]
        return rows
    raise ValueError(mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=MODES + ("all",))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument(
        "--preview-answers",
        action="store_true",
        help="inspect answers without consuming or publishing them",
    )
    parser.add_argument("--scope", default=os.environ.get("ORCH_WATCHERS_PR_SCOPE", ""))
    parser.add_argument("--interval", type=int)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.mode == "all" and args.loop:
        parser.error("--loop all is not supported; run one loop per mode")
    if args.mode in ("prs", "all") and not args.fixture and not args.scope:
        parser.error("--scope or ORCH_WATCHERS_PR_SCOPE is required for PR watchers")
    if args.interval is None:
        args.interval = (
            WATCH_TIMING[args.mode]["interval"] if args.mode != "all" else 60
        )
    if args.interval < 5:
        parser.error("interval must be >= 5")
    for _ in runtime.iterations(args.interval, args.loop, args.mode, budget=None):
        for mode in MODES if args.mode == "all" else (args.mode,):

            def collector(before):
                if args.fixture:
                    return json.loads(args.fixture.read_text())
                return collect(mode, before, args.scope, args.state_dir)

            if mode == "answers" and args.preview_answers:
                with runtime.deadline(
                    2 * WATCH_TIMING["answers"]["soft_budget"], "answers preview"
                ):
                    _, events = reduce_answers(
                        None, collect_answers(None, consume=False)
                    )
                line = json.dumps({"mode": mode, "events": events}) if events else None
            else:
                line = cycle(args.state_dir, mode, collector)
            if line:
                with runtime.deadline(runtime.ITERATION_TIMEOUT, f"{mode} output"):
                    print(line, flush=True)


if __name__ == "__main__":
    main()
