"""Feed policy for watcher alerts: one start line and one clear line per condition; alert and state files keep every event."""
import math
import os
import re

ESCALATE_RATIO = 1.5
DIGEST_SECONDS = 1800
CLEAR_HOLD_SECONDS = 1800
LINE_LIMIT = 160
DIGEST_KEYS = 5
MAX_FOLDED = 200
KEY_RETAIN_SECONDS = 86400
MAX_KEYS = 1000
DANGER_STEP = 2
STATE_VERSION = 3
MODE_KEY_PREFIXES = {
    "host": ("host:",),
    "balance": ("balance:",),
    "hog": ("hog:", "gitbig:", "gitlong:"),
}
HELD_PREFIXES = tuple(prefix for prefixes in MODE_KEY_PREFIXES.values() for prefix in prefixes)

HOST_METRICS = {"PRESSURE": ("free", "ORCH_HOST_PRESSURE_MIN_PCT", 20, True),
                "SWAP": ("swap", "ORCH_HOST_SWAP_MAX_MB", 1024, False),
                "PROCS": ("procs", "ORCH_HOST_PROCS_MAX", 1300, False),
                "FILES": ("files", "ORCH_HOST_FILES_MAX", 150000, False),
                "LOAD": ("load", "ORCH_HOST_LOAD_MAX", 40, False)}
BALANCE_METRICS = {"LOAD5": ("load5", None, 20, False), "SLOTS": ("slots", "ORCH_HEAVY_SLOTS_MAX", 4, False),
                   "HEAVYWT": ("heavy", None, 4, False), "MEMFREE": ("free", None, 30, True)}
HOG_KINDS = {"HOG": ("rss", "ORCH_HOG_GB", 6), "GITBIG": ("rss", "ORCH_HOG_GIT_GB", 2),
             "GITLONG": ("age", "ORCH_HOG_GIT_AGE_S", 180)}
PULL_KINDS = ("APPROVED", "APPROVED-OLDHEAD", "APPROVED-STACKED", "RED", "CHANGES", "THREADS", "COPILOT")
YOUNG_MARK = ",new)"
READFAIL_LABELS = {"workers": "WORKER"}
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
PULL_OWNER = re.compile(r"^\S+ \S+#\d+ owner=(\S+)")


def setting(name, default):
    return float(os.environ.get(name, default))


def ratio():
    value = setting("ORCH_ALERT_ESCALATE_RATIO", ESCALATE_RATIO)
    if not value > 1:
        raise ValueError(f"ORCH_ALERT_ESCALATE_RATIO must be greater than 1, not {value:g}")
    return value


def limits():
    return dict(ratio=ratio(),
                digest=setting("ORCH_ALERT_DIGEST_S", DIGEST_SECONDS),
                hold=setting("ORCH_ALERT_QUIET_S", CLEAR_HOLD_SECONDS))


def number(text):
    match = NUMBER.search(text or "")
    return float(match.group()) if match else None


def short(text):
    text = " ".join(text.split())
    return text if len(text) <= LINE_LIMIT else text[:LINE_LIMIT - 1] + "…"


def fact(key, text, value=None, limit=None, lower=False, clearable=False, terminal=False, danger=False):
    return dict(kind="alert", key=key, text=short(text), value=value, limit=limit, lower=lower,
                clearable=clearable, terminal=terminal, danger=danger)


def clear(prefix, exclude=(), exact=False):
    return dict(kind="clear", prefix=prefix, exclude=tuple(exclude), exact=exact)


def fold(text):
    return dict(kind="fold", text=short(text))


def metric_facts(mode, line, table):
    body = line.split(" top: ", 1)[0]
    out = []
    for label, detail in re.findall(r"([A-Z0-9]+)\(([^)]*)\)", body):
        if label not in table:
            continue
        name, env, default, lower = table[label]
        limit = setting(env, default) if env else float(default)
        out.append(fact(f"{mode}:{name}", f"{mode.upper()} {label} {detail}", number(detail), limit, lower,
                        clearable=True, danger=mode == "host"))
    if out:
        out.append(dict(kind="snapshot", prefix=f"{mode}:", exclude=(f"{mode}:readfail:", f"{mode}:health:"),
                        keep={item["key"] for item in out}))
    return out


