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

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load('pr_tabs_adapter_test', ROOT / 'env/herdr/pr_tabs.py')
host = load('pr_tabs_host_test', ROOT / 'chrome/pr-tabs/host/pr_tabs_host.py')


class PrTabsTest(IsolatedTestCase):
    def test_adapter_scopes_branch_to_repo_and_resolves_owner_label(self):
        workspaces = [
            {'workspace_id': 'a', 'label': 'Finder'},
            {'workspace_id': 'b', 'label': 'Agent::Tooling'},
            {'workspace_id': 'worker', 'tokens': {'owner': 'a'}, 'worktree': {'checkout_path': '/web'}},
            {'workspace_id': 'panes', 'tokens': {'owner': 'b'}},
            {'workspace_id': 'orphan', 'tokens': {'owner': 'gone'}},
        ]

        def run(args, **kwargs):
            self.assertLessEqual(kwargs['timeout'], 5)
            self.assertEqual(kwargs['env']['HERDR_SOCKET_PATH'], '/fixture/socket')
            if args[1:] == ['workspace', 'list']:
                return SimpleNamespace(stdout=json.dumps({'result': {'workspaces': workspaces}}))
            if args[1:3] == ['pane', 'list']:
                return SimpleNamespace(stdout=json.dumps({'result': {'panes': [{'cwd': '/docs'}]}}))
            if args[3] == 'symbolic-ref':
                return SimpleNamespace(stdout='feature\n')
            return SimpleNamespace(stdout='git@github.com:Acme/' + ('webapp.git' if args[2] == '/web' else 'docs.git'))

        with patch.object(adapter.subprocess, 'run', side_effect=run):
            self.assertEqual(adapter.branch_owners({'ORCH_HERDR_SOCKET': '/fixture/socket'}),
                             {'acme/webapp:feature': 'Finder', 'acme/docs:feature': 'Agent::Tooling'})

    def test_conflicting_owners_and_detached_checkouts_are_omitted(self):
        workspaces = [{'workspace_id': 'a', 'label': 'A'}, {'workspace_id': 'b', 'label': 'B'}] + [
            {'workspace_id': str(i), 'tokens': {'owner': owner}, 'worktree': {'checkout_path': path}}
            for i, (owner, path) in enumerate([('a', '/one'), ('b', '/two'), ('a', '/detached')])]

        def run(args, **kwargs):
            if args[1:] == ['workspace', 'list']:
                return SimpleNamespace(stdout=json.dumps({'result': {'workspaces': workspaces}}))
            if args[2] == '/detached':
                raise subprocess.CalledProcessError(1, args)
            return SimpleNamespace(stdout='feature' if args[3] == 'symbolic-ref' else 'https://github.com/acme/webapp.git')

        with patch.object(adapter.subprocess, 'run', side_effect=run):
            self.assertEqual(adapter.branch_owners({}), {})

    def test_host_cache_expires_and_missing_or_broken_adapter_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.write_text('[install]\nenv = "herdr"\n')
            environ = {'ORCH_CONFIG': str(config), 'PR_TABS_LOG': str(Path(directory) / 'host.log'), 'ORCH_ENV': 'herdr'}
            with patch.dict(os.environ, environ), patch.object(host.subprocess, 'run', return_value=SimpleNamespace(stdout='{}')) as run:
                # A malformed adapter response must not break states/search.
                self.assertEqual(host.ownership(), {})
                self.assertTrue(run.called)
            fake = SimpleNamespace(branch_owners=lambda _: {'acme/webapp:feature': 'Finder'})
            original = host.importlib.util.module_from_spec

            def module(spec):
                if spec.name == 'pr_tabs_adapter':
                    # Keep exec_module from replacing the fake function.
                    spec.loader.exec_module = lambda _: None
                    return fake
                return original(spec)

            with patch.dict(os.environ, environ), patch.object(host.importlib.util, 'module_from_spec', side_effect=module), patch.object(host.time, 'time', return_value=100):
                self.assertEqual(host.ownership(), {'acme/webapp:feature': 'Finder'})
                fake.branch_owners = lambda _: {'acme/webapp:feature': 'Tooling'}
                self.assertEqual(host.ownership(), {'acme/webapp:feature': 'Finder'})
                with patch.object(host.time, 'time', return_value=131):
                    self.assertEqual(host.ownership(), {'acme/webapp:feature': 'Tooling'})
                with patch.dict(os.environ, {'ORCH_ENV': 'none'}):
                    self.assertEqual(host.ownership(), {})
