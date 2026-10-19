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
GITHUB_REMOTE = re.compile(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")
READ_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, MissingMetric, subprocess.SubprocessError)
ADAPTERS = {}


class Unavailable(RuntimeError):
    """The env adapter cannot report open work."""


def state_path():
    return Path.home() / '.claude/coordinator/open-work.json'


def adapter():
    name = os.environ.get('ORCH_ENV') or 'herdr'
    if name == 'none':
        raise Unavailable('install.env is "none"')
    path = ROOT / 'env' / name / 'open_work.py'
    if not path.is_file():
        raise Unavailable(f'env adapter {name} has no open_work.py')
    if path not in ADAPTERS:
        spec = importlib.util.spec_from_file_location(f'env_{name}_open_work', path)
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


def github_repo(command, clone):
    """owner/name of the clone's GitHub origin, else None."""
    try:
        match = GITHUB_REMOTE.search(command('git', '-C', clone, 'remote', 'get-url', 'origin').strip())
    except READ_ERRORS:
        return None
    return match[1] if match else None


def save(found):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(found, stream)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def saved():
    try:
        found = json.loads(state_path().read_text())
        return dict(clones=list(found['clones']), repos=list(found['repos']))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def repos(command, work=None):
    """{'clones': local clone paths, 'repos': GitHub owner/name} of the open workspaces, deduplicated.

    Raises Unavailable when the adapter cannot answer and no earlier set is saved."""
    try:
        work = open_work(command) if work is None else work
    except Unavailable as error:
        found = saved()
        if found is None:
            raise Unavailable(f'open work unreadable ({error}) and no saved set at {state_path()}') from error
        print(f'open work unreadable ({error}); using the repos last discovered in {state_path()}', file=sys.stderr)
        return found
    clones = sorted({row['repo_root'] for row in work['workspaces'] if row.get('repo_root')})
    names = {}
    for clone in clones:
        repo = github_repo(command, clone)
        if repo:
            names.setdefault(repo.lower(), repo)
    found = dict(clones=clones, repos=sorted(names.values(), key=str.lower))
    try:
        save(found)
    except OSError as error:
        print(f'cannot save the discovered repos to {state_path()}: {error}', file=sys.stderr)
    return found


def main(argv):
    from watchers import command
    if argv[1:] not in (['clones'], ['repos']):
        print('usage: discovery.py clones|repos', file=sys.stderr)
        return 2
    try:
        found = repos(command)
    except Unavailable as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    print('\n'.join(found[argv[1]]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
