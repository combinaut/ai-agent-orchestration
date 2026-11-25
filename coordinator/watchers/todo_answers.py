"""Answers queued by the to-do UI; the existing todo producer and notifier own delivery."""

import fcntl
import hashlib
import os
import re
import time
import uuid
from pathlib import Path

import discovery
from question_lock import answers_lock
from worker_signals import read, write

ANSWER_MAX = 512
CHOICES_MAX = 9
PENDING_MAX = 200
REASON_MAX = 200
KEEP_SECONDS = 3600
LOCK_SECONDS = 2
CHOICE = re.compile(r"\[([0-9]+): ([^\[\]\r\n]+)\]")


def choices(text):
    matches = CHOICE.findall(text.rsplit(" -> ", 1)[0])
    if (
        not matches
        or len(matches) > CHOICES_MAX
        or [key for key, _ in matches] != [str(n) for n in range(1, len(matches) + 1)]
    ):
        return {}
    return (
        {key: value.strip() for key, value in matches}
        if all(0 < len(value.strip()) <= ANSWER_MAX for _, value in matches)
        else {}
    )


def printable(text):
    """The text with every non-printable character removed (ESC, BEL, tab, newline, other controls, DEL) and runs of spaces collapsed."""
    return " ".join(
        "".join(c for c in str(text) if c.isprintable() or c == " ").split()
    )


def directory():
    return Path.home() / ".claude/coordinator/todo-answers"


def setting(name, fallback):
    if os.environ.get(name):
        return os.environ[name]
    values = discovery.orch_settings.resolved_global(
        discovery.orch_settings.config_path(os.environ), os.environ
    )
    return values.get(name) or fallback


def status_directory():
    return Path(
        setting("ORCH_USER_TODO_STATUS_DIR", Path.home() / ".claude/coordinator/status")
    )


def item_id(owner, text):
    return owner + ".md\0" + text


def records():
    return [record for path in directory().glob("*.json") if (record := read(path))]


def pending_item_ids(generations=None):
    return {
        record["item_id"]
        for record in records()
        if not record.get("discarded")
        and (
            generations is None
            or record.get("generation") is None
            or record["generation"] == generations.get(record["item_id"])
        )
    }


def pending_ids():
    return {record["id"] for record in records() if not record.get("discarded")}


def queue_lock():
    return answers_lock(
        setting(
            "ORCH_USER_QUESTIONS_DIR", Path.home() / ".claude/coordinator/questions"
        ),
        timeout=LOCK_SECONDS,
    )


def undo_dismissal(record):
    path = Path(
        setting(
            "ORCH_USER_TODO_STATE",
            Path.home() / ".claude/coordinator/user-todo.state.json",
        )
    )
    if not path.exists():
        return
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(path)
        if record.get("generation") in (
            None,
            state.get("first_seen", {}).get(record["item_id"]),
        ):
            state["dismissed"] = [
                identifier
                for identifier in state.get("dismissed", [])
                if identifier != record["item_id"]
            ]
            write(path, state)


def prune(now):
    from watchers import todo_module

    items, unreadable = todo_module().status_items(status_directory())
    present = {item["id"] for item in items}
    try:
        state = (
            read(
                Path(
                    setting(
                        "ORCH_USER_TODO_STATE",
                        Path.home() / ".claude/coordinator/user-todo.state.json",
                    )
                )
            )
            or {}
        )
        generations = state.get("first_seen", {})
        if not isinstance(generations, dict):
            generations = {}
    except (OSError, ValueError, AttributeError):
        generations = {}
    current = []
    for record in records():
        source = status_directory() / (record["architect"] + ".md")
        changed = (
            (
                record["item_id"] not in present
                or (
                    record.get("generation") is not None
                    and record["item_id"] in generations
                    and record["generation"] != generations[record["item_id"]]
                )
            )
            and str(status_directory()) not in unreadable
            and str(source) not in unreadable
        )
        expired = (
            record.get("delivered_at") and now - record["delivered_at"] > KEEP_SECONDS
        )
        if record.get("discarded"):
            current.append(record)
        elif changed and not record.get("delivered_at"):
            undo_dismissal(record)
            record["discarded"] = True
            write(directory() / (record["key"] + ".json"), record)
            current.append(record)
        elif changed or expired:
            (directory() / (record["key"] + ".json")).unlink(missing_ok=True)
        else:
            current.append(record)
    return current


