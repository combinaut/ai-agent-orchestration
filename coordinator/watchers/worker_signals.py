"""Explicit, harness-independent worker signals. Runtime files live outside the checkout."""

import argparse
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import discovery
from question_lock import answers_lock, questions_directory
from question_remind import atomic_write

POLL_SECONDS = 1
HEARTBEAT_MIN_SECONDS = 30
HEARTBEAT_FRESH_SECONDS = 300
QUESTION_RETAIN_SECONDS = 3600
QUESTIONS_MAX = 200
CLEANUP_MAX = 1
CLEANUP_SECONDS = 2
DONE_EVENTS_MAX = 200
DONE_EVENTS_KEEP_SECONDS = 3600
ID = re.compile(r"signal-[a-f0-9]{32}\Z")


def directory():
    return Path.home() / ".claude/coordinator/worker-signals"


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def write(path, value):
    atomic_write(path, json.dumps(value))


def pane_path(pane):
    return directory() / ("pane-" + hashlib.sha256(pane.encode()).hexdigest() + ".json")


def question_path(identifier):
    if not ID.fullmatch(identifier):
        raise ValueError("invalid question id")
    return directory() / (identifier + ".json")


def answer_path(identifier):
    return question_path(identifier).with_suffix(".answer")


def worktree_of(path):
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            env={
                key: value
                for key, value in os.environ.items()
                if not key.startswith("GIT_")
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return os.path.realpath(result.stdout.strip()) if result.returncode == 0 else None


def identity(recover=False):
    from watchers import command

    pane, workspace = (
        os.environ.get("HERDR_PANE_ID"),
        os.environ.get("HERDR_WORKSPACE_ID"),
    )
    if not pane or not workspace:
        raise ValueError("HERDR_PANE_ID and HERDR_WORKSPACE_ID are required")
    work = discovery.open_work(command)
    panes = work["panes"]
    recovered_from = None
    if not any(row["pane"] == pane and row["workspace"] == workspace for row in panes):
        claimed_pane = next((row for row in panes if row["pane"] == pane), None)
        if claimed_pane is None and recover:
            cwd, worktree = os.getcwd(), worktree_of(os.getcwd())
            candidates = [
                row
                for row in panes
                if row.get("agent")
                and worktree
                and row.get("cwd")
                and worktree_of(row["cwd"]) == worktree
            ]
            shared_root = any(
                row.get("repo_root") and os.path.realpath(row["repo_root"]) == worktree
                for row in work["workspaces"]
            )
            if (
                len(candidates) == 1
                and candidates[0].get("agent") == "codex"
                and not shared_root
            ):
                recovered_from, pane, workspace = (
                    pane,
                    candidates[0]["pane"],
                    candidates[0]["workspace"],
                )
                print(
                    f"orch-signal: recovered stale pane {recovered_from!r} as {pane!r}",
                    file=sys.stderr,
                )
            else:
                root_detail = (
                    "; worktree is a workspace repo_root" if shared_root else ""
                )
                raise ValueError(
                    "caller pane does not belong to its workspace "
                    f"(pane={pane!r}, workspace={workspace!r}, "
                    f"socket={os.environ.get('HERDR_SOCKET_PATH', '<default>')!r}, "
                    f"panes={len(panes)}, cwd={cwd!r}, worktree={worktree!r}, "
                    f"worktree_matches={len(candidates)}{root_detail})"
                )
        else:
            location = (
                f"pane={pane!r} is in workspace={claimed_pane['workspace']!r}, "
                f"not workspace={workspace!r}"
                if claimed_pane
                else f"pane={pane!r} is not live; expected workspace={workspace!r}"
            )
            raise ValueError(
                "caller pane does not belong to its workspace "
                f"({location}; recovery={'disabled' if not recover else 'unavailable'}; "
                f"socket={os.environ.get('HERDR_SOCKET_PATH', '<default>')!r}, "
                f"panes={len(panes)})"
            )
    row = next((row for row in work["workspaces"] if row["id"] == workspace), {})
    return dict(
        pane=pane,
        workspace=workspace,
        owner=row.get("owner"),
        **({"recovered_from": recovered_from} if recovered_from else {}),
    )


def worker_identity(ident):
    if not ident["owner"] or not re.fullmatch(r"[A-Za-z0-9_-]+", ident["owner"]):
        raise ValueError("worker workspace has no valid owner token")
    return ident


def record_signal(ident, kind, new_job=False, **fields):
    worker_identity(ident)
    with answers_lock():
        path, now = pane_path(ident["pane"]), time.time()
        old = read(path)
        same = old and all(old.get(key) == ident[key] for key in ident)
        if same and not new_job:
            if old["kind"] == "done":
                if kind != "done":
                    return old
                if all(old.get(key) == value for key, value in fields.items()):
                    print(
                        "orch-signal: done unchanged; no new completion",
                        file=sys.stderr,
                    )
                    return old
                new_job = True
            if kind == "heartbeat" and now - old["at"] < HEARTBEAT_MIN_SECONDS:
                return old
        record = dict(
            ident,
            kind=kind,
            at=now,
            job=old["job"] if same and not new_job else uuid.uuid4().hex,
            **fields,
        )
        record["id"] = "signal-" + record["job"]
        pending = [
            event
            for event in (old.get("done_events", []) if same else [])
            if now - event["at"] <= DONE_EVENTS_KEEP_SECONDS
        ]
        if kind == "done":
            pending.append(dict(record))
        record["done_events"] = pending[-DONE_EVENTS_MAX:]
        write(path, record)
        return record


def remove_question(record, close_panel=True, close_timeout=CLEANUP_SECONDS):
    """Waiters defer Q closure; the workers poll retries failed closes with the record intact."""
    identifier = record["id"]
    panel = questions_directory() / (identifier + ".md")
    if panel.exists():
        if not close_panel:
            record["cleanup"] = True
            write(question_path(identifier), record)
            answer_path(identifier).unlink(missing_ok=True)
            panel.with_suffix(".remind").unlink(missing_ok=True)
            return False
        try:
            if open_user_question("close", identifier, timeout=close_timeout) != 0:
                return False
        except (OSError, subprocess.SubprocessError):
            return False
    question_path(identifier).unlink(missing_ok=True)
    answer_path(identifier).unlink(missing_ok=True)
    directory = questions_directory()
    for suffix in ("md", "remind", "answer", "answer.seen", "opening"):
        (directory / f"{identifier}.{suffix}").unlink(missing_ok=True)
    for path in directory.glob(f"{identifier}.*.answer.*"):
        path.unlink(missing_ok=True)
    return True


def asker_alive(record):
    pid = record.get("asker_pid")
    if pid is None:  # Records from before PID tracking keep their existing deadline.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def new_question(ident, question, options, timeout, deadline=None):
    worker_identity(ident)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number of seconds")
    if not question.strip():
        raise ValueError("question must not be empty")
    deadline = time.monotonic() + timeout if deadline is None else deadline
    try:
        with answers_lock(timeout=max(0, deadline - time.monotonic())):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            now = time.time()
            for path in directory().glob("signal-*.json"):
                old = read(path)
                if old["pane"] == ident["pane"]:
                    alive = asker_alive(old)
                    if old.get("cleanup") and not alive:
                        continue
                    if old.get("cleanup"):
                        raise ValueError(
                            "pane has a previous question awaiting panel cleanup"
                        )
                    if old["deadline"] > now and alive:
                        raise ValueError("pane already has a pending question")
                    if not remove_question(old, close_panel=False) and alive:
                        raise ValueError(
                            "pane has a previous question awaiting panel cleanup"
                        )
            if sum(1 for _ in directory().glob("signal-*.json")) >= QUESTIONS_MAX:
                raise ValueError(
                    f"worker question capacity reached ({QUESTIONS_MAX}); clear pending panel cleanup"
                )
            record = dict(
                ident,
                id="signal-" + uuid.uuid4().hex,
                kind="question",
                asker_pid=os.getpid(),
                question=question,
                options=options,
                at=now,
                deadline=now + remaining,
            )
            write(question_path(record["id"]), record)
            return record
    except TimeoutError:
        raise ValueError(
            f"question timed out after {timeout:g}s before creation"
        ) from None


def wait_answer(record, timeout, deadline=None):
    deadline = time.monotonic() + timeout if deadline is None else deadline
    try:
        while True:
            with answers_lock(timeout=max(0, deadline - time.monotonic())):
                path = answer_path(record["id"])
                if path.exists():
                    text = path.read_text().strip()
                    remove_question(record, close_panel=False)
                    return text
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    remove_question(record, close_panel=False)
                    break
            time.sleep(min(POLL_SECONDS, remaining))
    except TimeoutError:
        pass  # Leave cancellation to bounded retention cleanup when the lock becomes available.
    raise ValueError(f"question {record['id']} timed out after {timeout:g}s")


def open_user_question(identifier, path, timeout=None):
    from watchers import open_question

    return open_question(
        identifier, path, **({"timeout": timeout} if timeout is not None else {})
    )


def answer(identifier, text, to_user=False):
    caller = identity()
    with answers_lock():
        record = read(question_path(identifier))
        if not record or record.get("cleanup") or record["deadline"] <= time.time():
            raise ValueError("question is missing or expired")
        if caller["workspace"] != record["owner"]:
            raise ValueError("only the owner architect workspace may answer")
        if not asker_alive(record):
            raise ValueError("asker is gone")
        if answer_path(identifier).exists():
            raise ValueError("question already answered")
        if to_user:
            target = questions_directory() / (identifier + ".md")
            choices = "\n".join(
                f"{number}. " + choice.strip()
                for number, choice in enumerate(record["options"].split(","), 1)
                if choice.strip()
            )
            atomic_write(
                target,
                f"# Worker question {identifier}\n\n{record['question']}\n\n{choices}",
            )
            # Use the existing reminder retry mechanism if opening the Q panel fails.
            from question_remind import write_remind

            write_remind(target, time.time())
            if open_user_question(identifier, target) in (0, 73):
                target.with_suffix(".remind").unlink(missing_ok=True)
        else:
            if not text or not text.strip():
                raise ValueError("answer text is required")
            atomic_write(answer_path(identifier), text)


def bridge_user_answer(identifier, text):
    """Called by the answers watcher while holding its existing answers lock; idempotent on replay."""
    if not ID.fullmatch(identifier):
        return
    record = read(question_path(identifier))
    if (
        record
        and not record.get("cleanup")
        and record["deadline"] > time.time()
        and not answer_path(identifier).exists()
    ):
        if not asker_alive(record):
            if not record.get("user_answer_discarded"):
                record["user_answer_discarded"] = True
                write(question_path(identifier), record)
                return f"ANSWERS READFAIL answer discarded: {identifier}: {text} (asker is gone)"
            return
        atomic_write(answer_path(identifier), text)


def collect(rows, owners, now, snapshot_at=None):
    """Read and prune only after a successful pane AND owner snapshot; never infer closed from a failed read."""
    if not directory().exists():
        return {}, []
    live = (
        {row["pane_id"]: row.get("workspace_id") for row in rows}
        if rows is not None
        else None
    )
    snapshot_at = now if snapshot_at is None else snapshot_at
    signals, events = {}, []
    with answers_lock():
        saved = [(path, read(path)) for path in directory().glob("*.json")]
        saved.sort(
            key=lambda item: (
                item[1].get("cleanup_tried", 0) if item[1] else 0,
                item[0].name,
            )
        )
        cleanup_used = 0
        for path, record in saved:
            if not record:
                continue
            pane, workspace = record["pane"], record["workspace"]
            closed = (
                live is not None
                and owners is not None
                and record["at"] <= snapshot_at
                and (
                    live.get(pane) != workspace
                    or owners.get(workspace) != record["owner"]
                )
            )
            if record["kind"] == "question":
                answered = answer_path(record["id"]).exists()
                if (
                    record.get("cleanup")
                    or closed
                    or now > record["deadline"] + QUESTION_RETAIN_SECONDS
                ):
                    panel = questions_directory() / (record["id"] + ".md")
                    if panel.exists():
                        if cleanup_used >= CLEANUP_MAX:
                            remove_question(record, close_panel=False)
                            continue
                        cleanup_used += 1
                        record.update(cleanup=True, cleanup_tried=now)
                        write(path, record)
                    remove_question(record)
                elif not answered and now < record["deadline"]:
                    events.append(record)
            elif closed:
                events.extend(
                    event
                    for event in record.get("done_events", [])
                    if now - event["at"] <= DONE_EVENTS_KEEP_SECONDS
                )
                path.unlink(missing_ok=True)
            else:
                signals[pane] = record
                pending = [
                    event
                    for event in record.get("done_events", [])
                    if now - event["at"] <= DONE_EVENTS_KEEP_SECONDS
                ]
                if pending != record.get("done_events", []):
                    record["done_events"] = pending
                    write(path, record)
                events.extend(pending)
    return signals, events


def collect_live(command, now):
    """Read liveness only through the env adapter, with a cutoff taken before the snapshot starts."""
    snapshot_at = time.time()
    try:
        work = discovery.open_work(command)
        rows = [
            dict(pane_id=pane["pane"], workspace_id=pane["workspace"])
            for pane in work["panes"]
        ]
        owners = {row["id"]: row.get("owner") for row in work["workspaces"]}
    except discovery.Unavailable:
        rows, owners = None, None
    return collect(rows, owners, now, snapshot_at)


def suppressed(row, raw):
    record = raw.get("worker_signals", {}).get(row["pane_id"])
    if not record or record["workspace"] != row.get("workspace_id"):
        return False
    owners = {
        r["workspace_id"]: (r.get("tokens") or {}).get("owner")
        for r in (raw.get("workspaces") or {}).get("result", {}).get("workspaces", [])
    }
    if record["owner"] != owners.get(row.get("workspace_id")):
        return False
    return (
        record["kind"] == "done"
        and record.get("id") not in raw.get("resumed_done_ids", [])
    ) or (
        record["kind"] == "heartbeat"
        and 0 <= raw["observed_at"] - record["at"] <= HEARTBEAT_FRESH_SECONDS
    )


def event_line(record):
    fields = (
        ("outcome", "report", "summary")
        if record["kind"] == "done"
        else ("question", "options")
    )
    line = f"WORKER {record['kind'].upper()} {record['pane']} owner={record['owner']} id={record['id']} workspace={record['workspace']}"
    line += "".join(
        f" {key}=" + " ".join(str(record.get(key, "")).split()) for key in fields
    )
    return line + (
        f" recovered_from={record['recovered_from']}"
        if record.get("recovered_from")
        else ""
    )


def main():
    parser = argparse.ArgumentParser(prog="orch-signal")
    subs = parser.add_subparsers(dest="action", required=True)
    done = subs.add_parser("done")
    done.add_argument("--outcome", choices=("succeeded", "failed"), required=True)
    done.add_argument("--report", required=True)
    done.add_argument("--summary", required=True)
    heartbeat = subs.add_parser("heartbeat")
    heartbeat.add_argument("--new-job", action="store_true")
    ask = subs.add_parser("ask")
    ask.add_argument("--question", required=True)
    ask.add_argument("--options", default="")
    ask.add_argument(
        "--timeout", type=float, default=os.environ.get("ORCH_ASK_TIMEOUT_S", "1800")
    )
    ans = subs.add_parser("answer")
    ans.add_argument("id")
    ans.add_argument("text", nargs="?")
    ans.add_argument("--to-user", action="store_true")
    subs.add_parser("list")
    args = parser.parse_args()
    try:
        if args.action == "answer":
            answer(args.id, args.text, args.to_user)
        elif args.action == "ask":
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise ValueError("timeout must be a positive finite number of seconds")
            import watcher_runtime as runtime

            deadline = time.monotonic() + args.timeout
            record = None

            def interrupted(signum, frame):
                raise KeyboardInterrupt

            previous = {
                number: signal.signal(number, interrupted)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
            }
            try:
                try:
                    with runtime.deadline(args.timeout, "orch-signal ask", soft=True):
                        record = new_question(
                            identity(recover=True),
                            args.question,
                            args.options,
                            args.timeout,
                            deadline,
                        )
                        result = wait_answer(record, args.timeout, deadline)
                except TimeoutError:
                    raise ValueError(
                        f"question timed out after {args.timeout:g}s"
                    ) from None
                except KeyboardInterrupt:
                    # Ignore further interrupts while cancelling under the lock.
                    for number in previous:
                        signal.signal(number, signal.SIG_IGN)
                    if record:
                        with answers_lock():
                            current = read(question_path(record["id"]))
                            if current:
                                remove_question(current, close_panel=False)
                    raise ValueError("question cancelled") from None
            finally:
                for number, handler in previous.items():
                    signal.signal(number, handler)
            print(result)
        elif args.action == "list":
            caller = identity()
            with answers_lock():
                for path in sorted(directory().glob("signal-*.json")):
                    record = read(path)
                    if (
                        caller["workspace"] in (record["workspace"], record["owner"])
                        and not record.get("cleanup")
                        and record["deadline"] > time.time()
                        and not answer_path(record["id"]).exists()
                    ):
                        print(event_line(record))
        else:
            fields = vars(args).copy()
            fields.pop("action")
            record_signal(identity(recover=True), args.action, **fields)
        return 0
    except (OSError, ValueError, discovery.Unavailable) as error:
        print(f"orch-signal: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