def health_line(mode, line):
    return line.startswith(f"{mode} read failed ") or line == f"{mode} READ RECOVERED"


def readfail_facts(mode, line):
    """Only the producer's own forms: `<LABEL> READFAIL …` and `<mode> read failed …`, never text inside a PR line."""
    label = READFAIL_LABELS.get(mode, mode.upper())
    if line == f"{label} READFAIL cleared":
        return [clear(f"{mode}:readfail:")]
    if line == f"{mode} READ RECOVERED":
        return [clear(f"{mode}:health:read", exact=True)]
    if line.startswith(f"{mode} read failed "):
        return [fact(f"{mode}:health:read", line, clearable=True)]
    if line.startswith(f"{label} READFAIL "):
        prefix, sources = line.split(" READFAIL ", 1)
        out = []
        for source in sources.split():
            if source.endswith(YOUNG_MARK):
                out.append(fold(f"{prefix} READFAIL {source}"))
            else:
                out.append(fact(f"{mode}:readfail:{source}", f"{prefix} READFAIL {source}", clearable=True))
        keep = {item["key"] for item in out if item["kind"] == "alert"}
        return out + [dict(kind="snapshot", prefix=f"{mode}:readfail:", exclude=(), keep=keep)]
    return None


def state_reset_facts(mode, line):
    if line.startswith(f"STATE RESET {mode}: "):
        return [dict(kind="reset", prefixes=MODE_KEY_PREFIXES.get(mode, (f"{mode}:",))),
                fact(f"{mode}:state-reset:{line}", line, terminal=True)]
    return None


def hog_facts(line):
    gone = re.match(r"(HOG|GITBIG|GITLONG) pid=(\d+) comm=\[([^\]]*)\] cleared$", line)
    if gone:
        kind, pid, process = gone.groups()
        return [clear(f"{kind.lower()}:{pid}/{process}", exact=True)]
    if line == "hog LOG WRITE FAILED":
        return [fact("hog:log", line, clearable=True)]
    if line == "hog LOG WRITE RECOVERED":
        return [clear("hog:log", exact=True)]
    match = (re.match(r"(HOG|GITBIG|GITLONG) pid=(\d+) rss=([\d.]+)GB age=(\d+)s comm=\[([^\]]*)\]", line)
             or re.match(r"(HOG|GITBIG|GITLONG) pid=(\d+) rss=([\d.]+)GB age=(\d+)s cmd=\[(?:[^\] ]*/)?([^\] /]+)", line))
    if not match:
        return None
    kind, pid, rss, age, process = match.groups()
    field, env, default = HOG_KINDS[kind]
    value = float(rss) if field == "rss" else float(age)
    shown = f"{rss}GB" if field == "rss" else f"{age}s"
    return [fact(f"{kind.lower()}:{pid}/{process}", f"{kind} {pid}/{process} {shown}", value, setting(env, default),
                 clearable=True)]


def context_facts(line):
    gone = re.match(r"RECYCLE (.+) cleared$", line)
    if gone:
        return [clear(f"context:{gone.group(1)}", exact=True)]
    if line.startswith("NOSENSOR "):
        if line.startswith("NOSENSOR CLEARED "):
            return [clear("context:nosensor:" + line.split(" ")[2], exact=True)]
        return [fact("context:nosensor:" + line.split(" ")[1], line, clearable=True)]
    match = re.match(r"RECYCLE (.+) (\d+)(k|%)$", line)
    if not match:
        return [fact(f"context:{line[8:]}", line, clearable=True)] if line.startswith("RECYCLE ") else None
    subject, amount, unit = match.groups()
    limit = (setting("ORCH_RECYCLE_TOKENS", 300000) / 1000 if unit == "k" else setting("ORCH_RECYCLE_PCT", 55))
    return [fact(f"context:{subject}", line, float(amount), limit, clearable=True)]


def worker_facts(line):
    match = re.match(r"WORKER (\S+) (RECOVERED )?(.+)$", line)
    if not match:
        return None
    pane, recovered, state = match.groups()
    if state.startswith("MOVED "):
        return [dict(kind="move", key=f"workers:{pane}", target=f"workers:{state.split(' ', 1)[1]}")]
    if state == "closed":
        return [clear(f"workers:{pane}", exact=True), fold(line)]
    if recovered:
        return [clear(f"workers:{pane}", exact=True)]
    return [fact(f"workers:{pane}", line, clearable=True, terminal=state == "dead/disappeared")]