def filter_canceled(text):
    current = pending_ids()
    kept = []
    for line in text.splitlines():
        match = re.search(
            r"TODO ANSWER owner=[A-Za-z0-9_-]+ id=(todo-[a-f0-9]+)\b", line
        )
        if not match or match.group(1) in current:
            kept.append(line)
    return "\n".join(kept)


def mark_delivered(text, owner=None):
    with queue_lock():
        mark_delivered_locked(text, owner)


def mark_delivered_locked(text, owner=None):
    """Caller holds the queue lock; the lock is not re-entrant in one process."""
    identifiers = {
        identifier
        for target, identifier in re.findall(
            r"TODO ANSWER owner=([A-Za-z0-9_-]+) id=(todo-[a-f0-9]+)\b", text
        )
        if owner is None or target == owner
    }
    if not identifiers:
        return
    for record in records():
        if (
            record["id"] in identifiers
            and not record.get("delivered_at")
            and not record.get("discarded")
        ):
            record["delivered_at"] = time.time()
            record.pop("failure", None)
            record.pop("failed_at", None)
            write(directory() / (record["key"] + ".json"), record)


def submit(owner, text, answer, generation=None):
    with queue_lock():
        existing = prune(time.time())
        return submit_locked(owner, text, answer, generation, existing)


def submit_locked(owner, text, answer, generation, existing):
    """Caller holds the queue lock; prune before acquiring a to-do state lock."""
    from watchers import todo_module

    if not owner or Path(owner).name != owner or owner in (".", "..") or ": " in owner:
        raise ValueError("invalid architect name")
    if len(answer) > ANSWER_MAX or not answer.strip():
        raise ValueError(f"answer must contain 1-{ANSWER_MAX} characters")
    answer = " ".join(answer.split())
    current, _ = todo_module().status_items(status_directory())
    identifier = item_id(owner, text)
    if not any(item["id"] == identifier for item in current):
        raise ValueError(
            "item changed or its status file is unreadable; refresh the list"
        )
    now = time.time()
    if any(
        record["item_id"] == identifier and record.get("generation") == generation
        for record in existing
    ):
        raise ValueError("an answer is already queued for this item")
    if len(existing) >= PENDING_MAX:
        raise ValueError(
            "to-do answer capacity reached; try after delivery or a status change"
        )
    record = dict(
        id="todo-" + uuid.uuid4().hex,
        key=hashlib.sha256((identifier + "\0" + str(generation)).encode()).hexdigest(),
        item_id=identifier,
        generation=generation,
        architect=owner,
        text=text,
        answer=answer,
        at=now,
    )
    write(directory() / (record["key"] + ".json"), record)
    return record


def reason_text(text):
    return " ".join(str(text).split())[:REASON_MAX]


def record_failures(reasons):
    """Stores each failed answer's reason and first failure time; clears both for answers now ready."""
    with queue_lock():
        for record in records():
            if record.get("delivered_at") or record.get("discarded"):
                continue
            reason = reasons.get(record["id"])
            if reason is None:
                update = {
                    key: record.pop(key, None) for key in ("failure", "failed_at")
                }
                changed = any(value is not None for value in update.values())
            else:
                changed = record.get("failure") != reason or "failed_at" not in record
                record["failure"] = reason
                record.setdefault("failed_at", time.time())
            if changed:
                write(directory() / (record["key"] + ".json"), record)


def undelivered():
    return sorted(
        (
            record
            for record in records()
            if not record.get("delivered_at") and not record.get("discarded")
        ),
        key=lambda record: record["at"],
    )


