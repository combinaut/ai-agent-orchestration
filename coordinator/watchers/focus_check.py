"""One FOCUS line: working workers, idle architects with open work, architects without a focus worker, heavy slots.

Worker owner comes from the workspace token tokens.owner. An architect whose status file has a `Paused: <reason>` line is left out."""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import discovery

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import github_poll
import orch_settings

PR_REPOS_MAX = 20
PR_LIMIT = 200
PAUSED = re.compile(r"^Paused:\s*\S")


def command(*argv):
    env = dict(os.environ, LC_ALL="C") if os.path.basename(argv[0]) == "git" else None

    def invoke(env):
        return subprocess.run(argv, capture_output=True, text=True, timeout=30, env=env)

    result = github_poll.run(argv, invoke) if argv[0] == "gh" else invoke(env)
    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, argv, output=result.stdout, stderr=result.stderr
        )
    return result.stdout


def status_dir():
    return Path(
        os.environ.get("ORCH_USER_TODO_STATUS_DIR")
        or Path.home() / ".claude/coordinator/status"
    )


def status_dir_absent():
    """True only when the status directory is known not to exist; a failed check is not absence."""
    try:
        os.stat(status_dir())
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


class StatusUnreadable(Exception):
    """A status file exists but cannot be read."""


def status_lines(title, name):
    for stem in (title, name):
        path = status_dir() / f"{stem}.md"
        try:
            return path.read_text().splitlines(), f"{stem}.md"
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            raise StatusUnreadable(f"{path}: {error}") from error
    return [], None


def user_todo():
    spec = importlib.util.spec_from_file_location(
        "user_todo", Path(__file__).resolve().parents[1] / "user-todo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paused(lines):
    fenced = False
    for line in lines:
        if line.strip().startswith("```"):
            fenced = not fenced
        elif not fenced and PAUSED.match(line):
            return True
    return False


class OpenPrs:
    """Open PR head branches per repo, read once per pass for at most PR_REPOS_MAX repos; a list at PR_LIMIT is unknown."""

    def __init__(self):
        self.branches, self.repos = {}, {}

    def repo(self, root):
        """owner/name of the clone's GitHub origin, None without one, False when it is gone or cannot be read."""
        if root not in self.repos:
            try:
                self.repos[root] = discovery.github_repo(command, root)
            except discovery.Unavailable:
                self.repos[root] = False
        return self.repos[root]

    def upstream(self, root):
        """owner/name of the clone's GitHub upstream, the base repo of a fork; None without one, False when it cannot be read."""
        if (root, "upstream") not in self.repos:
            try:
                self.repos[root, "upstream"] = (
                    discovery.github_repo(command, root, "upstream") or None
                )
            except discovery.Unavailable:
                self.repos[root, "upstream"] = False
        return self.repos[root, "upstream"]

    def has(self, repo, branch, owner=None):
        """True, False, or None when the repo's PRs cannot be read. A PR counts when its head repository belongs to owner,
        by default the repo's owner, so PRs from other forks do not count."""
        owner = (owner or repo.split("/")[0]).lower()
        repo = repo.lower()
        if repo not in self.branches:
            if len(self.branches) >= PR_REPOS_MAX:
                return None
            try:
                rows = json.loads(
                    command(
                        "gh",
                        "pr",
                        "list",
                        "-R",
                        repo,
                        "--state",
                        "open",
                        "--json",
                        "headRefName,headRepositoryOwner",
                        "--limit",
                        str(PR_LIMIT),
                    )
                )
                self.branches[repo] = (
                    None
                    if len(rows) >= PR_LIMIT
                    else {
                        (
                            row["headRepositoryOwner"]["login"].lower(),
                            row["headRefName"],
                        )
                        for row in rows
                        if row.get("headRepositoryOwner")
                    }
                )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                subprocess.SubprocessError,
            ):
                self.branches[repo] = None
        found = self.branches[repo]
        return None if found is None else (owner, branch) in found


