"""Optional PR-tabs runtime adapter. Never writes to Herdr or a checkout."""
import json
import os
import subprocess
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
from orch_settings import GITHUB_REMOTE  # noqa: E402


def branch_owners(settings):
    # Bound the whole scan, including git reads for every owned workspace.
    deadline = time.monotonic() + 5
    environ = dict(os.environ)
    if settings.get("ORCH_HERDR_SOCKET"):
        environ["HERDR_SOCKET_PATH"] = settings["ORCH_HERDR_SOCKET"]
    binary = os.environ.get("HERDR_BIN_PATH") or settings.get("ORCH_HERDR_BIN") or "herdr"

    def run(args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, 5)
        return subprocess.run(args, env=environ, capture_output=True, text=True,
                              timeout=remaining, check=True).stdout.strip()

    workspaces = json.loads(run([binary, "workspace", "list"]))["result"]["workspaces"]
    labels = {w["workspace_id"]: w.get("label") for w in workspaces}
    owners = {}
    ambiguous = set()
    for workspace in workspaces:
        label = labels.get((workspace.get("tokens") or {}).get("owner"))
        if not label:
            continue
        path = (workspace.get("worktree") or {}).get("checkout_path")
        try:
            paths = [path] if path else [p.get("foreground_cwd") or p.get("cwd") for p in
                    json.loads(run([binary, "pane", "list", "--workspace", workspace["workspace_id"]]))["result"]["panes"]]
        except (subprocess.CalledProcessError, ValueError, KeyError, TypeError):
            continue  # One workspace may have closed since workspace list.
        for path in set(paths) - {None, ""}:
            try:
                branch = run(["git", "-C", path, "symbolic-ref", "--quiet", "--short", "HEAD"])
                remote = run(["git", "-C", path, "remote", "get-url", "origin"])
            except subprocess.CalledProcessError:
                continue  # Missing checkout, detached HEAD, or no origin.
            match = GITHUB_REMOTE.fullmatch(remote)
            if not match:
                continue
            key = f"{match[1].lower()}:{branch}"
            if key in owners and owners[key] != label:
                ambiguous.add(key)
            owners[key] = label
    return {key: value for key, value in owners.items() if key not in ambiguous}
