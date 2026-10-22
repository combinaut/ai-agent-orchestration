"""Repos of the open work, read through the env adapter; the last discovered set when the adapter cannot answer."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from watcher_platform import MissingMetric

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import orch_settings  # noqa: E402

GITHUB_REMOTE = orch_settings.GITHUB_REMOTE
NO_REMOTE = re.compile(r"No such remote", re.I)
GONE = re.compile(r"cannot change to .*: No such file or directory", re.I)
READ_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    MissingMetric,
    subprocess.SubprocessError,
)
ADAPTERS = {}


class Unavailable(RuntimeError):
    """The env adapter cannot report open work."""


def state_path():
    return Path.home() / ".claude/coordinator/open-work.json"


def adapter():
    name = os.environ.get("ORCH_ENV") or "herdr"
    if name == "none":
        raise Unavailable('install.env is "none"')
    path = ROOT / "env" / name / "open_work.py"
    if not path.is_file():
        raise Unavailable(f"env adapter {name} has no open_work.py")
    if path not in ADAPTERS:
        spec = importlib.util.spec_from_file_location(f"env_{name}_open_work", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ADAPTERS[path] = module
    return ADAPTERS[path]


def open_work(command):
    """Workspaces and panes of the open work; raises Unavailable when the env adapter cannot report them."""
    try:
        return adapter().open_work(command)
    except READ_ERRORS as error:
        raise Unavailable(str(error)[:200]) from error


def github_repo(command, clone, remote="origin"):
    """owner/name of the clone's GitHub remote; None without one, False when the clone's directory is gone.

    Raises Unavailable when the remote cannot be read."""
    try:
        url = command("git", "-C", clone, "remote", "get-url", remote).strip()
    except subprocess.CalledProcessError as error:
        if NO_REMOTE.search(str(error.stderr or "")):
            return None
        if GONE.search(str(error.stderr or "")):
            return False
        raise Unavailable(
            f"{remote} of {clone} unreadable: {str(error.stderr or error).strip()[:200]}"
        ) from error
    except READ_ERRORS as error:
        raise Unavailable(
            f"{remote} of {clone} unreadable: {str(error)[:200]}"
        ) from error
    match = GITHUB_REMOTE.search(url)
    return match[1] if match else None


def branch_owners(command, work=None):
    """Architect labels for repo:branch, using the same open-work snapshot and origin parser as repo discovery."""
    work = open_work(command) if work is None else work
    labels = {row["id"]: row.get("label") for row in work["workspaces"]}
    owners, ambiguous = {}, set()
    for row in work["workspaces"]:
        label, checkout = labels.get(row.get("owner")), row.get("checkout")
        if not label or not checkout:
            continue
        try:
            branch = (
                command("git", "-C", checkout, "symbolic-ref", "--quiet", "HEAD")
                .strip()
                .removeprefix("refs/heads/")
            )
        except subprocess.CalledProcessError as error:
            if error.returncode == 1 or GONE.search(str(error.stderr or "")):
                continue  # Detached or confirmed closed checkout; other workspaces still contribute.
            raise Unavailable(
                f"branch of {checkout} unreadable: {str(error.stderr or error).strip()[:200]}"
            ) from error
        repo = github_repo(command, checkout)
        if not repo or not branch:
            continue
        key = f"{repo.lower()}:{branch}"
        if key in owners and owners[key] != label:
            ambiguous.add(key)
        owners[key] = label
    return {key: label for key, label in owners.items() if key not in ambiguous}


def upstream_repo(command, clone, failed):
    """owner/name of the clone's GitHub upstream, the base repo of a fork; None when it has none or cannot be read.

    An unreadable upstream is added to failed, so that it cannot hide the origin or the other clones."""
    try:
        return github_repo(command, clone, "upstream") or None
    except Unavailable as error:
        failed.append(str(error))
        return None


def discover(command, work, failed):
    """The repos of the open work, each once with its first spelling, origin before upstream (read only for a GitHub origin);
    failed lists unreadable upstreams."""
    clones, names = [], {}
    for clone in sorted(
        {row["repo_root"] for row in work["workspaces"] if row.get("repo_root")}
    ):
        repo = github_repo(command, clone)
        if repo is False:
            continue
        clones.append(clone)
        for name in (repo, upstream_repo(command, clone, failed) if repo else None):
            if name:
                names.setdefault(name.lower(), name)
    return dict(clones=clones, repos=sorted(names.values(), key=str.lower))


def save(found):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(found, stream)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def saved():
    try:
        found = json.loads(state_path().read_text())
        return dict(clones=list(found["clones"]), repos=list(found["repos"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def repos(command, work=None):
    """{'clones': local clone paths, 'repos': GitHub owner/name} of the open workspaces, deduplicated.

    work is the open work already read, or the Unavailable that reading it raised.

    Raises Unavailable when the adapter or an origin cannot be read and no earlier set is saved.
    An upstream that cannot be read adds the repos last saved to this pass and saves nothing, so no repo drops out."""
    failed = []
    try:
        if isinstance(work, Unavailable):
            raise work
        found = discover(command, open_work(command) if work is None else work, failed)
    except Unavailable as error:
        found = saved()
        if found is None:
            raise Unavailable(
                f"open work unreadable ({error}) and no saved set at {state_path()}"
            ) from error
        print(
            f"open work unreadable ({error}); using the repos last discovered in {state_path()}",
            file=sys.stderr,
        )
        return found
    if failed:
        print(
            f"{'; '.join(failed)}; adding the repos last discovered in {state_path()}",
            file=sys.stderr,
        )
        names = {name.lower(): name for name in (saved() or {}).get("repos", [])}
        names.update({name.lower(): name for name in found["repos"]})
        return {
            "clones": found["clones"],
            "repos": sorted(names.values(), key=str.lower),
        }
    try:
        save(found)
    except OSError as error:
        print(
            f"cannot save the discovered repos to {state_path()}: {error}",
            file=sys.stderr,
        )
    return found


def main(argv):
    from watchers import command

    if argv[1:] not in (["clones"], ["repos"]):
        print("usage: discovery.py clones|repos", file=sys.stderr)
        return 2
    try:
        found = repos(command)
    except Unavailable as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("\n".join(found[argv[1]]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
