import base64
import contextlib
import copy
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase
import audit
import watchers as w


RULES = (w.ROOT / 'fixtures/legacy-rules.toml').read_text()
PR = json.loads((w.ROOT / 'fixtures/audit-pr.json').read_text())
LABEL = 'owner/product#7 Fix menu'
DIFF = '''diff --git a/app/views/menus/update.js.erb b/app/views/menus/update.js.erb
--- a/app/views/menus/update.js.erb
+++ b/app/views/menus/update.js.erb
@@ -1,2 +1,3 @@
 keep
+plain line in erb
+++ not a header
diff --git a/app/helpers/menu_helper.rb b/app/helpers/menu_helper.rb
--- a/app/helpers/menu_helper.rb
+++ b/app/helpers/menu_helper.rb
@@ -1 +1,3 @@
-old = 1
+link_to 'x', remote: true
+plain = 2
diff --git a/app/assets/javascripts/menu.js b/app/assets/javascripts/menu.js
--- a/app/assets/javascripts/menu.js
+++ b/app/assets/javascripts/menu.js
@@ -1 +1,3 @@
+$('.menu').hide()
+var plain = 1
diff --git a/spec/features/menu_spec.rb b/spec/features/menu_spec.rb
--- a/spec/features/menu_spec.rb
+++ b/spec/features/menu_spec.rb
@@ -1 +1,2 @@
+find('.select2-choice').click
+expect(page).to have_content 'x'
diff --git a/app/components/menu.vue b/app/components/menu.vue
--- a/app/components/menu.vue
+++ b/app/components/menu.vue
@@ -1 +1,2 @@
+<div>modern</div>
diff --git a/old.rb b/gone.rb
deleted file mode 100644
--- a/old.rb
+++ /dev/null
@@ -1 +0,0 @@
-$(removed)
'''
HITS = [('JSERB', 'app/views/menus/update.js.erb', 'plain line in erb'),
        ('JSERB', 'app/views/menus/update.js.erb', '++ not a header'),
        ('JQUERY', 'app/helpers/menu_helper.rb', "link_to 'x', remote: true"),
        ('LEGACY', 'app/assets/javascripts/menu.js', "$('.menu').hide()"),
        ('LEGACY', 'app/assets/javascripts/menu.js', 'var plain = 1'),
        ('TEST', 'spec/features/menu_spec.rb', "find('.select2-choice').click")]
SUMMARY = f'LEGACY {LABEL} [6 lines: JSERB 2, JQUERY 1, LEGACY 2, TEST 1]'
SCAN = RULES.replace('[legacy]\n', '[legacy]\nscan = true\n', 1)


VIEW = ('case "$*" in *"/contents/.orchestration.toml"*) if [ -s "$STUB_DIR/rules" ]; then cat "$STUB_DIR/rules"; exit 0; fi; '
        'echo "gh: Not Found (HTTP 404)" >&2; exit 1;; esac\n'
        'case "$2" in view) echo \'{"baseRefName":"main","headRefOid":"headsha"}\'; exit 0;; esac\n')


def new_file(path, *lines):
    return (f'diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n'
            + ''.join(f'+{line}\n' for line in lines))


def files_pages(diff, per_page=2):
    files = []
    for chunk in diff.split('diff --git ')[1:]:
        name = next(line[6:] for line in chunk.splitlines() if line.startswith('+++ b/') or line.startswith('--- a/'))
        body = chunk[chunk.index('@@'):]
        added = sum(line.startswith('+') for line in body.splitlines()[1:])
        files.append(dict(filename=name, additions=added, patch=body))
    return ''.join(json.dumps(files[n:n + per_page]) for n in range(0, len(files), per_page))


def too_large():
    return subprocess.CalledProcessError(1, ['gh'], stderr='could not find pull request diff: diff too_large')


class LegacyTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.pr = copy.deepcopy(PR)
        self.diff = DIFF
        self.calls = []
        self.big = False
        self.settings_reads = []
        self.pages = None
        self.view = {}
        self.versions = {}
        self.env = patch.dict(os.environ, dict(ORCH_PR_AUTHOR='author', ORCH_PR_REPOS='owner/product',
                            ORCH_GIT_WATCH_REPOS='', ORCH_PR_SINCE='',
                            ORCH_LEGACY_SCANS_MAX='10', ORCH_AUDIT_PAGE_PRS='80', ORCH_AUDIT_PAGE_NODES='100'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.repo_files = {'owner/product': SCAN}

    def fake(self, *args):
        if '/contents/.orchestration.toml?' in args[-1]:
            return self.repo_file(args[-1])
        self.calls.append(args)
        if args[:2] == ('gh', 'api') and '/compare/' in args[2]:
            return 'mergebase\n'
        if args[:2] == ('gh', 'api') and '/contents/' in args[-1]:
            return self.version(args[-1])
        if args[:3] == ('gh', 'pr', 'view'):
            return json.dumps(dict(baseRefName='main', headRefOid='headsha', **self.view))
        if args[:3] == ('gh', 'pr', 'diff'):
            if self.big:
                raise too_large()
            return self.diff
        if args[:3] == ('gh', 'api', '--paginate'):
            return self.pages if self.pages is not None else files_pages(self.diff)
        if args[0] == 'gh':
            return json.dumps(dict(data=dict(search=dict(nodes=[self.pr], pageInfo=dict(hasNextPage=False)))))
        return json.dumps(dict(result=dict(workspaces=[])))

    def repo_file(self, url):
        self.settings_reads.append(url)
        repo = url.split('repos/', 1)[1].split('/contents/', 1)[0]
        if repo not in self.repo_files:
            raise subprocess.CalledProcessError(1, ['gh'], stderr='gh: Not Found (HTTP 404)')
        return self.repo_files[repo]

    def version(self, url):
        path, ref = url.split('/contents/', 1)[1].rsplit('?ref=', 1)
        value = self.versions.get((path, ref))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise subprocess.CalledProcessError(1, ['gh'], stderr='gh: Not Found (HTTP 404)')
        raw = value.encode() if isinstance(value, str) else value
        return json.dumps(dict(encoding='base64', size=len(raw), content=base64.encodebytes(raw).decode()))

    def patchless(self, *files):
        self.big = True
        self.pages = json.dumps(list(files))
        return audit.legacy_scan(self.fake, 'owner/product', 7)

    def reads(self):
        return [c[-1].split('/contents/', 1)[1] for c in self.calls if '/contents/' in c[-1]]

    def scans(self):
        return [call for call in self.calls if call[:2] == ('gh', 'pr') or call[:2] == ('gh', 'api') and call[2] == '--paginate']

    def scan(self, command=None):
        return w.cycle(self.base, 'audit', lambda before: audit.collect(command or self.fake, self.base / 'slots', before))

    def events(self, line):
        return json.loads(line)['events'] if line else []

    def saved(self):
        return json.loads((self.base / 'audit.json').read_text())

    def test_each_kind_is_classified(self):
        self.assertEqual(audit.legacy_scan(self.fake, 'owner/product', 7), HITS)

    def test_quoted_diff_paths_are_decoded(self):
        diff = ('diff --git "a/app/views/t\\303\\251.js.erb" "b/app/views/t\\303\\251.js.erb"\n'
                '--- "a/app/views/t\\303\\251.js.erb"\n+++ "b/app/views/t\\303\\251.js.erb"\n@@ -1 +1 @@\n+x\n'
                'diff --git "a/spec/a\\tb_spec.rb" "b/spec/a\\tb_spec.rb"\n+++ "b/spec/a\\tb_spec.rb"\n@@ -1 +1 @@\n+$(x)\n')
        self.assertEqual(list(audit.diff_added(diff)), [('app/views/t\u00e9.js.erb', 'x'), ('spec/a\tb_spec.rb', '$(x)')])
        self.assertEqual(audit.unquote('"\\a\\b\\f\\r\\v"'), '\a\b\f\r\v')
        self.assertEqual(audit.unquote('plain'), 'plain')
        self.assertEqual(audit.unquote('"q\\"x\\\\y"'), 'q"x\\y')

    def test_summary_line_and_zero_count(self):
        kinds = ('JSERB', 'JQUERY', 'LEGACY', 'TEST')
        self.assertEqual(audit.legacy_line(LABEL, audit.legacy_scan(self.fake, 'owner/product', 7), kinds), SUMMARY)
        self.assertEqual(audit.legacy_line(LABEL, [], kinds), '')

    def test_large_pr_fallback_matches_diff_path(self):
        direct = audit.legacy_scan(self.fake, 'owner/product', 7)
        self.big = True
        self.calls.clear()
        self.assertEqual(audit.legacy_scan(self.fake, 'owner/product', 7), direct)
        self.assertEqual(self.calls[-1], ('gh', 'api', '--paginate', 'repos/owner/product/pulls/7/files?per_page=100'))

    def test_fallback_only_for_too_large(self):
        def denied(*args):
            raise subprocess.CalledProcessError(1, args, stderr='HTTP 502')
        with self.assertRaises(subprocess.CalledProcessError):
            audit.legacy_scan(denied, 'owner/product', 7)

    def test_fallback_without_patch_or_at_file_cap_is_an_error(self):
        pages = {'value': json.dumps([dict(filename='a.rb', additions=3)])}
        def command(*args):
            if args[:3] == ('gh', 'pr', 'diff'):
                raise too_large()
            if args[:3] == ('gh', 'pr', 'view') or '/contents/' in args[-1]:
                return self.fake(*args)
            return pages['value']
        with self.assertRaises(ValueError):
            audit.legacy_scan(command, 'owner/product', 7)
        pages['value'] = json.dumps([dict(filename='a.rb', additions=3, patch='@@ -1 +1,2 @@\n+one\n+two')])
        with self.assertRaises(ValueError):
            audit.legacy_scan(command, 'owner/product', 7)
        pages['value'] = json.dumps([dict(filename='a.png', additions=0)])
        self.assertEqual(audit.legacy_scan(command, 'owner/product', 7), [])
        pages['value'] = json.dumps([dict(filename=f'{n}.rb', additions=0) for n in range(audit.FILES_API_LIMIT)])
        with self.assertRaises(ValueError):
            audit.legacy_scan(command, 'owner/product', 7)

    def test_patchless_added_file_is_read_at_the_head(self):
        path = 'app/frontend/src/admin/review_pane/review-form-controller.spec.js'
        rows = [f"it('case {n}', () => {{}})" for n in range(3490)]
        rows[999] = "$('.x').click()"
        self.versions[(path, 'headsha')] = '\n'.join(rows) + '\n'
        hits = self.patchless(dict(filename=path, status='added', additions=3490, deletions=0, changes=3490))
        self.assertEqual(hits, [('TEST', path, "$('.x').click()")])
        self.assertEqual(self.reads(), [f'{path}?ref=headsha'])
        self.assertFalse([c for c in self.calls if '/compare/' in c[2]])

    def test_patchless_modified_and_renamed_files_diff_against_the_merge_base(self):
        self.versions[('app/a.rb', 'mergebase')] = '$(old)\nkeep\nchanged\n'
        self.versions[('app/a.rb', 'headsha')] = '$(old)\nkeep\n$(new)\nlast\n'
        self.versions[('app/old.rb', 'mergebase')] = 'one\ntwo\n'
        self.versions[('app/new.rb', 'headsha')] = 'one\ntwo\n$(three)\n'
        hits = self.patchless(dict(filename='app/a.rb', status='modified', additions=2),
                              dict(filename='app/new.rb', previous_filename='app/old.rb', status='renamed', additions=1))
        self.assertEqual(hits, [('JQUERY', 'app/a.rb', '$(new)'), ('JQUERY', 'app/new.rb', '$(three)')])
        self.assertEqual(len([c for c in self.calls if '/compare/' in c[2]]), 1)
        self.assertEqual(self.reads(), ['app/a.rb?ref=headsha', 'app/a.rb?ref=mergebase',
                                        'app/new.rb?ref=headsha', 'app/old.rb?ref=mergebase'])

    def test_patchless_removed_file_needs_no_read(self):
        self.assertEqual(self.patchless(dict(filename='app/gone.rb', status='removed', additions=0, deletions=900)), [])
        self.assertEqual(self.reads(), [])

    def test_patchless_binary_files_are_skipped_and_logged(self):
        self.versions[('app/a.bin', 'headsha')] = 'ab\0cd'
        self.versions[('app/b.dat', 'headsha')] = b'\xff\xfe'
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            hits = self.patchless(dict(filename='app/a.bin', status='added', additions=4),
                                  dict(filename='app/b.dat', status='added', additions=4))
        self.assertEqual(hits, [])
        self.assertEqual(stderr.getvalue(), 'legacy-scan skipped binary file app/a.bin\nlegacy-scan skipped binary file app/b.dat\n')

    def test_patchless_read_failures_and_mismatches_are_errors(self):
        added = dict(filename='app/a.rb', status='added', additions=2)
        with self.assertRaisesRegex(subprocess.CalledProcessError, 'returned non-zero'):
            self.patchless(added)
        self.versions[('app/a.rb', 'headsha')] = 'one\ntwo\nthree\n'
        with self.assertRaisesRegex(ValueError, 'line count mismatch for app/a.rb'):
            self.patchless(added)
        self.versions[('app/a.rb', 'headsha')] = 'one\ntwo\n'
        self.assertEqual(self.patchless(added), [])
        with self.assertRaisesRegex(ValueError, 'no patch for app/a.rb'):
            self.patchless(dict(added, status='unknown'))
        with self.assertRaisesRegex(ValueError, 'no patch for app/a.rb'):
            list(audit.files_lines(self.pages))
        self.pages = None
        self.versions[('app/a.rb', 'mergebase')] = None
        with self.assertRaises(subprocess.CalledProcessError):
            self.patchless(dict(added, status='modified'))

    def test_patchless_reads_and_sizes_are_bounded(self):
        files = [dict(filename=f'app/{n}.rb', status='added', additions=1) for n in range(3)]
        for file in files:
            self.versions[(file['filename'], 'headsha')] = 'x\n'
        with patch.dict(os.environ, {'ORCH_LEGACY_READS_MAX': '3'}):
            self.assertEqual(self.patchless(*files), [])
        with patch.dict(os.environ, {'ORCH_LEGACY_READS_MAX': '2'}):
            with self.assertRaisesRegex(ValueError, 'more than 2 file reads for files without a patch'):
                self.patchless(*files)
        with patch.object(audit, 'FILE_READ_LIMIT', 1):
            with self.assertRaisesRegex(ValueError, 'app/0.rb cannot be read in full'):
                self.patchless(*files)

    def test_the_merge_base_lookup_is_not_a_file_read(self):
        self.versions[('app/m.rb', 'headsha')] = 'x\ny\n'
        self.versions[('app/m.rb', 'mergebase')] = 'x\n'
        with patch.dict(os.environ, {'ORCH_LEGACY_READS_MAX': '2'}):
            self.assertEqual(self.patchless(dict(filename='app/m.rb', status='modified', additions=1)), [])

    def test_patchless_modified_diff_work_is_bounded(self):
        self.versions[('app/m.rb', 'headsha')] = 'x\n' * 6 + 'y\n'
        self.versions[('app/m.rb', 'mergebase')] = 'x\n' * 6
        file = dict(filename='app/m.rb', status='modified', additions=1)
        with patch.object(audit, 'DIFF_LINES_LIMIT', 6):
            with self.assertRaisesRegex(ValueError, 'app/m.rb has over 6 lines and no patch'):
                self.patchless(file)
            self.versions[('app/a.rb', 'headsha')] = 'x\n' * 7
            self.assertEqual(self.patchless(dict(filename='app/a.rb', status='added', additions=7)), [])
        self.assertEqual(self.patchless(file), [])

    def test_inserted_lines_match_a_shortest_edit_script(self):
        def lcs(a, b):
            table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
            for i, x in enumerate(a):
                for j, y in enumerate(b):
                    table[i + 1][j + 1] = table[i][j] + 1 if x == y else max(table[i][j + 1], table[i + 1][j])
            return table[-1][-1]
        def subsequence(small, big):
            it = iter(big)
            return all(any(x == y for y in it) for x in small)
        rng = random.Random(7)
        for _ in range(300):
            old = [rng.choice('abcd') for _ in range(rng.randint(0, 9))]
            new = [rng.choice('abcd') for _ in range(rng.randint(0, 9))]
            marked = audit.inserted_lines(old, new, 100)
            self.assertEqual(len(marked), len(new) - lcs(old, new), (old, new))
            self.assertTrue(subsequence([x for n, x in enumerate(new) if n not in marked], old), (old, new))
        self.assertEqual(len(audit.inserted_lines(list('tide'), list('diet'), 10)), 2)
        with self.assertRaisesRegex(ValueError, 'more than 1 changed lines'):
            audit.inserted_lines(list('abc'), list('xyz'), 1)

    def test_patchless_addition_count_follows_git_not_difflib(self):
        self.versions[('app/m.rb', 'headsha')] = 'd\ni\ne\nt\n'
        self.versions[('app/m.rb', 'mergebase')] = 't\ni\nd\ne\n'
        self.assertEqual(self.patchless(dict(filename='app/m.rb', status='modified', additions=2)), [])

    def test_patchless_content_keeps_bare_carriage_returns(self):
        self.versions[('app/a.rb', 'headsha')] = 'a\rb\nc\n'
        self.assertEqual(self.patchless(dict(filename='app/a.rb', status='added', additions=2)), [])
        self.versions[('app/e.rb', 'headsha')] = ''
        self.versions[('app/f.rb', 'headsha')] = 'x\n'
        self.versions[('app/f.rb', 'mergebase')] = ''
        self.assertEqual(self.patchless(dict(filename='app/f.rb', status='modified', additions=1)), [])

    def test_only_http_404_means_no_repo_settings(self):
        def odd(*args):
            if '/contents/.orchestration.toml' in args[-1]:
                raise subprocess.CalledProcessError(1, args, stderr='error: Not Found proxy response')
            return self.fake(*args)
        self.assertIn('Not Found proxy', self.fail_reason(odd))

    def test_the_production_command_keeps_bare_carriage_returns(self):
        out = w.command(sys.executable, '-c', 'import sys; sys.stdout.buffer.write(b"a\\rb\\n$(x)\\n")')
        self.assertEqual(out, 'a\rb\n$(x)\n')
        diff = 'diff --git a/app/a.rb b/app/a.rb\n--- a/app/a.rb\n+++ b/app/a.rb\n@@ -0,0 +1 @@\n+a\rb\n'
        self.assertEqual(audit.diff_added(diff), [('app/a.rb', 'a\rb')])

    def test_patchless_head_reads_use_the_head_repository(self):
        self.versions[('app/a.rb', 'headsha')] = 'x\n'
        self.versions[('app/m.rb', 'headsha')] = 'x\ny\n'
        self.versions[('app/m.rb', 'mergebase')] = 'x\n'
        self.view = dict(headRepository=dict(name='product'), headRepositoryOwner=dict(login='fork'))
        self.patchless(dict(filename='app/a.rb', status='added', additions=1),
                       dict(filename='app/m.rb', status='modified', additions=1))
        urls = [c[-1] for c in self.calls if '/contents/' in c[-1]]
        compare = [c[2] for c in self.calls if '/compare/' in c[2]]
        self.assertEqual(urls, ['repos/fork/product/contents/app/a.rb?ref=headsha', 'repos/fork/product/contents/app/m.rb?ref=headsha',
                                'repos/owner/product/contents/app/m.rb?ref=mergebase'])
        self.assertEqual(compare, ['repos/owner/product/compare/main...headsha'])
        self.view = {}
        self.calls.clear()
        self.patchless(dict(filename='app/a.rb', status='added', additions=1))
        self.assertIn('repos/owner/product/contents/app/a.rb?ref=headsha', [c[-1] for c in self.calls])

    def test_unicode_separators_stay_inside_a_line(self):
        diff = new_file('app/a.rb', 'x\u2028$(y)', 'r\rjquery')
        self.assertEqual(audit.diff_added(diff), [('app/a.rb', 'x\u2028$(y)'), ('app/a.rb', 'r\rjquery')])
        pages = json.dumps([dict(filename='app/a.rb', additions=1, patch='@@ -0,0 +1 @@\n+x\u2028$(y)')])
        self.assertEqual(audit.files_added(pages), [('app/a.rb', 'x\u2028$(y)')])

    def test_emits_once_per_head_and_rescans_new_head(self):
        self.assertEqual(self.events(self.scan()), ['AUDIT BASELINE github [0 active lines; see audit.json before.sources]',
                                                    'AUDIT BASELINE herdr [0 active lines; see audit.json before.sources]',
                                                    'AUDIT BASELINE slots [0 active lines; see audit.json before.sources]',
                                                    'AUDIT BASELINE legacy [1 active lines; see audit.json before.legacy]'])
        self.assertEqual(len(self.scans()), 1)
        self.assertIsNone(self.scan())
        self.assertEqual(len(self.scans()), 1)
        self.pr['headRefOid'] = 'next'
        self.assertEqual(self.events(self.scan()), [SUMMARY])
        self.assertEqual(len(self.scans()), 2)
        self.assertIsNone(self.scan())
        self.assertEqual(len(self.scans()), 2)
        self.assertEqual(self.saved()['before']['legacy']['owner/product#7']['head'], 'next')

    def test_new_head_with_changed_count_emits_new_count(self):
        self.scan()
        self.pr['headRefOid'] = 'next'
        self.diff = DIFF.replace("+plain = 2\n", "+$(x)\n")
        self.assertEqual(self.events(self.scan()), [SUMMARY.replace('JQUERY 1', 'JQUERY 2').replace('6 lines', '7 lines')])

    def test_zero_count_emits_nothing_then_a_hit_emits(self):
        self.diff = 'diff --git a/a.rb b/a.rb\n--- a/a.rb\n+++ b/a.rb\n@@ -1 +1 @@\n+plain\n'
        self.scan()
        self.pr['headRefOid'] = 'next'
        self.assertIsNone(self.scan())
        self.pr['headRefOid'] = 'third'
        self.diff = DIFF
        self.assertEqual(self.events(self.scan()), [SUMMARY])

    def test_other_repos_and_closed_prs_are_not_scanned(self):
        self.repo_files = {}
        self.scan()
        self.assertEqual(self.scans(), [])
        self.repo_files = {'owner/product': '[legacy]\nscan = false\n', 'owner/other': SCAN}
        self.scan()
        self.assertEqual(self.scans(), [])
        self.assertEqual(self.saved()['before']['legacy'], {})
        self.pr['state'] = 'CLOSED'
        self.scan()
        self.assertEqual(self.scans(), [])

    def test_scan_failure_is_readfail_and_keeps_the_last_state(self):
        self.scan()
        self.pr['headRefOid'] = 'next'
        def failing(*args):
            if args[:3] == ('gh', 'pr', 'diff'):
                raise subprocess.CalledProcessError(1, args, stderr='HTTP 502')
            return self.fake(*args)
        self.assertEqual(self.events(self.scan(failing)), ['READFAIL LEGACY owner/product#7 Fix menu: HTTP 502', 'CLEARED ' + SUMMARY])
        self.assertIsNone(self.scan(failing))
        self.assertEqual(self.saved()['before']['legacy']['owner/product#7']['head'], 'next')
        self.assertEqual(self.events(self.scan()), [SUMMARY, 'CLEARED READFAIL LEGACY owner/product#7 Fix menu: HTTP 502'])

    def test_a_head_without_legacy_hits_clears_the_legacy_line(self):
        line = 'LEGACY o/r#1 Fix [2 lines: JSERB 0, JQUERY 2, LEGACY 0, TEST 0]'
        raw = lambda legacy: dict(sources={'github': []}, unreadable={}, legacy=legacy)
        before, _ = audit.reduce(None, raw({'o/r#1': dict(head='a', line=line)}))
        _, events = audit.reduce(before, raw({'o/r#1': dict(head='b', line='')}))
        self.assertEqual(events, ['CLEARED ' + line])

    def test_first_github_read_failing_defers_legacy_baseline(self):
        def failing(*args):
            if args[0] == 'gh':
                raise ValueError('offline')
            return self.fake(*args)
        self.scan(failing)
        self.assertNotIn('legacy', self.saved()['before'])
        self.assertIn('AUDIT BASELINE legacy', self.scan())

    def test_scans_per_pass_are_capped_and_resumed(self):
        prs = []
        for n in range(3):
            pr = copy.deepcopy(PR)
            pr['number'] = n
            prs.append(pr)
        def command(*args):
            if args[:2] == ('gh', 'graphql') or args[:3] == ('gh', 'api', 'graphql'):
                return json.dumps(dict(data=dict(search=dict(nodes=prs, pageInfo=dict(hasNextPage=False)))))
            return self.fake(*args)
        with patch.dict(os.environ, {'ORCH_LEGACY_SCANS_MAX': '2'}):
            self.scan(command)
            self.assertEqual(len(self.scans()), 2)
            self.assertEqual(sorted(self.saved()['before']['legacy']), ['owner/product#0', 'owner/product#1'])
            self.scan(command)
        self.assertEqual(len(self.scans()), 3)
        self.assertEqual(sorted(self.saved()['before']['legacy']), ['owner/product#0', 'owner/product#1', 'owner/product#2'])

    def test_unscanned_entries_beyond_the_budget_are_kept(self):
        other = copy.deepcopy(PR)
        other['number'] = 8
        prs = [self.pr, other]
        def command(*args):
            if args[:3] == ('gh', 'api', 'graphql'):
                return json.dumps(dict(data=dict(search=dict(nodes=prs, pageInfo=dict(hasNextPage=False)))))
            return self.fake(*args)
        with patch.dict(os.environ, {'ORCH_LEGACY_SCANS_MAX': '2'}):
            self.scan(command)
        for pr in prs:
            pr['headRefOid'] = 'next'
        with patch.dict(os.environ, {'ORCH_LEGACY_SCANS_MAX': '1'}):
            self.scan(command)
        legacy = self.saved()['before']['legacy']
        self.assertEqual((legacy['owner/product#7']['head'], legacy['owner/product#8']['head']), ('next', 'head'))

    def test_empty_first_read_still_baselines_once(self):
        self.repo_files = {'owner/other': SCAN}
        self.assertIn('AUDIT BASELINE legacy [0 active lines; see audit.json before.legacy]', self.events(self.scan()))
        self.assertIsNone(self.scan())

    def test_invalid_scan_budget_is_readfail(self):
        with patch.dict(os.environ, {'ORCH_LEGACY_SCANS_MAX': '0'}):
            self.assertIn('READFAIL github:', self.scan())

    def fail_reason(self, command=None):
        return audit.collect(command or self.fake, self.base / 'slots')['legacy']['owner/product#7']['line']

    def test_bad_rules_and_scan_errors_are_per_pr_and_retry(self):
        self.pr['reviewDecision'] = 'CHANGES_REQUESTED'
        other = copy.deepcopy(PR)
        other.update(number=8, repository=dict(nameWithOwner='owner/other', defaultBranchRef=dict(name='trunk')))
        self.repo_files = {'owner/product': SCAN, 'owner/other': SCAN}
        for failure in ('rules', 'diff', 'decode'):
            def command(*args):
                if args[:3] == ('gh', 'api', 'graphql'):
                    return json.dumps(dict(data=dict(search=dict(nodes=[self.pr, other], pageInfo=dict(hasNextPage=False)))))
                if failure == 'rules' and 'repos/owner/other/contents/' in args[-1]:
                    return '{nope'
                if failure != 'rules' and args[:4] == ('gh', 'pr', 'diff', '8'):
                    if failure == 'decode':
                        raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid byte')
                    raise subprocess.CalledProcessError(1, args, stderr='HTTP 502')
                return self.fake(*args)
            raw = audit.collect(command, self.base / 'slots')
            self.assertNotIn('github', raw['unreadable'])
            self.assertTrue(raw['sources']['github'])
            self.assertIn('JQUERY 1', raw['legacy']['owner/product#7']['line'])
            self.assertTrue(raw['legacy']['owner/other#8']['line'].startswith('READFAIL LEGACY owner/other#8'))
            def recovered_command(*args):
                if args[:3] == ('gh', 'api', 'graphql'):
                    return json.dumps(dict(data=dict(search=dict(nodes=[self.pr, other], pageInfo=dict(hasNextPage=False)))))
                return self.fake(*args)
            recovered = audit.collect(recovered_command, self.base / 'slots', raw)
            self.assertNotIn('READFAIL', recovered['legacy']['owner/other#8']['line'])

    def test_failed_rules_load_is_cached_only_for_the_pass(self):
        other = copy.deepcopy(self.pr)
        other['number'] = 8
        for failure in ('{nope', 'HTTP 502'):
            self.calls = []
            def command(*args):
                if args[:3] == ('gh', 'api', 'graphql'):
                    return json.dumps(dict(data=dict(search=dict(nodes=[self.pr, other], pageInfo=dict(hasNextPage=False)))))
                if '/contents/' in args[-1]:
                    self.calls.append(args)
                    if failure == 'HTTP 502':
                        raise subprocess.CalledProcessError(1, args, stderr=failure)
                    return failure
                return self.fake(*args)
            raw = audit.collect(command, self.base / 'slots')
            self.assertNotIn('github', raw['unreadable'])
            self.assertEqual(len(raw['legacy']), 2)
            self.assertTrue(all(entry['line'].startswith('READFAIL LEGACY') for entry in raw['legacy'].values()))
            self.assertEqual(len([c for c in self.calls if '/contents/' in c[-1]]), 1)
            audit.collect(command, self.base / 'slots', raw)
            self.assertEqual(len([c for c in self.calls if '/contents/' in c[-1]]), 2)

    def test_strict_command_decode_failure_is_readfail(self):
        def command(*args):
            if args[0] != 'gh':
                return w.command(sys.executable, '-c', "import sys; sys.stdout.buffer.write(bytes([255]))")
            return self.fake(*args)
        raw = audit.collect(command, self.base / 'slots')
        self.assertIn('READFAIL herdr:', raw['unreadable']['herdr'])
        self.assertIn('github', raw['sources'])

    def test_empty_files_api_is_clean(self):
        self.big, self.pages = True, '[]'
        self.assertEqual(audit.legacy_scan(self.fake, 'owner/product', 7), [])

    def test_pattern_kinds_only_apply_to_frontend_sources(self):
        data = ''.join(new_file(path, '"command": "$(git rev-parse --show-toplevel)"', 'select2 jQuery rails-ujs')
                       for path in ('.codex/hooks.json', 'app/a.sh', 'app/b.yml', 'app/c.yaml', 'app/d.md', 'lib/e.bash'))
        code = ''.join(new_file(path, '$(x)') for path in
                       ('app/a.js', 'app/a.mjs', 'app/a.cjs', 'app/a.ts', 'app/a.vue', 'app/a.erb', 'app/a.haml',
                        'app/a.hbs', 'app/a.ejs', 'app/a.coffee', 'app/a.rb'))
        self.diff = data
        self.assertEqual(audit.legacy_scan(self.fake, 'owner/product', 7), [])
        self.diff = code
        self.assertEqual(len(audit.legacy_scan(self.fake, 'owner/product', 7)), 11)

    def test_rules_fetch_errors_other_than_missing_are_scan_failures(self):
        def denied(*args):
            if '/contents/' in args[-1]:
                raise subprocess.CalledProcessError(1, args, stderr='HTTP 502')
            return self.fake(*args)
        self.assertEqual(self.fail_reason(denied), 'READFAIL LEGACY owner/product#7 Fix menu: HTTP 502')

    def test_retargeted_pr_is_scanned_again(self):
        self.scan()
        self.assertEqual(len(self.scans()), 1)
        self.pr['baseRefName'] = 'release'
        self.scan()
        self.assertEqual(len(self.scans()), 2)
        self.assertEqual(self.saved()['before']['legacy']['owner/product#7']['base'], 'release')
        self.scan()
        self.assertEqual(len(self.scans()), 2)

    def test_rules_kinds_map_to_the_output_buckets(self):
        self.assertEqual(audit.legacy_scan(self.fake, 'owner/product', 7), HITS)
        self.diff = ''.join([
            new_file('app/a.rb', 'rails-ujs', 'ajax:success', '$.ajax(x)', 'select2', 'legacy-select', 'JQUERY ref', 'plain'),
            new_file('app/assets/javascripts/b.js', 'plain', '$(x)'),
            new_file('app/c.coffee', 'plain'),
            new_file('docs/d.rb', '$(x)'),
            new_file('app/e.sh', '$(x)'),
            new_file('spec/f_spec.rb', 'ajax:error', 'plain')])
        self.assertEqual([(kind, path, text) for kind, path, text in audit.legacy_scan(self.fake, 'owner/product', 7)], [
            ('JQUERY', 'app/a.rb', 'rails-ujs'), ('JQUERY', 'app/a.rb', 'ajax:success'),
            ('JQUERY', 'app/a.rb', '$.ajax(x)'), ('JQUERY', 'app/a.rb', 'select2'), ('JQUERY', 'app/a.rb', 'legacy-select'),
            ('LEGACY', 'app/assets/javascripts/b.js', 'plain'), ('LEGACY', 'app/assets/javascripts/b.js', '$(x)'),
            ('JQUERY', 'docs/d.rb', '$(x)'), ('TEST', 'spec/f_spec.rb', 'ajax:error')])

    def test_scan_without_rules_says_so_once_per_base_and_fetches_no_diff(self):
        self.repo_files = {'owner/product': '[legacy]\nscan = true\n'}
        self.assertIsNone(audit.legacy_scan(self.fake, 'owner/product', 7))
        self.assertEqual([call for call in self.scans() if call[2] != 'view'], [])
        line = ('NORULES LEGACY owner/product@main [legacy.scan is on but the repo settings have no [[legacy.rules]]; '
                'its PRs are not scanned]')
        other = copy.deepcopy(PR)
        other['number'] = 8
        def command(*args):
            if args[:3] == ('gh', 'api', 'graphql'):
                return json.dumps(dict(data=dict(search=dict(nodes=[self.pr, other], pageInfo=dict(hasNextPage=False)))))
            return self.fake(*args)
        self.scan(command)
        self.assertEqual(self.events(self.scan(command)), [])
        self.assertEqual(self.saved()['before']['legacy'], {'owner/product@main': dict(head='', base='main', line=line)})
        self.assertEqual([call for call in self.scans() if call[2] != 'view'], [])
        self.repo_files = {'owner/product': SCAN}
        self.assertEqual(self.events(self.scan(command)), [SUMMARY, SUMMARY.replace('#7', '#8'), 'CLEARED ' + line])

    def test_invalid_rules_are_a_scan_failure_that_names_the_rule(self):
        bad = {'not toml': ('[legacy\n', 'invalid TOML'),
               'bad regex': (SCAN + '[[legacy.rules]]\nkind = "A"\npaths = ["**"]\npattern = "("\n',
                             'legacy.rules[6] (A): pattern is not a valid rg regex'),
               'removed key': ('[legacy]\nscan = true\nextra_pattern = "x"\n',
                               'legacy.extra_pattern: was removed; run install.sh to convert it to [[legacy.rules]]')}
        for name, (text, reason) in bad.items():
            self.repo_files = {'owner/product': text}
            line = self.fail_reason()
            self.assertTrue(line.startswith('READFAIL LEGACY owner/product#7 Fix menu: '), name)
            self.assertIn(reason, line, name)

    def test_settings_are_fetched_once_per_base_per_pass(self):
        others = []
        for number, base in ((8, 'main'), (9, 'release')):
            pr = copy.deepcopy(PR)
            pr.update(number=number, baseRefName=base)
            others.append(pr)
        prs = [self.pr] + others
        def command(*args):
            if args[:3] == ('gh', 'api', 'graphql'):
                return json.dumps(dict(data=dict(search=dict(nodes=prs, pageInfo=dict(hasNextPage=False)))))
            return self.fake(*args)
        self.scan(command)
        self.assertEqual(sorted(self.settings_reads), [f'repos/owner/product/contents/.orchestration.toml?ref={ref}'
                                                       for ref in ('main', 'release')])

    def test_a_stacked_pr_is_scanned_with_the_rules_of_its_base(self):
        stacked = copy.deepcopy(PR)
        stacked.update(number=8, baseRefName='feat/parent')
        def command(*args):
            if args[:3] == ('gh', 'api', 'graphql'):
                return json.dumps(dict(data=dict(search=dict(nodes=[self.pr, stacked], pageInfo=dict(hasNextPage=False)))))
            if '/contents/.orchestration.toml?ref=feat%2Fparent' in args[-1]:
                return '[legacy]\nscan = true\n[[legacy.rules]]\nkind = "PARENT"\npaths = ["app/helpers/**"]\n'
            return self.fake(*args)
        legacy = audit.collect(command, self.base / 'slots')['legacy']
        self.assertEqual(legacy['owner/product#7']['line'], SUMMARY)
        self.assertEqual(legacy['owner/product#8']['line'], 'LEGACY owner/product#8 Fix menu [2 lines: PARENT 2]')

    def test_changed_rules_on_the_base_rescan_an_unchanged_head(self):
        self.scan()
        self.assertIsNone(self.scan())
        self.assertEqual(len(self.scans()), 1)
        self.repo_files = {'owner/product': SCAN + '[[legacy.rules]]\nkind = "ANY"\npaths = ["app/components/**"]\n'}
        events = self.events(self.scan())
        self.assertEqual(len(self.scans()), 2)
        self.assertEqual(events[0], f'LEGACY {LABEL} [7 lines: JSERB 2, JQUERY 1, LEGACY 2, TEST 1, ANY 1]')
        self.assertIsNone(self.scan())
        self.repo_files = {'owner/product': SCAN.replace('exempt = [', 'exempt = ["app/views/**", ', 1)}
        self.assertEqual(self.events(self.scan())[0], f'LEGACY {LABEL} [4 lines: JSERB 0, JQUERY 1, LEGACY 2, TEST 1]')
        self.assertEqual(len(self.scans()), 3)

    def test_cli_reports_missing_and_unreadable_rules(self):
        result = self.cli('7', 'owner/product', rules='[legacy]\nscan = true\n')
        self.assertEqual((result.returncode, result.stdout), (0, ''))
        self.assertEqual(result.stderr, 'legacy-scan: owner/product@main has no [[legacy.rules]] in its repo settings; nothing to scan\n')
        result = self.cli('7', 'owner/product', rules='[legacy\n')
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertIn('legacy-scan failed: owner/product@main:.orchestration.toml: invalid TOML', result.stderr)

    def cli(self, *args, mode='diff', config=None, rules=None):
        stubs = self.base / 'bin'
        stubs.mkdir(exist_ok=True)
        (self.base / 'diff').write_text(self.diff)
        (self.base / 'files').write_text(files_pages(self.diff))
        (self.base / 'rules').write_text(SCAN if rules is None else rules)
        (stubs / 'gh').write_text('#!/bin/sh\n' + VIEW + {
            'diff': 'cat "$STUB_DIR/diff"\n',
            'big': 'if [ "$2" = diff ]; then echo "diff too_large" >&2; exit 1; fi\ncat "$STUB_DIR/files"\n',
            'fail': 'echo "HTTP 502" >&2\nexit 1\n'}[mode])
        (stubs / 'gh').chmod(0o755)
        script = w.ROOT.parent / 'legacy-scan.sh'
        (self.base / 'no-config').touch()
        env = dict(os.environ, PATH=f'{stubs}:{os.environ["PATH"]}', STUB_DIR=str(self.base),
                   ORCH_CONFIG=str(config or self.base / 'no-config'))
        return subprocess.run([str(script), *args], env=env, capture_output=True, text=True)

    def test_cli_prints_classified_lines_for_both_paths(self):
        expected = ''.join(f'{kind:<6} {path}: {text}\n' for kind, path, text in HITS)
        for mode in ('diff', 'big'):
            result = self.cli('7', 'owner/product', mode=mode)
            self.assertEqual((result.returncode, result.stdout), (0, expected), mode)

    def test_cli_uses_the_configured_python(self):
        python = self.base / 'python'
        python.write_text('#!/bin/sh\ncase "$1" in -c) exec python3 "$@" ;; esac\necho configured "$@"\n')
        python.chmod(0o755)
        config = self.base / 'orchestration.toml'
        config.write_text(f'[watchers]\npython3 = "{python}"\n')
        result = self.cli('7', config=config)
        self.assertTrue(result.stdout.startswith('configured '), result.stdout)

    def test_cli_failure_exits_nonzero_without_output(self):
        result = self.cli('7', "owner/product", mode='fail')
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertIn('legacy-scan failed: HTTP 502', result.stderr)
        self.assertEqual(self.cli().returncode, 2)
        with patch.dict(os.environ, ORCH_WATCHERS_PRIMARY_REPO=""):
            self.assertEqual(self.cli("7").returncode, 2)
        config = self.base / "primary.toml"
        config.write_text('[watchers]\nprimary_repo = "owner/product"\n')
        with patch.dict(os.environ):
            os.environ.pop("ORCH_WATCHERS_PRIMARY_REPO", None)
            self.assertEqual(self.cli("7", config=config).returncode, 0)


if __name__ == '__main__':
    unittest.main()
