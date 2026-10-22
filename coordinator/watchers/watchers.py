#!/usr/bin/env python3
"""Event reducers. Never sends messages or mutates source state."""
from question_lock import answers_lock, questions_directory
import alert_policy
import argparse
import audit
import discovery
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import uuid
import os
import re
import subprocess
import time
import watcher_runtime as runtime
from pathlib import Path
from watcher_platform import MissingMetric, herdr_bin, host_metrics, process_rows, balance_metrics

ROOT = Path(__file__).resolve().parent
MODES = ("context", "workers", "hog", "balance", "host", "prs", "audit", "pulls", "todo", "answers")

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
GITHUB_REMOTE = discovery.GITHUB_REMOTE

BALANCE_LIMITS = {"free": 30, "slots": 4, "heavy": 4, "load5": 20, "cpu": 80}


def balance_limits():
    limits = dict(BALANCE_LIMITS)
    limits["slots"] = int(os.environ.get("ORCH_HEAVY_SLOTS_MAX", limits["slots"]))
    return limits


def command(*argv):
    result = runtime.run(argv)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, argv, output=result.stdout, stderr=result.stderr)
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
        "host": {"free": (configured("ORCH_HOST_PRESSURE_MIN_PCT", 20), False),
                 "swap": (configured("ORCH_HOST_SWAP_MAX_MB", 1024), True),
                 "procs": (configured("ORCH_HOST_PROCS_MAX", 1300), True),
                 "files": (configured("ORCH_HOST_FILES_MAX", 150000), True),
                 "load": (configured("ORCH_HOST_LOAD_MAX", 40), True)},
        "balance": {key: (limit, key != "free") for key, limit in balance_limits().items() if key != "cpu"},
    }[mode]


def thresholds(mode, values):
    return {key: f"{key}={values[key]}" for key, (limit, above) in pressure_limits(mode).items()
            if key in values
            and (float(values[key]) > limit if above else float(values[key]) < limit)}


def agents(raw):
    rows = raw["result"]["agents"]
    if not isinstance(rows, list):
        raise ValueError("agents not a list")
    return rows


def identity(agent):
    return (agent.get("agent_session") or {}).get("value") or agent["pane_id"]


def conditions(mode, raw):
    if mode in PRESSURE_SETTINGS:
        return thresholds(mode, {key: value for key, value in raw.items() if value is not None})
    if mode == "context":
        out = {}
        for agent in agents(raw["herdr"]):
            label = agent.get("name") or agent.get("terminal_title_stripped") or ""
            role = (agent.get("tokens") or {}).get("role")
            if agent.get("agent") != "claude" or not (
                label.startswith("Architect::") or role in ("architect", "coordinator")
                or "Architect::" in (agent.get("terminal_title_stripped") or "")
            ):
                continue
            sid = (agent.get("agent_session") or {}).get("value")
            value = raw["usage"].get(sid, {})
            if not sid or not value or (value.get("tokens") is None and value.get("remaining") is None):
                out[f"nosensor:{sid or agent['pane_id']}"] = f"NOSENSOR {agent['pane_id']} {label}"
                continue
            if value.get("tokens") is not None:
                hit = int(value["tokens"]) >= int(os.environ.get("ORCH_RECYCLE_TOKENS", 300000))
            else:
                hit = 100 - float(value["remaining"]) >= float(os.environ.get("ORCH_RECYCLE_PCT", 55))
            if hit:
                amount = (f"{int(value['tokens']) // 1000}k" if value.get("tokens") is not None
                          else f"{100 - float(value['remaining']):.0f}%")
                out[sid] = f"RECYCLE {agent['pane_id']} {label} {amount}"
        return out
    if mode == "hog":
        out = {}
        for proc in raw:
            rss, age = float(proc["rss"]), int(proc["age"])
            kind = "HOG" if rss >= float(os.environ.get("ORCH_HOG_GB", 6)) * 1048576 else None
            if Path(proc["comm"]).name == "git":
                kind = "GITBIG" if rss >= float(os.environ.get("ORCH_HOG_GIT_GB", 2)) * 1048576 else kind
                kind = kind or ("GITLONG" if age >= int(os.environ.get("ORCH_HOG_GIT_AGE_S", 180)) else None)
            if kind:
                out[f"{proc['pid']}:{proc['start']}:{kind}"] = hog_detail(kind, proc, raw)
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
        ci = ((pr["commits"]["nodes"] or [{}])[0].get("commit", {}).get("statusCheckRollup") or {}).get("state")
        if state == "OPEN" and ci not in ("SUCCESS", "PENDING", "EXPECTED", "FAILURE", "ERROR"):
            raise ValueError("unknown/empty head checks")
        out[pr["url"]] = {"id": pr["id"], "title": pr["title"], "sha": sha,
                          "state": state, "decision": pr["reviewDecision"], "ci": ci,
                          "threads": sorted(t["id"] for t in threads["nodes"] if not t["isResolved"]),
                          "requests": sorted(review["id"] for review in pr.get("latestReviews", {}).get("nodes", [])
                                             if review.get("state") == "CHANGES_REQUESTED")}
    return out


