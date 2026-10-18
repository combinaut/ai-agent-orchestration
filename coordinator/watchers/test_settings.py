import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase
import audit
import watchers as w

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'lib'))
import orch_settings as s  # noqa: E402


def git(path, *args):
    subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True)


class SettingsTest(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def repo(self, name='repo', origin='git@github.com:acme/webapp.git'):
        path = self.base / name
        path.mkdir()
        git(path, 'init', '-q')
        git(path, 'remote', 'add', 'origin', origin)
        return path

    def error(self, text, scope='global'):
        with self.assertRaises(s.ConfigError) as caught:
            s.read_file(self.write('c.toml', text), scope)
        return str(caught.exception)


class GlobalFileTest(SettingsTest):
    def test_a_valid_file_gives_the_orch_strings_scripts_read(self):
        path = self.write('c.toml', '[pr]\nrepos = ["a/b", "c/d"]\nauthor = "me"\n'
                                    '[recycle]\npark_idle = true\npct = 55.5\ntokens = 1000\n'
                                    '[focus]\nskip_architects = ["A", "B"]\n'
                                    '[watchers]\npath = ["~/bin", "/usr/bin"]\n'
                                    '[pr_tabs]\ntopics = [{ name = "W", color = "blue", repo = ["^w$"] }]\n')
        self.assertEqual(s.load_global(path), {
            'ORCH_PR_REPOS': 'a/b c/d', 'ORCH_PR_AUTHOR': 'me', 'ORCH_PARK_IDLE': '1', 'ORCH_RECYCLE_PCT': '55.5',
            'ORCH_RECYCLE_TOKENS': '1000', 'ORCH_FOCUS_SKIP_ARCHITECTS': 'A,B', 'ORCH_PATH': f'{Path.home()}/bin:/usr/bin',
            'ORCH_PR_TABS_TOPICS': '[{"name":"W","color":"blue","repo":["^w$"]}]'})

    def test_a_missing_file_sets_nothing(self):
        self.assertEqual(s.load_global(self.base / 'missing.toml'), {})

    def test_each_problem_names_the_file_and_the_key(self):
        path = str(self.base / 'c.toml')
        cases = {
            '[pr\n': 'invalid TOML',
            '[pr]\nnope = 1\n': 'pr.nope: unknown key',
            '[nope]\nx = 1\n': 'nope.x: unknown key',
            'stray = 1\n': 'stray: unknown key',
            '[pr]\nauthor = 1\n': 'pr.author: must be a string, got int',
            '[heavy_slots]\nmax = "4"\n': 'heavy_slots.max: must be an integer, got str',
            '[heavy_slots]\nmax = true\n': 'heavy_slots.max: must be an integer, got bool',
            '[recycle]\npark_idle = 1\n': 'recycle.park_idle: must be true or false, got int',
            '[pr]\nrepos = "a/b"\n': 'pr.repos: must be a list of strings, got str',
            '[pr]\nrepos = [1]\n': 'pr.repos: must be a list of strings, got list',
            '[pr]\nauthor = "a\\u0000b"\n': 'pr.author: contains a NUL character',
            '[audit]\ngit_watch_repos = ["/tmp/project clone"]\n': 'audit.git_watch_repos: entry "/tmp/project clone" is empty or contains whitespace',
            '[focus]\nskip_architects = ["A,B"]\n': 'focus.skip_architects: entry "A,B" is empty or contains ","',
            '[watchers]\npath = ["/a:/b"]\n': 'watchers.path: entry "/a:/b" is empty or contains ":"',
            '[pr]\nrepos = [""]\n': 'pr.repos: entry "" is empty',
        }
        for text, problem in cases.items():
            with self.subTest(text=text):
                message = self.error(text)
                self.assertTrue(message.startswith(path + ': '), message)
                self.assertIn(problem, message)

    def test_a_repo_key_in_the_global_file_is_an_error(self):
        message = self.error('[guard]\nbig_repo = true\n')
        self.assertIn('guard.big_repo: is a repo setting', message)
        self.assertIn('.orchestration.toml', message)

    def test_a_global_key_in_a_repo_file_is_an_error(self):
        message = self.error('[pr]\nrepos = []\n', 'repo')
        self.assertIn('pr.repos: is a global setting; set it in the global ~/.claude/orchestration.toml', message)

    def test_a_repo_list_entry_with_its_separator_is_an_error(self):
        message = self.error('[legacy]\nexempt_paths = ["a b.js"]\n', 'repo')
        self.assertIn('legacy.exempt_paths: entry "a b.js" is empty or contains whitespace', message)
        message = self.error('[audit]\nmerge_gate_checks = ["A|B"]\n', 'repo')
        self.assertIn('audit.merge_gate_checks: entry "A|B" is empty or contains "|"', message)

    def test_old_python_is_named(self):
        with patch.object(s, 'tomllib', None):
            with self.assertRaisesRegex(s.ConfigError, 'needs Python 3.11 or newer'):
                s.parse('', 'c.toml')


class SetKeyTest(SettingsTest):
    def test_set_key_edits_dotted_quoted_and_plain_forms_in_place(self):
        for text in ('# mine\npr.repos = []\n[herdr]\nbin = "x"\n', '# mine\n["pr"]\n"repos" = []\n[herdr]\nbin = "x"\n',
                     '# mine\n[pr]\nrepos = []\n[herdr]\nbin = "x"\n', '# mine\n"pr"."repos" = []\n[herdr]\nbin = "x"\n'):
            with self.subTest(text=text):
                path = self.write('c.toml', text)
                s.set_key(path, 'ORCH_PR_REPOS', 'a/b c/d')
                s.set_key(path, 'ORCH_PR_AUTHOR', 'me')
                self.assertEqual(s.load_global(path), {'ORCH_PR_REPOS': 'a/b c/d', 'ORCH_PR_AUTHOR': 'me', 'ORCH_HERDR_BIN': 'x'})
                self.assertTrue(path.read_text().startswith('# mine\n'))


class RepoFileTest(SettingsTest):
    def test_no_repo_file_gives_the_built_in_defaults(self):
        root = self.repo()
        self.assertEqual(s.load_repo(root, {}), {setting.env: s.env_value(setting, setting.default)
                                                for setting in s.scoped('repo')})
        self.assertEqual(s.load_repo(self.base, {})['ORCH_BIG_REPO'], '0')

    def test_the_repo_file_is_found_from_a_subdirectory(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text('[guard]\nbig_repo = true\n[legacy]\nexempt_paths = ["a", "b"]\n')
        (root / 'app/models').mkdir(parents=True)
        values = s.load_repo(root / 'app/models', {})
        self.assertEqual((values['ORCH_BIG_REPO'], values['ORCH_LEGACY_EXEMPT_PATHS']), ('1', 'a b'))

    def test_the_local_file_overrides_the_committed_one_and_the_environment_wins(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text('[focus]\npattern = "shared"\narchitect = "A"\n[legacy]\nexempt_paths = ["a", "b"]\n')
        (root / s.LOCAL_FILE).write_text('[focus]\npattern = "mine"\n[legacy]\nexempt_paths = ["c"]\n')
        values = s.load_repo(root, {})
        self.assertEqual((values['ORCH_FOCUS_PATTERN'], values['ORCH_FOCUS_ARCHITECT'], values['ORCH_LEGACY_EXEMPT_PATHS']),
                         ('mine', 'A', 'c'))
        self.assertEqual(s.load_repo(root, {'ORCH_FOCUS_PATTERN': 'env'})['ORCH_FOCUS_PATTERN'], 'env')

    def test_an_exported_variable_wins_for_every_repo_setting(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text(s.render({setting: example_value(setting) for setting in s.scoped('repo')}))
        environ = {setting.env: f'env-{setting.env}' for setting in s.scoped('repo')}
        self.assertEqual(s.load_repo(root, environ), environ)

    def test_a_tracked_local_file_warns(self):
        root = self.repo()
        (root / s.LOCAL_FILE).write_text('[guard]\nbig_repo = true\n')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(s.load_repo(root, {})['ORCH_BIG_REPO'], '1')
        self.assertEqual(stderr.getvalue(), '')
        git(root, 'add', s.LOCAL_FILE)
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(s.load_repo(root, {})['ORCH_BIG_REPO'], '1')
        self.assertIn(f'WARNING: {root.resolve() / s.LOCAL_FILE} is tracked by git', stderr.getvalue())
        self.assertIn(f'rm --cached {s.LOCAL_FILE}', stderr.getvalue())

    def test_a_global_key_in_the_local_file_is_an_error(self):
        root = self.repo()
        (root / s.LOCAL_FILE).write_text('[heavy_slots]\nmax = 2\n')
        with self.assertRaisesRegex(s.ConfigError, 'heavy_slots.max: is a global setting'):
            s.load_repo(root, {})


class RemoteReadTest(SettingsTest):
    def command(self, committed):
        calls = []

        def run(*args):
            calls.append(args)
            if args[-1].startswith('repos/acme/webapp/contents/.orchestration.toml?ref='):
                if committed is None:
                    raise subprocess.CalledProcessError(1, args, stderr='gh: Not Found (HTTP 404)')
                return committed
            raise AssertionError(args)
        return run, calls

    def test_the_committed_file_comes_from_the_base_ref(self):
        run, calls = self.command('[legacy]\nscan = true\nextra_pattern = "shared"\n')
        with patch.dict(os.environ, {'ORCH_GIT_WATCH_REPOS': ''}):
            values = audit.repo_settings(run, 'acme/webapp', 'feature/base', {})
        self.assertEqual((values['ORCH_LEGACY_SCAN'], values['ORCH_LEGACY_EXTRA_PATTERN']), ('1', 'shared'))
        self.assertEqual(calls[0][-1], 'repos/acme/webapp/contents/.orchestration.toml?ref=feature%2Fbase')

    def test_the_local_file_of_a_known_clone_overrides_the_base_ref(self):
        clone = self.repo('clone')
        (clone / s.LOCAL_FILE).write_text('[legacy]\nextra_pattern = "mine"\n')
        other = self.repo('other', 'https://github.com/acme/other.git')
        (other / s.LOCAL_FILE).write_text('[legacy]\nextra_pattern = "wrong clone"\n')
        run, _ = self.command('[legacy]\nscan = true\nextra_pattern = "shared"\n')
        with patch.dict(os.environ, {'ORCH_GIT_WATCH_REPOS': f'{other} {clone}'}):
            values = audit.repo_settings(run, 'acme/webapp', 'main', {})
        self.assertEqual((values['ORCH_LEGACY_SCAN'], values['ORCH_LEGACY_EXTRA_PATTERN']), ('1', 'mine'))

    def test_without_a_known_clone_only_the_committed_file_applies(self):
        run, _ = self.command(None)
        with patch.dict(os.environ, {'ORCH_GIT_WATCH_REPOS': str(self.base / 'missing')}):
            values = audit.repo_settings(run, 'acme/webapp', 'main', {})
        self.assertEqual(values['ORCH_LEGACY_SCAN'], '0')

    def test_a_broken_committed_file_is_a_read_error_naming_repo_and_ref(self):
        run, _ = self.command('[pr]\nrepos = []\n')
        with patch.dict(os.environ, {'ORCH_GIT_WATCH_REPOS': ''}):
            with self.assertRaisesRegex(ValueError, r'acme/webapp@main:\.orchestration\.toml: pr\.repos: is a global setting'):
                audit.repo_settings(run, 'acme/webapp', 'main', {})

    def test_one_read_per_repo_and_ref(self):
        run, calls = self.command('')
        cache = {}
        with patch.dict(os.environ, {'ORCH_GIT_WATCH_REPOS': ''}):
            for _ in range(3):
                audit.repo_settings(run, 'acme/webapp', 'main', cache)
            audit.repo_settings(run, 'acme/webapp', 'next', cache)
        self.assertEqual(len(calls), 2)


class PullsSettingsTest(SettingsTest):
    def test_pr_events_read_merge_gate_checks_from_the_base_ref(self):
        pr = json.loads((REPO / 'coordinator/watchers/fixtures/audit-pr.json').read_text())
        pr.update(reviewDecision='APPROVED', baseRefName='feature/base')
        pr['latestOpinionatedReviews']['nodes'] = [dict(state='APPROVED', commit=dict(oid=pr['headRefOid']))]
        calls = []

        def command(*args):
            calls.append(args[-1])
            if '/contents/.orchestration.toml?' in args[-1]:
                return '[audit]\nmerge_gate_checks = ["Full gate"]\n'
            if args[:3] == ('gh', 'api', 'graphql'):
                return json.dumps(dict(data=dict(search=dict(nodes=[pr], pageInfo=dict(hasNextPage=False)))))
            return json.dumps(dict(result=dict(workspaces=[])))
        with patch.dict(os.environ, {'ORCH_PR_AUTHOR': 'author', 'ORCH_PR_REPOS': 'owner/product', 'ORCH_GIT_WATCH_REPOS': ''}):
            os.environ.pop('ORCH_MERGE_GATE_CHECKS', None)
            pulls = w.collect_pulls(command, None)
        line = next(iter(pulls['prs'].values()))['lines'].get('APPROVED-STACKED', '')
        self.assertIn('full CI not run', line)
        self.assertIn('repos/owner/product/contents/.orchestration.toml?ref=feature%2Fbase', calls)

    def test_a_broken_repo_file_is_a_pulls_readfail_until_it_recovers(self):
        pr = json.loads((REPO / 'coordinator/watchers/fixtures/audit-pr.json').read_text())
        pr.update(reviewDecision='APPROVED', baseRefName='feature/base')
        pr['latestOpinionatedReviews']['nodes'] = [dict(state='APPROVED', commit=dict(oid=pr['headRefOid']))]
        repo_file = ['[audit]\nmerge_gate_checks = ["Full gate"]\n']

        def command(*args):
            if '/contents/.orchestration.toml?' in args[-1]:
                return repo_file[0]
            if args[:3] == ('gh', 'api', 'graphql'):
                return json.dumps(dict(data=dict(search=dict(nodes=[pr], pageInfo=dict(hasNextPage=False)))))
            return json.dumps(dict(result=dict(workspaces=[])))
        with patch.dict(os.environ, {'ORCH_PR_AUTHOR': 'author', 'ORCH_PR_REPOS': 'owner/product', 'ORCH_GIT_WATCH_REPOS': ''}):
            os.environ.pop('ORCH_MERGE_GATE_CHECKS', None)
            before, _ = w.reduce_pulls(None, w.collect_pulls(command, None))
            repo_file[0] = '[pr]\nrepos = []\n'
            broken, events = w.reduce_pulls(before, w.collect_pulls(command, before))
            self.assertEqual(events, ['PULLS READFAIL settings:owner/product#7'])
            repo_file[0] = '[audit]\nmerge_gate_checks = ["Full gate"]\n'
            _, events = w.reduce_pulls(broken, w.collect_pulls(command, broken))
            self.assertEqual(events, ['PULLS READFAIL cleared'])


def example_value(setting):
    return {'str': 'x', 'int': 7, 'number': 7, 'bool': True, 'list': ['x', 'y'], 'json': [{'name': 'x'}]}[setting.kind]


class DriftTest(SettingsTest):
    def test_coord_lib_keeps_every_schema_setting(self):
        text = (REPO / 'coordinator/watchers/coord-lib.sh').read_text()
        names = re.search(r'^ORCH_SETTINGS="([^"]*)"', text, re.M)[1].replace('\\\n', ' ').split()
        self.assertEqual(names, s.env_names())

    def test_the_examples_are_the_schema(self):
        self.assertEqual((REPO / 'config/orchestration.toml.example').read_text(), s.example('global'))
        self.assertEqual((REPO / 'config/repo.orchestration.toml.example').read_text(), s.example('repo'))

    def test_the_settings_doc_lists_the_schema(self):
        doc = (REPO / 'docs/settings.md').read_text()
        self.assertIn('## Global settings\n\n' + s.markdown_table('global') + '\n\n## Repo settings\n\n'
                      + s.markdown_table('repo') + '\n', doc)

    def test_every_setting_has_one_scope_a_unique_name_and_a_valid_default(self):
        self.assertEqual(len(s.BY_KEY), len(s.SETTINGS))
        self.assertEqual(len(s.BY_ENV), len(s.SETTINGS))
        for setting in s.SETTINGS:
            with self.subTest(setting.key):
                self.assertIn(setting.scope, ('global', 'repo'))
                self.assertTrue(s.check_type(setting, setting.default))

    def test_written_values_read_back_unchanged(self):
        values = {setting: example_value(setting) for setting in s.scoped('global')}
        path = self.write('round.toml', s.render(values))
        self.assertEqual(s.read_file(path, 'global'), values)