def audit_facts(line):
    if line.startswith("AUDIT BASELINE "):
        return [fact("audit:baseline:" + re.sub(r" \[[^\]]*\]$", "", line[len("AUDIT BASELINE "):]), line)]
    if line.startswith("READFAIL "):
        return [fact("audit:readfail:" + line.split(" ", 1)[1].split(": ", 1)[0], line, clearable=True)]
    cleared = re.fullmatch(r"(github|herdr|slots|(?:slot|git):.+) READFAIL cleared", line)
    if cleared:
        return [clear("audit:readfail:" + cleared.group(1), exact=True)]
    if line.startswith("CLEARED "):
        return [clear(audit_facts(line[len("CLEARED "):])[0]["key"], exact=True)]
    match = re.match(r"(PACKS|WORKTREES) (.+?) has over (\d+)", line)
    if match:
        kind, repo, count = match.groups()
        return [fact(f"audit:{kind} {repo}", line, float(count), clearable=True)]
    subject = re.sub(r" \[[^\]]*\]$", "", line)
    return [fact("audit:" + subject, line, clearable=True)]


def pull_facts(line):
    if line.startswith("PULLS BASELINE "):
        return [fact("pulls:baseline", line)]
    match = re.match(r"(\S+) (\S+#\d+)(?: (.*))?$", line)
    if not match or match.group(1) not in PULL_KINDS + ("MERGED", "CLOSED"):
        return None
    kind, pr, rest = match.groups()
    if rest == "cleared":
        return [clear(f"pulls:{kind} {pr}", exact=True)]
    item = fact(f"pulls:{kind} {pr}", line, clearable=True, terminal=kind in ("MERGED", "CLOSED"))
    head = PULL_OWNER.match(line)
    if head and not item["text"].startswith(head.group(0)):
        item["text"] = head.group(0) + " " + short(line[head.end():])[:max(1, LINE_LIMIT - head.end() - 1)]
    return [item]


def todo_facts(line):
    if line.startswith("TODO READFAIL "):
        return [fact("todo:readfail:" + line[len("TODO READFAIL "):], line, clearable=True)]
    if line.startswith("TODO READ RECOVERED "):
        return [clear("todo:readfail:" + line[len("TODO READ RECOVERED "):], exact=True)]
    if line.startswith("TODO ") and " new: " in line:
        return [fact("todo:" + line, line, terminal=True)]
    return None


def facts(alert):
    """A `snapshot` alert lists every current condition of its mode; keys of that mode it does not list are released."""
    mode = alert.get("mode", "?")
    out = []
    if alert.get("snapshot"):
        listed = facts(dict(alert, snapshot=False))
        keep = {item["key"] for item in listed if item["kind"] == "alert"}
        return listed + [dict(kind="snapshot", prefix=f"{mode}:", exclude=(f"{mode}:health:", f"{mode}:baseline"), keep=keep)]
    for line in alert.get("events", []):
        if line == "HEARTBEAT STALE":
            out.append(fact(f"heartbeat:{mode}", f"{mode} {line}", clearable=True))
            continue
        if line == "HEARTBEAT RECOVERED":
            out.append(clear(f"heartbeat:{mode}", exact=True))
            continue
        if line == f"{mode.upper()} RECOVERED":
            out.append(clear(f"{mode}:", exclude=(f"{mode}:readfail:", f"{mode}:health:")))
            continue
        health = health_line(mode, line)
        parsed = todo_facts(line) if mode == "todo" else readfail_facts(mode, line) if mode != "audit" or health else None
        if parsed is None and mode == "todo":
            parsed = readfail_facts(mode, line)
        if parsed is None:
            parsed = state_reset_facts(mode, line)
        if parsed is None and mode == "host" and line.startswith("HOST "):
            parsed = metric_facts(mode, line, HOST_METRICS)
        if parsed is None and mode == "balance" and line.startswith("BALANCE "):
            parsed = metric_facts(mode, line, BALANCE_METRICS)
        if parsed is None and mode == "hog":
            parsed = hog_facts(line)
        if parsed is None and mode == "context":
            parsed = context_facts(line)
        if parsed is None and mode == "workers":
            parsed = worker_facts(line)
        if parsed is None and mode == "audit":
            parsed = audit_facts(line)
        if parsed is None and mode == "pulls":
            parsed = pull_facts(line)
        out.extend(parsed or [dict(fact(f"{mode}:unparsed:" + unparsed_format(line), line), unparsed=True)])
    return out