HOST_FLAGS = {"free": "PRESSURE", "swap": "SWAP", "procs": "PROCS", "files": "FILES", "load": "LOAD"}


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


BALANCE_FLAGS = {"load5": "LOAD5", "slots": "SLOTS", "heavy": "HEAVYWT", "free": "MEMFREE"}


def balance_detail(key, value):
    if value is None:
        return "unavailable"
    if key == "load5":
        return f"{value:.2f}"
    if key == "free":
        return f"{value:g}%"
    return str(value)


def balance_top(raw):
    return " top: " + " ".join(f"{row['name']}({row['cpu']:.0f}%,{row['mb']:.0f}MB)"
                                for row in raw.get("top", [])[:4])


PRESSURE_SETTINGS = {
    "host": dict(labels=HOST_FLAGS, steps={"swap": 1000, "files": 10000}, counts=(),
                 detail=host_detail, suffix=None, defer_recovery=False, state_fields=()),
    "balance": dict(labels=BALANCE_FLAGS, steps={"load5": 10}, counts=("slots",),
                    detail=balance_detail, suffix=balance_top, defer_recovery=True, state_fields=("cpu_sample",)),
}


def ratio_step(key, raw, limits, detail):
    return alert_policy.step(alert_policy.number(detail(key, float(raw[key]))), limits[key][0], not limits[key][1])


def pressure_keys(mode, flags, raw, after, settings):
    steps, limits = settings["steps"], pressure_limits(mode)
    keys = {key: raw[key] if key in settings["counts"]
            else f"{int(float(raw[key]) // steps[key])}/{ratio_step(key, raw, limits, settings['detail'])}" if key in steps
            else ratio_step(key, raw, limits, settings["detail"]) for key in after}
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
    keys = pressure_keys(mode, flags, raw, after, settings)
    unreadable = [key for key in labels if raw.get(key) is None] + raw.get("unreadable", [])
    pending = previous.get("pending_recovery", False) or bool(flags) or bool(keys)
    events = readfail_changes(prefix, previous.get("unreadable", []), unreadable)
    if keys != flags and keys:
        line = prefix + " " + " ".join(f"{label}({settings['detail'](key, raw.get(key))})"
                                        for key, label in labels.items() if key in keys)
        if settings["suffix"]:
            line += settings["suffix"](raw)
        events.append(line)
    if not keys and pending and (not settings["defer_recovery"] or not unreadable):
        events.append(prefix + " RECOVERED")
        pending = False
    state = dict(flags=keys, unreadable=unreadable, pending_recovery=pending)
    state.update({key: raw.get(key) for key in settings["state_fields"]})
    return state, events


def context_step(line):
    found = alert_policy.context_facts(line) if isinstance(line, str) else None
    return alert_policy.step(found[0]["value"], found[0]["limit"], False) if found else 0


def recycle_cleared(line):
    return re.sub(r"( \d+(?:k|%))?$", " cleared", line, count=1)


def nosensor_cleared(line):
    return "NOSENSOR CLEARED " + line.split(" ")[1]


def subject_cleared(line):
    return nosensor_cleared(line) if line.startswith("NOSENSOR ") else recycle_cleared(line)


def reduce_context(before, after, raw):
    previous = before or {}
    moved = {key for key in after if key in previous and previous[key].startswith(("RECYCLE ", "NOSENSOR "))
             and subject_cleared(previous[key]) != subject_cleared(after[key])}
    events = [subject_cleared(previous[key]) for key in sorted(moved)]
    events += [after[key] for key in sorted(after)
               if key not in previous or key in moved or context_step(after[key]) > context_step(previous[key])]
    present = {(a.get("agent_session") or {}).get("value") for a in agents(raw["herdr"])}
    remembered = {key: value for key, value in previous.items() if not key.startswith("nosensor:") and key in present}
    events += [nosensor_cleared(value) for key, value in sorted(previous.items())
               if key.startswith("nosensor:") and key not in after]
    events += [recycle_cleared(value) for key, value in sorted(previous.items())
               if key not in remembered and key not in after and value.startswith("RECYCLE ")]
    remembered.update(after)
    return remembered, events


