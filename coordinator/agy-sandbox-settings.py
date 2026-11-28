#!/usr/bin/env python3
"""Print the agy --settings JSON of read_file/write_file rules for a worker whose folder is --cwd.

Read: the orch checkout, each --read, the XDG git config, the global git config files that set an entry, the excludes, ignore and attributes files, the pane's git common dir.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path


def git(*args):
    """Stdout of a git call, or "" when git fails or is missing."""
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def git_config_files(cwd):
    """The global and XDG git config files and their includes in `cwd`, their excludes files, git's ignore and attributes files."""
    where = ["-C", cwd] if os.path.isdir(cwd) else []
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    xdg_config = os.path.join(xdg, "git", "config")
    files = [xdg_config]
    # --global leaves out the XDG config while ~/.gitconfig exists.
    for source in (["--global"], ["--file", xdg_config]):
        for entry in git(
            *where, "config", *source, "-z", "--show-origin", "--list", "--includes"
        ).split("\0")[::2]:
            if entry.startswith("file:"):
                files.append(entry[len("file:") :])
        excludes = git(
            *where, "config", *source, "--includes", "--path", "core.excludesFile"
        ).strip()
        if excludes:
            files.append(excludes)
    files += [
        os.path.join(xdg, "git", "ignore"),
        os.path.join(xdg, "git", "attributes"),
    ]
    return files


def git_common_dir(cwd):
    """The git common dir of `cwd` (for a worktree: the main checkout's .git), or None."""
    path = git(
        "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ).strip()
    return path or None


def grant(rules, action, path):
    """Add action(path) for an existing path, and for its resolved target when that differs."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return
    for each in (path, os.path.realpath(path)):
        rule = f"{action}({each})"
        if rule not in rules:
            rules.append(rule)


def settings(cwd, reads, writes):
    """The settings object: permissions.allow holding read_file/write_file rules only."""
    rules = []
    orch_repo = Path(os.path.realpath(__file__)).parent.parent
    for path in [str(orch_repo), *reads, *git_config_files(cwd)]:
        grant(rules, "read_file", path)
    common = git_common_dir(cwd)
    if common:
        grant(rules, "read_file", common)
    for path in writes:
        grant(rules, "write_file", path)
    return {"permissions": {"allow": rules}}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--read", action="append", default=[])
    parser.add_argument("--write", action="append", default=[])
    args = parser.parse_args()
    print(
        json.dumps(
            settings(args.cwd, args.read, args.write),
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
