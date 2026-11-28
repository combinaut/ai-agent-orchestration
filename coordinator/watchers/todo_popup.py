"""Session-scoped ownership and replacement of one popup per process: the to-do view unless configure() names another."""

import fcntl
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

VIEW = Path(__file__).resolve().parent.parent / "user-todo-view.py"
PLUGIN = "combinaut.todo"
TOKEN_ENV = "ORCH_TODO_POPUP_TOKEN"
STEM = "todo-popup"
NAME = "to-do popup"


def configure(view, plugin, token_env, stem, name):
    global VIEW, PLUGIN, TOKEN_ENV, STEM, NAME
    VIEW, PLUGIN, TOKEN_ENV = Path(view).resolve(), plugin, token_env
    STEM, NAME = stem, name


def rpc(method, params):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(1)
        client.connect(os.environ["HERDR_SOCKET_PATH"])
        client.sendall(
            json.dumps({"id": STEM, "method": method, "params": params}).encode()
            + b"\n"
        )
        with client.makefile("rb") as stream:
            return json.loads(stream.readline(1024 * 1024))


def pidfile():
    session = hashlib.sha256(os.environ["HERDR_SOCKET_PATH"].encode()).hexdigest()[:16]
    directory = Path(
        os.environ.get("HERDR_PLUGIN_STATE_DIR", Path.home() / ".claude/coordinator")
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{STEM}-{session}.json"


def process_identity(pid):
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "lstart=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=1,
        )
        parts = result.stdout.strip().split(maxsplit=5)
        if len(parts) != 6:
            return None
        executable, _, arguments = parts[5].partition(" ")
        if not re.fullmatch(r"[Pp]ython(?:\d+(?:\.\d+)*)?", Path(executable).name):
            return None
        match = re.fullmatch(r"(.+?) ((?:--(?:popup|demo) ?)+)", arguments)
        if (
            not match
            or Path(match[1]).resolve() != VIEW
            or "--popup" not in match[2].split()
        ):
            return None
        return {"started": " ".join(parts[:5]), "command": parts[5]}
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_record(path):
    try:
        record = json.loads(path.read_text())
        if (
            isinstance(record, dict)
            and type(record.get("pid")) is int
            and record["pid"] > 1
            and record.get("socket") == os.environ.get("HERDR_SOCKET_PATH")
        ):
            return record
    except (OSError, ValueError):
        pass
    return None


def owns(record):
    return (
        record is not None
        and record.get("identity") is not None
        and process_identity(record["pid"]) == record["identity"]
    )


def replace(path):
    record = read_record(path)
    if not owns(record):
        return
    for number in (signal.SIGTERM, signal.SIGKILL):
        if not owns(record):
            return
        try:
            os.kill(record["pid"], number)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not owns(record):
                return
            time.sleep(0.05)
    raise RuntimeError(f"the earlier {NAME} did not exit")


def register():
    if not os.environ.get("HERDR_SOCKET_PATH"):
        return None
    path = pidfile()
    registered_at = time.time()
    identity = process_identity(os.getpid())
    record = {
        "pid": os.getpid(),
        "identity": identity,
        "socket": os.environ["HERDR_SOCKET_PATH"],
        "token": os.environ.get(TOKEN_ENV),
        "registered_at": registered_at,
    }
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def pane_exists(pane):
    try:
        reply = rpc("pane.get", {"pane_id": pane})
        return reply.get("error", {}).get("code") != "pane_not_found"
    except (OSError, ValueError, KeyError):
        return True


def registered_after(record, opened_at):
    if record.get("token") is not None:
        return False
    try:
        started = time.mktime(
            time.strptime(record["identity"]["started"], "%a %b %d %H:%M:%S %Y")
        )
        return record["registered_at"] >= opened_at and started >= int(opened_at)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def open_popup():
    path = pidfile()
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        replace(path)
        token = uuid.uuid4().hex
        command = [
            os.environ.get("HERDR_BIN_PATH", "herdr"),
            "plugin",
            "pane",
            "open",
            "--plugin",
            PLUGIN,
            "--entrypoint",
            "view",
            "--env",
            f"{TOKEN_ENV}={token}",
        ]
        opened_at = time.time()
        deadline = time.monotonic() + 3
        while True:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=5, check=False
            )
            try:
                error = json.loads(result.stdout or result.stderr or "{}").get(
                    "error", {}
                )
            except ValueError:
                error = {}
            message = (result.stdout + result.stderr).lower()
            if (
                (result.returncode or error)
                and "--env" in command
                and (
                    "env" in message
                    or re.search(
                        r"unknown[ _]+(?:parameter|field|option|argument)", message
                    )
                )
            ):
                command = command[:-2]
                continue
            if error.get("code") == "ui_busy" and time.monotonic() < deadline:
                time.sleep(0.05)
                continue
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            if result.returncode or error:
                return result.returncode or 1
            break
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = read_record(path)
            if record and (
                record.get("token") == token or registered_after(record, opened_at)
            ):
                return 0
            time.sleep(0.05)
        print(f"{NAME} did not register its process", file=sys.stderr)
        return 1