WORKER_STATUSES = ("working", "idle", "done", "blocked", "error", "waiting")
ACTIVE_WORKER_STATUSES = ("working", "waiting", "blocked", "error")


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
    tail = (f" pane={row['pane_id']} owner={entry['owner']} "
            f"{int((now - entry['since']) // 60)}m session={key}" + (f" {suffix}" if suffix else "")
            + alert_policy.WORKER_WAIT_NOTE)
    budget = max(1, alert_policy.LINE_LIMIT - len("WORKER WAITING ") - len(tail))
    name = " ".join((row.get("name") or row.get("terminal_title_stripped") or "worker").split())[:min(24, budget)]
    return f"WORKER WAITING {name}" + tail


def reduce_waiting(previous, raw):
    """Only real session IDs own timers. Failed ownership/status reads freeze existing state."""
    old = previous.get("waiting", {})
    now = raw.get("observed_at", time.time())
    rows = {}
    for row in agents(raw):
        sid = (row.get("agent_session") or {}).get("value")
        if sid:
            rows[sid] = row
    gaps = {}
    old_panes = {entry["row"]["pane_id"]: key for key, entry in old.items()}
    for row in agents(raw):
        key = old_panes.get(row["pane_id"])
        if not (row.get("agent_session") or {}).get("value") and key and key not in rows:
            rows[key] = row
            gaps[key] = True
    spaces = raw.get("workspaces")
    owners = None if spaces is None else {row["workspace_id"]: (row.get("tokens") or {}).get("owner")
                                         for row in spaces}
    current, events = {}, []
    for key, row in rows.items():
        entry = old.get(key)
        role = (row.get("tokens") or {}).get("role")
        if role in ("architect", "coordinator"):
            continue
        if row.get("agent_status") == "working" and key not in gaps:
            continue
        owner = owners.get(row.get("workspace_id")) if owners is not None else None
        if owners is not None and not owner:
            continue
        if row.get("agent_status") not in WORKER_STATUSES or owners is None or key in gaps:
            if entry and owners is not None and owner != entry["owner"]:
                continue
            if entry:
                current[key] = dict(entry, row=dict(entry["row"], pane_id=row["pane_id"]))
            continue
        if row.get("agent_status") not in ("done", "idle"):
            continue
        if entry and entry["owner"] != owner:
            if entry["stage"]:
                events.append(waiting_line(key, entry, now, "cleared"))
            entry = None
        entry = dict(entry or dict(since=now, stage=0, owner=owner), row=dict(row))
        stage = entry["stage"]
        delay = float(os.environ.get("ORCH_WAIT_MIN", 600)) if stage == 0 else float(os.environ.get("ORCH_WAIT_REPEAT_MIN", 1800))
        since = entry["since"] if stage < 2 else entry["sent"]
        if stage < 3 and (now - since > delay if stage == 0 else now - since >= delay):
            events.append(waiting_line(key, entry, now, ("", "REPEAT", "ESCALATE")[stage]))
            entry.update(stage=stage + 1, sent=now)
        current[key] = entry
    for key, entry in old.items():
        if key not in current and entry["stage"]:
            events.append(waiting_line(key, entry, now, "cleared"))
    return current, events


def reduce_workers(before, raw):
    previous = before or {}
    old_bad = worker_alerts(previous)
    old_present = dict(previous.get("present", {}))
    present = worker_snapshot(raw, old_present, old_bad)
    now, closed = raw.get("observed_at"), set(raw.get("closed", []))
    panes = {row["pane_id"] for row in present.values()}
    seen = {pane: since for pane, since in previous.get("seen", {}).items() if pane in panes}
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
                present[key] = dict(row, agent_status=old_present[key].get("agent_status"))
        elif status in ("blocked", "error"):
            bad[key] = dict(pane=row["pane_id"], state=status)
    for key, row in old_present.items():
        if key not in present and row.get("agent_status") in ACTIVE_WORKER_STATUSES:
            bad[key] = dict(pane=row["pane_id"], state="closed" if row["pane_id"] in closed else "dead/disappeared")
    unreadable.sort()
    events = readfail_changes("WORKER", previous.get("unreadable", []), unreadable)
    for key in sorted(set(old_bad) | set(bad)):
        old, new = old_bad.get(key, {}), bad.get(key, {})
        pane = new.get("pane") or (present[key]["pane_id"] if key in present else None)
        if old and pane and pane != old["pane"]:
            events.append(f"WORKER {old['pane']} MOVED {pane}")
        if old.get("state") == new.get("state"):
            continue
        if old and key in present:
            events.append(worker_line(dict(old, pane=present[key]["pane_id"]), recovered=True))
        if new:
            events.append(worker_line(new))
    waiting, waiting_events = reduce_waiting(previous, raw)
    events += waiting_events
    return dict(bad={key: value for key, value in bad.items() if key in present},
                present=present, unreadable=unreadable, seen=seen, waiting=waiting), events


