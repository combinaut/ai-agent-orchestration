"""Open work of the running Herdr session, for the watchers: workspaces with their repo and owner, and agent panes."""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from herdr_identity import architect_labels  # noqa: E402

NOT_A_CHECKOUT = re.compile(
    r"not a git repository \(or any (of the parent directories|parent up to mount point)",
    re.I,
)
GONE = re.compile(
    r"cannot change to .*: No such file or directory|not a git repository: |gitfile does not point to a valid repository",
    re.I,
)


def herdr_bin():
    """HERDR_BIN_PATH (set by the services), else the configured ORCH_HERDR_BIN, else herdr on PATH."""
    return (
        os.environ.get("HERDR_BIN_PATH") or os.environ.get("ORCH_HERDR_BIN") or "herdr"
    )


def result(command, *args):
    return json.loads(command(herdr_bin(), *args))["result"]


def checkout_of(command, path, roots=True):
    """(checkout, main clone) of the git checkout containing path; main clone None when roots is false."""
    top = command("git", "-C", path, "rev-parse", "--show-toplevel").strip()
    if not roots:
        return top, None
    common = Path(
        command(
            "git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).strip()
    )
    return top, str(common.parent) if common.name == ".git" else top


def open_work(command, roots=True):
    """{'workspaces': [{id, label, owner, status, checkout, repo_root, gone}], 'panes': [{workspace, pane, title, role, agent, status, cwd}]}.

    A workspace Herdr reports without a worktree takes the checkout of its first pane's directory; none when git says it is
    no checkout, and none with gone=True when the directory is gone or its worktree lost its clone. Any other failure raises."""
    workspaces = result(command, "workspace", "list")["workspaces"]
    panes = result(command, "pane", "list")["panes"]
    if not isinstance(workspaces, list) or not isinstance(panes, list):
        raise ValueError("herdr workspace or pane list is not a list")
    first_cwd = {}
    for pane in panes:
        first_cwd.setdefault(pane.get("workspace_id"), pane.get("cwd"))
    rows, probed = [], {}
    for row in workspaces:
        tree = row.get("worktree") or {}
        checkout, root, gone = tree.get("checkout_path"), tree.get("repo_root"), False
        cwd = first_cwd.get(row["workspace_id"])
        if not root and cwd:
            if cwd not in probed:
                try:
                    probed[cwd] = (*checkout_of(command, cwd, roots), False)
                except Exception as error:
                    detail = str(getattr(error, "stderr", None) or "")
                    gone = bool(GONE.search(detail))
                    if not gone and not NOT_A_CHECKOUT.search(detail):
                        raise
                    probed[cwd] = (None, None, gone)
            checkout, root, gone = probed[cwd]
        rows.append(
            dict(
                id=row["workspace_id"],
                label=row.get("label", ""),
                owner=(row.get("tokens") or {}).get("owner"),
                status=row.get("agent_status"),
                checkout=checkout,
                repo_root=root,
                gone=gone,
            )
        )
    return dict(
        workspaces=rows,
        panes=[
            dict(
                workspace=pane.get("workspace_id"),
                pane=pane.get("pane_id"),
                title=pane.get("terminal_title_stripped", ""),
                role=(pane.get("tokens") or {}).get("role"),
                agent=pane.get("agent"),
                status=pane.get("agent_status"),
                cwd=pane.get("cwd"),
            )
            for pane in panes
        ],
    )


def architect_owners(command):
    """Saved architect names to workspace IDs; ambiguous names are omitted."""
    rows = result(command, "agent", "list")["agents"]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("agents is not a list of records")
    owners, ambiguous = {}, set()
    for row in rows:
        workspace = row.get("workspace_id")
        if not workspace:
            continue
        for label in architect_labels(row):
            if label in owners and owners[label] != workspace:
                ambiguous.add(label)
            owners[label] = workspace
    return {name: owner for name, owner in owners.items() if name not in ambiguous}