def describe(record):
    stamp = time.strftime("%H:%M", time.localtime(record["at"]))
    reason = printable(record.get("failure") or "")
    return (
        f"Not delivered yet, queued {stamp}"
        + (f" ({reason})" if reason else "")
        + f": {printable(record['architect'])}: {printable(record['text'])}"
        + f" = {printable(record['answer'])}"
    )


def cancel(record_id):
    """Removes one undelivered answer and puts its item back; returns a status message. A delivered answer stays."""
    with queue_lock():
        record = next(
            (
                found
                for found in records()
                if found["id"] == record_id and not found.get("discarded")
            ),
            None,
        )
        if record is None:
            return "answer is no longer queued"
        if record.get("delivered_at"):
            return "answer was already delivered; it stays"
        (directory() / (record["key"] + ".json")).unlink(missing_ok=True)
        undo_dismissal(record)
    return "answer cancelled; the item returns"


def collect(command):
    """Returns (ready records, discard notices, failures); a failure is one architect's undelivered answers."""
    if not directory().exists():
        return [], [], []
    with queue_lock():
        pruned = prune(time.time())
        notices = []
        for record in pruned:
            if record.get("discarded"):
                notices.append(
                    f"answer discarded: {record['architect']}: {record['text']} = {record['answer']} (status item changed before delivery)"
                )
                (directory() / (record["key"] + ".json")).unlink(missing_ok=True)
        current = [
            record
            for record in pruned
            if not record.get("delivered_at") and not record.get("discarded")
        ]
    if not current:
        return [], notices, []
    ambiguous = set()
    try:
        owners = discovery.architect_owners(command, ambiguous_out=ambiguous)
    except discovery.Unavailable as error:
        reason = reason_text(error) or "Herdr is unavailable"
        owners = None
    ready, reasons = [], {}
    for record in current:
        name = record["architect"]
        owner = owners.get(name) if owners is not None else None
        if owners is None:
            reasons[record["id"]] = reason
        elif owner and re.fullmatch(r"[A-Za-z0-9_-]+", owner):
            ready.append(dict(record, owner=owner))
        elif name in ambiguous:
            reasons[record["id"]] = reason_text(
                f"ambiguous: more than one live architect named {name}"
            )
        else:
            reasons[record["id"]] = reason_text(f"no live architect named {name}")
    try:
        record_failures(reasons)
    except TimeoutError:
        pass
    failed = {}
    for record in current:
        if record["id"] in reasons:
            failed.setdefault(record["architect"], reasons[record["id"]])
    return (
        ready,
        notices,
        [dict(architect=name, reason=failed[name]) for name in sorted(failed)],
    )


def event_line(record):
    def clean(text):
        return " ".join(str(text).split())

    return f"TODO ANSWER owner={record['owner']} id={record['id']} architect={clean(record['architect'])}: {clean(record['text'])} = {clean(record['answer'])}"


class AnswerInput:
    def __init__(self, options):
        self.options, self.entry, self.error = options, "", ""
        self.mode = "choices" if options else "type"

    def feed(self, char):
        self.error = ""
        if char == "\x1b":
            return "cancel", None
        if self.mode == "choices":
            if char in self.options:
                return "answer", self.options[char]
            if char == "t":
                self.mode = "type"
        elif char in "\r\n":
            if self.entry.strip():
                return "answer", self.entry.strip()
            self.error = "Answer required"
        elif char in "\x7f\b":
            self.entry = self.entry[:-1]
        elif char.isprintable():
            if len(self.entry) < ANSWER_MAX:
                self.entry += char
            else:
                self.error = f"Answer limit: {ANSWER_MAX} characters"
        return None, None

    def prompt(self):
        if self.mode == "type":
            return f"Answer: {self.entry}▏ (Enter sends; Esc cancels)"
        return (
            " · ".join(
                f"{key}: {printable(value)}" for key, value in self.options.items()
            )
            + " · t type · Esc cancels"
        )
