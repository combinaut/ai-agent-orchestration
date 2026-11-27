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
from question_lock import DENIED, answers_lock, questions_directory
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
IDENTITY_KEYS = ("pane", "workspace", "owner")
EXIT_UNWRITABLE = 73


def directory():
    return Path.home() / ".claude/coordinator/worker-signals"


def read_text(path):
    try:
        return path.read_text()
    except PermissionError as error:
        error.read_denied = True
        raise


def read(path):
    try:
        return json.loads(read_text(path))
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


def escalation_directory(error):
    """The questions or worker-signals directory a denied write or lock lies under, or that a denied mkdir was creating; None for anything else."""
    if error.errno not in DENIED or not error.filename:
        return None
    if getattr(error, "read_denied", False):
        return None
    path = Path(os.path.abspath(error.filename))
    for root in (questions_directory(), directory()):
        absolute = Path(os.path.abspath(root))
        if path == absolute or absolute in path.parents or path in absolute.parents:
            return root
    return None


def identity():
    from watchers import command

    work = discovery.open_work(command)
    try:
        caller = discovery.adapter().caller_pane(command, work["panes"])
    except TimeoutError:
        raise
    except discovery.READ_ERRORS as error:
        raise discovery.Unavailable(str(error)) from error
    pane, workspace = caller["pane"], caller["workspace"]
    inherited = os.environ.get("HERDR_PANE_ID")
    recovered_from = inherited if inherited and inherited != pane else None
    if recovered_from:
        print(
            f"orch-signal: verified caller pane {pane!r}; ignored inherited pane {inherited!r}",
            file=sys.stderr,
        )
    row = next((row for row in work["workspaces"] if row["id"] == workspace), {})
    return dict(
        pane=pane,
        workspace=workspace,
        owner=row.get("owner"),
        **({"agent": caller["agent"]} if caller.get("agent") else {}),
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
        same = old and all(old.get(key) == ident[key] for key in IDENTITY_KEYS)
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
    if panel.exists() or (not close_panel and record.get("resume_answer")):
        if not close_panel:
            record["cleanup"] = True
            write(question_path(identifier), record)
            answer_path(identifier).unlink(missing_ok=True)
            panel.with_suffix(".remind").unlink(missing_ok=True)
            return False
        return False
    question_path(identifier).unlink(missing_ok=True)
    answer_path(identifier).unlink(missing_ok=True)
    questions = questions_directory()
    for suffix in (
        "md",
        "remind",
        "asker-pane",
        "answer",
        "answer.seen",
        "opening",
    ):
        (questions / f"{identifier}.{suffix}").unlink(missing_ok=True)
    for path in questions.glob(f"{identifier}.*.answer.*"):
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
                    text = read_text(path).strip()
                    current = read(question_path(record["id"])) or record
                    remove_question(current, close_panel=False)
                    return text
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    current = read(question_path(record["id"]))
                    remove_question(record, close_panel=False)
                    break
            time.sleep(min(POLL_SECONDS, remaining))
    except TimeoutError:
        pass  # Leave cancellation to bounded retention cleanup when the lock becomes available.
    raise ValueError(f"question {record['id']} timed out after {timeout:g}s")


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
            raise ValueError(
                "ask the user in the architect's pane and answer with plain orch-signal answer"
            )
        if not text or not text.strip():
            raise ValueError("answer text is required")
        publish_answer(record, text)


def publish_answer(record, text):
    # Retain a receipt even if the CLI consumes its output before the model reads it.
    if record.get("agent") == "codex":
        record["resume_answer"] = text
        write(question_path(record["id"]), record)
    atomic_write(answer_path(record["id"]), text)


def collect(rows, owners, now, snapshot_at=None, resume=None):
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
                max(item[1].get("cleanup_tried", 0), item[1].get("resume_tried", 0))
                if item[1]
                else 0,
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
                pending_resume = record.get("resume_answer")
                if (
                    pending_resume
                    and (answered or record.get("cleanup"))
                    and not closed
                    and now < record["deadline"]
                ):
                    if resume is None or not resume(record):
                        continue
                    record.pop("resume_answer")
                    write(path, record)
                if (
                    record.get("cleanup")
                    or closed
                    or (now > record["deadline"] + QUESTION_RETAIN_SECONDS)
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
                else:
                    if not answered and now < record["deadline"]:
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
        work = {"panes": []}

    resume_used = False

    def resume(record):
        nonlocal resume_used
        pane = next((p for p in work["panes"] if p["pane"] == record["pane"]), {})
        if pane.get("agent") != "codex" or pane.get("status") not in ("idle", "done"):
            return bool(record.get("cleanup") and pane.get("status") == "working")
        if resume_used:
            return False
        resume_used = True
        record["resume_tried"] = now
        write(question_path(record["id"]), record)
        try:
            session = idle_codex_session(command, record)
            if not session:
                return False
            message = (
                f"Answer ready for {record['id']}. Read the running ask session's output "
                "with write_stdin and empty input, then continue. If you already read "
                "the answer, ignore this wake-up. If that session is no longer readable "
                "after a restart, use the recovery answer below instead.\n"
                f"Recovery answer: {record['resume_answer']}"
            )
            queue_wakeup(session, message)
            return True
        except discovery.READ_ERRORS as error:
            print(
                f"orch-signal: native wake-up failed for {record['id']}: {error}",
                file=sys.stderr,
            )
            return False

    result = collect(rows, owners, now, snapshot_at, resume)
    if rows is not None and owners is not None and not resume_used:
        import codex_subagents

        with answers_lock():
            codex_subagents.poll(
                work,
                directory() / "subagent-wakes",
                lambda pane: idle_codex_session(command, pane),
                queue_wakeup,
            )
    return result


def idle_codex_session(command, pane):
    agent = json.loads(
        command(
            discovery.adapter().herdr_bin(),
            "agent",
            "get",
            pane["pane"],
            timeout=2,
        )
    )["result"]["agent"]
    session = agent.get("agent_session") or {}
    if (
        agent.get("pane_id") == pane["pane"]
        and agent.get("workspace_id") == pane["workspace"]
        and agent.get("agent_status") in ("idle", "done")
        and session.get("agent") == "codex"
    ):
        return session.get("value")
    return None


def queue_wakeup(session, message):
    import watcher_runtime as runtime

    argv = ["codex", "queue", "--thread", session, "--message", message]
    result = runtime.run(argv, timeout=2)
    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, argv, stderr=result.stderr
        )


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


def event_key(record):
    return record["id"] + (":" + record["event"] if record.get("event") else "")


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
    ans.add_argument(
        "--to-user",
        action="store_true",
        help="unsupported for worker questions; ask in the architect's pane instead",
    )
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
                            identity(),
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
            record_signal(identity(), args.action, **fields)
        return 0
    except OSError as error:
        denied = escalation_directory(error)
        if denied is None:
            print(f"orch-signal: {error}", file=sys.stderr)
            return 1
        print(
            f"orch-signal: cannot write {denied} (sandbox?); run this command escalated",
            file=sys.stderr,
        )
        return EXIT_UNWRITABLE
    except (ValueError, discovery.Unavailable) as error:
        print(f"orch-signal: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