def open_pr(workspaces, owner, prs):
    """True when a workspace the architect owns has a branch with an open PR, on its origin repo or on its upstream repo from
    the origin's owner; a gone clone or an unreadable origin, upstream, branch or lookup counts as one."""
    for row in workspaces:
        if row.get("owner") != owner:
            continue
        if row.get("gone"):
            return True
        if not row.get("checkout") or not row.get("repo_root"):
            continue
        repo = prs.repo(row["repo_root"])
        if repo is False:
            return True
        if not repo:
            continue
        try:
            branch = command(
                "git", "-C", row["checkout"], "branch", "--show-current"
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return True
        if not branch:
            continue
        if prs.has(repo, branch) is not False:
            return True
        base = prs.upstream(row["repo_root"])
        if base is False or (
            base and prs.has(base, branch, repo.split("/")[0]) is not False
        ):
            return True
    return False


def has_live_slot(name):
    slot_dir = os.environ.get("ORCH_HEAVY_SLOTS_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "heavy-slots"
    )
    prefix = name.lower().replace(":", "") + "-"
    try:
        files = os.listdir(slot_dir)
    except OSError:
        return False
    for filename in files:
        if not filename.startswith(prefix):
            continue
        path = os.path.join(slot_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as slot:
                pid = int(slot.read().split()[0])
            if pid <= 0:
                continue
        except (IndexError, OSError, ValueError):
            continue
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, OverflowError):
            continue
    return False


def line():
    try:
        work = discovery.open_work(command)
    except discovery.Unavailable as error:
        return f"FOCUS READFAIL open work: {error}"
    panes, workspaces = work["panes"], work["workspaces"]
    owners = {row["id"]: row.get("owner") for row in workspaces}
    labels = {row["id"]: row.get("label", "") for row in workspaces}
    arch, focus = {}, {}
    for pane in panes:
        title = pane.get("title") or ""
        if title.startswith("Architect::") and pane.get("role") == "architect":
            arch[pane["workspace"]] = (
                title,
                title.replace("Architect::", ""),
                pane.get("status"),
            )
            try:
                settings = (
                    orch_settings.load_repo(pane["cwd"])
                    if pane.get("cwd")
                    else orch_settings.merged([], os.environ)
                )
                focus[pane["workspace"]] = (
                    re.compile(settings["ORCH_FOCUS_PATTERN"], re.I)
                    if settings["ORCH_FOCUS_PATTERN"]
                    else None,
                    settings["ORCH_FOCUS_ARCHITECT"],
                )
            except (orch_settings.ConfigError, re.error) as error:
                return f"FOCUS READFAIL ORCH_FOCUS_PATTERN {error}"
    working, running, fwork = {}, {}, {}
    for pane in panes:
        if not pane.get("agent") or pane.get("role") in ("architect", "coordinator"):
            continue
        owner = owners.get(pane["workspace"])
        if not owner:
            continue
        running[owner] = running.get(owner, 0) + 1
        pattern = focus.get(owner, (None, ""))[0]
        if pattern and pattern.search(labels.get(pane["workspace"], "")):
            fwork[owner] = fwork.get(owner, 0) + 1
        if pane.get("status") == "working":
            working[owner] = working.get(owner, 0) + 1
    items, unreadable = user_todo().status_items(status_dir())
    unreadable = [
        path
        for path in unreadable
        if path != str(status_dir()) or not status_dir_absent()
    ]
    if unreadable:
        return f"FOCUS READFAIL status {', '.join(unreadable)}"
    waits = {item["source"] for item in items}
    live, idle, prs = {}, [], OpenPrs()
    for ws, (title, name, state) in arch.items():
        try:
            lines, source = status_lines(title, name)
        except StatusUnreadable as error:
            return f"FOCUS READFAIL status {error}"
        if paused(lines):
            continue
        live[ws] = name
        if working.get(ws, 0) or state == "working" or has_live_slot(name):
            continue
        if running.get(ws, 0) or source in waits or open_pr(workspaces, ws, prs):
            idle.append(name)
        else:
            del live[ws]
    nofocus = [
        name
        for ws, name in live.items()
        if focus[ws][0] and fwork.get(ws, 0) == 0 and name != focus[ws][1]
    ]
    try:
        listing = subprocess.run(
            ["heavy-slot", "list"], capture_output=True, text=True, timeout=30
        ).stdout
        slots = sum(row.endswith(" live") for row in listing.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        slots = "?"
    return (
        "FOCUS workers=%d focus_workers_live=%d slots=%s idle_no_worker=%s no_focus_worker=%s"
        % (
            sum(working.values()),
            sum(fwork.values()),
            slots,
            ",".join(idle) or "none",
            ",".join(nofocus) or "none",
        )
    )


if __name__ == "__main__":
    print(line(), flush=True)
