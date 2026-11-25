"""Open work of the running Herdr session, for the watchers: workspaces with their repo and owner, and agent panes."""

import json
import os
import re
import subprocess
from pathlib import Path

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


def architect_owners(command, ambiguous_out=None):
    """Saved architect names to workspace IDs; ambiguous names are omitted and added to `ambiguous_out` when given."""
    rows = result(command, "agent", "list")["agents"]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("agents is not a list of records")
    owners, ambiguous = {}, set()
    for row in rows:
        name, workspace = row.get("name"), row.get("workspace_id")
        role = (row.get("tokens") or {}).get("role")
        if (
            not workspace
            or not name
            or (role != "architect" and not name.startswith("Architect::"))
        ):
            continue
        for label in {name, row.get("terminal_title_stripped")} - {None, ""}:
            if label in owners and owners[label] != workspace:
                ambiguous.add(label)
            owners[label] = workspace
    if ambiguous_out is not None:
        ambiguous_out.update(ambiguous)
    return {name: owner for name, owner in owners.items() if name not in ambiguous}


def caller_pane(command, panes):
    """Verify the caller through live process ancestry; cached pane and session IDs can cross terminals."""
    try:
        text = command("ps", "-A", "-o", "pid=,ppid=", timeout=5)
    except TimeoutError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "cannot read caller process ancestry (sandbox?); run this command escalated"
        ) from error
    parents = {
        int(pid): int(parent)
        for pid, parent in (line.split() for line in text.splitlines())
    }
    ancestors, pid = set(), os.getpid()
    while pid and pid not in ancestors:
        ancestors.add(pid)
        pid = parents.get(pid, 0)

    hint = os.environ.get("HERDR_PANE_ID")
    for pane in sorted(panes, key=lambda row: row["pane"] != hint):
        try:
            info = result(command, "pane", "process-info", "--pane", pane["pane"])[
                "process_info"
            ]
        except subprocess.CalledProcessError:
            continue  # A pane can close after the topology snapshot.
        if info["pane_id"] != pane["pane"]:
            raise ValueError(
                "pane process identity changed during lookup; retry the command"
            )
        if any(process["pid"] in ancestors for process in info["foreground_processes"]):
            return pane
    raise ValueError(
        "cannot verify caller pane from live foreground processes; refusing inherited identity"
    )
