"""Closes a finished worker's pane for the calling architect and leaves one recovery line in its status file."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(
    0, str(ROOT)
)  # the script runs with -I, which leaves its own directory off the path

from watchers import architect_like  # noqa: E402

SECTION = "## Closed workers"
KEEP_LINES = 10
PROTECTED_ROLES = ("architect", "coordinator")


class Refused(Exception):
    """The pane is not one this caller may close."""


def herdr(*argv):
    binary = (
        os.environ.get("HERDR_BIN_PATH") or os.environ.get("ORCH_HERDR_BIN") or "herdr"
    )
    result = subprocess.run(
        [binary, *argv], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise Refused(
            f"herdr {' '.join(argv)} failed: {result.stderr.strip() or result.returncode}"
        )
    return json.loads(result.stdout)["result"]


def status_dir():
    return Path(
        os.environ.get("ORCH_USER_TODO_STATUS_DIR")
        or Path.home() / ".claude/coordinator/status"
    )


def one_line(text):
    return re.sub(r"\s+", " ", text).strip()


def branch_of(checkout):
    result = subprocess.run(
        ["git", "-C", checkout, "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result.stdout.strip()
        if result.returncode == 0 and result.stdout.strip()
        else "unknown"
    )


def facts(pane, caller_pane, caller_workspace):
    """The worker's pane, name, branch, worktree and session id; raises Refused for a pane this caller may not close."""
    caller = herdr("agent", "get", caller_pane)["agent"]
    if (caller.get("tokens") or {}).get("role") != "architect" or not caller.get(
        "name"
    ):
        raise Refused(f"the caller {caller_pane} is not a named architect pane")
    if (
        caller["name"] in (".", "..")
        or "/" in caller["name"]
        or "\\" in caller["name"]
        or "\0" in caller["name"]
    ):
        raise Refused(f"the caller name {caller['name']!r} is not a file name")
    if pane == caller_pane:
        raise Refused("an architect never closes its own pane")
    agent = herdr("agent", "get", pane)["agent"]
    role = (agent.get("tokens") or {}).get("role")
    if role in PROTECTED_ROLES or architect_like(agent):
        raise Refused(f"{pane} is {role or 'an architect'}, not a worker")
    workspace = herdr("workspace", "get", agent["workspace_id"])["workspace"]
    owner = (workspace.get("tokens") or {}).get("owner")
    if not owner or owner != caller_workspace:
        raise Refused(
            f"{pane} belongs to workspace owner {owner or 'none'}, not {caller_workspace}"
        )
    worktree = (
        (workspace.get("worktree") or {}).get("checkout_path")
        or agent.get("cwd")
        or "unknown"
    )
    return {
        "caller": caller["name"],
        "pane": pane,
        "name": agent.get("name") or "unnamed",
        "worktree": worktree,
        "branch": branch_of(worktree),
        "session": (agent.get("agent_session") or {}).get("value") or "unknown",
        "agent": (agent.get("agent_session") or {}).get("agent")
        or agent.get("agent")
        or "",
    }


RESUME = {"claude": "claude --resume", "codex": "codex resume"}


def recovery_line(found, task, report, now):
    resume = RESUME.get(found.get("agent") or "", "session")
    fields = {key: one_line(str(value)) for key, value in found.items()}
    return (
        f"- {now:%Y-%m-%d %H:%M %Z} {fields['pane']} ({fields['name']}): {one_line(task)}"
        f" | report {one_line(report) or 'none'} | branch {fields['branch']} | worktree {fields['worktree']}"
        f" | resume: {resume} {fields['session']}"
    )


def add_line(text, line):
    """`text` with `line` as the last line of the Closed workers section, which ends at the next line that starts
    with `#`. Only `- ` lines are entries; the oldest go when more than KEEP_LINES would remain. Every other line
    stays as it was."""
    lines = text.split("\n")
    if SECTION not in lines:
        head = (
            text
            if not text or text.endswith("\n\n")
            else text + ("\n" if text.endswith("\n") else "\n\n")
        )
        return f"{head}{SECTION}\n{line}\n"
    start = lines.index(SECTION) + 1
    end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines)
    )
    entries = [i for i in range(start, end) if lines[i].startswith("- ")]
    dropped = set(entries[: max(0, len(entries) + 1 - KEEP_LINES)])
    body = [text for i, text in enumerate(lines[start:end], start) if i not in dropped]
    last = max((i for i, text in enumerate(body) if text.strip()), default=-1)
    body.insert(last + 1, line)
    return "\n".join(lines[:start] + body + lines[end:])


def write_status(path, line):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    descriptor, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(descriptor, "w") as stream:
        stream.write(add_line(text, line))
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="close-worker.sh")
    parser.add_argument("pane")
    parser.add_argument("--task", required=True)
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)
    if "/" in args.pane or args.pane in (".", ".."):
        print(f"close-worker: not a pane id: {args.pane}", file=sys.stderr)
        return 2
    try:
        found = facts(
            args.pane,
            os.environ.get("HERDR_PANE_ID", ""),
            os.environ.get("HERDR_WORKSPACE_ID", ""),
        )
    except (Refused, KeyError, ValueError) as error:
        print(f"close-worker: refused: {error}", file=sys.stderr)
        return 3
    line = recovery_line(found, args.task, args.report, datetime.now().astimezone())
    if subprocess.run([str(ROOT / "close-pane.sh"), args.pane], check=False).returncode:
        print(
            f"close-worker: {args.pane} was not closed; no recovery line written",
            file=sys.stderr,
        )
        return 1
    try:
        write_status(status_dir() / f"{found['caller']}.md", line)
    except OSError as error:
        print(
            f"close-worker: {args.pane} closed, but the status file is unwritable ({error}); recovery line:\n{line}",
            file=sys.stderr,
        )
        return 4
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