def pull_owners(command, prs, failed=None, work=None):
    """Owner tag of the open workspace whose checkout has each PR's head branch, by <repo>#<n>.
    work is the open work already read (discovery.open_work), or the Unavailable it raised.
    Each root is identified by its `origin` remote; only roots of watched repositories have their worktrees listed.
    A root that fails is skipped and listed in `failed` by its `owner/name`, or as `*/<name>` when its remote could not
    be read."""
    work = discovery.open_work(command) if work is None else work
    if isinstance(work, discovery.Unavailable):
        raise work
    rows = work["workspaces"]
    by_path = {row["checkout"]: row for row in rows if row.get("checkout") and row.get("owner")}
    watched = {pr["repository"]["nameWithOwner"].lower() for pr in prs}
    branches = {}
    for root in sorted({row["repo_root"] for row in rows if row.get("repo_root")}):
        identity = "*/" + Path(root).name.lower()
        try:
            remote = GITHUB_REMOTE.search(command("git", "-C", root, "remote", "get-url", "origin").strip())
            if not remote or remote.group(1).lower() not in watched:
                continue
            identity = remote.group(1).lower()
            listing = command("git", "-C", root, "worktree", "list", "--porcelain")
        except audit.READ_ERRORS as error:
            if isinstance(error, subprocess.CalledProcessError) and audit.orch_settings.NO_ORIGIN.search(str(error.stderr or "")):
                continue
            if failed is not None:
                failed.append(identity)
            continue
        path = None
        for line in listing.splitlines():
            if line.startswith("worktree "):
                path = line[9:]
            elif line.startswith("branch refs/heads/") and path in by_path:
                branches[(remote.group(1).lower(), line[18:])] = path
    owners = {}
    for pr in prs:
        repo = pr["repository"]["nameWithOwner"]
        owner = (by_path.get(branches.get((repo.lower(), pr.get("headRefName")))) or {}).get("owner")
        if owner:
            owners[f"{repo}#{pr['number']}"] = owner
    return owners


def collect_pulls(command, before, clock=time.monotonic):
    """Departed-PR reads run right after the search with their own reserved window, before the owner lookup."""
    known = (before or {}).get("prs", {})
    page_nodes = audit.positive_setting("ORCH_AUDIT_PAGE_NODES", 100, 100)
    try:
        work = discovery.open_work(command)
    except discovery.Unavailable as error:
        work = error
    found = discovery.repos(command, work)
    prs = audit.fetch_prs(command, page_nodes, found["repos"])
    searched = {f"{pr['repository']['nameWithOwner']}#{pr['number']}" for pr in prs}
    departed, failed, attempted = {}, [], set()
    pending = sorted(set(known) - searched, key=lambda key: (known[key].get("misses", 0), key))
    deadline = clock() + PULLS_DEPARTED_SECONDS
    for key in pending[:PULLS_DEPARTED_MAX]:
        if clock() >= deadline:
            break
        attempted.add(key)
        repo, number = key.rsplit("#", 1)
        try:
            row = json.loads(command("gh", "api", f"repos/{repo}/pulls/{number}"))
            departed[key] = "MERGED" if row.get("merged") else row["state"].upper()
        except audit.READ_ERRORS:
            failed.append(key)
    waiting = [key for key in pending if key not in attempted and known[key].get("misses", 0)]
    unreadable, roots, lookup_failed = [], [], False
    try:
        owners = pull_owners(command, prs, roots, work)
    except (*audit.READ_ERRORS, discovery.Unavailable):
        owners, unreadable, lookup_failed = {}, ["owners"], True
    if roots:
        unreadable = ["owners"]
    unresolved = set(roots)
    current, settings, settings_failed = {}, {"clones": found["clones"]}, []
    for pr in prs:
        key = f"{pr['repository']['nameWithOwner']}#{pr['number']}"

        def repo_settings(pr=pr, key=key):
            try:
                return audit.repo_settings(command, pr["repository"]["nameWithOwner"], pr["baseRefName"], settings)
            except audit.READ_ERRORS:
                settings_failed.append(f"settings:{key}")
                raise
        lines = {line.split(" ", 1)[0]: line for line in audit.pr_lines(pr, page_nodes, repo_settings)
                 if line.split(" ", 1)[0] in alert_policy.PULL_KINDS}
        repo = pr["repository"]["nameWithOwner"].lower()
        kept = lookup_failed or repo in unresolved or "*/" + repo.split("/")[-1] in unresolved
        owner = owners.get(key) or (known.get(key, {}).get("owner") if kept else None)
        current[key] = dict(title=pr["title"], owner=owner, lines=lines)
    return dict(prs=current, departed=departed, failed=failed,
                unreadable=unreadable + [f"pr:{key}" for key in sorted(failed + waiting)] + sorted(set(settings_failed)))


