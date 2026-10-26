"""Answer bookkeeping and relay queue for the interactive to-do views."""

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
        and not record.get("pane_delivery_unknown_at")
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
        retained_at = record.get("delivered_at") or record.get(
            "pane_delivery_unknown_at"
        )
        expired = retained_at and now - retained_at > KEEP_SECONDS
        if record.get("discarded"):
            current.append(record)
        elif record.get("pane_delivery_unknown_at") and (changed or expired):
            (directory() / (record["key"] + ".json")).unlink(missing_ok=True)
        elif changed and not record.get("delivered_at"):
            undo_dismissal(record)
            record["discarded"] = True
            write(directory() / (record["key"] + ".json"), record)
            current.append(record)
        elif expired or (changed and not record.get("pane_delivered")):
            (directory() / (record["key"] + ".json")).unlink(missing_ok=True)
        else:
            current.append(record)
    return current


def filter_canceled(text):
    current = {
        record["id"]
        for record in records()
        if not record.get("discarded")
        and not record.get("pane_delivered")
        and not record.get("pane_delivery_unknown_at")
    }
    kept = []
    for line in text.splitlines():
        match = re.search(
            r"TODO ANSWER owner=[A-Za-z0-9_-]+ id=(todo-[a-f0-9]+)\b", line
        )
        if not match or match.group(1) in current:
            kept.append(line)
    return "\n".join(kept)


def mark_delivered(text, owner=None):
    identifiers = {
        identifier
        for target, identifier in re.findall(
            r"TODO ANSWER owner=([A-Za-z0-9_-]+) id=(todo-[a-f0-9]+)\b", text
        )
        if owner is None or target == owner
    }
    if not identifiers:
        return
    with queue_lock():
        for record in records():
            if (
                record["id"] in identifiers
                and not record.get("delivered_at")
                and not record.get("discarded")
            ):
                record["delivered_at"] = time.time()
                write(directory() / (record["key"] + ".json"), record)


def submit(owner, text, answer, generation=None):
    with queue_lock():
        existing = prune(time.time())
        return submit_locked(owner, text, answer, generation, existing)


def submit_locked(owner, text, answer, generation, existing):
    """Caller holds the queue lock; prune before acquiring a to-do state lock."""
    from watchers import todo_module

    if not owner or Path(owner).name != owner or owner in (".", ".."):
        raise ValueError("invalid architect name")
    if not choices(text):
        raise ValueError("this item has no valid choices")
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


def collect(command):
    if not directory().exists():
        return [], []
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
            if (not record.get("delivered_at") or record.get("pane_delivered"))
            and not record.get("discarded")
            and not record.get("pane_delivery_unknown_at")
        ]
    if not current:
        return [], notices
    direct = [record for record in current if record.get("pane_delivered")]
    current = [record for record in current if not record.get("pane_delivered")]
    if not current:
        return direct, notices
    try:
        owners = discovery.architect_owners(command)
    except discovery.Unavailable:
        return direct, notices + ["answer:" + record["architect"] for record in current]
    ready, failed = direct, []
    for record in current:
        owner = owners.get(record["architect"])
        if owner and re.fullmatch(r"[A-Za-z0-9_-]+", owner):
            ready.append(dict(record, owner=owner))
        else:
            failed.append("answer:" + record["architect"])
    return ready, notices + sorted(set(failed))


def event_line(record):
    def clean(text):
        return " ".join(str(text).split())

    return f"TODO ANSWER owner={record['owner']} id={record['id']} architect={clean(record['architect'])}: {clean(record['text'])} = {clean(record['answer'])}"


class AnswerInput:
    def __init__(self, options):
        self.options, self.mode, self.entry, self.error = options, "choices", "", ""

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
            " · ".join(f"{key}: {value}" for key, value in self.options.items())
            + " · t type · Esc cancels"
        )
