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
sys.path.insert(0, str(REPO / "lib"))
import orch_settings as s  # noqa: E402


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


class SettingsTest(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        home = patch.dict(os.environ, {"HOME": str(self.base / "home")})
        home.start()
        self.addCleanup(home.stop)

    def write(self, name, text):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def repo(self, name="repo", origin="git@github.com:acme/webapp.git"):
        path = self.base / name
        path.mkdir()
        git(path, "init", "-q")
        git(path, "remote", "add", "origin", origin)
        return path

    def error(self, text, scope="global"):
        with self.assertRaises(s.ConfigError) as caught:
            s.read_file(self.write("c.toml", text), scope)
        return str(caught.exception)


class GlobalFileTest(SettingsTest):
    def test_a_valid_file_gives_the_orch_strings_scripts_read(self):
        path = self.write(
            "c.toml",
            '[pr]\nauthor = "me"\n'
            "[recycle]\npark_idle = true\npct = 55.5\ntokens = 1000\n"
            '[watchers]\npath = ["~/bin", "/usr/bin"]\n'
            '[pr_tabs]\norg = "acme"\n',
        )
        self.assertEqual(
            s.load_global(path),
            {
                "ORCH_PR_AUTHOR": "me",
                "ORCH_PARK_IDLE": "1",
                "ORCH_RECYCLE_PCT": "55.5",
                "ORCH_RECYCLE_TOKENS": "1000",
                "ORCH_PATH": f"{Path.home()}/bin:/usr/bin",
                "ORCH_PR_TABS_ORG": "acme",
            },
        )

    def test_a_missing_file_sets_nothing(self):
        self.assertEqual(s.load_global(self.base / "missing.toml"), {})

    def test_each_problem_names_the_file_and_the_key(self):
        path = str(self.base / "c.toml")
        cases = {
            "[pr\n": "invalid TOML",
            "[pr]\nnope = 1\n": "pr.nope: unknown key",
            "[nope]\nx = 1\n": "nope.x: unknown key",
            "stray = 1\n": "stray: unknown key",
            "[pr]\nauthor = 1\n": "pr.author: must be a string, got int",
            '[heavy_slots]\nmax = "4"\n': "heavy_slots.max: must be an integer, got str",
            "[heavy_slots]\nmax = true\n": "heavy_slots.max: must be an integer, got bool",
            "[recycle]\npark_idle = 1\n": "recycle.park_idle: must be true or false, got int",
            '[watchers]\npath = "a/b"\n': "watchers.path: must be a list of strings, got str",
            "[watchers]\npath = [1]\n": "watchers.path: must be a list of strings, got list",
            '[pr]\nauthor = "a\\u0000b"\n': "pr.author: contains a NUL character",
            '[watchers]\npath = ["/a:/b"]\n': 'watchers.path: entry "/a:/b" is empty or contains ":"',
            '[watchers]\npath = [""]\n': 'watchers.path: entry "" is empty',
        }
        for text, problem in cases.items():
            with self.subTest(text=text):
                message = self.error(text)
                self.assertTrue(message.startswith(path + ": "), message)
                self.assertIn(problem, message)

    def test_a_repo_key_in_the_global_file_is_an_error(self):
        message = self.error("[guard]\nbig_repo = true\n")
        self.assertIn("guard.big_repo: is a repo setting", message)
        self.assertIn(".orchestration.toml", message)

    def test_a_global_key_in_a_repo_file_is_an_error(self):
        message = self.error('[pr]\nauthor = "me"\n', "repo")
        self.assertIn(
            "pr.author: is a global setting; set it in the global ~/.claude/orchestration.toml",
            message,
        )

    def test_a_dropped_key_is_skipped_with_a_warning(self):
        stderr = io.StringIO()
        path = self.write(
            "c.toml",
            '[pr]\nrepos = ["a/b"]\nauthor = "me"\n[audit]\ngit_watch_repos = []\n'
            '[focus]\nskip_architects = ["A"]\n',
        )
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(s.load_global(path), {"ORCH_PR_AUTHOR": "me"})
        for key in ("pr.repos", "audit.git_watch_repos", "focus.skip_architects"):
            self.assertIn(f"WARNING: {path}: {key}: not read", stderr.getvalue())

    def test_a_repo_list_entry_with_its_separator_is_an_error(self):
        message = self.error('[legacy]\nexempt = ["a b.js"]\n', "repo")
        self.assertIn(
            'legacy.exempt: entry "a b.js" is empty or contains whitespace', message
        )
        message = self.error('[audit]\nmerge_gate_checks = ["A|B"]\n', "repo")
        self.assertIn(
            'audit.merge_gate_checks: entry "A|B" is empty or contains "|"', message
        )

    def test_old_python_is_named(self):
        with patch.object(s, "tomllib", None):
            with self.assertRaisesRegex(s.ConfigError, "needs Python 3.11 or newer"):
                s.parse("", "c.toml")


class SetKeyTest(SettingsTest):
    def test_set_key_edits_dotted_quoted_and_plain_forms_in_place(self):
        for text in (
            '# mine\npr.since = ""\n[herdr]\nbin = "x"\n',
            '# mine\n["pr"]\n"since" = ""\n[herdr]\nbin = "x"\n',
            '# mine\n[pr]\nsince = ""\n[herdr]\nbin = "x"\n',
            '# mine\n"pr"."since" = ""\n[herdr]\nbin = "x"\n',
        ):
            with self.subTest(text=text):
                path = self.write("c.toml", text)
                s.set_key(path, "ORCH_PR_SINCE", "2026-10-01")
                s.set_key(path, "ORCH_PR_AUTHOR", "me")
                self.assertEqual(
                    s.load_global(path),
                    {
                        "ORCH_PR_SINCE": "2026-10-01",
                        "ORCH_PR_AUTHOR": "me",
                        "ORCH_HERDR_BIN": "x",
                    },
                )
                self.assertTrue(path.read_text().startswith("# mine\n"))


class DropKeysTest(SettingsTest):
    def test_drop_removes_every_form_and_its_comment_and_keeps_the_rest(self):
        text = (
            '# mine\n[pr]\n# GitHub repos\nrepos = [\n  "a/b",  # first\n  "c/d",\n]\nauthor = "me"\n'
            '[audit]\n"git_watch_repos" = ["/x"]\npacks_max = 9\n'
            "[focus]\n# Architect names left out of focus reports\nskip_architects = []\n"
            '[herdr]\nbin = "x"  # kept\n'
        )
        path = self.write("c.toml", text)
        self.assertEqual(
            s.drop_keys(path),
            ["pr.repos", "audit.git_watch_repos", "focus.skip_architects"],
        )
        self.assertEqual(
            path.read_text(),
            '# mine\n[pr]\nauthor = "me"\n[audit]\npacks_max = 9\n[focus]\n'
            '[herdr]\nbin = "x"  # kept\n',
        )
        self.assertEqual(s.drop_keys(path), [])

    def test_drop_handles_dotted_keys(self):
        path = self.write("c.toml", "pr.repos = ['a/b']\npr.author = 'me'\n")
        self.assertEqual(s.drop_keys(path), ["pr.repos"])
        self.assertEqual(path.read_text(), "pr.author = 'me'\n")

    def test_drop_of_the_only_dotted_key_of_a_table(self):
        path = self.write("c.toml", 'pr.author = "me"\nfocus.skip_architects = ["A"]\n')
        self.assertEqual(s.drop_keys(path), ["focus.skip_architects"])
        self.assertEqual(path.read_text(), 'pr.author = "me"\n')

    def test_drop_names_a_key_it_cannot_reach_instead_of_leaving_it(self):
        text = 'pr = { repos = ["acme/web"], author = "me" }\n'
        path = self.write("c.toml", text)
        with self.assertRaisesRegex(
            s.ConfigError, "cannot drop pr.repos in place; edit it by hand"
        ):
            s.drop_keys(path)
        self.assertEqual(path.read_text(), text)

    def test_drop_leaves_a_file_without_dropped_keys_untouched(self):
        path = self.write("c.toml", '[pr]\nauthor = "me"')
        self.assertEqual(s.drop_keys(path), [])
        self.assertEqual(path.read_text(), '[pr]\nauthor = "me"')

    def test_drop_refuses_an_edit_it_cannot_make_cleanly(self):
        for text in (
            "[pr]\nrepos = '''\nauthor = \"x\"\n'''\nauthor = \"me\"\n",
            "[pr]\nrepos = ['''\n[x]\n''']\nauthor = \"me\"\n",
        ):
            with self.subTest(text=text):
                path = self.write("c.toml", text)
                with self.assertRaisesRegex(
                    s.ConfigError, "cannot drop pr.repos in place"
                ):
                    s.drop_keys(path)
                self.assertEqual(path.read_text(), text)


class RepoFileTest(SettingsTest):
    def test_no_repo_file_gives_the_built_in_defaults(self):
        root = self.repo()
        self.assertEqual(
            s.load_repo(root, {}),
            {
                setting.env: s.env_value(setting, setting.default)
                for setting in s.scoped("repo")
            },
        )
        self.assertEqual(s.load_repo(self.base, {})["ORCH_BIG_REPO"], "0")

    def test_finalize_commands_default_to_both_skills_and_a_repo_file_overrides(self):
        both = "/finalize-change-footprint|/finalize-code-comments"
        root = self.repo()
        self.assertEqual(
            s.load_repo(root, {"ORCH_CONFIG": str(self.write("empty.toml", ""))})[
                "ORCH_REVIEW_FINALIZE_COMMANDS"
            ],
            both,
        )
        (root / s.REPO_FILE).write_text(
            '[review]\nfinalize_commands = ["/finalize-code-comments --fix"]\n'
        )
        self.assertEqual(
            s.load_repo(root, {})["ORCH_REVIEW_FINALIZE_COMMANDS"],
            "/finalize-code-comments --fix",
        )
        (root / s.REPO_FILE).write_text("[review]\nfinalize_commands = []\n")
        self.assertEqual(s.load_repo(root, {})["ORCH_REVIEW_FINALIZE_COMMANDS"], "")
        self.assertEqual(
            s.load_repo(
                root, {"ORCH_REVIEW_FINALIZE_COMMANDS": "/finalize-code-comments"}
            )["ORCH_REVIEW_FINALIZE_COMMANDS"],
            "/finalize-code-comments",
        )

    def test_the_global_file_sets_the_finalize_defaults_for_every_repo(self):
        root = self.repo()
        config = self.write(
            "global.toml",
            '[review]\nfinalize_commands = ["/only-this"]\nfinalize_comment = false\n',
        )
        environ = {"ORCH_CONFIG": str(config)}
        values = s.load_repo(root, environ)
        self.assertEqual(values["ORCH_REVIEW_FINALIZE_COMMANDS"], "/only-this")
        self.assertEqual(values["ORCH_REVIEW_FINALIZE_COMMENT"], "0")
        (root / s.REPO_FILE).write_text(
            "[review]\nfinalize_commands = []\nfinalize_comment = true\n"
        )
        values = s.load_repo(root, environ)
        self.assertEqual(values["ORCH_REVIEW_FINALIZE_COMMANDS"], "")
        self.assertEqual(values["ORCH_REVIEW_FINALIZE_COMMENT"], "1")
        remote = s.load_remote("acme/app", "main", None, environ=environ)
        self.assertEqual(remote["ORCH_REVIEW_FINALIZE_COMMANDS"], "/only-this")

    def test_finalize_comment_is_on_by_default_and_reads_from_the_repo_file(self):
        root = self.repo()
        self.assertEqual(
            s.load_repo(root, {"ORCH_CONFIG": str(self.write("empty.toml", ""))})[
                "ORCH_REVIEW_FINALIZE_COMMENT"
            ],
            "1",
        )
        (root / s.REPO_FILE).write_text("[review]\nfinalize_comment = false\n")
        self.assertEqual(s.load_repo(root, {})["ORCH_REVIEW_FINALIZE_COMMENT"], "0")
        self.assertEqual(
            s.load_repo(root, {"ORCH_REVIEW_FINALIZE_COMMENT": "1"})[
                "ORCH_REVIEW_FINALIZE_COMMENT"
            ],
            "1",
        )

    def test_the_global_loader_leaves_the_shared_review_keys_to_the_repo_loader(self):
        config = self.write("global.toml", "[review]\nfinalize_comment = false\n")
        self.assertEqual(s.load_global(config), {})

    def test_the_repo_file_is_found_from_a_subdirectory(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text(
            '[guard]\nbig_repo = true\n[legacy]\nexempt = ["a", "b"]\n'
        )
        (root / "app/models").mkdir(parents=True)
        values = s.load_repo(root / "app/models", {})
        self.assertEqual(
            (values["ORCH_BIG_REPO"], values["ORCH_LEGACY_EXEMPT"]), ("1", "a b")
        )

    def test_the_local_file_overrides_the_committed_one_and_the_environment_wins(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text(
            '[focus]\npattern = "shared"\narchitect = "A"\n[legacy]\nexempt = ["a", "b"]\n'
        )
        (root / s.LOCAL_FILE).write_text(
            '[focus]\npattern = "mine"\n[legacy]\nexempt = ["c"]\n'
        )
        values = s.load_repo(root, {})
        self.assertEqual(
            (
                values["ORCH_FOCUS_PATTERN"],
                values["ORCH_FOCUS_ARCHITECT"],
                values["ORCH_LEGACY_EXEMPT"],
            ),
            ("mine", "A", "c"),
        )
        self.assertEqual(
            s.load_repo(root, {"ORCH_FOCUS_PATTERN": "env"})["ORCH_FOCUS_PATTERN"],
            "env",
        )

    def test_pi_deepseek_is_off_unless_the_local_file_turns_it_on(self):
        root = self.repo()
        self.assertEqual(s.load_repo(root, {})["ORCH_PI_DEEPSEEK"], "0")
        (root / s.LOCAL_FILE).write_text("[pi]\ndeepseek = true\n")
        self.assertEqual(s.load_repo(root, {})["ORCH_PI_DEEPSEEK"], "1")
        (root / s.LOCAL_FILE).write_text("[pi]\ndeepseek = false\n")
        self.assertEqual(s.load_repo(root, {})["ORCH_PI_DEEPSEEK"], "0")

    def test_pi_deepseek_in_the_committed_file_does_not_opt_in(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text(
            "[pi]\ndeepseek = true\n[guard]\nbig_repo = true\n"
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            values = s.load_repo(root, {})
        self.assertEqual(
            (values["ORCH_PI_DEEPSEEK"], values["ORCH_BIG_REPO"]), ("0", "1")
        )
        self.assertIn("not read from a committed file", err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                s.load_remote("acme/webapp", "main", "[pi]\ndeepseek = true\n")[
                    "ORCH_PI_DEEPSEEK"
                ],
                "0",
            )
        (root / s.LOCAL_FILE).write_text("[pi]\ndeepseek = true\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(s.load_repo(root, {})["ORCH_PI_DEEPSEEK"], "1")

    def test_pi_deepseek_in_a_tracked_local_file_does_not_opt_in(self):
        root = self.repo()
        (root / s.LOCAL_FILE).write_text(
            "[pi]\ndeepseek = true\n[guard]\nbig_repo = true\n"
        )
        git(root, "add", "-f", s.LOCAL_FILE)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            values = s.load_repo(root, {})
        self.assertEqual(
            (values["ORCH_PI_DEEPSEEK"], values["ORCH_BIG_REPO"]), ("0", "1")
        )
        self.assertIn("is tracked by git", err.getvalue())
        self.assertIn("not read from a committed file", err.getvalue())

    def test_pi_deepseek_does_not_opt_in_when_git_cannot_say_whether_the_file_is_tracked(
        self,
    ):
        root = self.repo()
        (root / s.LOCAL_FILE).write_text(
            "[pi]\ndeepseek = true\n[guard]\nbig_repo = true\n"
        )
        self.assertEqual(s.load_repo(root, {})["ORCH_PI_DEEPSEEK"], "1")
        (root / ".git" / "index").write_bytes(b"not an index")
        self.assertIsNone(s.local_tracked(root))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            values = s.load_repo(root, {})
        self.assertEqual(
            (values["ORCH_PI_DEEPSEEK"], values["ORCH_BIG_REPO"]), ("0", "1")
        )
        self.assertIn("could not say whether it is tracked", err.getvalue())

    def test_pi_deepseek_does_not_opt_in_when_the_tracking_probe_fails_to_run(self):
        root = self.repo()
        (root / s.LOCAL_FILE).write_text("[pi]\ndeepseek = true\n")
        with (
            patch.object(
                s.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 5)
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(s.load_repo(root, {})["ORCH_PI_DEEPSEEK"], "0")

    def test_a_linked_worktree_reads_pi_deepseek_from_its_main_checkout(self):
        main = self.repo("main")
        git(
            main,
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        )
        git(main, "worktree", "add", "-q", "-b", "side", str(self.base / "side"))
        (main / s.LOCAL_FILE).write_text("[pi]\ndeepseek = true\n")
        self.assertEqual(s.load_repo(self.base / "side", {})["ORCH_PI_DEEPSEEK"], "1")

    def test_an_exported_variable_wins_for_every_repo_setting(self):
        root = self.repo()
        (root / s.REPO_FILE).write_text(
            s.render({setting: example_value(setting) for setting in s.scoped("repo")})
        )
        environ = {setting.env: f"env-{setting.env}" for setting in s.scoped("repo")}
        self.assertEqual(s.load_repo(root, environ), environ)

    def test_a_tracked_local_file_warns(self):
        root = self.repo()
        (root / s.LOCAL_FILE).write_text("[guard]\nbig_repo = true\n")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(s.load_repo(root, {})["ORCH_BIG_REPO"], "1")
        self.assertEqual(stderr.getvalue(), "")
        git(root, "add", s.LOCAL_FILE)
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(s.load_repo(root, {})["ORCH_BIG_REPO"], "1")
        self.assertIn(
            f"WARNING: {root.resolve() / s.LOCAL_FILE} is tracked by git",
            stderr.getvalue(),
        )
        self.assertIn(f"rm --cached {s.LOCAL_FILE}", stderr.getvalue())

    def test_a_global_key_in_the_local_file_is_an_error(self):
        root = self.repo()
        (root / s.LOCAL_FILE).write_text("[heavy_slots]\nmax = 2\n")
        with self.assertRaisesRegex(
            s.ConfigError, "heavy_slots.max: is a global setting"
        ):
            s.load_repo(root, {})


class RemoteReadTest(SettingsTest):
    def command(self, committed):
        calls = []

        def run(*args):
            calls.append(args)
            if args[-1].startswith(
                "repos/acme/webapp/contents/.orchestration.toml?ref="
            ):
                if committed is None:
                    raise subprocess.CalledProcessError(
                        1, args, stderr="gh: Not Found (HTTP 404)"
                    )
                return committed
            raise AssertionError(args)

        return run, calls

    def test_the_committed_file_comes_from_the_base_ref(self):
        run, calls = self.command('[legacy]\nscan = true\nexempt = ["shared"]\n')
        with open_work():
            values = audit.repo_settings(run, "acme/webapp", "feature/base", {})
        self.assertEqual(
            (values["ORCH_LEGACY_SCAN"], values["ORCH_LEGACY_EXEMPT"]), ("1", "shared")
        )
        self.assertEqual(
            calls[0][-1],
            "repos/acme/webapp/contents/.orchestration.toml?ref=feature%2Fbase",
        )

    def test_a_clone_of_the_same_name_on_another_host_is_not_the_github_clone(self):
        lab = self.repo("lab", "git@gitlab.com:acme/webapp.git")
        hub = self.repo("hub", "ssh://git@github.com:22/acme/webapp.git")
        self.assertEqual(s.known_clone("acme/webapp", [str(lab), str(hub)]), str(hub))
        self.assertIsNone(s.known_clone("acme/webapp", [str(lab)]))

    def test_a_fork_clone_is_the_known_clone_of_its_upstream_repo(self):
        fork = self.repo("fork", "git@github.com:me/webapp.git")
        git(fork, "remote", "add", "upstream", "https://github.com/acme/webapp.git")
        elsewhere = self.repo("elsewhere", "git@github.com:acme/other.git")
        self.assertEqual(
            s.known_clone("acme/webapp", [str(elsewhere), str(fork)]), str(fork)
        )
        self.assertTrue(s.origin_matches(fork, "ACME/webapp"))
        self.assertIsNone(s.known_clone("acme/webapp", [str(elsewhere)]))

    def test_the_matching_remote_names_origin_before_upstream(self):
        clone = self.repo("clone", "git@github.com:acme/webapp.git")
        git(clone, "remote", "add", "upstream", "git@github.com:acme/webapp.git")
        self.assertEqual(s.matching_remote(clone, "acme/webapp"), "origin")
        fork = self.repo("fork", "git@github.com:me/webapp.git")
        git(fork, "remote", "add", "upstream", "git@github.com:acme/webapp.git")
        self.assertEqual(s.matching_remote(fork, "ACME/webapp"), "upstream")
        self.assertIsNone(s.matching_remote(fork, "acme/other"))

    def test_a_clone_whose_origin_is_the_repo_wins_over_a_fork_clone(self):
        fork = self.repo("fork", "git@github.com:me/webapp.git")
        git(fork, "remote", "add", "upstream", "https://github.com/acme/webapp.git")
        direct = self.repo("direct")
        self.assertEqual(
            s.known_clone("acme/webapp", [str(fork), str(direct)]), str(direct)
        )

    def test_a_non_github_upstream_is_not_a_match(self):
        fork = self.repo("fork", "git@github.com:me/webapp.git")
        git(fork, "remote", "add", "upstream", "git@gitlab.com:acme/webapp.git")
        self.assertIsNone(s.known_clone("acme/webapp", [str(fork)]))

    def test_an_unreadable_clone_upstream_is_a_settings_read_error(self):
        clone = self.repo("clone", "git@github.com:me/webapp.git")
        real = subprocess.run

        def run(args, *rest, **kwargs):
            if (
                args[:4] == ["git", "-C", str(clone), "remote"]
                and args[-1] == "upstream"
            ):
                return subprocess.CompletedProcess(
                    args, 128, "", "fatal: detected dubious ownership in repository"
                )
            return real(args, *rest, **kwargs)

        with (
            patch.object(s.subprocess, "run", run),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            s.known_clone("acme/webapp", [str(clone)])

    def test_the_local_file_of_a_fork_clone_applies_to_a_pr_on_its_upstream_repo(self):
        fork = self.repo("fork", "git@github.com:me/webapp.git")
        git(fork, "remote", "add", "upstream", "git@github.com:acme/webapp.git")
        (fork / s.LOCAL_FILE).write_text('[legacy]\nexempt = ["mine"]\n')
        run, _ = self.command('[legacy]\nscan = true\nexempt = ["shared"]\n')
        with open_work("", str(fork)):
            values = audit.repo_settings(run, "acme/webapp", "main", {})
        self.assertEqual(
            (values["ORCH_LEGACY_SCAN"], values["ORCH_LEGACY_EXEMPT"]), ("1", "mine")
        )

    def test_an_unreadable_clone_origin_is_a_settings_read_error(self):
        clone = self.repo("clone")
        real = subprocess.run

        def run(args, *rest, **kwargs):
            if args[:4] == ["git", "-C", str(clone), "remote"]:
                return subprocess.CompletedProcess(
                    args, 128, "", "fatal: detected dubious ownership in repository"
                )
            return real(args, *rest, **kwargs)

        command, _ = self.command("[legacy]\nscan = false\n")
        with open_work("", str(clone)), patch.object(s.subprocess, "run", run):
            with self.assertRaises(subprocess.CalledProcessError):
                audit.repo_settings(command, "acme/webapp", "main", {})
            with self.assertRaises(subprocess.CalledProcessError):
                s.known_clone("acme/webapp", [str(clone)])

    def test_a_clone_without_origin_is_not_the_known_clone(self):
        bare = self.base / "no-origin"
        bare.mkdir()
        git(bare, "init", "-q")
        self.assertIsNone(
            s.known_clone("acme/webapp", [str(bare), str(self.base / "gone")])
        )

    def test_the_local_file_of_a_known_clone_overrides_the_base_ref(self):
        clone = self.repo("clone")
        (clone / s.LOCAL_FILE).write_text('[legacy]\nexempt = ["mine"]\n')
        other = self.repo("other", "https://github.com/acme/other.git")
        (other / s.LOCAL_FILE).write_text('[legacy]\nexempt = ["wrong-clone"]\n')
        run, _ = self.command('[legacy]\nscan = true\nexempt = ["shared"]\n')
        with open_work("", f"{other} {clone}"):
            values = audit.repo_settings(run, "acme/webapp", "main", {})
        self.assertEqual(
            (values["ORCH_LEGACY_SCAN"], values["ORCH_LEGACY_EXEMPT"]), ("1", "mine")
        )

    def test_a_watched_linked_worktree_also_applies_its_main_checkout_file(self):
        main = self.repo("main")
        git(
            main,
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        )
        git(main, "worktree", "add", "-q", "-b", "side", str(self.base / "side"))
        (main / s.LOCAL_FILE).write_text('[legacy]\nscan = true\nexempt = ["main"]\n')
        (self.base / "side" / s.LOCAL_FILE).write_text('[legacy]\nexempt = ["side"]\n')
        run, _ = self.command(None)
        with open_work("", str(self.base / "side")):
            values = audit.repo_settings(run, "acme/webapp", "main", {})
        self.assertEqual(
            (values["ORCH_LEGACY_SCAN"], values["ORCH_LEGACY_EXEMPT"]), ("1", "side")
        )

    def test_without_a_known_clone_only_the_committed_file_applies(self):
        run, _ = self.command(None)
        with open_work("", str(self.base / "missing")):
            values = audit.repo_settings(run, "acme/webapp", "main", {})
        self.assertEqual(values["ORCH_LEGACY_SCAN"], "0")

    def test_a_broken_committed_file_is_a_read_error_naming_repo_and_ref(self):
        run, _ = self.command('[pr]\nauthor = "me"\n')
        with open_work():
            with self.assertRaisesRegex(
                ValueError,
                r"acme/webapp@main:\.orchestration\.toml: pr\.author: is a global setting",
            ):
                audit.repo_settings(run, "acme/webapp", "main", {})

    def test_one_read_per_repo_and_ref(self):
        run, calls = self.command("")
        cache = {}
        with open_work():
            for _ in range(3):
                audit.repo_settings(run, "acme/webapp", "main", cache)
            audit.repo_settings(run, "acme/webapp", "next", cache)
        self.assertEqual(len(calls), 2)


class PullsSettingsTest(SettingsTest):
    def test_pr_events_read_merge_gate_checks_from_the_base_ref(self):
        pr = json.loads(
            (REPO / "coordinator/watchers/fixtures/audit-pr.json").read_text()
        )
        pr.update(reviewDecision="APPROVED", baseRefName="feature/base")
        pr["latestOpinionatedReviews"]["nodes"] = [
            dict(state="APPROVED", commit=dict(oid=pr["headRefOid"]))
        ]
        calls = []

        def command(*args):
            calls.append(args[-1])
            if "/contents/.orchestration.toml?" in args[-1]:
                return '[audit]\nmerge_gate_checks = ["Full gate"]\n'
            if args[:3] == ("gh", "api", "graphql"):
                return json.dumps(
                    dict(
                        data=dict(
                            search=dict(nodes=[pr], pageInfo=dict(hasNextPage=False))
                        )
                    )
                )
            return json.dumps(dict(result=dict(workspaces=[], panes=[])))

        with (
            patch.dict(os.environ, {"ORCH_PR_AUTHOR": "author"}),
            open_work("owner/product"),
        ):
            os.environ.pop("ORCH_MERGE_GATE_CHECKS", None)
            pulls = w.collect_pulls(command, None)
        line = next(iter(pulls["prs"].values()))["lines"].get("APPROVED-STACKED", "")
        self.assertIn("full CI not run", line)
        self.assertIn(
            "repos/owner/product/contents/.orchestration.toml?ref=feature%2Fbase", calls
        )

    def test_a_broken_repo_file_is_a_pulls_readfail_until_it_recovers(self):
        pr = json.loads(
            (REPO / "coordinator/watchers/fixtures/audit-pr.json").read_text()
        )
        pr.update(reviewDecision="APPROVED", baseRefName="feature/base")
        pr["latestOpinionatedReviews"]["nodes"] = [
            dict(state="APPROVED", commit=dict(oid=pr["headRefOid"]))
        ]
        repo_file = ['[audit]\nmerge_gate_checks = ["Full gate"]\n']

        def command(*args):
            if "/contents/.orchestration.toml?" in args[-1]:
                return repo_file[0]
            if args[:3] == ("gh", "api", "graphql"):
                return json.dumps(
                    dict(
                        data=dict(
                            search=dict(nodes=[pr], pageInfo=dict(hasNextPage=False))
                        )
                    )
                )
            return json.dumps(dict(result=dict(workspaces=[], panes=[])))

        with (
            patch.dict(os.environ, {"ORCH_PR_AUTHOR": "author"}),
            open_work("owner/product"),
        ):
            os.environ.pop("ORCH_MERGE_GATE_CHECKS", None)
            before, _ = w.reduce_pulls(None, w.collect_pulls(command, None))
            repo_file[0] = '[pr]\nauthor = "me"\n'
            broken, events = w.reduce_pulls(before, w.collect_pulls(command, before))
            self.assertEqual(events, ["PULLS READFAIL settings:owner/product#7"])
            repo_file[0] = '[audit]\nmerge_gate_checks = ["Full gate"]\n'
            _, events = w.reduce_pulls(broken, w.collect_pulls(command, broken))
            self.assertEqual(events, ["PULLS READFAIL cleared"])


def example_value(setting):
    if setting.choices:
        return list(setting.required) or setting.choices[:1]
    return {
        "str": "x",
        "int": 7,
        "number": 7,
        "bool": True,
        "list": ["x", "y"],
        "json": [{"name": "x"}],
        "tables": [{"name": "x"}],
    }[setting.kind]


class LegacyUpgradeTest(SettingsTest):
    def test_rules_are_an_array_of_tables_passed_on_as_json(self):
        path = self.write(
            "r.toml",
            '[legacy]\nscan = true\n[[legacy.rules]]\nkind = "A"\npaths = ["**"]\npattern = \'\\$\\(\'\n',
        )
        self.assertEqual(
            s.as_env(s.read_file(path, "repo"))["ORCH_LEGACY_RULES"],
            '[{"kind":"A","paths":["**"],"pattern":"\\\\$\\\\("}]',
        )
        self.assertIn(
            "legacy.rules: must be an array of tables",
            self.error("[legacy]\nrules = [1]\n", "repo"),
        )

    def test_removed_keys_name_the_conversion(self):
        self.assertIn(
            "legacy.extra_pattern: was removed; run install.sh to convert it to [[legacy.rules]]",
            self.error('[legacy]\nextra_pattern = "x"\n', "repo"),
        )
        self.assertIn(
            "legacy.exempt_paths: was removed; run install.sh to convert it to legacy.exempt",
            self.error("[legacy]\nexempt_paths = []\n", "repo"),
        )

    def test_upgrade_converts_the_removed_keys_and_keeps_every_other_line(self):
        text = (
            '# mine\n[guard]\nbig_repo = true\n\n[legacy]\nscan = true  # on\nextra_pattern = "uber-select"\n'
            'exempt_paths = [\n  "a.js",\n  "b.js",\n]\n\n[focus]\npattern = "x"\n'
        )
        path = self.write("local.toml", text)
        report = s.upgrade(path)
        self.assertEqual(
            path.read_text(),
            "# mine\n[guard]\nbig_repo = true\n\n[legacy]\nscan = true  # on\n"
            'exempt = [\n  "a.js",\n  "b.js",\n]\n\n[focus]\npattern = "x"\n\n'
            '[[legacy.rules]]\nkind = "LEGACY"\npattern = "uber-select"\npaths = ["**"]\n',
        )
        self.assertEqual(
            report,
            [
                f"converted {path}: legacy.extra_pattern -> [[legacy.rules]] (kind LEGACY, every path), "
                "legacy.exempt_paths -> legacy.exempt",
                f"{path}: {s.NO_BUILTIN_RULES}",
            ],
        )
        self.assertEqual(s.upgrade(path), [])

    def test_upgrade_of_dotted_keys_and_an_empty_pattern(self):
        path = self.write(
            "local.toml", 'legacy.extra_pattern = ""\nlegacy.exempt_paths = ["a"]\n'
        )
        self.assertEqual(
            s.upgrade(path),
            [
                f"converted {path}: legacy.extra_pattern -> [[legacy.rules]] (kind LEGACY, every path), "
                "legacy.exempt_paths -> legacy.exempt"
            ],
        )
        self.assertEqual(path.read_text(), 'legacy.exempt = ["a"]\n')
        path = self.write(
            "only.toml", 'legacy.extra_pattern = ""\n[guard]\nbig_repo = true\n'
        )
        self.assertEqual(
            s.upgrade(path),
            [
                f"converted {path}: legacy.extra_pattern -> [[legacy.rules]] (kind LEGACY, every path)"
            ],
        )
        self.assertEqual(path.read_text(), "[guard]\nbig_repo = true\n")
        for text in (
            'legacy.extra_pattern = ""\n',
            '[legacy]\nextra_pattern = ""\n',
            '[legacy]\nextra_pattern = ""\n[guard]\nbig_repo = true\n',
        ):
            path = self.write("alone.toml", text)
            self.assertEqual(len(s.upgrade(path)), 1, text)
            self.assertNotIn("extra_pattern", path.read_text(), text)
            self.assertEqual(
                s.read_file(path, "repo").get(s.BY_KEY["legacy.rules"]), None, text
            )

    def test_configured_clones_read_the_dropped_key_as_written(self):
        path = self.write(
            "g.toml", '[audit]\ngit_watch_repos = ["~/a", "/b", ""]\npacks_max = 9\n'
        )
        self.assertEqual(s.configured_clones(path), [f"{Path.home()}/a", "/b"])
        for text in (
            "",
            "[audit]\npacks_max = 9\n",
            '[audit]\ngit_watch_repos = "x"\n',
            "not toml [",
        ):
            self.assertEqual(s.configured_clones(self.write("g.toml", text)), [], text)
        self.assertEqual(s.configured_clones(self.base / "missing.toml"), [])

    def test_upgrade_targets_cover_clones_their_main_checkouts_and_files_once(self):
        main = self.repo("main")
        git(
            main,
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        )
        git(main, "worktree", "add", "-q", "-b", "side", str(self.base / "side"))
        side = self.base / "side"
        (main / s.LOCAL_FILE).write_text("")
        (side / s.LOCAL_FILE).write_text("")
        snippet = self.write("snip.toml", "")
        stray = self.write("stray", "not toml [")
        targets = s.upgrade_targets(
            [str(snippet), str(snippet)],
            [str(side), str(main), str(self.base / "gone"), str(stray)],
        )
        self.assertEqual(
            [p.resolve() for p in targets],
            [
                snippet.resolve(),
                (side / s.LOCAL_FILE).resolve(),
                (main / s.LOCAL_FILE).resolve(),
            ],
        )

    def test_upgrade_reads_quoted_table_headers(self):
        for header in ('["legacy"]', "['legacy']", '[ "legacy" ]'):
            path = self.write(
                "local.toml", f'{header}\nscan = true\nexempt_paths = ["a"]\n'
            )
            s.upgrade(path)
            self.assertEqual(
                path.read_text(), f'{header}\nscan = true\nexempt = ["a"]\n', header
            )

    def test_upgrade_keeps_rules_already_there(self):
        path = self.write(
            "local.toml",
            '[legacy]\nextra_pattern = "b"\n\n[[legacy.rules]]\nkind = "A"\npaths = ["**"]\n',
        )
        s.upgrade(path)
        self.assertEqual(
            [
                rule["kind"]
                for rule in s.read_file(path, "repo")[s.BY_KEY["legacy.rules"]]
            ],
            ["A", "LEGACY"],
        )

    def test_upgrade_refuses_what_it_cannot_convert_in_place(self):
        for text in (
            '[legacy]\nextra_pattern = """x"""\n',
            '[legacy]\nexempt_paths = ["a"]\nexempt = ["b"]\n',
            "[legacy]\nextra_pattern = 1\n",
            '[focus]\npattern = """\n[legacy]\nexempt_paths = "old-widget"\n"""\n[legacy]\nexempt_paths = ["a"]\n',
            "[legacy]\nextra_pattern = '(ab)\\1'\n",
        ):
            path = self.write("local.toml", text)
            with self.assertRaises(s.ConfigError, msg=text):
                s.upgrade(path)
            self.assertEqual(path.read_text(), text)

    def test_migration_from_the_env_file_writes_rules(self):
        values = {
            "ORCH_LEGACY_REPOS": "acme/webapp",
            "ORCH_LEGACY_EXTRA_PATTERN": "old-widget",
            "ORCH_LEGACY_EXEMPT_PATHS": "a.js b.js",
        }
        report = []
        layer = s.repo_layers(values, report)["acme/webapp"]
        self.assertEqual(
            layer,
            {
                s.BY_KEY["legacy.scan"]: True,
                s.BY_KEY["legacy.rules"]: [
                    {"kind": "LEGACY", "pattern": "old-widget", "paths": ["**"]}
                ],
                s.BY_KEY["legacy.exempt"]: ["a.js", "b.js"],
            },
        )
        self.assertEqual(report, [f"acme/webapp: {s.NO_BUILTIN_RULES}"])
        report = []
        layer = s.repo_layers(
            dict(values, ORCH_LEGACY_EXTRA_PATTERN="(ab)\\1"), report
        )["acme/webapp"]
        self.assertNotIn(s.BY_KEY["legacy.rules"], layer)
        self.assertTrue(
            report[0].startswith(
                "not converted: ORCH_LEGACY_EXTRA_PATTERN for acme/webapp is not an rg regex ("
            ),
            report,
        )
        self.assertEqual(s.validate(s.parse(s.render(layer), "x"), "x", "repo"), layer)


class ProvidersTest(SettingsTest):
    def resolve(self, text="", **environ):
        path = self.write("g.toml", text)
        return s.resolved_global(path, environ)

    def test_the_default_is_claude_and_codex_plus_pi_where_a_pi_model_is_set(self):
        self.assertEqual(self.resolve()["ORCH_PROVIDERS"], "claude,codex")
        self.assertEqual(
            self.resolve('[models]\npi_smart = "lmstudio/q"\n')["ORCH_PROVIDERS"],
            "claude,codex,pi",
        )
        self.assertEqual(
            s.required_models(self.resolve()),
            [
                "ORCH_COORDINATOR_MODEL",
                "ORCH_ARCHITECT_MODEL",
                "ORCH_CODEX_WORKER_MODEL",
                "ORCH_CODEX_SMART_MODEL",
            ],
        )
        self.assertEqual(
            s.fill_models(self.resolve()),
            ["ORCH_CODEX_WORKER_MODEL", "ORCH_CODEX_SMART_MODEL"],
        )

    def test_codex_off_needs_no_codex_models(self):
        values = self.resolve(
            '[providers]\nenabled = ["claude"]\n[models]\ncodex_worker = ""\ncodex_smart = "<codex-smart-model>"\n'
        )
        self.assertEqual(values["ORCH_PROVIDERS"], "claude")
        self.assertEqual(
            s.required_models(values),
            ["ORCH_COORDINATOR_MODEL", "ORCH_ARCHITECT_MODEL"],
        )
        self.assertEqual(s.fill_models(values), [])

    def test_pi_listed_is_on_without_its_models_and_never_required(self):
        values = self.resolve('[providers]\nenabled = ["pi", "claude"]\n')
        self.assertEqual(values["ORCH_PROVIDERS"], "claude,pi")
        self.assertEqual(
            s.required_models(values),
            ["ORCH_COORDINATOR_MODEL", "ORCH_ARCHITECT_MODEL"],
        )

    def test_an_exported_list_wins_over_the_file(self):
        values = self.resolve(
            '[providers]\nenabled = ["claude", "codex"]\n', ORCH_PROVIDERS="claude"
        )
        self.assertEqual(values["ORCH_PROVIDERS"], "claude")

    def test_a_list_without_claude_is_an_error_naming_the_file_and_key(self):
        error = self.error('[providers]\nenabled = ["codex"]\n')
        self.assertIn(
            'providers.enabled: must include "claude" (the Coordinator and the architects run on it)',
            error,
        )
        with self.assertRaisesRegex(
            s.ConfigError,
            r'providers.enabled \(ORCH_PROVIDERS\): must include "claude"',
        ):
            self.resolve(ORCH_PROVIDERS="codex")

    def test_an_unknown_provider_is_an_error(self):
        self.assertIn(
            'providers.enabled: unknown entry "gemini"; known: claude, codex, pi',
            self.error('[providers]\nenabled = ["claude", "gemini"]\n'),
        )
        with self.assertRaisesRegex(s.ConfigError, 'unknown entry ""'):
            self.resolve(ORCH_PROVIDERS="claude,")

    def test_one_codex_model_is_enough_and_serves_both_tiers(self):
        for worker, smart in (
            ("gpt-w", ""),
            ("", "gpt-s"),
            ("<codex-worker-model>", "gpt-s"),
        ):
            values = self.resolve(
                f'[models]\ncodex_worker = "{worker}"\ncodex_smart = "{smart}"\n'
            )
            model = worker if s.filled(worker) else smart
            kept = (
                "ORCH_CODEX_WORKER_MODEL"
                if s.filled(worker)
                else "ORCH_CODEX_SMART_MODEL"
            )
            self.assertEqual(values["ORCH_PROVIDERS"], "claude,codex")
            self.assertEqual(
                s.required_models(values),
                ["ORCH_COORDINATOR_MODEL", "ORCH_ARCHITECT_MODEL", kept],
            )
            self.assertEqual(s.fill_models(values), [kept])
            self.assertEqual(s.tier_model(values, "ORCH_CODEX_WORKER_MODEL"), model)
            self.assertEqual(s.tier_model(values, "ORCH_CODEX_SMART_MODEL"), model)

    def test_print_shows_the_set_codex_model_for_an_unset_tier(self):
        for text, model in (
            ('[models]\ncodex_smart = "only-smart"\n', "only-smart"),
            (
                '[models]\ncodex_worker = "only-worker"\ncodex_smart = ""\n',
                "only-worker",
            ),
        ):
            values = s.printed(self.resolve(text))
            self.assertEqual(values["ORCH_CODEX_WORKER_MODEL"], model)
            self.assertEqual(values["ORCH_CODEX_SMART_MODEL"], model)
            self.assertEqual(
                s.fill_models(self.resolve(text)),
                [
                    m
                    for m in ("ORCH_CODEX_WORKER_MODEL", "ORCH_CODEX_SMART_MODEL")
                    if m.endswith("WORKER_MODEL") == (model == "only-worker")
                ],
            )

    def test_print_keeps_codex_and_pi_values_that_need_no_fallback(self):
        for text in (
            '[models]\ncodex_worker = "w"\ncodex_smart = "s"\n',
            "",
            '[models]\ncodex_worker = ""\ncodex_smart = ""\npi_smart = "lm/q"\n',
        ):
            values = self.resolve(text)
            self.assertEqual(s.printed(values), values)

    def test_both_codex_models_set_are_both_still_listed(self):
        values = self.resolve('[models]\ncodex_worker = "w"\ncodex_smart = "s"\n')
        self.assertEqual(
            s.fill_models(values), ["ORCH_CODEX_WORKER_MODEL", "ORCH_CODEX_SMART_MODEL"]
        )

    def test_both_codex_tiers_empty_need_both_and_serve_none(self):
        values = self.resolve('[models]\ncodex_worker = ""\n')
        self.assertEqual(
            s.fill_models(values), ["ORCH_CODEX_WORKER_MODEL", "ORCH_CODEX_SMART_MODEL"]
        )
        self.assertEqual(s.tier_model(values, "ORCH_CODEX_WORKER_MODEL"), "")
        self.assertEqual(s.tier_model(values, "ORCH_CODEX_SMART_MODEL"), "")

    def test_an_empty_pi_tier_is_not_served_by_the_other(self):
        values = self.resolve('[models]\npi_smart = "lmstudio/q"\n')
        self.assertEqual(s.tier_model(values, "ORCH_PI_WORKER_MODEL"), "")
        self.assertEqual(s.tier_model(values, "ORCH_PI_SMART_MODEL"), "lmstudio/q")

    def test_every_provider_names_existing_model_settings(self):
        self.assertEqual(
            s.BY_KEY["providers.enabled"].choices, [p.name for p in s.PROVIDERS]
        )
        for provider in s.PROVIDERS:
            for model in provider.models:
                self.assertIn(model, s.BY_ENV)


class ClaudeTiersTest(SettingsTest):
    resolve = ProvidersTest.resolve

    def test_the_default_allows_every_tier(self):
        self.assertEqual(self.resolve()["ORCH_CLAUDE_TIERS"], "haiku,sonnet,opus,fable")
        self.assertEqual(
            s.BY_KEY["providers.claude"].default,
            ["haiku", "sonnet", "opus", "fable"],
        )

    def test_a_list_resolves_in_tier_order_and_an_export_wins(self):
        self.assertEqual(
            self.resolve('[providers]\nclaude = ["opus", "haiku", "sonnet"]\n')[
                "ORCH_CLAUDE_TIERS"
            ],
            "haiku,sonnet,opus",
        )
        self.assertEqual(
            self.resolve('[providers]\nclaude = ["opus"]\n', ORCH_CLAUDE_TIERS="fable")[
                "ORCH_CLAUDE_TIERS"
            ],
            "fable",
        )

    def test_an_unknown_tier_is_an_error(self):
        self.assertIn(
            'providers.claude: unknown entry "mini"; known: haiku, sonnet, opus, fable',
            self.error('[providers]\nclaude = ["sonnet", "mini"]\n'),
        )
        with self.assertRaisesRegex(
            s.ConfigError,
            r'providers.claude \(ORCH_CLAUDE_TIERS\): unknown entry "Opus"',
        ):
            self.resolve(ORCH_CLAUDE_TIERS="Opus")

    def test_an_empty_list_is_an_error(self):
        self.assertIn(
            "providers.claude: must name at least one of haiku, sonnet, opus, fable",
            self.error("[providers]\nclaude = []\n"),
        )
        with self.assertRaisesRegex(
            s.ConfigError,
            r"providers.claude \(ORCH_CLAUDE_TIERS\): must name at least one",
        ):
            self.resolve(ORCH_CLAUDE_TIERS="")


class DriftTest(SettingsTest):
    def test_coord_lib_keeps_every_schema_setting(self):
        text = (REPO / "coordinator/watchers/coord-lib.sh").read_text()
        names = (
            re.search(r'^ORCH_SETTINGS="([^"]*)"', text, re.M)[1]
            .replace("\\\n", " ")
            .split()
        )
        self.assertEqual(names, s.env_names())

    def test_the_examples_are_the_schema(self):
        self.assertEqual(
            (REPO / "config/orchestration.toml.example").read_text(),
            s.example("global"),
        )
        repo = (REPO / "config/repo.orchestration.toml.example").read_text()
        self.assertTrue(repo.startswith(s.example("repo") + "\n#"))
        self.assertTrue(
            all(
                line.startswith("#") or not line
                for line in repo[len(s.example("repo")) :].splitlines()
            )
        )
        self.assertEqual(
            s.read_file(REPO / "config/repo.orchestration.toml.example", "repo")[
                s.BY_KEY["legacy.scan"]
            ],
            False,
        )

    def test_the_settings_doc_lists_the_schema(self):
        doc = (REPO / "docs/settings.md").read_text()
        self.assertIn(
            "## Global settings\n\n"
            + s.markdown_table("global")
            + "\n\n## Repo settings\n\n"
            + s.markdown_table("repo")
            + "\n",
            doc,
        )

    def test_every_setting_has_one_scope_a_unique_name_and_a_valid_default(self):
        self.assertEqual(len(s.BY_KEY), len(s.SETTINGS))
        self.assertEqual(len(s.BY_ENV), len(s.SETTINGS))
        for setting in s.SETTINGS:
            with self.subTest(setting.key):
                self.assertIn(setting.scope, ("global", "repo"))
                self.assertTrue(s.check_type(setting, setting.default))

    def test_written_values_read_back_unchanged(self):
        values = {setting: example_value(setting) for setting in s.scoped("global")}
        path = self.write("round.toml", s.render(values))
        self.assertEqual(s.read_file(path, "global"), values)


class RemovedKeysTest(SettingsTest):
    def test_drop_retired_rules_preserves_other_lines_and_is_idempotent(self):
        for rules in [
            "topics = []\n",
            '"topics" = [\n {name="Old", any=["foo"]},\n]\n',
        ]:
            text = (
                '# personal\n[pr_tabs]\norg = "acme" # keep\n'
                + rules
                + 'open_all = "is:pr"\n'
            )
            path = self.write("removed.toml", text)
            self.assertTrue(s.drop_removed_keys(path))
            self.assertEqual(path.read_text(), text.replace(rules, ""))
            self.assertFalse(s.drop_removed_keys(path))

    def test_dotted_and_array_table_rules_are_removed(self):
        for text, expected in [
            ('pr_tabs.topics = []\n[pr]\nauthor = "me"\n', '[pr]\nauthor = "me"\n'),
            (
                '[pr_tabs]\norg = "acme"\n[[pr_tabs.topics]]\nname="Old"\n[[pr_tabs.topics]]\nname="Two"\n',
                '[pr_tabs]\norg = "acme"\n',
            ),
        ]:
            path = self.write("removed.toml", text)
            self.assertTrue(s.drop_removed_keys(path))
            self.assertEqual(path.read_text(), expected)

    def test_disjoint_topic_blocks_and_inline_table_keep_unrelated_settings(self):
        for text, expected in [
            (
                '[[pr_tabs.topics]]\nname="One"\n[pr]\nauthor="me" # keep\n[[pr_tabs.topics]]\nname="Two"\n',
                {"pr": {"author": "me"}},
            ),
            (
                'pr_tabs = { org = "acme", topics = [] } # keep\n',
                {"pr_tabs": {"org": "acme"}},
            ),
            (
                'pr_tabs = { topics = [{name="One"}], org = "acme" } # keep\n',
                {"pr_tabs": {"org": "acme"}},
            ),
        ]:
            path = self.write("removed.toml", text)
            self.assertTrue(s.drop_removed_keys(path))
            self.assertEqual(s.parse(path.read_text(), str(path)), expected)
            self.assertIn("# keep", path.read_text())

    def test_only_the_immediately_preceding_retired_example_comment_is_removed(self):
        retired = "# Tab-group rules: tables with name, color and repo/title/any regex lists\n"
        text = (
            retired
            + '# Personal note\n[pr_tabs]\norg = "acme"\n'
            + retired
            + "topics = []\n"
        )
        path = self.write("removed.toml", text)
        self.assertTrue(s.drop_removed_keys(path))
        self.assertEqual(
            path.read_text(), retired + '# Personal note\n[pr_tabs]\norg = "acme"\n'
        )

    def test_retired_key_migration_preserves_crlf_bytes(self):
        path = self.base / "crlf.toml"
        path.write_bytes(b'# personal\r\n[pr_tabs]\r\norg = "acme"\r\ntopics = []\r\n')
        self.assertTrue(s.drop_removed_keys(path))
        self.assertEqual(
            path.read_bytes(), b'# personal\r\n[pr_tabs]\r\norg = "acme"\r\n'
        )

    def test_legacy_topic_variable_is_retired_without_an_unknown_key_warning(self):
        old = self.write("old.env", "ORCH_PR_TABS_TOPICS=old\nORCH_PR_TABS_ORG=acme\n")
        target = self.base / "converted.toml"
        with patch.object(
            s,
            "env_file_values",
            return_value={"ORCH_PR_TABS_TOPICS": "old", "ORCH_PR_TABS_ORG": "acme"},
        ):
            report = s.migrate(old, target, self.base / "snippets")
        self.assertNotIn("unknown keys", "\n".join(report))
        self.assertEqual(s.load_global(target), {"ORCH_PR_TABS_ORG": "acme"})

    def test_both_retirement_migrations_keep_supported_settings(self):
        path = self.write(
            "both.toml",
            '[pr]\nrepos=["acme/web"]\nauthor="me"\n'
            '[focus]\nskip_architects=["Old"]\n[pr_tabs]\norg="acme"\ntopics=[]\n',
        )
        self.assertEqual(set(s.drop_keys(path)), {"pr.repos", "focus.skip_architects"})
        self.assertTrue(s.drop_removed_keys(path))
        self.assertEqual(
            s.load_global(path), {"ORCH_PR_AUTHOR": "me", "ORCH_PR_TABS_ORG": "acme"}
        )
        self.assertEqual(s.drop_keys(path), [])
        self.assertFalse(s.drop_removed_keys(path))