def pull_line(line, key, owner):
    """`<kind> <repo>#<n> owner=<workspace or ?> <rest>`: the owner sits before any PR text."""
    kind, _, rest = line.partition(" ")
    return f"{kind} {key} owner={owner or '?'}" + rest[len(key):]


def reduce_pulls(before, raw):
    current = raw["prs"]
    if before is None:
        active = sum(len(pr["lines"]) for pr in current.values())
        return dict(prs=current, unreadable=[]), [f"PULLS BASELINE [{active} active lines]"]
    events = readfail_changes("PULLS", before.get("unreadable", []), raw["unreadable"])
    after = dict(current)
    for key, pr in sorted(current.items()):
        old = before["prs"].get(key, {}).get("lines", {})
        rerouted = pr["owner"] and pr["owner"] != before["prs"].get(key, {}).get("owner")
        events += [pull_line(line, key, pr["owner"]) for kind, line in sorted(pr["lines"].items())
                   if kind not in old or rerouted]
        events += [f"{kind} {key} cleared" for kind in sorted(set(old) - set(pr["lines"]))]
    for key, old in sorted(before["prs"].items()):
        if key in current:
            continue
        state = raw["departed"].get(key)
        if state is None:
            after[key] = dict(old, misses=old.get("misses", 0) + 1) if key in raw.get("failed", []) else old
            continue
        events += [f"{kind} {key} cleared" for kind in sorted(old["lines"])]
        if state in ("MERGED", "CLOSED"):
            events.append(pull_line(f"{state} {key} {old['title']}", key, old.get("owner")))
    return dict(prs=after, unreadable=raw["unreadable"]), events


_TODO_MODULE = None


def todo_module():
    global _TODO_MODULE
    if _TODO_MODULE is None:
        spec = importlib.util.spec_from_file_location("user_todo", ROOT.parent / "user-todo.py")
        _TODO_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_TODO_MODULE)
    return _TODO_MODULE


def collect_todo(before):
    module = todo_module()
    directory = Path(os.environ.get("ORCH_USER_TODO_STATUS_DIR") or Path.home() / ".claude/coordinator/status")
    coordinator = Path.home() / ".claude/coordinator"
    output = Path(os.environ.get("ORCH_USER_TODO_OUTPUT") or coordinator / "user-todo.md")
    state = Path(os.environ.get("ORCH_USER_TODO_STATE") or coordinator / "user-todo.state.json")
    return module.merge(directory, output, state)


