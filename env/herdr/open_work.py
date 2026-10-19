"""Open work of the running Herdr session, for the watchers: workspaces with their repo and owner, and agent panes."""
import json
import os
import re
from pathlib import Path

NOT_A_CHECKOUT = re.compile(r'not a git repository \(or any (of the parent directories|parent up to mount point)', re.I)
GONE = re.compile(r'cannot change to .*: No such file or directory|not a git repository: |gitfile does not point to a valid repository', re.I)


def herdr_bin():
    """HERDR_BIN_PATH (set by the services), else the configured ORCH_HERDR_BIN, else herdr on PATH."""
    return os.environ.get('HERDR_BIN_PATH') or os.environ.get('ORCH_HERDR_BIN') or 'herdr'


def result(command, *args):
    return json.loads(command(herdr_bin(), *args))['result']


def checkout_of(command, path):
    """(checkout, main clone) of the git checkout containing path."""
    top = command('git', '-C', path, 'rev-parse', '--show-toplevel').strip()
    common = Path(command('git', '-C', path, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip())
    return top, str(common.parent) if common.name == '.git' else top


def open_work(command):
    """{'workspaces': [{id, label, owner, status, checkout, repo_root, gone}], 'panes': [{workspace, pane, title, role, agent, status, cwd}]}.

    A workspace Herdr reports without a worktree takes the checkout of its first pane's directory; none when git says it is
    no checkout, and none with gone=True when the directory is gone or its worktree lost its clone. Any other failure raises."""
    workspaces = result(command, 'workspace', 'list')['workspaces']
    panes = result(command, 'pane', 'list')['panes']
    if not isinstance(workspaces, list) or not isinstance(panes, list):
        raise ValueError('herdr workspace or pane list is not a list')
    first_cwd = {}
    for pane in panes:
        first_cwd.setdefault(pane.get('workspace_id'), pane.get('cwd'))
    rows = []
    for row in workspaces:
        tree = row.get('worktree') or {}
        checkout, root, gone = tree.get('checkout_path'), tree.get('repo_root'), False
        cwd = first_cwd.get(row['workspace_id'])
        if not root and cwd:
            try:
                checkout, root = checkout_of(command, cwd)
            except Exception as error:
                detail = str(getattr(error, 'stderr', None) or '')
                gone = bool(GONE.search(detail))
                if not gone and not NOT_A_CHECKOUT.search(detail):
                    raise
                checkout, root = None, None
        rows.append(dict(id=row['workspace_id'], label=row.get('label', ''), owner=(row.get('tokens') or {}).get('owner'),
                         status=row.get('agent_status'), checkout=checkout, repo_root=root, gone=gone))
    return dict(workspaces=rows, panes=[
        dict(workspace=pane.get('workspace_id'), pane=pane.get('pane_id'), title=pane.get('terminal_title_stripped', ''),
             role=(pane.get('tokens') or {}).get('role'), agent=pane.get('agent'), status=pane.get('agent_status'),
             cwd=pane.get('cwd')) for pane in panes])
