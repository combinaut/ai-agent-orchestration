#!/usr/bin/env python3
"""agy PreToolUse hook: runs guard-bash.py on a run_command line or on the text
a manage_task send_input types into a running command.
A deny prints {"decision": "deny", "reason": ...}; a pass prints nothing: agy reads
"{}" as a deny. Errors fail open.
"""

import datetime
import json
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "guard-bash.py"
GUARD_TIMEOUT = 8  # seconds; the agy hook timeout is 10


def log_failure(exc, command=None):
    try:
        line = f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')} guard-bash-agy fail-open: {type(exc).__name__}: {exc}"
        if isinstance(command, str):
            line += f" command={command[:200]!r}"
        with (Path.home() / ".claude/hooks/guard-bash.log").open("a") as f:
            f.write(line.replace("\n", " ") + "\n")
    except Exception:
        pass


def main():
    command = None
    try:
        data = json.loads(sys.stdin.read())
        call = data["toolCall"]
        args = call["args"]
        if call.get("name") == "run_command":
            command = args["CommandLine"]
        elif call.get("name") == "manage_task" and args.get("Action") == "send_input":
            command = args["Input"]
        else:
            return 0
        cwd = data.get("cwd") or (data.get("workspacePaths") or [None])[0]
        if not isinstance(command, str) or not isinstance(cwd, str):
            raise ValueError("invalid hook input")
        result = subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps(
                {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}
            ),
            capture_output=True,
            text=True,
            timeout=GUARD_TIMEOUT,
        )
        if result.returncode == 2:
            print(json.dumps({"decision": "deny", "reason": result.stderr.strip()}))
        elif result.returncode != 0:
            raise RuntimeError(f"exit {result.returncode}: {result.stderr.strip()}")
    except Exception as exc:
        log_failure(exc, command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