def reduce_todo(before, raw):
    current = {item["id"]: item for item in raw["now"]}
    directory_down = any(Path(path).suffix != ".md" for path in raw["unreadable"])
    if directory_down and before:
        raw = dict(raw, unreadable=sorted(set(raw["unreadable"]) | set(before.get("unreadable", []))))
    unreadable_names = {Path(path).name for path in raw["unreadable"]}
    kept = set((before or {}).get("read", []))
    scanned = bool(raw.get("scanned")) or bool((before or {}).get("scanned"))
    read = sorted(set(raw.get("read", [])) | (kept if directory_down else kept & unreadable_names))
    if before is None:
        return {"now": current, "unreadable": raw["unreadable"], "read": read, "scanned": scanned}, [
            f"TODO READFAIL {path}" for path in sorted(raw["unreadable"])]
    events = []
    for path in sorted(set(raw["unreadable"]) - set(before.get("unreadable", []))):
        events.append(f"TODO READFAIL {path}")
    for path in sorted(set(before.get("unreadable", [])) - set(raw["unreadable"])):
        events.append(f"TODO READ RECOVERED {path}")
    recovered = set(before.get("unreadable", [])) - set(raw["unreadable"])
    known_sources = {item["source"] for item in before.get("now", {}).values()} | set(before.get("read", []))
    baseline_sources = {Path(path).name for path in recovered if Path(path).name not in known_sources}
    new = [current[key] for key in sorted(set(current) - set(before.get("now", {})))
           if current[key]["source"] not in baseline_sources]
    if any(Path(path).suffix != ".md" for path in recovered) and not before.get("now") and not before.get("read") and not before.get("scanned"):
        new = []
    if new:
        events.append("TODO " + str(len(new)) + " new: " + "; ".join(
            f"{item['source']}: {item['text']}" for item in new))
    return {"now": current, "unreadable": raw["unreadable"], "read": read, "scanned": scanned}, events


def answer_order(path):
    claim = path.name.split(".", 2)[1]
    if re.fullmatch(r"[0-9]{20}-[0-9a-f]{32}", claim):
        return int(claim.split("-", 1)[0]), path.name
    try:
        return path.stat().st_mtime_ns, path.name
    except OSError:
        return 0, path.name


def collect_answers(before, consume=True):
    directory = questions_directory()
    answers, unreadable, blocked = [], [], set()
    try:
        files = list(directory.iterdir())
        claimed = sorted((path for path in files if path.name.endswith(".answer.processing")), key=answer_order)
        last_order = max((answer_order(path)[0] for path in claimed), default=0)
        paths = claimed + sorted((path for path in files if path.name.endswith(".answer")), key=answer_order)
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
                processing = path.with_name(f"{slug}.{last_order:020d}-{uuid.uuid4().hex}.answer.processing")
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
    return {"answers": answers, "unreadable": unreadable}


def reduce_answers(before, raw):
    committed = set((before or {}).get("committed", []))
    try:
        pending = {path.name.split(".", 2)[1] for path in questions_directory().iterdir()
                   if path.name.endswith(".answer.processing")}
    except OSError:
        pending = committed.copy()
    pending.update(key for _, text, _, key in raw["answers"] if text)
    committed &= pending
    events = [f"ANSWER {slug}: {text}" for slug, text, _, key in raw["answers"] if text and key not in committed]
    events += [f"ANSWERS blank {slug}" for slug, text, _, _ in raw["answers"] if not text]
    events += [f"ANSWERS READFAIL {path}" for path in raw["unreadable"]]
    events += [f"ANSWERS READ RECOVERED {path}" for path in sorted(
        set((before or {}).get("unreadable", [])) - set(raw["unreadable"]))]
    committed.update(key for _, text, _, key in raw["answers"] if text)
    return {"unreadable": raw["unreadable"], "committed": sorted(committed)}, events


def acknowledge_answers(raw):
    for _, _, path, _ in raw.get("answers", []):
        if not path:
            continue
        processing = Path(path)
        processing.replace(processing.with_name(processing.name.removesuffix(".processing") + ".seen"))


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
            if (now["decision"] == "CHANGES_REQUESTED" and was.get("decision") != now["decision"]) or (set(now["requests"]) - set(was.get("requests", []))):
                events.append(f"{label}: CHANGES_REQUESTED")
            if now["ci"] in ("FAILURE", "ERROR") and (was.get("ci") not in ("FAILURE", "ERROR") or was.get("sha") != now["sha"]):
                events.append(f"{label}: red checks on {now['sha'][:9]}")
            if now["state"] in ("MERGED", "CLOSED") and now["state"] != was.get("state"):
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
    match = (re.match(r"(\S+) pid=(\d+) .*? comm=\[([^\]]*)\]", line)
             or re.match(r"(\S+) pid=(\d+) .*? cmd=\[(?:[^\] ]*/)?([^\] /]+)", line))
    return f"{match.group(1)} pid={match.group(2)} comm=[{match.group(3)}] cleared" if match else None


