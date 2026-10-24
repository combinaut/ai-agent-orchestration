"""Appends one usage line for a pane that is about to be closed on purpose; best effort, never blocks the close."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

COLUMNS = (
    "closed_at",
    "owner",
    "workspace_label",
    "agent_name",
    "pane",
    "session_id",
    "model",
    "cost_usd",
)


def herdr(*argv):
    binary = (
        os.environ.get("HERDR_BIN_PATH") or os.environ.get("ORCH_HERDR_BIN") or "herdr"
    )
    done = subprocess.run(
        [binary, *argv], capture_output=True, text=True, check=False, timeout=30
    )
    if done.returncode:
        raise RuntimeError(
            f"herdr {' '.join(argv)} failed: {done.stderr.strip() or done.returncode}"
        )
    return json.loads(done.stdout)["result"]


def ledger_path():
    return Path.home() / ".claude/coordinator/usage-ledger.tsv"


def cell(value):
    """A TSV cell: tabs and line breaks become spaces."""
    return " ".join(str("" if value is None else value).split())


def usage_of(session):
    """The .usage object of a session; empty when the file is missing or unreadable."""
    path = Path.home() / ".claude/context-usage" / f"{session}.usage"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(pane, now):
    """The ledger row for `pane`, or None when it has no agent."""
    agent = herdr("agent", "get", pane).get("agent") or {}
    session = (agent.get("agent_session") or {}).get("value")
    if not agent or not session:
        return None
    workspace = herdr("workspace", "get", agent["workspace_id"])["workspace"]
    usage = usage_of(session)
    return (
        now.isoformat(timespec="seconds"),
        (workspace.get("tokens") or {}).get("owner"),
        workspace.get("label"),
        agent.get("name"),
        pane,
        session,
        usage.get("model"),
        usage.get("cost_usd"),
    )


def append(row):
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "" if path.exists() and path.stat().st_size else "\t".join(COLUMNS) + "\n"
    with path.open("a") as out:
        out.write(header + "\t".join(cell(value) for value in row) + "\n")


def main(argv):
    try:
        row = record(argv[1], datetime.now().astimezone())
        if row:
            append(row)
    except Exception as error:  # best effort: the close must still run
        print(f"warning: usage ledger line not written: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
