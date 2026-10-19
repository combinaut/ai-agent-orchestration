"""Ownership discovery and one-shot native host cache regressions."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from test_support import IsolatedTestCase
import discovery

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load('pr_tabs_host_test', ROOT / 'chrome/pr-tabs/host/pr_tabs_host.py')


class PrTabsTest(IsolatedTestCase):
    def test_adapter_scopes_branch_to_repo_and_resolves_owner_label(self):
        work = {'workspaces': [
            {'id': 'a', 'label': 'Finder'}, {'id': 'b', 'label': 'Agent::Tooling'},
            {'id': 'w1', 'owner': 'a', 'checkout': '/web'},
            {'id': 'w2', 'owner': 'b', 'checkout': '/docs'},
            {'id': 'orphan', 'owner': 'gone', 'checkout': '/orphan'},
        ]}
        def command(*args):
            return 'feature' if args[3] == 'symbolic-ref' else 'git@github.com:Acme/' + ('webapp.git' if args[2] == '/web' else 'docs.git')
        self.assertEqual(discovery.branch_owners(command, work),
                         {'acme/webapp:feature': 'Finder', 'acme/docs:feature': 'Agent::Tooling'})

    def test_conflicting_owners_and_detached_checkouts_are_omitted(self):
        work = {'workspaces': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}] + [
            {'id': str(i), 'owner': owner, 'checkout': path}
            for i, (owner, path) in enumerate([('a', '/one'), ('b', '/two'), ('a', '/detached')])]}
        def command(*args):
            if args[2] == '/detached':
                raise subprocess.CalledProcessError(1, args)
            return 'feature' if args[3] == 'symbolic-ref' else 'https://github.com/acme/webapp.git'
        self.assertEqual(discovery.branch_owners(command, work), {})

    def test_host_cache_expires_and_missing_or_broken_adapter_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.write_text('[install]\nenv = "herdr"\n')
            environ = {'ORCH_CONFIG': str(config), 'PR_TABS_LOG': str(Path(directory) / 'host.log'), 'ORCH_ENV': 'herdr'}
            with patch.dict(os.environ, environ), patch.object(host.subprocess, 'run', return_value=SimpleNamespace(stdout='{}')) as run:
                self.assertIsNone(host.ownership())
                self.assertTrue(run.called)
            good = {'acme/webapp:feature': 'Finder'}
            with patch.dict(os.environ, environ), patch.object(host, 'discover_owners', return_value=good) as discover, patch.object(host.time, 'time', return_value=100):
                self.assertEqual(host.ownership(), good)
                discover.return_value = {'acme/webapp:feature': 'Tooling'}
                self.assertEqual(host.ownership(), good)
                self.assertEqual(discover.call_count, 1)
                with patch.object(host.time, 'time', return_value=131):
                    self.assertEqual(host.ownership(), {'acme/webapp:feature': 'Tooling'})
                with patch.dict(os.environ, {'ORCH_ENV': 'none'}):
                    self.assertEqual(host.ownership(), {})

    def test_failed_discovery_is_cached_for_the_normal_ttl(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.write_text('[install]\nenv = "herdr"\n')
            environ = {'ORCH_CONFIG': str(config), 'PR_TABS_LOG': str(Path(directory) / 'host.log'), 'ORCH_ENV': 'herdr'}
            with patch.dict(os.environ, environ), patch.object(host.subprocess, 'run', side_effect=subprocess.TimeoutExpired('herdr', 5)) as run, patch.object(host.time, 'time', return_value=100):
                self.assertIsNone(host.ownership())
                self.assertIsNone(host.ownership())
                self.assertEqual(run.call_count, 1)
                with patch.object(host.time, 'time', return_value=131):
                    self.assertIsNone(host.ownership())
                    self.assertEqual(run.call_count, 2)


    def test_one_closed_workspace_does_not_discard_other_owners(self):
        work = {'workspaces': [{'id': 'a', 'label': 'Finder'}, {'id': 'null', 'owner': None},
                              {'id': 'closing', 'owner': 'a', 'checkout': '/closed'},
                              {'id': 'good', 'owner': 'a', 'checkout': '/web'}]}
        def command(*args):
            if args[2] == '/closed':
                raise subprocess.CalledProcessError(128, args, stderr="fatal: cannot change to '/closed': No such file or directory")
            return 'feature' if args[3] == 'symbolic-ref' else 'git@github.com:acme/webapp.git'
        self.assertEqual(discovery.branch_owners(command, work), {'acme/webapp:feature': 'Finder'})

    def test_branch_read_failure_preserves_last_good_ownership_protocol(self):
        work = {'workspaces': [{'id': 'a', 'label': 'Finder'},
                               {'id': 'w', 'owner': 'a', 'checkout': '/web'}]}
        good = {'acme/webapp:feature': 'Finder'}
        for detail in ('fatal: detected dubious ownership in repository at /web',
                       "fatal: cannot change to '/web': Permission denied"):
            with self.subTest(detail=detail), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / 'config.toml'
                config.write_text('[install]\nenv="herdr"\n')
                environ = {'ORCH_CONFIG': str(config), 'ORCH_ENV': 'herdr',
                           'PR_TABS_LOG': str(Path(directory) / 'host.log')}
                failing = False
                def run(args, **kwargs):
                    if args[3] == 'symbolic-ref':
                        if failing:
                            raise subprocess.CalledProcessError(128, args, stderr=detail)
                        return SimpleNamespace(stdout='feature')
                    return SimpleNamespace(stdout='https://github.com/acme/webapp.git')
                with patch.dict(os.environ, environ), patch.object(discovery, 'open_work', return_value=work), patch.object(host.subprocess, 'run', side_effect=run), patch.object(host.time, 'time', return_value=100):
                    self.assertEqual(host.ownership(), good)
                    failing = True
                    with patch.object(host.time, 'time', return_value=131):
                        self.assertIsNone(host.ownership(), 'failed scan must retain Chrome last-good ownership')
                        self.assertIsNone(host.ownership(), 'failure is cached for the normal TTL')
                    self.assertIsNone(json.loads((Path(directory) / 'owners.json').read_text())['owners'])

    def test_cache_reuses_one_file_across_config_edits_and_removes_legacy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / 'config.toml'
            legacy = base / ('owners-' + '0' * 20 + '.json')
            legacy.write_text('{}')
            unrelated = base / 'preferences.json'
            unrelated.write_text('keep')
            environ = {'ORCH_CONFIG': str(config), 'PR_TABS_LOG': str(base / 'host.log'), 'ORCH_ENV': 'herdr'}
            reply = SimpleNamespace(stdout=json.dumps({'result': {'workspaces': [], 'panes': []}}))
            with patch.dict(os.environ, environ), patch.object(host.subprocess, 'run', return_value=reply) as run:
                for org in ('acme', 'other'):
                    config.write_text('[pr_tabs]\norg = "' + org + '"\n')
                    self.assertEqual(host.ownership(), {})
                self.assertEqual(run.call_count, 4, 'each changed config takes a fresh shared workspace/pane snapshot')
            self.assertEqual(list(base.glob('owners*.json')), [base / 'owners.json'])
            self.assertEqual(unrelated.read_text(), 'keep')

    def test_github_origin_host_case_ports_and_exact_host_matching(self):
        work = {'workspaces': [{'id': 'a', 'label': 'Finder'}, {'id': 'w', 'owner': 'a', 'checkout': '/web'}]}
        accepted = (
            'git@github.com:acme/webapp.git',
            'https://GitHub.com/acme/webapp.git',
            'ssh://git@github.com:22/acme/webapp.git',
            'ssh://git@GitHub.COM:2222/acme/webapp.git',
            'https://github.com:443/acme/webapp.git',
            'https://GitHub.com:8443/acme/webapp.git/',
        )
        rejected = (
            'https://github.com.evil.example/acme/webapp.git',
            'ssh://git@notgithub.com:22/acme/webapp.git',
            'https://github.com@evil.example/acme/webapp.git',
            '/srv/github.com/acme/webapp.git',
            'git@github-work:acme/webapp.git',
        )
        for remote in accepted + rejected:
            def command(*args):
                return 'feature' if args[3] == 'symbolic-ref' else remote
            with self.subTest(remote=remote):
                expected = {'acme/webapp:feature': 'Finder'} if remote in accepted else {}
                self.assertEqual(discovery.branch_owners(command, work), expected)

    def test_host_uses_shared_open_work_discovery(self):
        work = {'workspaces': [
            {'id': 'a', 'label': 'Finder', 'owner': None, 'checkout': None},
            {'id': 'w', 'label': 'Worker', 'owner': 'a', 'checkout': '/web'},
        ], 'panes': []}
        def run(args, **kwargs):
            self.assertLessEqual(kwargs['timeout'], 5)
            self.assertEqual(kwargs['env']['HERDR_SOCKET_PATH'], '/fixture/socket')
            return SimpleNamespace(stdout='feature' if args[3] == 'symbolic-ref' else 'ssh://git@GitHub.com:22/acme/webapp.git')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.write_text('[install]\nenv="herdr"\n')
            environ = {'ORCH_CONFIG': str(config), 'ORCH_ENV': 'herdr', 'ORCH_HERDR_SOCKET': '/fixture/socket',
                       'PR_TABS_LOG': str(Path(directory) / 'host.log')}
            with patch.dict(os.environ, environ), patch.object(discovery, 'open_work', return_value=work) as read, patch.object(host.subprocess, 'run', side_effect=run):
                self.assertEqual(host.ownership(), {'acme/webapp:feature': 'Finder'})
                read.assert_called_once()


    def test_real_shared_snapshot_uses_bulk_panes_and_normalizes_checkout(self):
        calls = []
        workspaces = [{'workspace_id': 'a', 'label': 'Finder'}, {'workspace_id': 'null', 'tokens': None},
                      {'workspace_id': 'w', 'tokens': {'owner': 'a'}}]
        def run(args, **kwargs):
            calls.append(args)
            self.assertLessEqual(kwargs['timeout'], 5)
            self.assertEqual(kwargs['env']['HERDR_SOCKET_PATH'], '/fixture/socket')
            if args[1:] == ('workspace', 'list'):
                value = json.dumps({'result': {'workspaces': workspaces}})
            elif args[1:] == ('pane', 'list'):
                value = json.dumps({'result': {'panes': [{'workspace_id': 'w', 'cwd': '/docs/sub'}]}})
            elif args[3] == 'rev-parse':
                value = '/docs/.git' if '--git-common-dir' in args else '/docs'
            elif args[3] == 'symbolic-ref':
                value = 'feature'
            else:
                value = 'https://GitHub.com:443/acme/docs.git'
            return SimpleNamespace(stdout=value)
        before = {key: os.environ.get(key) for key in ('ORCH_ENV', 'ORCH_HERDR_BIN', 'HERDR_SOCKET_PATH')}
        with patch.object(host.subprocess, 'run', side_effect=run):
            self.assertEqual(host.discover_owners(ROOT, {'ORCH_ENV': 'herdr', 'ORCH_HERDR_SOCKET': '/fixture/socket'}),
                             {'acme/docs:feature': 'Finder'})
        self.assertEqual([args[1:] for args in calls if args[0] != 'git'], [('workspace', 'list'), ('pane', 'list')])
        self.assertEqual(before, {key: os.environ.get(key) for key in before})