def reduce_hog(before, after):
    previous = {hog_key(key): line for key, line in (before or {}).items()}
    renamed = sorted(key for key in set(after) & set(previous) if hog_cleared(after[key]) != hog_cleared(previous[key]))
    events = [after[key] for key in sorted(set(after) - set(previous))]
    live = {hog_cleared(line) for line in after.values()}
    events += [line for key in renamed for line in (hog_cleared(previous[key]), after[key]) if line]
    events += [line for key in sorted(set(previous) - set(after))
               if (line := hog_cleared(previous[key])) and line not in live]
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
        parents.append(f"{pid} {clean_command(parent.get('command', parent['comm']), 90)}")
        pid = str(parent.get("ppid", "0"))
    cmd = clean_command(proc.get("command", proc["comm"]), 300)
    return (f"{kind} pid={proc['pid']} rss={float(proc['rss']) / 1048576:.1f}GB "
            f"age={proc['age']}s comm=[{clean_command(Path(proc['comm']).name, 60)}] cmd=[{cmd}] parents:" + "".join(" <- " + p for p in parents))


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
    line = json.dumps({"mode": mode, "events": events}, sort_keys=True) if events else None
    if line:
        state["events"].append({"seq": state["events"][-1]["seq"] + 1 if state["events"] else 1, "line": line,
                                "at": time.time()})
        if mode in CAPPED_HISTORY:
            state["events"] = state["events"][-HISTORY_BATCHES:]
    atomic(base / (mode + ".json"), json.dumps(state, sort_keys=True))
    atomic(base / (mode + ".alerts"), "".join(e["line"] + "\n" for e in state["events"]))
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
    events = ["hog LOG WRITE FAILED"] if failed and not was_failed else ["hog LOG WRITE RECOVERED"] if was_failed and not failed else []
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
    state = {"before": None, "fails": 0, "events": [], "reset": moved.name,
             "reset_line": reset_line, "baseline_pending": True}
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
            return isinstance(event, dict) and isinstance(event.get("seq"), int) and event["seq"] > 0 \
                and isinstance(event.get("line"), str) and isinstance(json.loads(event["line"]), dict)
        except (TypeError, ValueError, KeyError):
            return False
    if not isinstance(state["before"], (dict, type(None))) or not isinstance(state.get("fails"), int) \
            or state["fails"] < 0 or not isinstance(state.get("events"), list) \
            or any(not valid_event(event) for event in state["events"]):
        return reset_corrupt_state(state_path, "invalid state shape")
    if mode == "pulls" and state["before"] is not None and \
            (not isinstance(state["before"].get("prs"), dict) or not isinstance(state["before"].get("unreadable"), list)):
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
            if mode == "hog":
                hog_hits = after
            if mode in PRESSURE_SETTINGS and not any(raw.get(key) is not None for key in PRESSURE_SETTINGS[mode]["labels"]):
                events += failed_read(state, mode, failures, "MissingMetric")
                state["before"] = after
            else:
                if state["fails"] >= failures:
                    events.append(f"{mode} READ RECOVERED")
                state.update(before=after, fails=0)
                state.pop("baseline_pending", None)
        except (RuntimeError, ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired, MissingMetric, AttributeError, IndexError) as error:
            events += failed_read(state, mode, failures, type(error).__name__)
        line = publish_events(base, mode, state, events)
        if mode == "answers" and reduced:
            acknowledge_answers(raw)
        if mode == "hog":
            log_events = log_hog_hits(base, state, hog_hits)
            if log_events:
                line = json.dumps({"mode": mode, "events": events + log_events}, sort_keys=True)
        (base / (mode + ".heartbeat")).touch()
        if reset_event:
            return json.dumps({"mode": mode, "events": [reset_event] + (json.loads(line)["events"] if line else [])}, sort_keys=True)
        return line


def session_usage(sid):
    path = Path.home() / ".claude/context-usage" / sid
    try:
        try:
            return {"tokens": int(Path(str(path) + ".tokens").read_text().strip())}
        except FileNotFoundError:
            return {"remaining": float(path.read_text().strip())}
    except Exception:
        return {}


