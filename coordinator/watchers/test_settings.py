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

from test_support import IsolatedTestCase, open_work
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
        path = self.write('c.toml', '[pr]\nauthor = "me"\n'
                                    '[recycle]\npark_idle = true\npct = 55.5\ntokens = 1000\n'
                                    '[watchers]\npath = ["~/bin", "/usr/bin"]\n'
                                    '[pr_tabs]\ntopics = [{ name = "W", color = "blue", repo = ["^w$"] }]\n')
        self.assertEqual(s.load_global(path), {
            'ORCH_PR_AUTHOR': 'me', 'ORCH_PARK_IDLE': '1', 'ORCH_RECYCLE_PCT': '55.5',
            'ORCH_RECYCLE_TOKENS': '1000', 'ORCH_PATH': f'{Path.home()}/bin:/usr/bin',
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
            '[watchers]\npath = "a/b"\n': 'watchers.path: must be a list of strings, got str',
            '[watchers]\npath = [1]\n': 'watchers.path: must be a list of strings, got list',
            '[pr]\nauthor = "a\\u0000b"\n': 'pr.author: contains a NUL character',
            '[watchers]\npath = ["/a:/b"]\n': 'watchers.path: entry "/a:/b" is empty or contains ":"',
            '[watchers]\npath = [""]\n': 'watchers.path: entry "" is empty',
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
        message = self.error('[pr]\nauthor = "me"\n', 'repo')
        self.assertIn('pr.author: is a global setting; set it in the global ~/.claude/orchestration.toml', message)

    def test_a_dropped_key_is_skipped_with_a_warning(self):
        stderr = io.StringIO()
        path = self.write('c.toml', '[pr]\nrepos = ["a/b"]\nauthor = "me"\n[audit]\ngit_watch_repos = []\n'
                                    '[focus]\nskip_architects = ["A"]\n')
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(s.load_global(path), {'ORCH_PR_AUTHOR': 'me'})
        for key in ('pr.repos', 'audit.git_watch_repos', 'focus.skip_architects'):
            self.assertIn(f'WARNING: {path}: {key}: not read', stderr.getvalue())

    def test_a_repo_list_entry_with_its_separator_is_an_error(self):
        message = self.error('[legacy]\nexempt = ["a b.js"]\n', 'repo')
        self.assertIn('legacy.exempt: entry "a b.js" is empty or contains whitespace', message)
        message = self.error('[audit]\nmerge_gate_checks = ["A|B"]\n', 'repo')
        self.assertIn('audit.merge_gate_checks: entry "A|B" is empty or contains "|"', message)

    def test_old_python_is_named(self):
        with patch.object(s, 'tomllib', None):
            with self.assertRaisesRegex(s.ConfigError, 'needs Python 3.11 or newer'):
                s.parse('', 'c.toml')


class SetKeyTest(SettingsTest):
    def test_set_key_edits_dotted_quoted_and_plain_forms_in_place(self):
        for text in ('# mine\npr.since = ""\n[herdr]\nbin = "x"\n', '# mine\n["pr"]\n"since" = ""\n[herdr]\nbin = "x"\n',
                     '# mine\n[pr]\nsince = ""\n[herdr]\nbin = "x"\n', '# mine\n"pr"."since" = ""\n[herdr]\nbin = "x"\n'):
            with self.subTest(text=text):
                path = self.write('c.toml', text)
                s.set_key(path, 'ORCH_PR_SINCE', '2026-10-01')
                s.set_key(path, 'ORCH_PR_AUTHOR', 'me')
                self.assertEqual(s.load_global(path), {'ORCH_PR_SINCE': '2026-10-01', 'ORCH_PR_AUTHOR': 'me', 'ORCH_HERDR_BIN': 'x'})
                self.assertTrue(path.read_text().startswith('# mine\n'))


class DropKeysTest(SettingsTest):
    def test_drop_removes_every_form_and_its_comment_and_keeps_the_rest(self):
        text = ('# mine\n[pr]\n# GitHub repos\nrepos = [\n  "a/b",  # first\n  "c/d",\n]\nauthor = "me"\n'
                '[audit]\n"git_watch_repos" = ["/x"]\npacks_max = 9\n'
                '[focus]\n# Architect names left out of focus reports\nskip_architects = []\n'
                '[herdr]\nbin = "x"  # kept\n')
        path = self.write('c.toml', text)
        self.assertEqual(s.drop_keys(path), ['pr.repos', 'audit.git_watch_repos', 'focus.skip_architects'])
        self.assertEqual(path.read_text(), '# mine\n[pr]\nauthor = "me"\n[audit]\npacks_max = 9\n[focus]\n'
                                           '[herdr]\nbin = "x"  # kept\n')
        self.assertEqual(s.drop_keys(path), [])

    def test_drop_handles_dotted_keys(self):
        path = self.write('c.toml', "pr.repos = ['a/b']\npr.author = 'me'\n")
        self.assertEqual(s.drop_keys(path), ['pr.repos'])
        self.assertEqual(path.read_text(), "pr.author = 'me'\n")

    def test_drop_of_the_only_dotted_key_of_a_table(self):
        path = self.write('c.toml', 'pr.author = "me"\nfocus.skip_architects = ["A"]\n')
        self.assertEqual(s.drop_keys(path), ['focus.skip_architects'])
        self.assertEqual(path.read_text(), 'pr.author = "me"\n')

    def test_drop_names_a_key_it_cannot_reach_instead_of_leaving_it(self):
        text = 'pr = { repos = ["acme/web"], author = "me" }\n'
        path = self.write('c.toml', text)
        with self.assertRaisesRegex(s.ConfigError, 'cannot drop pr.repos in place; edit it by hand'):
            s.drop_keys(path)
        self.assertEqual(path.read_text(), text)

    def test_drop_leaves_a_file_without_dropped_keys_untouched(self):
        path = self.write('c.toml', '[pr]\nauthor = "me"')
        self.assertEqual(s.drop_keys(path), [])
        self.assertEqual(path.read_text(), '[pr]\nauthor = "me"')

    def test_drop_refuses_an_edit_it_cannot_make_cleanly(self):
        for text in ("[pr]\nrepos = '''\nauthor = \"x\"\n'''\nauthor = \"me\"\n",
                     "[pr]\nrepos = ['''\n[x]\n''']\nauthor = \"me\"\n"):
            with self.subTest(text=text):
                path = self.write('c.toml', text)
                with self.assertRaisesRegex(s.ConfigError, 'cannot drop pr.repos in place'):
                    s.drop_keys(path)
                self.assertEqual(path.read_text(), text)


class RepoFileTest(SettingsTest):
    def test_no_repo_file_gives_the_built_in_defaults(self):
        root = self.repo()
        self.assertEqual(s.load_repo(root, {}), {setting.env: s.env_value(setting, setting.default)
                                                for setting in s.scoped('repo')})
        self.assertEqual(s.load_repo(self.base, {})['ORCH_BIG_REPO'], '0')

    def test_the_repo_file_is_found_from_a_subdirectory(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text('[guard]\nbig_repo = true\n[legacy]\nexempt = ["a", "b"]\n')
        (root / 'app/models').mkdir(parents=True)
        values = s.load_repo(root / 'app/models', {})
        self.assertEqual((values['ORCH_BIG_REPO'], values['ORCH_LEGACY_EXEMPT']), ('1', 'a b'))

    def test_the_local_file_overrides_the_committed_one_and_the_environment_wins(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text('[focus]\npattern = "shared"\narchitect = "A"\n[legacy]\nexempt = ["a", "b"]\n')
        (root / s.LOCAL_FILE).write_text('[focus]\npattern = "mine"\n[legacy]\nexempt = ["c"]\n')
        values = s.load_repo(root, {})
        self.assertEqual((values['ORCH_FOCUS_PATTERN'], values['ORCH_FOCUS_ARCHITECT'], values['ORCH_LEGACY_EXEMPT']),
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
        run, calls = self.command('[legacy]\nscan = true\nexempt = ["shared"]\n')
        with open_work():
            values = audit.repo_settings(run, 'acme/webapp', 'feature/base', {})
        self.assertEqual((values['ORCH_LEGACY_SCAN'], values['ORCH_LEGACY_EXEMPT']), ('1', 'shared'))
        self.assertEqual(calls[0][-1], 'repos/acme/webapp/contents/.orchestration.toml?ref=feature%2Fbase')

    def test_a_clone_of_the_same_name_on_another_host_is_not_the_github_clone(self):
        lab = self.repo('lab', 'git@gitlab.com:acme/webapp.git')
        hub = self.repo('hub', 'ssh://git@github.com:22/acme/webapp.git')
        self.assertEqual(s.known_clone('acme/webapp', [str(lab), str(hub)]), str(hub))
        self.assertIsNone(s.known_clone('acme/webapp', [str(lab)]))

    def test_an_unreadable_clone_origin_is_a_settings_read_error(self):
        clone = self.repo('clone')
        real = subprocess.run

        def run(args, *rest, **kwargs):
            if args[:4] == ['git', '-C', str(clone), 'remote']:
                return subprocess.CompletedProcess(args, 128, '', 'fatal: detected dubious ownership in repository')
            return real(args, *rest, **kwargs)
        command, _ = self.command('[legacy]\nscan = false\n')
        with open_work('', str(clone)), patch.object(s.subprocess, 'run', run):
            with self.assertRaises(subprocess.CalledProcessError):
                audit.repo_settings(command, 'acme/webapp', 'main', {})
            with self.assertRaises(subprocess.CalledProcessError):
                s.known_clone('acme/webapp', [str(clone)])

    def test_a_clone_without_origin_is_not_the_known_clone(self):
        bare = self.base / 'no-origin'
        bare.mkdir()
        git(bare, 'init', '-q')
        self.assertIsNone(s.known_clone('acme/webapp', [str(bare), str(self.base / 'gone')]))

    def test_the_local_file_of_a_known_clone_overrides_the_base_ref(self):
        clone = self.repo('clone')
        (clone / s.LOCAL_FILE).write_text('[legacy]\nexempt = ["mine"]\n')
        other = self.repo('other', 'https://github.com/acme/other.git')
        (other / s.LOCAL_FILE).write_text('[legacy]\nexempt = ["wrong-clone"]\n')
        run, _ = self.command('[legacy]\nscan = true\nexempt = ["shared"]\n')
        with open_work('', f'{other} {clone}'):
            values = audit.repo_settings(run, 'acme/webapp', 'main', {})
        self.assertEqual((values['ORCH_LEGACY_SCAN'], values['ORCH_LEGACY_EXEMPT']), ('1', 'mine'))

    def test_a_watched_linked_worktree_also_applies_its_main_checkout_file(self):
        main = self.repo('main')
        git(main, '-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'init')
        git(main, 'worktree', 'add', '-q', '-b', 'side', str(self.base / 'side'))
        (main / s.LOCAL_FILE).write_text('[legacy]\nscan = true\nexempt = ["main"]\n')
        (self.base / 'side' / s.LOCAL_FILE).write_text('[legacy]\nexempt = ["side"]\n')
        run, _ = self.command(None)
        with open_work('', str(self.base / 'side')):
            values = audit.repo_settings(run, 'acme/webapp', 'main', {})
        self.assertEqual((values['ORCH_LEGACY_SCAN'], values['ORCH_LEGACY_EXEMPT']), ('1', 'side'))

    def test_without_a_known_clone_only_the_committed_file_applies(self):
        run, _ = self.command(None)
        with open_work('', str(self.base / 'missing')):
            values = audit.repo_settings(run, 'acme/webapp', 'main', {})
        self.assertEqual(values['ORCH_LEGACY_SCAN'], '0')

    def test_a_broken_committed_file_is_a_read_error_naming_repo_and_ref(self):
        run, _ = self.command('[pr]\nauthor = "me"\n')
        with open_work():
            with self.assertRaisesRegex(ValueError, r'acme/webapp@main:\.orchestration\.toml: pr\.author: is a global setting'):
                audit.repo_settings(run, 'acme/webapp', 'main', {})

    def test_one_read_per_repo_and_ref(self):
        run, calls = self.command('')
        cache = {}
        with open_work():
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
            return json.dumps(dict(result=dict(workspaces=[], panes=[])))
        with patch.dict(os.environ, {'ORCH_PR_AUTHOR': 'author'}), open_work('owner/product'):
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
            return json.dumps(dict(result=dict(workspaces=[], panes=[])))
        with patch.dict(os.environ, {'ORCH_PR_AUTHOR': 'author'}), open_work('owner/product'):
            os.environ.pop('ORCH_MERGE_GATE_CHECKS', None)
            before, _ = w.reduce_pulls(None, w.collect_pulls(command, None))
            repo_file[0] = '[pr]\nauthor = "me"\n'
            broken, events = w.reduce_pulls(before, w.collect_pulls(command, before))
            self.assertEqual(events, ['PULLS READFAIL settings:owner/product#7'])
            repo_file[0] = '[audit]\nmerge_gate_checks = ["Full gate"]\n'
            _, events = w.reduce_pulls(broken, w.collect_pulls(command, broken))
            self.assertEqual(events, ['PULLS READFAIL cleared'])


def example_value(setting):
    return {'str': 'x', 'int': 7, 'number': 7, 'bool': True, 'list': ['x', 'y'], 'json': [{'name': 'x'}], 'tables': [{'name': 'x'}]}[setting.kind]


class LegacyUpgradeTest(SettingsTest):
    def test_rules_are_an_array_of_tables_passed_on_as_json(self):
        path = self.write('r.toml', '[legacy]\nscan = true\n[[legacy.rules]]\nkind = "A"\npaths = ["**"]\npattern = \'\\$\\(\'\n')
        self.assertEqual(s.as_env(s.read_file(path, 'repo'))['ORCH_LEGACY_RULES'], '[{"kind":"A","paths":["**"],"pattern":"\\\\$\\\\("}]')
        self.assertIn('legacy.rules: must be an array of tables', self.error('[legacy]\nrules = [1]\n', 'repo'))

    def test_removed_keys_name_the_conversion(self):
        self.assertIn('legacy.extra_pattern: was removed; run install.sh to convert it to [[legacy.rules]]',
                      self.error('[legacy]\nextra_pattern = "x"\n', 'repo'))
        self.assertIn('legacy.exempt_paths: was removed; run install.sh to convert it to legacy.exempt',
                      self.error('[legacy]\nexempt_paths = []\n', 'repo'))

    def test_upgrade_converts_the_removed_keys_and_keeps_every_other_line(self):
        text = ('# mine\n[guard]\nbig_repo = true\n\n[legacy]\nscan = true  # on\nextra_pattern = "uber-select"\n'
                'exempt_paths = [\n  "a.js",\n  "b.js",\n]\n\n[focus]\npattern = "x"\n')
        path = self.write('local.toml', text)
        report = s.upgrade(path)
        self.assertEqual(path.read_text(), '# mine\n[guard]\nbig_repo = true\n\n[legacy]\nscan = true  # on\n'
                         'exempt = [\n  "a.js",\n  "b.js",\n]\n\n[focus]\npattern = "x"\n\n'
                         '[[legacy.rules]]\nkind = "LEGACY"\npattern = "uber-select"\npaths = ["**"]\n')
        self.assertEqual(report, [f'converted {path}: legacy.extra_pattern -> [[legacy.rules]] (kind LEGACY, every path), '
                                  'legacy.exempt_paths -> legacy.exempt', f'{path}: {s.NO_BUILTIN_RULES}'])
        self.assertEqual(s.upgrade(path), [])

    def test_upgrade_of_dotted_keys_and_an_empty_pattern(self):
        path = self.write('local.toml', 'legacy.extra_pattern = ""\nlegacy.exempt_paths = ["a"]\n')
        self.assertEqual(s.upgrade(path), [f'converted {path}: legacy.extra_pattern -> [[legacy.rules]] (kind LEGACY, every path), '
                                           'legacy.exempt_paths -> legacy.exempt'])
        self.assertEqual(path.read_text(), 'legacy.exempt = ["a"]\n')
        path = self.write('only.toml', 'legacy.extra_pattern = ""\n[guard]\nbig_repo = true\n')
        self.assertEqual(s.upgrade(path), [f'converted {path}: legacy.extra_pattern -> [[legacy.rules]] (kind LEGACY, every path)'])
        self.assertEqual(path.read_text(), '[guard]\nbig_repo = true\n')
        for text in ('legacy.extra_pattern = ""\n', '[legacy]\nextra_pattern = ""\n', '[legacy]\nextra_pattern = ""\n[guard]\nbig_repo = true\n'):
            path = self.write('alone.toml', text)
            self.assertEqual(len(s.upgrade(path)), 1, text)
            self.assertNotIn('extra_pattern', path.read_text(), text)
            self.assertEqual(s.read_file(path, 'repo').get(s.BY_KEY['legacy.rules']), None, text)

    def test_configured_clones_read_the_dropped_key_as_written(self):
        path = self.write('g.toml', '[audit]\ngit_watch_repos = ["~/a", "/b", ""]\npacks_max = 9\n')
        self.assertEqual(s.configured_clones(path), [f'{Path.home()}/a', '/b'])
        for text in ('', '[audit]\npacks_max = 9\n', '[audit]\ngit_watch_repos = "x"\n', 'not toml ['):
            self.assertEqual(s.configured_clones(self.write('g.toml', text)), [], text)
        self.assertEqual(s.configured_clones(self.base / 'missing.toml'), [])

    def test_upgrade_targets_cover_clones_their_main_checkouts_and_files_once(self):
        main = self.repo('main')
        git(main, '-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'init')
        git(main, 'worktree', 'add', '-q', '-b', 'side', str(self.base / 'side'))
        side = self.base / 'side'
        (main / s.LOCAL_FILE).write_text('')
        (side / s.LOCAL_FILE).write_text('')
        snippet = self.write('snip.toml', '')
        stray = self.write('stray', 'not toml [')
        targets = s.upgrade_targets([str(snippet), str(snippet)], [str(side), str(main), str(self.base / 'gone'), str(stray)])
        self.assertEqual([p.resolve() for p in targets],
                         [snippet.resolve(), (side / s.LOCAL_FILE).resolve(), (main / s.LOCAL_FILE).resolve()])

    def test_upgrade_reads_quoted_table_headers(self):
        for header in ('["legacy"]', "['legacy']", '[ "legacy" ]'):
            path = self.write('local.toml', f'{header}\nscan = true\nexempt_paths = ["a"]\n')
            s.upgrade(path)
            self.assertEqual(path.read_text(), f'{header}\nscan = true\nexempt = ["a"]\n', header)

    def test_upgrade_keeps_rules_already_there(self):
        path = self.write('local.toml', '[legacy]\nextra_pattern = "b"\n\n[[legacy.rules]]\nkind = "A"\npaths = ["**"]\n')
        s.upgrade(path)
        self.assertEqual([rule['kind'] for rule in s.read_file(path, 'repo')[s.BY_KEY['legacy.rules']]], ['A', 'LEGACY'])

    def test_upgrade_refuses_what_it_cannot_convert_in_place(self):
        for text in ('[legacy]\nextra_pattern = """x"""\n', '[legacy]\nexempt_paths = ["a"]\nexempt = ["b"]\n',
                     '[legacy]\nextra_pattern = 1\n',
                     '[focus]\npattern = """\n[legacy]\nexempt_paths = "old-widget"\n"""\n[legacy]\nexempt_paths = ["a"]\n',
                     '[legacy]\nextra_pattern = \'(ab)\\1\'\n'):
            path = self.write('local.toml', text)
            with self.assertRaises(s.ConfigError, msg=text):
                s.upgrade(path)
            self.assertEqual(path.read_text(), text)

    def test_migration_from_the_env_file_writes_rules(self):
        values = {'ORCH_LEGACY_REPOS': 'acme/webapp', 'ORCH_LEGACY_EXTRA_PATTERN': 'old-widget', 'ORCH_LEGACY_EXEMPT_PATHS': 'a.js b.js'}
        report = []
        layer = s.repo_layers(values, report)['acme/webapp']
        self.assertEqual(layer, {s.BY_KEY['legacy.scan']: True,
                                 s.BY_KEY['legacy.rules']: [{'kind': 'LEGACY', 'pattern': 'old-widget', 'paths': ['**']}],
                                 s.BY_KEY['legacy.exempt']: ['a.js', 'b.js']})
        self.assertEqual(report, [f'acme/webapp: {s.NO_BUILTIN_RULES}'])
        report = []
        layer = s.repo_layers(dict(values, ORCH_LEGACY_EXTRA_PATTERN='(ab)\\1'), report)['acme/webapp']
        self.assertNotIn(s.BY_KEY['legacy.rules'], layer)
        self.assertTrue(report[0].startswith('not converted: ORCH_LEGACY_EXTRA_PATTERN for acme/webapp is not an rg regex ('), report)
        self.assertEqual(s.validate(s.parse(s.render(layer), 'x'), 'x', 'repo'), layer)


class DriftTest(SettingsTest):
    def test_coord_lib_keeps_every_schema_setting(self):
        text = (REPO / 'coordinator/watchers/coord-lib.sh').read_text()
        names = re.search(r'^ORCH_SETTINGS="([^"]*)"', text, re.M)[1].replace('\\\n', ' ').split()
        self.assertEqual(names, s.env_names())

    def test_the_examples_are_the_schema(self):
        self.assertEqual((REPO / 'config/orchestration.toml.example').read_text(), s.example('global'))
        repo = (REPO / 'config/repo.orchestration.toml.example').read_text()
        self.assertTrue(repo.startswith(s.example('repo') + '\n#'))
        self.assertTrue(all(line.startswith('#') or not line for line in repo[len(s.example('repo')):].splitlines()))
        self.assertEqual(s.read_file(REPO / 'config/repo.orchestration.toml.example', 'repo')[s.BY_KEY['legacy.scan']], False)

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