def unparsed_format(line):
    """A line no parser knows is keyed on its shape: digits and bracketed details removed."""
    return short(re.sub(r"\[[^\]]*\]", "[…]", NUMBER.sub("#", line)))


def band(value, limit, lower, base=2.0):
    if value is None or not limit:
        return 0
    if lower:
        if value >= limit:
            return 0
        return 99 if value <= 0 else math.floor(math.log(limit / value, base)) + 1
    return 0 if value < limit else math.floor(math.log(value / limit, base)) + 1


def step(value, limit, lower):
    """Severity step shared by producers and delivery: a value past the escalation ratio of another is in a later step."""
    return band(value, limit, lower, ratio())


def held(key):
    return key.startswith(HELD_PREFIXES) or ":readfail:" in key or ":health:" in key


def matches(key, item):
    if item["exact"]:
        return key == item["prefix"]
    return key.startswith(item["prefix"]) and not (item["exclude"] and key.startswith(item["exclude"]))


def last_activity(entry):
    return max(entry.get("seen_at", 0), entry.get("cleared_at", 0), entry.get("started_at", 0))


def prune(keys, now):
    """Drops inactive or never-clearing keys idle for KEY_RETAIN_SECONDS, then the least recently active beyond MAX_KEYS."""
    for key in [key for key, entry in keys.items()
                if (not entry["active"] or not entry["clearable"]) and now - last_activity(entry) > KEY_RETAIN_SECONDS]:
        del keys[key]
    for key in sorted(keys, key=lambda key: last_activity(keys[key]))[:max(0, len(keys) - MAX_KEYS)]:
        del keys[key]


def pull_owner(text):
    match = PULL_OWNER.match(text)
    return match.group(1) if match else None


def feed_line(mode, text, reason):
    return f"{mode}: {text} ({reason})"