def closed_panes(base, present, before):
    """Pane ids a recycle closed on purpose. A marker is consumed once neither this scan nor the saved state lists its pane."""
    directory = base / "closed"
    try:
        names = sorted(path.name for path in directory.iterdir())
    except FileNotFoundError:
        return []
    saved = {row.get("pane_id") for row in ((before or {}).get("present") or {}).values()}
    for name in names:
        if name not in present and name not in saved:
            (directory / name).unlink(missing_ok=True)
    return names


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
        raw = json.loads(command(herdr_bin(), "agent", "list"))
        agents(raw)
        if mode == "workers":
            raw["observed_at"] = time.time()
            try:
                spaces = json.loads(command(herdr_bin(), "workspace", "list"))["result"]["workspaces"]
                if not isinstance(spaces, list) or not all(isinstance(row, dict) and "workspace_id" in row
                                                          and (row.get("tokens") is None or isinstance(row["tokens"], dict)) for row in spaces):
                    raise ValueError("invalid workspace list")
                raw["workspaces"] = spaces
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, MissingMetric):
                raw["workspaces"] = None
            if base is not None:
                raw["closed"] = closed_panes(base, {row["pane_id"] for row in agents(raw)}, before)
            return raw
        usage = {}
        for agent in agents(raw):
            sid = (agent.get("agent_session") or {}).get("value")
            if not sid:
                continue
            value = session_usage(sid)
            if value:
                usage[sid] = value
        return {"herdr": raw, "usage": usage}
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
        module.QUERY = module.QUERY.replace("url number title", "id state url number title").replace(
            "reviewThreads(first: 100) { nodes { isResolved } }",
            "reviewThreads(first: 100) { pageInfo { hasNextPage endCursor } nodes { id isResolved } }").replace(
            "latestReviews(first: 20) { nodes { author", "latestReviews(first: 100) { nodes { id author")
        rows = module.fetch(scope)
        known = {pr["url"] for pr in rows}
        selection = module.QUERY.split("... on PullRequest {", 1)[1].rsplit("} }", 1)[0]
        for url, old in (before or {}).items():
            if url not in known and old["state"] == "OPEN":
                query = "query($id:ID!){node(id:$id){... on PullRequest {" + selection + "}}}"
                response = json.loads(command("gh", "api", "graphql", "-f", "query=" + query, "-f", "id=" + old["id"]))
                if response.get("errors") or not response.get("data", {}).get("node"):
                    raise ValueError("tracked PR read failed")
                rows.append(response["data"]["node"])
            elif url not in known:
                # Terminal records are retained; no repeated reads needed.
                rows.append({"id": old["id"], "url": url, "title": old["title"], "headRefOid": old["sha"],
                             "state": old["state"], "reviewDecision": old["decision"],
                             "reviewThreads": {"nodes": [{"id": t, "isResolved": False} for t in old["threads"]],
                                               "pageInfo": {"hasNextPage": False}},
                             "latestReviews": {"nodes": [{"id": rid, "state": "CHANGES_REQUESTED"} for rid in old.get("requests", [])]},
                             "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": old["ci"]}}}]}})
        for pr in rows:
            connection = pr["reviewThreads"]
            while connection["pageInfo"]["hasNextPage"]:
                query = "query($id:ID!,$cursor:String!){node(id:$id){... on PullRequest {reviewThreads(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id isResolved}}}}}"
                response = json.loads(command("gh", "api", "graphql", "-f", "query=" + query,
                                               "-f", "id=" + pr["id"], "-f", "cursor=" + connection["pageInfo"]["endCursor"]))
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
    parser.add_argument("--preview-answers", action="store_true", help="inspect answers without consuming or publishing them")
    parser.add_argument("--scope", default=os.environ.get("ORCH_WATCHERS_PR_SCOPE", ""))
    parser.add_argument("--interval", type=int)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.mode == "all" and args.loop:
        parser.error("--loop all is not supported; run one loop per mode")
    if args.mode in ("prs", "all") and not args.fixture and not args.scope:
        parser.error("--scope or ORCH_WATCHERS_PR_SCOPE is required for PR watchers")
    if args.interval is None:
        args.interval = WATCH_TIMING[args.mode]["interval"] if args.mode != "all" else 60
    if args.interval < 5:
        parser.error("interval must be >= 5")
    for _ in runtime.iterations(args.interval, args.loop, args.mode, budget=None):
        for mode in MODES if args.mode == "all" else (args.mode,):
            def collector(before):
                if args.fixture:
                    return json.loads(args.fixture.read_text())
                return collect(mode, before, args.scope, args.state_dir)
            if mode == "answers" and args.preview_answers:
                with runtime.deadline(2 * WATCH_TIMING["answers"]["soft_budget"], "answers preview"):
                    _, events = reduce_answers(None, collect_answers(None, consume=False))
                line = json.dumps({"mode": mode, "events": events}) if events else None
            else:
                line = cycle(args.state_dir, mode, collector)
            if line:
                with runtime.deadline(runtime.ITERATION_TIMEOUT, f"{mode} output"):
                    print(line, flush=True)


if __name__ == "__main__":
    main()
