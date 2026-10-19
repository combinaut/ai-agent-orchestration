import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase
import legacy_rules
import orch_settings
import watchers as w

RULES = (w.ROOT / 'fixtures/legacy-rules.toml').read_text()


def rules(text):
    """Rules of a repo settings file, read the way the scan and the map read them."""
    return legacy_rules.from_settings(orch_settings.load_remote('owner/product', 'main', text, environ={}))


def rule(**fields):
    body = ''.join(f'{key} = {value}\n' for key, value in fields.items())
    return f'[[legacy.rules]]\n{body}'


class RulesTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_no_rules_is_none(self):
        self.assertIsNone(rules(''))
        self.assertIsNone(rules('[legacy]\nscan = true\nexempt = ["a"]\n'))
        self.assertIsNone(legacy_rules.from_settings({}))

    def test_kinds_follow_rule_order(self):
        self.assertEqual(rules(RULES).kinds, ('JSERB', 'JQUERY', 'LEGACY', 'TEST'))
        text = rule(kind='"B"', paths='["**"]') + rule(kind='"A"', paths='["**"]') + rule(kind='"B"', paths='["x"]')
        self.assertEqual(rules(text).kinds, ('B', 'A'))

    def test_fixture_rules_classify_like_the_jquery_conventions(self):
        parsed = rules(RULES)
        for text in ('$(', 'jQuery', '$.ajax', 'ajax:success', 'jquery_ujs', 'jquery-ujs', 'rails-ujs',
                     'data-remote', 'remote:true', 'remote:  true', '.trigger(', 'select2', 'legacy-select'):
            self.assertEqual(parsed.classify('app/a.rb', text), 'JQUERY', text)
        self.assertIsNone(parsed.classify('app/a.rb', 'plain'))
        self.assertEqual(parsed.classify('app/assets/javascripts/a.js', 'plain'), 'LEGACY')
        self.assertEqual(parsed.classify('app/assets/javascripts/a.js', '$(x)'), 'LEGACY')
        self.assertEqual(parsed.classify('app/assets/javascripts/spec/a.js', '$(x)'), 'TEST')
        self.assertEqual(parsed.classify('app/assets/javascripts/spec/a.js', 'plain'), 'LEGACY')
        self.assertEqual(parsed.classify('app/a.js.erb', 'plain'), 'JSERB')
        self.assertEqual(parsed.classify('spec/a.rb', 'jQuery'), 'TEST')
        for path in ('test/a.rb', 'app/spec/a.rb', 'a/b_spec.rb', 'a/b.test.js', 'a/b-spec.js', 'a/b.spec.foo.js'):
            self.assertEqual(parsed.classify(path, 'jQuery'), 'TEST', path)
        for path in ('app/models/contest.rb', 'app/assets/javascripts/testimonials.js', 'app/latest/a.rb'):
            self.assertNotEqual(parsed.classify(path, 'jQuery'), 'TEST', path)
        self.assertIsNone(parsed.classify('app/assets/javascripts/README.md', 'plain'))
        for path in ('bin/build.sh', 'bin/x.bash', 'docs/a.md', '.codex/hooks.json', 'app/b.yml'):
            self.assertIsNone(parsed.classify(path, 'value=$(command)'), path)
        self.assertIsNone(parsed.classify('spec/a.rb', 'plain'))
        self.assertIsNone(parsed.classify('.orchestration.toml', '$('))
        self.assertIsNone(parsed.classify('scripts/legacy-guard.test.mjs', '$('))

    def test_rule_without_pattern_counts_every_line(self):
        parsed = rules(rule(kind='"OLD"', paths='["src/old/**"]'))
        self.assertEqual(parsed.classify('src/old/a.js', ''), 'OLD')
        self.assertEqual(parsed.classify('src/old/deep/b.txt', 'anything'), 'OLD')
        self.assertIsNone(parsed.classify('src/new/a.js', 'anything'))

    def test_exclude_skips_paths_for_that_rule_only(self):
        parsed = rules(rule(kind='"A"', pattern="'x'", paths='["**/*.js"]', exclude='["vendor/**"]')
                       + rule(kind='"B"', paths='["vendor/**"]'))
        self.assertEqual(parsed.classify('app/a.js', 'x'), 'A')
        self.assertEqual(parsed.classify('vendor/a.js', 'x'), 'B')

    def test_exempt_paths_are_never_counted(self):
        parsed = rules('[legacy]\nexempt = ["tools/guard.mjs", "gen/**"]\n' + rule(kind='"A"', paths='["**"]'))
        self.assertIsNone(parsed.classify('tools/guard.mjs', 'x'))
        self.assertIsNone(parsed.classify('gen/a/b.js', 'x'))
        self.assertEqual(parsed.classify('tools/other.mjs', 'x'), 'A')

    def test_first_matching_rule_wins_per_line(self):
        parsed = rules(rule(kind='"FIRST"', pattern="'jQuery'", paths='["**"]')
                       + rule(kind='"SECOND"', pattern="'jQuery|\\$\\('", paths='["**"]'))
        self.assertEqual(parsed.classify('a.js', 'jQuery $('), 'FIRST')
        self.assertEqual(parsed.classify('a.js', '$('), 'SECOND')
        lines = [('a.js', 1, 'jQuery', True), ('a.js', 2, '$(x)', True), ('a.js', 3, '$(y)', False), ('a.js', 4, 'plain', True)]
        self.assertEqual(parsed.scan(lines), [('FIRST', 'a.js', 'jQuery'), ('SECOND', 'a.js', '$(x)')])

    def test_inline_flags_work_in_patterns(self):
        self.assertEqual(rules(rule(kind='"A"', pattern="'(?i)jquery'", paths='["**"]')).classify('a', 'JQuery'), 'A')
        self.assertIsNone(rules(rule(kind='"A"', pattern="'jquery'", paths='["**"]')).classify('a', 'JQuery'))

    def test_personal_rules_replace_the_committed_ones(self):
        root = self.base / 'clone'
        root.mkdir()
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        (root / orch_settings.LOCAL_FILE).write_text(rule(kind='"MINE"', paths='["**"]'))
        settings = orch_settings.load_remote('owner/product', 'main', rule(kind='"SHARED"', paths='["**"]'), str(root), environ={})
        self.assertEqual(legacy_rules.from_settings(settings).kinds, ('MINE',))

    def test_fingerprint_follows_rules_and_exempt(self):
        base = rule(kind='"A"', paths='["**"]')
        prints = {rules(text).fingerprint for text in (base, base, rule(kind='"B"', paths='["**"]'), '[legacy]\nexempt = ["x"]\n' + base)}
        self.assertEqual(len(prints), 3)

    def test_each_validation_error_names_the_rule_and_problem(self):
        cases = {
            "legacy.rules[1] (A): unknown key 'path'": rule(kind='"A"', path='["**"]'),
            "legacy.rules[1] (A): unknown key 'flags'": rule(kind='"A"', paths='["**"]', flags='"i"'),
            'legacy.rules[1]: kind must be a word': rule(kind='"two words"', paths='["**"]'),
            'legacy.rules[1]: kind must be a word ': rule(kind='1', paths='["**"]'),
            'legacy.rules[1] (A): paths must be a non-empty list of non-empty strings': rule(kind='"A"'),
            'legacy.rules[1] (A): paths must be a non-empty list of non-empty strings ': rule(kind='"A"', paths='[]'),
            'legacy.rules[1] (A): exclude must be a list of non-empty strings': rule(kind='"A"', paths='["**"]', exclude='[1]'),
            'legacy.rules[1] (A): pattern must be a non-empty string': rule(kind='"A"', paths='["**"]', pattern='1'),
            'legacy.rules[1] (A): pattern is not a valid rg regex': rule(kind='"A"', paths='["**"]', pattern="'('"),
            "legacy.rules[1] (A): paths: bad brace group in '{a'": rule(kind='"A"', paths='["{a"]'),
            "legacy.exempt: bad brace group in '{a'": '[legacy]\nexempt = ["{a"]\n' + rule(kind='"A"', paths='["**"]'),
        }
        for message, text in cases.items():
            with self.assertRaises(ValueError, msg=message) as caught:
                rules(text)
            self.assertIn(message.strip(), str(caught.exception))
        with self.assertRaisesRegex(ValueError, r'legacy\.rules\[2\] \(B\): pattern is not a valid rg regex'):
            rules(rule(kind='"A"', paths='["**"]') + rule(kind='"B"', paths='["**"]', pattern='"["'))

    def test_settings_reject_rules_that_are_not_tables(self):
        with self.assertRaisesRegex(orch_settings.ConfigError, 'legacy.rules: must be an array of tables'):
            rules('[legacy]\nrules = ["x"]\n')

    def test_exported_rules_must_be_json(self):
        with self.assertRaisesRegex(ValueError, 'ORCH_LEGACY_RULES is not JSON'):
            legacy_rules.from_settings({'ORCH_LEGACY_RULES': '[{'})
        self.assertEqual(legacy_rules.from_settings(
            {'ORCH_LEGACY_RULES': '[{"kind":"A","paths":["**"]}]', 'ORCH_LEGACY_EXEMPT': 'x y'}).classify('y', ''), None)

    def test_patterns_are_rg_regexes(self):
        for pattern in ('(?=a)b', '(a)\\1', '('):
            with self.assertRaisesRegex(ValueError, 'pattern is not a valid rg regex', msg=pattern):
                legacy_rules.compile_pattern(pattern)
        for pattern in ('(a+)+$', 'a+b+', '(a|b)*', 'ajax:(success|error)', 'remote: *true', '\\$\\(', '(?i)jquery'):
            self.assertEqual(legacy_rules.compile_pattern(pattern), pattern)

    def test_a_backtracking_pattern_runs_in_bounded_time(self):
        parsed = rules(rule(kind='"A"', pattern="'a*a*a*a*a*a*b'", paths='["**"]') + rule(kind='"B"', pattern="'(a+)+$'", paths='["**"]'))
        start = time.monotonic()
        self.assertIsNone(parsed.classify('x.txt', 'a' * 1500 + '!'))
        self.assertEqual(parsed.classify('x.txt', 'a' * 5000 + '!'), 'B')
        self.assertLess(time.monotonic() - start, 5)

    def test_missing_rg_is_named(self):
        with patch.object(legacy_rules.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, r'legacy rules need rg \(ripgrep\) on PATH'):
                rules(rule(kind='"A"', pattern="'x'", paths='["**"]'))

    def test_a_failing_rg_fails_the_scan(self):
        parsed = rules(rule(kind='"A"', pattern="'x'", paths='["**"]'))
        fake = self.base / 'rg'
        fake.write_text('#!/bin/sh\ncat >/dev/null\necho boom >&2\nexit 2\n')
        fake.chmod(0o755)
        with patch.object(legacy_rules, 'rg', return_value=str(fake)):
            with self.assertRaisesRegex(ValueError, 'rg failed with status 2: boom'):
                parsed.classify('a', 'x')

    def test_too_many_rules_are_an_error(self):
        text = rule(kind='"A"', paths='["**"]') * (legacy_rules.RULES_MAX + 1)
        with self.assertRaisesRegex(ValueError, f'legacy.rules has {legacy_rules.RULES_MAX + 1} rules; at most {legacy_rules.RULES_MAX}'):
            rules(text)

    def test_the_map_ends_promptly_on_a_backtracking_pattern(self):
        root = self.base / 'slow'
        root.mkdir()
        git = ['git', '-C', str(root), '-c', 'user.email=t@example.invalid', '-c', 'user.name=t']
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        (root / orch_settings.REPO_FILE).write_text(rule(kind='"OLD"', paths='["**/*.txt"]', pattern="'a*a*a*a*a*a*b'"))
        (root / 'long.txt').write_text(('a' * 5000 + '\n') * 200)
        (root / 'hit.txt').write_text('aaab\n')
        subprocess.run([*git, 'add', '-A'], check=True)
        subprocess.run([*git, 'commit', '-qm', 'seed'], check=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith('ORCH_LEGACY_')}
        start = time.monotonic()
        result = subprocess.run([sys.executable, str(w.ROOT / 'legacy_rules.py'), 'map', str(root), 'HEAD'],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual((result.returncode, result.stdout), (0, 'OLD\thit.txt\n'), result.stderr)
        self.assertLess(time.monotonic() - start, 10)

    def test_long_lines_are_searched_up_to_a_limit(self):
        parsed = rules(rule(kind='"A"', pattern="'needle'", paths='["**"]'))
        self.assertEqual(parsed.classify('a', 'x' * (legacy_rules.LINE_SEARCH_LIMIT - 6) + 'needle'), 'A')
        self.assertIsNone(parsed.classify('a', 'x' * legacy_rules.LINE_SEARCH_LIMIT + 'needle'))

    def test_globs(self):
        match = lambda glob, path: bool(legacy_rules.compile_glob(glob).fullmatch(path))
        self.assertTrue(match('app/**', 'app/a/b.rb'))
        self.assertTrue(match('app/**/x.rb', 'app/x.rb'))
        self.assertTrue(match('app/**/x.rb', 'app/a/b/x.rb'))
        self.assertTrue(match('app/*.rb', 'app/x.rb'))
        self.assertFalse(match('app/*.rb', 'app/a/x.rb'))
        self.assertTrue(match('app/', 'app/a/x.rb'))
        self.assertTrue(match('a/{b,c}/x?.rb', 'a/c/x1.rb'))
        self.assertFalse(match('a/{b,c}/x?.rb', 'a/d/x1.rb'))
        self.assertFalse(match('app/x.rb', 'app/xarb'))
        self.assertTrue(match('**/*.js', 'a.js'))

    def test_map_rejects_a_short_or_failed_cat_file(self):
        rules_ = rules(rule(kind='"A"', paths='["**"]'))
        oid = 'a' * 40

        class Batch:
            def __init__(self, reply, status):
                self.stdin, self.stdout, self.returncode, self.status = io.BytesIO(), io.BytesIO(reply), None, status

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def wait(self):
                self.returncode = self.status
                return self.status
        tree = f'100644 blob {oid}\ta.js\0'.encode()
        for reply, status, message in ((f'{oid} blob 100\n'.encode() + b'safe\n', 7, 'short read'),
                                       (f'{oid} blob 5\n'.encode() + b'safe\n\n', 7, 'git cat-file failed with status 7'),
                                       (f'{"b" * 40} blob 5\n'.encode() + b'safe\n\n', 0, 'cannot read a.js')):
            with patch.object(legacy_rules, 'git', return_value=tree), \
                    patch.object(legacy_rules.subprocess, 'Popen', return_value=Batch(reply, status)):
                with self.assertRaisesRegex(ValueError, message):
                    legacy_rules.map_rows('repo', 'HEAD', rules_)
        with patch.object(legacy_rules, 'git', return_value=tree), \
                patch.object(legacy_rules.subprocess, 'Popen', return_value=Batch(f'{oid} blob 5\n'.encode() + b'safe\n\n', 0)):
            self.assertEqual(legacy_rules.map_rows('repo', 'HEAD', rules_), [('A', 'a.js')])

    def test_map_skips_submodules_and_without_rules_exits_with_its_own_status(self):
        root = self.base / 'repo'
        root.mkdir()
        git = ['git', '-C', str(root), '-c', 'user.email=t@example.invalid', '-c', 'user.name=t']
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        (root / 'a.js').write_text('$(x)\n')
        subprocess.run([*git, 'add', '-A'], check=True)
        subprocess.run([*git, 'commit', '-qm', 'seed'], check=True)
        subprocess.run([*git, 'update-index', '--add', '--cacheinfo', f'160000,{"1" * 40},vendor/sub'], check=True)
        blob = subprocess.run([*git, 'hash-object', '-w', '--stdin'], input=b'$(x)\n', check=True, capture_output=True).stdout.strip()
        subprocess.run([*git, 'update-index', '--add', '--cacheinfo', b'100644,' + blob + b',docs/old-\xff.js'], check=True)
        (root / orch_settings.REPO_FILE).write_text(rule(kind='"ALL"', paths='["**"]'))
        subprocess.run([*git, 'add', orch_settings.REPO_FILE], check=True)
        subprocess.run([*git, 'commit', '-qm', 'rules and a submodule'], check=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith('ORCH_LEGACY_')}
        mapped = subprocess.run([sys.executable, str(w.ROOT / 'legacy_rules.py'), 'map', str(root), 'HEAD'],
                                capture_output=True, text=True, env=env)
        self.assertEqual((mapped.returncode, mapped.stdout, mapped.stderr), (0, 'ALL\t.orchestration.toml\nALL\ta.js\nALL\tdocs/old-\ufffd.js\n', ''))
        subprocess.run([*git, 'rm', '-q', '--cached', 'vendor/sub', b'docs/old-\xff.js', orch_settings.REPO_FILE], check=True)
        subprocess.run([*git, 'commit', '-qm', 'no rules'], check=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith('ORCH_LEGACY_')}
        result = subprocess.run([sys.executable, str(w.ROOT / 'legacy_rules.py'), 'map', str(root), 'HEAD'],
                                capture_output=True, text=True, env=env)
        self.assertEqual((result.returncode, result.stdout), (legacy_rules.NO_RULES, ''))
        self.assertIn('has no [[legacy.rules]]', result.stderr)
        missing = subprocess.run([sys.executable, str(w.ROOT / 'legacy_rules.py'), 'map', str(root), 'origin/main'],
                                 capture_output=True, text=True, env=env)
        self.assertEqual(missing.returncode, 1)
        self.assertIn(f'legacy-map.sh: cannot read origin/main in {root}', missing.stderr)


if __name__ == '__main__':
    unittest.main()
