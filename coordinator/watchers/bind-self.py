#!/usr/bin/env python3
"""Future owner action: run only from recipient Claude's own Bash environment.
No token is printed. Never discovers or borrows another session's credentials.
"""
import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

BINDINGS = Path.home() / ".claude/coordinator/watchers/bindings"
NAME = re.compile(r"(coordinator|architect-[A-Za-z0-9_-]+)\.json")

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
args.output = args.output.expanduser().resolve()
if args.output.parent != BINDINGS.resolve() or not NAME.fullmatch(args.output.name):
    sys.exit(f"bind-self: --output must be coordinator.json or architect-<id>.json in {BINDINGS}")
workspace = os.environ.get("HERDR_WORKSPACE_ID")
if workspace and args.output.name.startswith("architect-") and args.output.name != f"architect-{workspace}.json":
    sys.exit(f"bind-self: architect binding name must be architect-{workspace}.json (your HERDR_WORKSPACE_ID)")
path = os.environ["CLAUDE_CODE_MESSAGING_SOCKET"]
token = os.environ["CLAUDE_CODE_MESSAGING_TOKEN"]
pid = int(Path(path).stem)
record = json.loads((Path.home() / ".claude/sessions" / f"{pid}.json").read_text())
if record["messagingSocketPath"] != path:
    raise ValueError("recipient registry mismatch")
value = {"pid": pid, "socket": path, "token": token,
         "session_id": record["sessionId"], "proc_start": record["procStart"]}
try:
    info = args.output.lstat()
except FileNotFoundError:
    pass
else:
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("existing binding must be an owned regular mode-0600 file")
fd, temporary = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, args.output)
except BaseException:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
print("Binding created; token omitted. Owner must verify a round-trip after cutover.")
