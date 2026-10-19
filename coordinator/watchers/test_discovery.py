import contextlib
import io
import json
import os
import subprocess
from unittest.mock import patch

from test_support import IsolatedTestCase
import audit
import discovery

WORKSPACES = [
    dict(workspace_id='a', label='Architect', worktree=dict(checkout_path='/r/web', repo_root='/r/web')),
    dict(workspace_id='w1', label='fix', tokens=dict(owner='a'), worktree=dict(checkout_path='/wt/web/fix', repo_root='/r/web')),
    dict(workspace_id='w2', label='other', tokens=dict(owner='a'), worktree=dict(checkout_path='/r/WEB2', repo_root='/r/WEB2')),
    dict(workspace_id='w3', label='gitlab', worktree=dict(checkout_path='/r/lab', repo_root='/r/lab')),
    dict(workspace_id='w4', label='no worktree'),
    dict(workspace_id='w5', label='no checkout'),
]
PANES = [dict(workspace_id='w4', pane_id='w4:p1', cwd='/wt/tools/x/sub', agent='claude', agent_status='idle'),
         dict(workspace_id='w5', pane_id='w5:p1', cwd='/tmp')]
REMOTES = {'/r/web': 'git@github.com:acme/web.git', '/r/WEB2': 'https://github.com/ACME/Web', '/r/lab': 'git@gitlab.com:acme/lab.git',
           '/r/tools': 'https://github.com/acme/tools.git'}


def open_work_command(*argv):
    if argv[1:] == ('workspace', 'list'):
        return json.dumps(dict(result=dict(workspaces=WORKSPACES)))
    if argv[1:] == ('pane', 'list'):
        return json.dumps(dict(result=dict(panes=PANES)))
    if argv[0] == 'git' and argv[3:] == ('remote', 'get-url', 'origin'):
        return REMOTES[argv[2]] + '\n'
    if argv[0] == 'git' and argv[2] == '/wt/tools/x/sub' and argv[3:] == ('rev-parse', '--show-toplevel'):
        return '/wt/tools/x\n'
    if argv[0] == 'git' and argv[2] == '/wt/tools/x/sub' and argv[-1] == '--git-common-dir':
        return '/r/tools/.git\n'
    if argv[0] == 'git':
        raise subprocess.CalledProcessError(128, argv, stderr='not a git repository')
    raise AssertionError(argv)


class DiscoveryTest(IsolatedTestCase):
    def setUp(self):
        discovery.state_path().unlink(missing_ok=True)
        self.addCleanup(lambda: discovery.state_path().unlink(missing_ok=True))
        env = patch.dict(os.environ, {'ORCH_ENV': ''})
        env.start()
        self.addCleanup(env.stop)
        self.calls = []

    def command(self, *argv):
        self.calls.append(argv)
        return open_work_command(*argv)

    def broken(self, *argv):
        self.calls.append(argv)
        raise subprocess.CalledProcessError(1, argv, stderr='herdr: no server')

    def test_repos_and_clones_of_the_open_workspaces_are_deduplicated(self):
        found = discovery.repos(self.command)
        self.assertEqual(found, dict(clones=['/r/WEB2', '/r/lab', '/r/tools', '/r/web'], repos=['acme/tools', 'ACME/Web']))

    def test_a_workspace_without_a_worktree_takes_its_pane_checkout(self):
        rows = {row['id']: row for row in discovery.open_work(self.command)['workspaces']}
        self.assertEqual((rows['w4']['checkout'], rows['w4']['repo_root']), ('/wt/tools/x', '/r/tools'))
        self.assertEqual((rows['w5']['checkout'], rows['w5']['repo_root']), (None, None))
        self.assertEqual(rows['w1']['owner'], 'a')

    def test_an_unreadable_adapter_falls_back_to_the_saved_set_and_logs_it(self):
        found = discovery.repos(self.command)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(discovery.repos(self.broken), found)
        self.assertIn('open work unreadable (', stderr.getvalue())
        self.assertIn(f'using the repos last discovered in {discovery.state_path()}', stderr.getvalue())

    def test_an_unreadable_adapter_without_a_saved_set_is_unavailable(self):
        with self.assertRaisesRegex(discovery.Unavailable, 'no saved set'):
            discovery.repos(self.broken)

    def test_env_none_asks_no_adapter(self):
        with patch.dict(os.environ, {'ORCH_ENV': 'none'}):
            with self.assertRaisesRegex(discovery.Unavailable, 'install.env is "none"'):
                discovery.open_work(self.command)
        self.assertEqual(self.calls, [])

    def test_an_env_without_an_open_work_adapter_is_unavailable(self):
        with patch.dict(os.environ, {'ORCH_ENV': 'missing-env'}):
            with self.assertRaisesRegex(discovery.Unavailable, 'has no open_work.py'):
                discovery.open_work(self.command)


class AuditDiscoveryTest(IsolatedTestCase):
    def setUp(self):
        discovery.state_path().unlink(missing_ok=True)
        self.addCleanup(lambda: discovery.state_path().unlink(missing_ok=True))
        env = patch.dict(os.environ, {'ORCH_PR_AUTHOR': 'me', 'ORCH_ENV': '', 'ORCH_PR_SINCE': ''})
        env.start()
        self.addCleanup(env.stop)

    def test_the_search_names_each_discovered_repo(self):
        queries = []

        def command(*argv):
            if argv[:3] == ('gh', 'api', 'graphql'):
                queries.append(next(arg for arg in argv if arg.startswith('q=')))
                return json.dumps(dict(data=dict(search=dict(nodes=[], pageInfo=dict(hasNextPage=False)))))
            return open_work_command(*argv)
        self.assertEqual(audit.fetch_prs(command), [])
        self.assertEqual(queries, ['q=is:pr is:open author:me repo:acme/tools repo:ACME/Web'])

    def test_no_open_repos_means_no_search(self):
        def command(*argv):
            raise AssertionError(argv)
        self.assertEqual(audit.fetch_prs(command, repos=[]), [])

    def test_without_open_work_or_a_saved_set_github_and_known_git_sources_stay_unreadable(self):
        def command(*argv):
            if argv[1:] == ('workspace', 'list') and len(argv) == 3:
                raise subprocess.CalledProcessError(1, argv, stderr='herdr: no server')
            raise AssertionError(argv)
        before = dict(sources={'github': [], 'git:/r/web': ['PACKS web has over 50 packs'], 'herdr': []})
        with patch.object(audit, 'workspace_lines', lambda command: []), patch.object(audit, 'slot_sources', lambda slots: ({}, {})):
            raw = audit.collect(command, before=before)
        self.assertTrue(raw['unreadable']['github'].startswith('READFAIL github: open work unreadable'))
        self.assertTrue(raw['unreadable']['git:/r/web'].startswith('READFAIL git:/r/web: open work unreadable'))
        after, events = audit.reduce(before, raw)
        self.assertEqual(after['sources']['git:/r/web'], ['PACKS web has over 50 packs'])
        self.assertFalse([event for event in events if event.startswith('CLEARED')])
