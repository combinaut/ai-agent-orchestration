#!/usr/bin/env python3
"""Print the agy --settings JSON of read_file/write_file rules for a worker whose folder is --cwd.

Read: the orch checkout, each --read, the global and XDG git config files and every file they include, the excludes, ignore and attributes files, the pane's git common dir.
"""

import argparse
import json
import os
import subprocess
import tempfile
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


def condition_holds(where, condition, folder):
    """Whether git follows an includeIf `condition` written in a config file of `folder`."""
    kind, _, pattern = condition.partition(":")
    if kind in ("gitdir", "gitdir/i") and pattern.startswith("./"):
        condition = f"{kind}:{os.path.realpath(folder)}/{pattern[2:]}"
    quoted = condition.replace("\\", "\\\\").replace('"', '\\"')
    with tempfile.TemporaryDirectory() as temp:
        marker, probe = os.path.join(temp, "marker"), os.path.join(temp, "probe")
        Path(marker).write_text("[orch]\n\tprobe = yes\n")
        Path(probe).write_text(f'[includeIf "{quoted}"]\n\tpath = "{marker}"\n')
        found = git(*where, "config", "--file", probe, "--includes", "orch.probe")
    return found.strip() == "yes"


def include_target(where, source, entry):
    """The file that `entry` of the config file `source` includes, also one that sets no entry, or None."""
    key, _, value = entry.partition("\n")
    folder = os.path.dirname(source)
    if key != "include.path":
        if not (key.startswith("includeif.") and key.endswith(".path")):
            return None
        if not condition_holds(where, key[len("includeif.") : -len(".path")], folder):
            return None
    target = os.path.join(folder, os.path.expanduser(value))
    return target if os.path.isfile(target) else None


def git_config_files(cwd):
    """The global and XDG git config files and their includes in `cwd`, their excludes files, git's ignore and attributes files."""
    where = ["-C", cwd] if os.path.isdir(cwd) else []
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    xdg_config = os.path.join(xdg, "git", "config")
    # git opens each of these when it exists, also one that sets no entry.
    files = [os.path.expanduser("~/.gitconfig"), xdg_config]
    if os.environ.get("GIT_CONFIG_GLOBAL"):
        files.append(os.environ["GIT_CONFIG_GLOBAL"])
    # --global leaves out the XDG config while ~/.gitconfig exists.
    for source in (["--global"], ["--file", xdg_config]):
        listed = git(
            *where, "config", *source, "-z", "--show-origin", "--list", "--includes"
        ).split("\0")
        for origin, entry in zip(listed[::2], listed[1::2]):
            if not origin.startswith("file:"):
                continue
            path = origin[len("file:") :]
            files.append(path)
            target = include_target(where, path, entry)
            if target:
                files.append(target)
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
