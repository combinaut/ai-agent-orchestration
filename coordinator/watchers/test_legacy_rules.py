import subprocess
import sys
import unittest
from unittest.mock import patch

from test_support import IsolatedTestCase
import legacy_rules
import watchers as w

RULES = (w.ROOT / 'fixtures/legacy-rules.toml').read_text()
SOURCE = 'owner/product@main .orchestration/legacy-rules.toml'


def rules(text):
    return legacy_rules.parse_rules(text, SOURCE)


def rule(**fields):
    body = ''.join(f'{key} = {value}\n' for key, value in fields.items())
    return f'version = 1\n[[rules]]\n{body}'


class RulesFileTests(IsolatedTestCase):
    def test_valid_file_lists_kinds_in_file_order(self):
        self.assertEqual(rules(RULES).kinds, ('JSERB', 'JQUERY', 'LEGACY', 'TEST'))
        text = rule(kind='"B"', paths='["**"]') + '[[rules]]\nkind = "A"\npaths = ["**"]\n[[rules]]\nkind = "B"\npaths = ["x"]\n'
        self.assertEqual(rules(text).kinds, ('B', 'A'))

    def test_fixture_rules_classify_like_the_jquery_conventions(self):
        parsed = rules(RULES)
        for text in ('$(', 'jQuery', '$.ajax', 'ajax:success', 'jquery_ujs', 'jquery-ujs', 'rails-ujs',
                     'data-remote', 'remote:true', 'remote:  true', '.trigger(', 'select2'):
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
        self.assertIsNone(parsed.classify('.orchestration/legacy-rules.toml', '$('))
        self.assertIsNone(parsed.classify('scripts/legacy-guard.test.mjs', '$('))

    def test_rule_without_pattern_counts_every_line(self):
        parsed = rules(rule(kind='"OLD"', paths='["src/old/**"]'))
        self.assertEqual(parsed.classify('src/old/a.js', ''), 'OLD')
        self.assertEqual(parsed.classify('src/old/deep/b.txt', 'anything'), 'OLD')
        self.assertIsNone(parsed.classify('src/new/a.js', 'anything'))

    def test_exclude_skips_paths_for_that_rule_only(self):
        parsed = rules(rule(kind='"A"', pattern="'x'", paths='["**/*.js"]', exclude='["vendor/**"]')
                       + '[[rules]]\nkind = "B"\npaths = ["vendor/**"]\n')
        self.assertEqual(parsed.classify('app/a.js', 'x'), 'A')
        self.assertEqual(parsed.classify('vendor/a.js', 'x'), 'B')

    def test_exempt_paths_are_never_counted(self):
        parsed = rules('version = 1\nexempt = ["tools/guard.mjs", "gen/**"]\n'
                       '[[rules]]\nkind = "A"\npaths = ["**"]\n')
        self.assertIsNone(parsed.classify('tools/guard.mjs', 'x'))
        self.assertIsNone(parsed.classify('gen/a/b.js', 'x'))
        self.assertEqual(parsed.classify('tools/other.mjs', 'x'), 'A')

    def test_first_matching_rule_wins_per_line(self):
        parsed = rules(rule(kind='"FIRST"', pattern="'jQuery'", paths='["**"]')
                       + '[[rules]]\nkind = "SECOND"\npattern = \'jQuery|\\$\\(\'\npaths = ["**"]\n')
        self.assertEqual(parsed.classify('a.js', 'jQuery $('), 'FIRST')
        self.assertEqual(parsed.classify('a.js', '$('), 'SECOND')
        lines = [('a.js', 1, 'jQuery', True), ('a.js', 2, '$(x)', True), ('a.js', 3, '$(y)', False), ('a.js', 4, 'plain', True)]
        self.assertEqual(parsed.scan(lines), [('FIRST', 'a.js', 'jQuery'), ('SECOND', 'a.js', '$(x)')])

    def test_flags(self):
        self.assertEqual(rules(rule(kind='"A"', pattern="'jquery'", flags='"i"', paths='["**"]')).classify('a', 'JQuery'), 'A')
        self.assertIsNone(rules(rule(kind='"A"', pattern="'jquery'", paths='["**"]')).classify('a', 'JQuery'))
        self.assertEqual(rules(rule(kind='"A"', pattern="'a.b'", flags='"s"', paths='["**"]')).classify('a', 'a\rb'), 'A')
        self.assertEqual(rules(rule(kind='"A"', pattern="'^b'", flags='"im"', paths='["**"]')).classify('a', 'a \nB'), 'A')

    def test_each_validation_error_names_the_file_rule_and_problem(self):
        cases = {
            'not valid TOML': 'version = ',
            "unknown key 'kinds'": 'version = 1\nkinds = []\n[[rules]]\nkind = "A"\npaths = ["**"]\n',
            "rules[1] (A): unknown key 'path'": rule(kind='"A"', path='["**"]'),
            'unsupported version 2 (expected 1)': 'version = 2\n[[rules]]\nkind = "A"\npaths = ["**"]\n',
            "unsupported version '1' (expected 1)": 'version = "1"\n[[rules]]\nkind = "A"\npaths = ["**"]\n',
            'unsupported version True (expected 1)': 'version = true\n[[rules]]\nkind = "A"\npaths = ["**"]\n',
            'unsupported version None (expected 1)': '[[rules]]\nkind = "A"\npaths = ["**"]\n',
            'exempt must be a list of non-empty strings': 'version = 1\nexempt = "x"\n[[rules]]\nkind = "A"\npaths = ["**"]\n',
            'rules must be a non-empty array of tables': 'version = 1\n',
            'rules[1]: kind must be a word': rule(kind='"two words"', paths='["**"]'),
            'rules[1]: kind must be a word ': rule(kind='1', paths='["**"]'),
            'rules[1] (A): paths must be a non-empty list of non-empty strings': rule(kind='"A"'),
            'rules[1] (A): paths must be a non-empty list of non-empty strings ': rule(kind='"A"', paths='[]'),
            'rules[1] (A): exclude must be a list of non-empty strings': rule(kind='"A"', paths='["**"]', exclude='[1]'),
            'rules[1] (A): pattern must be a non-empty string': rule(kind='"A"', paths='["**"]', pattern='1'),
            'rules[1] (A): pattern is not a valid regex': rule(kind='"A"', paths='["**"]', pattern="'('"),
            "rules[1] (A): unsupported flags 'g' (use i, m, s)": rule(kind='"A"', paths='["**"]', pattern="'a'", flags='"g"'),
            "rules[1] (A): unsupported flags 'ii' (use i, m, s)": rule(kind='"A"', paths='["**"]', pattern="'a'", flags='"ii"'),
            'rules[1] (A): flags need a pattern': rule(kind='"A"', paths='["**"]', flags='"i"'),
            "rules[1] (A): paths: bad brace group in '{a'": rule(kind='"A"', paths='["{a"]'),
        }
        for message, text in cases.items():
            with self.assertRaises(ValueError, msg=message) as caught:
                rules(text)
            self.assertTrue(str(caught.exception).startswith(SOURCE + ': '), caught.exception)
            self.assertIn(message.strip(), str(caught.exception))
        text = rule(kind='"A"', paths='["**"]') + '[[rules]]\nkind = "B"\npaths = ["**"]\npattern = "["\n'
        with self.assertRaisesRegex(ValueError, r'rules\[2\] \(B\): pattern is not a valid regex'):
            rules(text)

    def test_old_python_says_it_needs_tomllib(self):
        with patch.object(legacy_rules, 'tomllib', None):
            self.assertRegex(legacy_rules.python_problem(), r'^legacy scan needs Python 3\.11\+ for tomllib; .+ is \d+\.\d+')
            with self.assertRaisesRegex(ValueError, 'needs Python 3.11'):
                rules(RULES)
        self.assertIsNone(legacy_rules.python_problem())

    def test_module_imports_without_tomllib(self):
        code = ("import sys; sys.modules['tomllib'] = None; import legacy_rules, audit; "
                "print(legacy_rules.python_problem())")
        result = subprocess.run([sys.executable, '-c', code], cwd=w.ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('legacy scan needs Python 3.11+ for tomllib', result.stdout)

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


if __name__ == '__main__':
    unittest.main()