def apply(state, alerts, now, cfg=None, trace=None):
    """Updates state in place; returns the feed lines due now: start, danger, clear and digest.
    Alerts apply in order of observation time (`at`, else now), so one producer's later batch cannot expire another's
    earlier return. `trace` receives (key, reason) for every keyed line."""
    cfg = cfg or limits()
    if state.get("version") != STATE_VERSION:
        state.clear()
        state["version"] = STATE_VERSION
    keys = state.setdefault("keys", {})
    folded = state.setdefault("folded", [])
    state.setdefault("digest_at", now)
    lines = []

    def emit(key, mode, text, reason):
        lines.append(feed_line(mode, text, reason))
        if trace is not None:
            trace.append((key, reason))

    def finish(key, at):
        entry = keys[key]
        entry.pop("clearing_at", None)
        entry.update(active=False, cleared_at=at)
        emit(key, entry["mode"], entry["text"], "clear")

    def release(key, at):
        entry = keys[key]
        if not entry["active"]:
            return
        if held(key):
            entry.setdefault("clearing_at", at)
        else:
            finish(key, at)

    def finish_expired(at):
        for key in [key for key, entry in keys.items() if "clearing_at" in entry and at - entry["clearing_at"] >= cfg["hold"]]:
            finish(key, at)

    for alert in sorted(alerts, key=lambda alert: min(alert.get("at", now), now)):
        observed = min(alert.get("at", now), now)
        finish_expired(observed)
        mode = alert.get("mode", "?")
        items = facts(alert)
        moves = [item for item in items if item["kind"] == "move"]
        keys.update({item["target"]: keys.pop(item["key"]) for item in moves if item["key"] in keys})
        for item in items:
            if item["kind"] == "move":
                continue
            if item["kind"] == "fold":
                if len(folded) < MAX_FOLDED:
                    folded.append(item["text"])
                else:
                    state["folded_extra"] = state.get("folded_extra", 0) + 1
                continue
            if item["kind"] == "snapshot":
                for key in [key for key in keys if key.startswith(item["prefix"])
                            and not (item["exclude"] and key.startswith(item["exclude"])) and key not in item["keep"]]:
                    release(key, observed)
                continue
            if item["kind"] == "clear":
                for key in [key for key in keys if matches(key, item)]:
                    release(key, observed)
                continue
            if item["kind"] == "reset":
                for key in [key for key in keys if key.startswith(item["prefixes"])]:
                    keys[key].pop("clearing_at", None)
                    keys[key].update(active=False, cleared_at=now)
                continue
            if item.get("unparsed"):
                counts = state.setdefault("unparsed", {})
                shape = item["key"].split(":unparsed:", 1)[1]
                if shape in counts:
                    counts[shape] += 1
                elif len(counts) < MAX_KEYS:
                    counts[shape] = 1
                    lines.append(feed_line("watchers", f"unparsed {mode} line format: {shape}", "unparsed"))
            danger = item["danger"] and band(item["value"], item["limit"], item["lower"], cfg["ratio"]) >= DANGER_STEP
            entry = keys.get(item["key"])
            if entry and entry["active"] and item["terminal"]:
                finish(item["key"], observed)
            if entry and entry["active"] and not item["terminal"]:
                entry.pop("clearing_at", None)
                entry.update(seen_at=now, value=item["value"])
                if mode == "pulls" and pull_owner(item["text"]) not in (None, "?", pull_owner(entry["text"])):
                    entry["text"] = item["text"]
                    emit(item["key"], mode, item["text"], "owner")
                if danger and not entry["danger"]:
                    entry.update(danger=True, text=item["text"])
                    emit(item["key"], mode, item["text"], "danger")
                continue
            keys[item["key"]] = dict(mode=mode, text=item["text"], value=item["value"], clearable=item["clearable"],
                                     active=not item["terminal"], danger=danger, started_at=now, seen_at=now)
            if item["terminal"]:
                keys[item["key"]]["cleared_at"] = now
            emit(item["key"], mode, item["text"], "danger" if danger else "start")
    finish_expired(now)
    prune(keys, now)
    if folded and now - state["digest_at"] >= cfg["digest"]:
        total = len(folded) + state.pop("folded_extra", 0)
        shown = "; ".join(folded[:DIGEST_KEYS]) + (f"; +{total - DIGEST_KEYS} more" if total > DIGEST_KEYS else "")
        lines.append(feed_line("watchers", short(f"Watcher digest, {total} folded: {shown}"), "digest"))
        folded.clear()
        state["digest_at"] = now
    return lines


def rebuild(alerts, now, cfg=None, current=None):
    """Fresh state from an alert history, clears applied at once; returns it with one line per condition still active.
    `current` maps a mode to {key: text or None} from its producer snapshot; other keys of that mode end without a
    line, and a given text replaces the historical one."""
    state, cfg, notes = {}, dict(cfg or limits(), hold=0, digest=math.inf), []
    for alert in alerts:
        notes += [line for line in apply(state, [alert], now, cfg)
                  if line.endswith("(unparsed)") or (alert.get("notify_reset") and " STATE RESET " in line)]
    apply(state, [], now, cfg)
    for key, entry in state["keys"].items():
        if entry["mode"] in (current or {}) and key not in current[entry["mode"]] \
                and not key.startswith("heartbeat:") and ":health:" not in key:
            entry.update(active=False, cleared_at=now)
        elif entry["active"] and (current or {}).get(entry["mode"], {}).get(key):
            entry["text"] = current[entry["mode"]][key]
    state.update(folded=[], digest_at=now)
    state.pop("folded_extra", None)
    lines = [feed_line(entry["mode"], entry["text"], "danger" if entry["danger"] else "start")
             for entry in state["keys"].values() if entry["active"]]
    return state, notes + lines


def repair_owners(state, current):
    """Version 2 to 3: an active PR event whose saved owner differs from the producer's current owner gets an `owner`
    line, as the owner change would have written. `current` maps pulls keys to their current text."""
    lines = []
    for key, entry in state.get("keys", {}).items():
        text = current.get(key)
        if entry["mode"] == "pulls" and entry["active"] and text \
                and pull_owner(text) not in (None, "?", pull_owner(entry["text"])):
            entry["text"] = text
            lines.append(feed_line("pulls", text, "owner"))
    state["version"] = STATE_VERSION
    return lines
