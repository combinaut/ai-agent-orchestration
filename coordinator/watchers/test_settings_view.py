import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import feed_actions
import feed_attach

WATCHERS = Path(__file__).resolve().parent
sys.path.insert(0, str(WATCHERS.parents[1] / "lib"))
import orch_settings as s  # noqa: E402
from reminder_picker import SGR  # noqa: E402

Size = namedtuple("Size", "columns lines")
UP, DOWN, RIGHT, LEFT = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"
ENTER, ESC, BACKSPACE, CLEAR = "\r", "\x1b", "\x7f", "\x15"
FILLED = re.compile(r"\x1b\[[\d;]*48;5;\d+[\d;]*m ([a-z]+) \x1b\[0m")


def load_view():
    spec = importlib.util.spec_from_file_location(
        "settings_view", WATCHERS.parent / "settings-view.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoUpdates:
    """Stands in for the updater so no test of the other tabs runs git."""

    lines, offer = ["no update check"], False

    def __init__(self):
        self.checked, self.updated = [], []

    def check(self, repo):
        self.checked.append(repo)

    def update(self, repo):
        self.updated.append(repo)

    def poll(self):
        pass


class ViewCase(IsolatedTestCase):
    TEXT = '# mine\n[pr]\nauthor = "me"  # who\n'

    def setUp(self):
        self.view = load_view()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "orchestration.toml"
        self.path.write_text(self.TEXT)
        self.env = {"ORCH_CONFIG": str(self.path)}
        self.size = Size(100, 30)
        self.feed = Path(tmp.name) / "coordinator-feed.log"
        feed_attach.write_record(self.feed.with_name(feed_attach.MONITOR_NAME), 1, 0, 0)

    def updater(self):
        return NoUpdates()

    def app(self):
        self.out = io.StringIO()
        app = self.view.App(
            self.env,
            self.out,
            lambda: self.size,
            feed=self.feed,
            updates=self.updater(),
        )
        app.tick()
        return app

    def select(self, app, key):
        app.show_tab(self.view.tab_of(s.BY_KEY[key]))
        app.selected = [row.key for row in app.rows].index(key)
        app.render()

    def at(self, key):
        app = self.app()
        self.select(app, key)
        return app

    def keys(self, app, *keys):
        for key in keys:
            app.feed(key)
            app.lone_escape()

    def frame(self):
        return SGR.sub("", self.out.getvalue().split("\033[2J")[-1])

    def active(self):
        """The tab name drawn on a background colour."""
        (name,) = FILLED.findall(self.out.getvalue().split("\033[2J")[-1])
        return name

    def saved(self, key):
        return s.read_file(self.path, "global").get(s.BY_KEY[key])


class CoverageTest(ViewCase):
    def test_every_global_setting_says_when_it_takes_effect(self):
        self.assertEqual(
            sorted(self.view.APPLIES), sorted(x.key for x in s.listed("global"))
        )

    def test_apply_times_name_every_kind_of_reader(self):
        both = self.view.BOTH
        self.assertEqual(self.view.APPLIES["legacy.reads_max"], both)
        for key, words in (
            ("recycle.tokens", ("watchers", "sessions")),
            ("recycle.pct", ("script run", "watchers", "sessions")),
            ("heavy_slots.max", ("script run", "watchers", "sessions")),
            ("herdr.bin", ("script run", "services", "install.sh")),
            ("watchers.python3", ("script run", "services", "install.sh")),
            ("review.finalize_commands", ("script run", "session")),
        ):
            with self.subTest(key=key):
                for word in words:
                    self.assertIn(word, self.view.APPLIES[key])

    def test_every_global_setting_has_an_editor(self):
        kinds = {x.key: self.view.editor_kind(x) for x in s.listed("global")}
        self.assertEqual(
            set(kinds.values()),
            {"toggle", "cycle", "checklist", "list", "number", "text"},
        )
        self.assertEqual(
            [
                kinds[key]
                for key in (
                    "herdr.todo_pane",
                    "models.worker_effort",
                    "providers.claude",
                    "watchers.path",
                    "host.load_max",
                    "recycle.tokens",
                    "pr.author",
                )
            ],
            ["toggle", "cycle", "checklist", "list", "number", "number", "text"],
        )


TABS = [
    ("Models", ("providers", "models", "pi")),
    ("Workers", ("workers", "recycle", "heavy_slots", "review", "guard", "focus")),
    ("GitHub", ("pr", "pr_tabs", "audit", "legacy")),
    ("Watchers", ("watchers", "alerts", "hog", "host")),
    ("Herdr", ("herdr", "user_todo", "user_questions", "remote_control")),
    ("Update", ()),
    ("Info", ("install",)),
]


class TabTest(ViewCase):
    def tab_names(self):
        return [name.lower() for name, _ in TABS]

    def test_there_are_seven_tabs_in_order(self):
        self.assertEqual(list(self.view.TABS), TABS)

    def test_every_section_of_the_schema_is_on_exactly_one_tab(self):
        listed = [table for _, tables in self.view.TABS for table in tables]
        self.assertEqual(sorted(listed), sorted({x.table for x in s.SETTINGS}))

    def test_each_tab_lists_its_sections_in_order_and_nothing_is_lost(self):
        app = self.app()
        seen = []
        for _, tables in TABS:
            with self.subTest(tables=tables):
                keys = [row.key for row in app.rows]
                expected = [
                    x.key
                    for table in tables
                    for x in s.listed("global")
                    if x.table == table
                ]
                self.assertEqual(keys, expected)
                seen += keys
                self.keys(app, RIGHT)
        self.assertEqual(sorted(seen), sorted(x.key for x in s.listed("global")))

    def test_rows_show_full_keys_without_section_headers(self):
        self.at("models.architect")
        self.assertRegex(self.frame(), r"> models\.architect +\S")
        self.assertIn("\n  providers.enabled ", self.frame())
        self.assertNotIn("[providers]", self.frame())

    def test_the_longest_key_keeps_a_gap_before_its_value(self):
        longest = max((x.key for x in s.listed("global")), key=len)
        self.at(longest)
        self.assertRegex(self.frame(), rf"> {longest}  +\S")

    def test_arrows_switch_tabs_both_ways_and_wrap(self):
        app = self.app()
        names = []
        for key in (None, RIGHT, LEFT, LEFT, RIGHT):
            if key:
                self.keys(app, key)
            else:
                app.render()
            names.append(self.active())
        self.assertEqual(names, ["models", "workers", "models", "info", "models"])

    def test_a_new_tab_starts_on_its_first_row(self):
        app = self.app()
        self.keys(app, DOWN, DOWN, RIGHT)
        self.assertEqual(app.selected, 0)

    def test_arrows_on_the_list_never_write(self):
        for key in ("models.worker_effort", "herdr.todo_pane"):
            with self.subTest(key=key):
                self.keys(self.at(key), RIGHT)
                self.keys(self.at(key), LEFT)
                self.assertEqual(self.path.read_text(), self.TEXT)

    def test_the_tab_bar_names_all_tabs(self):
        self.app()
        self.assertRegex(self.frame(), " +".join(self.tab_names()))

    def test_a_narrow_terminal_still_shows_the_active_tab(self):
        self.size = Size(30, 30)
        app = self.app()
        self.keys(app, LEFT)
        self.assertEqual(self.active(), "info")

    def test_the_help_line_names_the_tab_keys(self):
        self.size = Size(100, 30)
        self.app()
        self.assertIn(
            "←→ tab · ↑↓ select · Enter edit · u default · a Apply · / filter · Esc/q close",
            self.frame(),
        )


class TabLookTest(ViewCase):
    def tab_rows(self):
        """The raw first two lines of the screen: tab row and the line under it."""
        frame = self.out.getvalue().split("\033[2J")[-1]
        return frame.split("\r\n")[:2]

    def test_the_active_tab_is_a_filled_bold_block_and_the_others_are_plain(self):
        self.app()
        names = self.tab_rows()[0]
        self.assertEqual(SGR.findall(names), ["\033[1;48;5;141;38;5;16m", "\033[0m"])
        self.assertIn("\033[1;48;5;141;38;5;16m models \033[0m", names)

    def test_a_thin_rule_runs_under_the_tab_row(self):
        for width in (100, 30):
            with self.subTest(width=width):
                self.size = Size(width, 30)
                self.app()
                self.assertEqual(self.tab_rows()[1], "─" * width)


class InfoTabTest(ViewCase):
    def test_the_info_tab_alone_shows_the_file_path(self):
        self.size = Size(200, 30)
        app = self.app()
        self.assertNotIn(str(self.path), self.frame())
        self.keys(app, LEFT)
        self.assertEqual(self.active(), "info")
        self.assertIn(f"Settings file: {self.path}", self.frame())
        self.assertNotIn("no setting matches", self.frame())

    def test_a_narrow_info_tab_wraps_the_whole_path(self):
        self.size = Size(30, 40)
        app = self.app()
        self.keys(app, LEFT)
        self.assertIn(str(self.path), self.frame().replace("\r\n", ""))

    def test_the_install_settings_sit_under_advanced_on_the_info_tab(self):
        app = self.app()
        self.keys(app, LEFT)
        frame = self.frame()
        order = [
            frame.index(text)
            for text in (
                "Settings file:",
                "\r\nadvanced\r\n",
                "install.repo_path",
                "install.env",
                "install.radar_path",
            )
        ]
        self.assertEqual(order, sorted(order))

    def test_an_install_setting_is_still_edited_on_the_info_tab(self):
        app = self.app()
        self.keys(app, LEFT, DOWN, ENTER, "x", ENTER)
        self.assertEqual(self.saved("install.env"), "x")

    def test_a_short_info_tab_scrolls_back_up_to_the_path(self):
        self.size = Size(60, 16)
        app = self.app()
        self.keys(app, LEFT, DOWN, DOWN)
        self.assertNotIn("Settings file:", self.frame())
        self.keys(app, UP, UP)
        self.assertIn("Settings file:", self.frame())
        self.assertIn("> install.repo_path ", self.frame())

    def test_a_filter_with_no_match_on_the_info_tab_says_so(self):
        app = self.app()
        self.keys(app, LEFT, "/", *"zzzz")
        self.assertIn("no setting matches", self.frame())

    def test_closing_an_empty_filter_returns_to_the_update_tab(self):
        for closing in (ESC, ENTER):
            with self.subTest(closing=closing):
                app = self.app()
                self.keys(app, LEFT, LEFT, "/", closing)
                self.assertEqual(self.active(), "update")

    def test_a_match_picked_from_the_update_tab_opens_its_own_tab(self):
        app = self.app()
        self.keys(app, LEFT, LEFT, "/", *"herdr.todo_pane", ENTER, ESC)
        self.assertEqual(self.active(), "herdr")
        self.assertEqual(app.rows[app.selected].key, "herdr.todo_pane")


class UpdateKeyTest(ViewCase):
    def app(self):
        self.fake = NoUpdates()
        self.updater = lambda: self.fake
        return super().app()

    def test_u_updates_only_on_the_update_tab(self):
        app = self.at("pr.author")
        self.keys(app, "u")
        self.assertEqual(self.fake.updated, [])
        self.keys(self.app(), LEFT, LEFT, "u")
        self.assertEqual(len(self.fake.updated), 1)

    def test_u_on_a_match_found_from_the_update_tab_means_default(self):
        app = self.app()
        self.keys(app, LEFT, LEFT, "/", *"pr.author", ENTER, "u")
        self.assertEqual(self.fake.updated, [])
        self.assertEqual(self.saved("pr.author"), "")

    def test_each_opening_of_the_tab_checks_once(self):
        app = self.app()
        self.assertEqual(self.fake.checked, [])
        self.keys(app, LEFT, LEFT)
        app.render()
        self.assertEqual(len(self.fake.checked), 1)
        self.keys(app, LEFT, RIGHT)
        self.assertEqual(len(self.fake.checked), 2)

    def test_the_checked_folder_follows_the_setting_and_the_environment(self):
        default = str(Path("~/.claude/ai-agent-orchestration").expanduser())
        for text, env, wanted in (
            ("", {}, default),
            ('[install]\nrepo_path = "/tmp/set"\n', {}, "/tmp/set"),
            (
                '[install]\nrepo_path = "/tmp/set"\n',
                {"ORCH_REPO_PATH": "/tmp/env"},
                "/tmp/env",
            ),
        ):
            with self.subTest(wanted=wanted):
                self.path.write_text(text)
                self.env = {"ORCH_CONFIG": str(self.path), **env}
                self.keys(self.app(), LEFT, LEFT)
                self.assertEqual(self.fake.checked, [wanted])


INSTALL_STUB = """#!/bin/sh
printf 'ran\\n' >>"$STUB_LOG"
if [ -n "${STUB_FAIL:-}" ]; then
  echo "ERROR: the stub install broke" >&2
  exit 1
fi
"""


class UpdateTest(ViewCase):
    """The update tab against a temporary origin and clone with a stub install.sh."""

    def setUp(self):
        super().setUp()
        root = self.path.parent
        self.origin, self.clone = root / "origin", root / "clone"
        self.stub_log = root / "stub.log"
        patcher = patch.dict(
            os.environ,
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "STUB_LOG": str(self.stub_log),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.origin.mkdir()
        self.git(self.origin, "init", "-q", "-b", "main")
        stub = self.origin / "install.sh"
        stub.write_text(INSTALL_STUB)
        stub.chmod(0o755)
        self.git(self.origin, "add", "install.sh")
        self.commit("first")
        self.git(root, "clone", "-q", str(self.origin), str(self.clone))
        self.path.write_text(f'[install]\nrepo_path = "{self.clone}"\n')

    def updater(self):
        import update_check

        self.updates = update_check.Updater()
        return self.updates

    def git(self, folder, *args):
        return subprocess.run(
            ["git", "-C", str(folder), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, *subjects):
        for subject in subjects:
            self.git(self.origin, "commit", "-q", "--allow-empty", "-m", subject)

    def settle(self, app):
        if self.updates.thread:
            self.updates.thread.join(60)
        if self.updates.process:
            self.updates.process.wait(60)
        app.tick()
        app.last_refresh = None
        app.tick()
        return self.frame()

    def open_tab(self):
        self.size = Size(100, 40)
        app = self.app()
        self.keys(app, LEFT, LEFT)
        return app, self.settle(app)

    def head(self):
        return self.git(self.clone, "rev-parse", "HEAD")

    def test_a_current_clone_says_so_and_offers_nothing(self):
        app, frame = self.open_tab()
        self.assertIn("Orchestrator is up to date.", frame)
        self.assertNotIn("u Update now", frame)
        self.keys(app, "u")
        self.assertIsNone(self.updates.process)

    def test_new_commits_are_counted_and_listed_newest_first(self):
        self.commit("older change", "newer change")
        _, frame = self.open_tab()
        self.assertIn("Update available: 2 new changes.", frame)
        self.assertLess(frame.index("- newer change"), frame.index("- older change"))
        self.assertIn("u Update now", frame)
        self.assertNotIn("more", frame)
        self.assertNotIn(str(self.path.parent), frame)

    def test_one_new_commit_is_one_change(self):
        self.commit("only")
        self.assertIn("Update available: 1 new change.\r\n", self.open_tab()[1])

    def test_a_long_list_is_cut_after_eight(self):
        self.commit(*(f"change {n:02}" for n in range(11)))
        _, frame = self.open_tab()
        self.assertIn("Update available: 11 new changes.", frame)
        self.assertEqual(frame.count("- change "), 8)
        self.assertIn("- change 03", frame)
        self.assertIn("and 3 more", frame)

    def test_checking_is_shown_while_the_fetch_runs(self):
        import update_check

        release = threading.Event()
        self.addCleanup(release.set)

        def slow(repo):
            release.wait(60)
            return ["done"], False

        with patch.object(update_check, "check", slow):
            app = self.app()
            self.keys(app, LEFT, LEFT)
            self.assertIn("Checking for updates...", self.frame())
            first = self.updates.thread
            self.keys(app, LEFT, RIGHT)
            self.assertIs(self.updates.thread, first)
            release.set()
            self.assertIn("done", self.settle(app))

    def test_a_fetch_error_is_one_plain_line(self):
        self.git(self.clone, "remote", "set-url", "origin", str(self.clone) + "-gone")
        _, frame = self.open_tab()
        (line,) = [x for x in frame.split("\r\n") if "Could not check" in x]
        self.assertIn("does not appear to be a git repository", line)
        self.assertNotIn("fatal:", frame)
        self.assertNotIn("u Update now", frame)

    def test_a_slow_git_is_reported(self):
        import update_check

        with patch.object(update_check, "FETCH_S", 0):
            self.assertIn(
                "Could not check for updates: git took too long", self.open_tab()[1]
            )

    def refused(self, words):
        before = self.head()
        app, frame = self.open_tab()
        self.assertIn(words, frame)
        self.assertNotIn("u Update now", frame)
        self.keys(app, "u")
        self.assertIsNone(self.updates.process)
        self.assertEqual(self.head(), before)
        self.assertFalse(self.stub_log.exists())

    def test_local_edits_are_named_and_no_update_is_offered(self):
        self.commit("new")
        (self.clone / "mine.txt").write_text("mine")
        self.refused("This copy has local changes, so it cannot be updated here.")

    def test_another_branch_is_named_and_no_update_is_offered(self):
        self.commit("new")
        self.git(self.clone, "switch", "-q", "-c", "side", "--track", "origin/main")
        self.refused("This copy is on branch side, not main")

    def test_local_commits_are_named_and_no_update_is_offered(self):
        self.commit("new")
        self.git(self.clone, "commit", "-q", "--allow-empty", "-m", "mine")
        self.refused("This copy has its own commits")

    def test_u_pulls_then_installs_and_says_updated(self):
        self.commit("new")
        app, _ = self.open_tab()
        self.keys(app, "u")
        frame = self.settle(app)
        self.assertIn("Updated. Close and reopen this popup", frame)
        self.assertEqual(self.head(), self.git(self.origin, "rev-parse", "HEAD"))
        self.assertEqual(self.stub_log.read_text(), "ran\n")
        self.assertFalse(self.updates.log.exists())
        self.keys(app, LEFT, RIGHT)
        self.assertIn("Updated.", self.settle(app))

    def test_a_failed_install_shows_its_error_in_one_line(self):
        self.commit("new")
        app, _ = self.open_tab()
        with patch.dict(os.environ, {"STUB_FAIL": "1"}):
            self.keys(app, "u")
            frame = self.settle(app)
        self.assertIn("Update failed: the stub install broke\r\n", frame)
        self.assertNotIn("Updated.", frame)

    def test_a_branch_changed_after_the_check_is_not_pulled(self):
        self.commit("new")
        app, _ = self.open_tab()
        self.git(self.clone, "switch", "-q", "-c", "side", "--track", "origin/main")
        before = self.head()
        self.keys(app, "u")
        self.assertIn("Update failed: This copy is on branch side", self.settle(app))
        self.assertEqual(self.head(), before)
        self.assertFalse(self.stub_log.exists())

    def test_the_update_runs_in_its_own_session(self):
        self.commit("new")
        app, _ = self.open_tab()
        self.keys(app, "u")
        pid = self.updates.process.pid
        try:
            self.assertNotEqual(os.getsid(pid), os.getsid(0))
        finally:
            self.settle(app)


class TitleTest(ViewCase):
    def test_the_view_leaves_the_title_to_the_popup_border(self):
        app = self.app()
        for _ in TABS:
            self.assertNotIn("Settings Orchestrator", self.frame())
            self.keys(app, RIGHT)

    def test_the_herdr_manifest_has_the_title(self):
        manifest = tomllib.loads(
            (
                WATCHERS.parents[1] / "env/herdr/settings-plugin/herdr-plugin.toml"
            ).read_text()
        )
        self.assertEqual(manifest["panes"][0]["title"], "Settings Orchestrator")
        self.assertEqual(
            manifest["actions"][0]["title"], "Settings Orchestrator: open the popup"
        )
        self.assertIn("'Settings Orchestrator'", manifest["description"])


class InlineEditTest(ViewCase):
    def row(self, key):
        return next(
            line for line in self.frame().split("\r\n") if line.startswith(f"> {key} ")
        )

    def test_a_text_value_is_edited_inside_its_row(self):
        app = self.at("pr.author")
        self.keys(app, ENTER, "x")
        self.assertRegex(self.row("pr.author"), r" \[mex▏ *\]$")
        self.assertNotIn("edit:", self.frame())
        self.assertIn("Enter save · Esc cancel", self.frame())

    def test_a_long_value_keeps_its_end_and_the_cursor_in_a_narrow_row(self):
        for width in (80, 40):
            with self.subTest(width=width):
                self.size = Size(width, 30)
                app = self.at("pr.author")
                self.keys(app, ENTER, CLEAR, *("a" * 90), *"END")
                line = next(x for x in self.frame().split("\r\n") if x.startswith("> "))
                self.assertTrue(line.endswith("END▏]"), line)
                self.assertLessEqual(len(line), width)

    def test_wide_characters_do_not_push_the_cursor_off_the_row(self):
        self.size = Size(60, 30)
        app = self.at("pr.author")
        self.keys(app, ENTER, CLEAR, *("界" * 40))
        line = next(x for x in self.frame().split("\r\n") if x.startswith("> "))
        self.assertTrue(line.endswith("界▏]"), line)

    def test_a_boolean_is_chosen_with_arrows_and_saved_by_enter(self):
        app = self.at("herdr.todo_pane")
        self.keys(app, ENTER)
        self.assertIn("‹ on ›", self.row("herdr.todo_pane"))
        self.keys(app, RIGHT)
        self.assertIn("‹ off ›", self.row("herdr.todo_pane"))
        self.assertEqual(self.path.read_text(), self.TEXT, "written before Enter")
        self.keys(app, ENTER)
        self.assertIs(self.saved("herdr.todo_pane"), False)
        self.assertIsNone(app.field)

    def test_arrows_walk_fixed_choices_both_ways(self):
        app = self.at("models.worker_effort")
        self.keys(app, ENTER, RIGHT, ENTER)
        self.assertEqual(self.saved("models.worker_effort"), "low")
        self.keys(app, ENTER, LEFT, LEFT, ENTER)
        self.assertEqual(self.saved("models.worker_effort"), "max")

    def test_space_saves_the_next_choice_at_once(self):
        app = self.at("models.worker_effort")
        self.keys(app, " ")
        self.assertEqual(self.saved("models.worker_effort"), "low")
        self.assertIsNone(app.field)

    def test_escape_cancels_a_choice(self):
        app = self.at("models.worker_effort")
        self.keys(app, ENTER, RIGHT, ESC)
        self.assertEqual((app.field, app.closing), (None, False))
        self.assertNotIn("‹", self.frame())
        self.assertEqual(self.path.read_text(), self.TEXT)

    def test_typing_does_nothing_to_a_choice(self):
        app = self.at("models.worker_effort")
        self.keys(app, ENTER, RIGHT, "x", BACKSPACE, CLEAR, ENTER)
        self.assertEqual(self.saved("models.worker_effort"), "low")

    def test_a_choice_changed_in_the_file_while_open_is_not_overwritten(self):
        app = self.at("models.worker_effort")
        self.keys(app, ENTER, RIGHT)
        self.path.write_text('[models]\nworker_effort = "high"\n')
        self.keys(app, ENTER)
        self.assertIn("changed in the file", self.frame())
        self.assertEqual(self.saved("models.worker_effort"), "high")

    def test_a_list_entry_is_edited_inside_its_row(self):
        app = self.at("pr_tabs.repos")
        self.keys(app, ENTER, "a", *"one")
        self.assertIn("\n > [one▏]", self.frame())
        self.assertNotIn("(no entries)", self.frame())
        self.keys(app, ENTER, "a", *"two", ENTER, UP, ENTER, "!")
        self.assertIn("\n > [one!▏]", self.frame())
        self.assertIn("\n   two", self.frame())

    def test_the_edited_row_stays_visible_on_a_very_short_terminal(self):
        for height in range(5, 12):
            with self.subTest(height=height):
                self.size = Size(80, height)
                app = self.at("audit.page_prs")
                for key in (ENTER, CLEAR, *"777"):
                    self.keys(app, key)
                    self.assertIn("▏]", self.frame())
                    self.assertLessEqual(len(self.frame().split("\r\n")), height)


class FilterTest(ViewCase):
    def test_the_filter_searches_every_tab_and_groups_by_tab_name(self):
        app = self.app()
        self.keys(app, "/", *"max", ENTER)
        self.assertEqual(
            [row.key for row in app.rows],
            [
                row.key
                for _, tables in TABS
                for table in tables
                for row in s.listed("global")
                if row.table == table and "max" in row.key
            ],
        )
        lines = [line.rstrip() for line in self.frame().split("\r\n")]
        groups = [line for line in lines if line in dict(TABS)]
        self.assertEqual(groups, ["Workers", "GitHub", "Watchers"])

    def test_slash_alone_lists_every_tab(self):
        app = self.app()
        self.keys(app, "/")
        self.assertEqual(len(app.rows), len(s.listed("global")))

    def test_the_active_tab_follows_the_selected_match(self):
        app = self.app()
        self.keys(app, "/", *"herdr.todo_pane", ENTER)
        self.assertEqual(self.active(), "herdr")

    def test_escape_returns_to_the_tab_of_the_selected_match(self):
        app = self.app()
        self.keys(app, "/", *"herdr.todo_pane", ENTER, ESC)
        self.assertEqual(app.rows[app.selected].key, "herdr.todo_pane")
        self.assertEqual(app.rows[0].table, "herdr")

    def test_escape_with_no_match_returns_to_the_tab_it_started_on(self):
        app = self.app()
        self.keys(app, RIGHT, RIGHT, "/", *"zzz", ENTER)
        self.assertIn("no setting matches", self.frame())
        self.keys(app, ESC)
        self.assertEqual(app.rows[0].table, "pr")

    def test_arrows_do_not_leave_the_search_results(self):
        app = self.app()
        self.keys(app, "/", *"max", ENTER)
        before = list(app.rows)
        self.keys(app, DOWN, RIGHT)
        self.assertEqual((app.rows, app.selected), (before, 1))


class EditTest(ViewCase):
    def test_a_short_terminal_keeps_the_field_and_its_error_on_screen(self):
        self.size = Size(80, 11)
        app = self.at("audit.page_prs")
        self.keys(app, ENTER, CLEAR, *"101", ENTER)
        self.assertIn("[101▏]", self.frame())
        self.assertIn("at most 100", self.frame())

    def test_q_and_escape_close_the_editor(self):
        for key in ("q", ESC):
            with self.subTest(key=key):
                app = self.app()
                self.keys(app, key)
                self.assertTrue(app.closing)

    def test_space_toggles_a_boolean_and_keeps_the_rest_of_the_file(self):
        app = self.at("herdr.todo_pane")
        self.keys(app, " ")
        self.assertIs(self.saved("herdr.todo_pane"), False)
        self.assertTrue(self.path.read_text().startswith(self.TEXT))
        self.keys(app, " ")
        self.assertIs(self.saved("herdr.todo_pane"), True)

    def test_a_text_field_starts_with_the_value_and_saves_on_enter(self):
        app = self.at("pr.author")
        self.keys(app, ENTER, BACKSPACE, "y", " ", "q")
        self.assertEqual(self.saved("pr.author"), "me")
        self.keys(app, ENTER)
        self.assertEqual(self.saved("pr.author"), "my q")
        self.assertIn("# who", self.path.read_text())

    def test_number_fields_save_integers_and_decimals(self):
        for key, typed, expected in (
            ("recycle.tokens", "1000", 1000),
            ("host.load_max", "12.5", 12.5),
        ):
            with self.subTest(key=key):
                self.keys(self.at(key), ENTER, CLEAR, *typed, ENTER)
                self.assertEqual(self.saved(key), expected)

    def test_a_bad_value_stays_in_the_field_with_the_reason_and_writes_nothing(self):
        for key, typed, reason in (
            ("recycle.tokens", "1.5", "not a whole number"),
            ("host.load_max", "6O", "not a number"),
            ("host.load_max", "nan", "not a number"),
            ("host.load_max", "1e999", "finite"),
            ("audit.page_prs", "101", "at most 100"),
            ("models.codex_smart_reasoning_effort", "No Way", "lowercase"),
        ):
            with self.subTest(key=key, typed=typed):
                app = self.at(key)
                self.keys(app, ENTER, CLEAR, *typed, ENTER)
                self.assertEqual(app.field.text, typed)
                self.assertIn(reason, self.frame())
                self.assertEqual(self.path.read_text(), self.TEXT)

    def test_escape_leaves_the_field_first_and_closes_the_popup_second(self):
        app = self.at("pr.author")
        self.keys(app, ENTER, "x", ESC)
        self.assertIsNone(app.field)
        self.assertFalse(app.closing)
        self.assertEqual(self.path.read_text(), self.TEXT)
        self.keys(app, ESC)
        self.assertTrue(app.closing)

    def test_the_text_field_is_bounded(self):
        app = self.at("pr.author")
        self.keys(app, ENTER)
        app.field.text = "x" * self.view.FIELD_MAX
        self.keys(app, "y")
        self.assertEqual(len(app.field.text), self.view.FIELD_MAX)

    def test_u_writes_the_default(self):
        app = self.at("pr.author")
        self.keys(app, "u")
        self.assertEqual(self.saved("pr.author"), "")

    def test_a_checklist_saves_the_ticked_choices_in_schema_order(self):
        app = self.at("providers.claude")
        self.keys(app, ENTER, " ", DOWN, " ", " ", "s")
        self.assertEqual(self.saved("providers.claude"), ["sonnet", "opus", "fable"])
        self.keys(app, ENTER, " ", "s")
        self.assertEqual(
            self.saved("providers.claude"), ["haiku", "sonnet", "opus", "fable"]
        )

    def test_a_checklist_the_schema_rejects_writes_nothing(self):
        app = self.at("providers.enabled")
        self.keys(app, ENTER, DOWN, " ", "s")
        self.assertIn("must include", self.frame())
        self.assertEqual(self.path.read_text(), self.TEXT)

    def test_turning_a_provider_on_names_the_models_still_missing(self):
        app = self.at("providers.enabled")
        self.keys(app, ENTER, " ", DOWN, " ", "s")
        self.assertEqual(self.saved("providers.enabled"), ["claude", "codex"])
        self.assertIn("ORCH_CODEX_WORKER_MODEL", app.status)

    def test_the_list_editor_adds_edits_moves_and_deletes_entries(self):
        app = self.at("pr_tabs.repos")
        self.keys(app, ENTER, "a", *"one", ENTER, "a", *"two", ENTER)
        self.keys(app, "a", *"three", ENTER, "K", ENTER, "!", ENTER)
        self.keys(app, DOWN, "x")
        self.assertEqual(self.path.read_text(), self.TEXT, "written before s")
        self.keys(app, "s")
        self.assertEqual(self.saved("pr_tabs.repos"), ["one", "three!"])

    def test_the_list_editor_moves_an_entry_down(self):
        app = self.at("pr_tabs.repos")
        self.keys(app, ENTER, "a", "a", ENTER, "a", "b", ENTER, UP, "J", "s")
        self.assertEqual(self.saved("pr_tabs.repos"), ["b", "a"])

    def test_escape_discards_the_list_changes(self):
        app = self.at("watchers.path")
        self.keys(app, ENTER, "x", ESC)
        self.assertEqual((app.editor, app.closing), (None, False))
        self.assertEqual(self.path.read_text(), self.TEXT)

    def test_a_list_entry_the_schema_rejects_keeps_the_editor_open(self):
        app = self.at("pr_tabs.repos")
        self.keys(app, ENTER, "a", *"a,b", ENTER, "s")
        self.assertIsNotNone(app.editor)
        self.assertIn("separates entries", self.frame())
        self.assertEqual(self.path.read_text(), self.TEXT)

    def test_the_list_editor_is_bounded(self):
        app = self.at("pr_tabs.repos")
        self.keys(app, ENTER)
        app.editor.items = ["x"] * self.view.LIST_MAX
        self.keys(app, "a")
        self.assertIsNone(app.field)

    def test_a_value_changed_in_the_file_while_editing_is_not_overwritten(self):
        app = self.at("pr.author")
        self.keys(app, ENTER, "x")
        self.path.write_text('[pr]\nauthor = "other"\n')
        self.keys(app, ENTER)
        self.assertIn("changed in the file", self.frame())
        self.assertEqual(self.saved("pr.author"), "other")


class ApplyTest(ViewCase):
    def change(self, app, key, *keys):
        self.select(app, key)
        self.keys(app, *keys)

    def two_changes(self):
        app = self.app()
        self.change(app, "herdr.todo_pane", " ")
        self.change(app, "pr.author", ENTER, "x", ENTER)
        return app

    def sent(self):
        return self.feed.read_text().splitlines() if self.feed.exists() else []

    def test_the_footer_counts_each_saved_setting_once(self):
        app = self.app()
        app.render()
        self.assertNotIn("not applied", self.frame())
        self.change(app, "herdr.todo_pane", " ")
        self.assertIn("\r\n1 change not applied\r\n", self.frame())
        self.change(app, "pr.author", ENTER, "x", ENTER)
        self.change(app, "herdr.todo_pane", " ")
        self.assertIn("\r\n2 changes not applied\r\n", self.frame())

    def test_a_save_that_changes_no_value_is_not_pending(self):
        app = self.app()
        self.change(app, "pr.author", ENTER, ENTER)
        self.change(app, "herdr.todo_pane", "u")
        self.assertEqual(app.unapplied, [])

    def test_a_refused_value_is_not_pending(self):
        app = self.app()
        self.change(app, "audit.page_prs", ENTER, CLEAR, *"101", ENTER)
        self.assertEqual(app.unapplied, [])

    def test_a_sends_one_line_naming_the_keys_and_clears_the_list(self):
        app = self.two_changes()
        self.keys(app, "a")
        (line,) = self.sent()
        self.assertRegex(line, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4} ")
        self.assertEqual(
            line.split(" ", 1)[1],
            "settings: SETTINGS APPLY herdr.todo_pane, pr.author (start)",
        )
        self.assertEqual(app.status, "Sent to the Coordinator")
        self.assertIn("Sent to the Coordinator", self.frame())
        self.assertNotIn("not applied", self.frame())

    def test_a_feed_that_cannot_be_written_shows_the_error_and_keeps_the_list(self):
        app = self.two_changes()
        self.feed.mkdir()
        self.keys(app, "a")
        self.assertIn("not sent:", self.frame())
        self.assertIn("directory", app.status)
        self.assertIn("2 changes not applied", self.frame())

    def test_without_a_feed_monitor_nothing_is_sent_and_the_list_stays(self):
        app = self.two_changes()
        self.feed.with_name(feed_attach.MONITOR_NAME).unlink()
        self.keys(app, "a")
        self.assertIn("no Coordinator Feed Monitor", app.status)
        self.assertEqual(self.sent(), [])
        self.assertEqual(app.unapplied, ["herdr.todo_pane", "pr.author"])

    def test_a_heartbeat_without_a_running_follower_sends_nothing(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        app = self.two_changes()
        beat = self.feed.with_name(feed_attach.MONITOR_NAME)
        for record in (f"1 0 0 0 {gone.pid}", "1 0 0", "1 0 0 0 0", "1 0 0 0 x"):
            with self.subTest(record=record):
                beat.write_text(record + "\n")
                self.keys(app, "a")
                self.assertIn("no Coordinator Feed Monitor", app.status)
                self.assertEqual(self.sent(), [])
                self.assertEqual(len(app.unapplied), 2)

    def test_a_stale_heartbeat_sends_nothing(self):
        app = self.two_changes()
        beat = self.feed.with_name(feed_attach.MONITOR_NAME)
        old = beat.stat().st_mtime - feed_attach.MONITOR_FRESH_SECONDS - 1
        os.utime(beat, (old, old))
        self.keys(app, "a")
        self.assertEqual((self.sent(), len(app.unapplied)), ([], 2))

    def test_a_with_nothing_changed_sends_nothing(self):
        self.keys(self.app(), "a")
        self.assertEqual(self.sent(), [])

    def test_a_typed_into_a_field_or_the_filter_sends_nothing(self):
        app = self.two_changes()
        self.keys(app, ENTER, "a", ESC, "/", "a", ENTER)
        self.assertEqual(self.sent(), [])
        self.assertEqual(app.filter, "a")

    def test_a_short_terminal_keeps_the_count_of_unapplied_changes(self):
        app = self.two_changes()
        self.size = Size(100, 9)
        app.render()
        self.assertIn("2 changes not applied", self.frame())
        self.assertLessEqual(len(self.frame().split("\r\n")), 9)

    def test_the_feed_monitor_prints_the_sent_line_as_an_action(self):
        app = self.two_changes()
        self.keys(app, "a")
        (line,) = self.sent()
        patterns = feed_actions.action_patterns()
        self.assertTrue(feed_actions.is_action(line, patterns))
        self.assertFalse(feed_actions.is_action(line, feed_actions.fyi_patterns()))
        self.assertEqual(feed_actions.native(line), line)
        for other in (
            line.replace("pr.author", "pr.author; rm -rf x"),
            line.replace("settings:", "todo:"),
        ):
            self.assertFalse(feed_actions.is_action(other, patterns), other)


class OverrideAndErrorTest(ViewCase):
    def test_an_environment_override_is_shown_and_nothing_is_written(self):
        self.env["ORCH_PARK_IDLE"] = "1"
        app = self.at("recycle.park_idle")
        self.assertIn("env ORCH_PARK_IDLE wins", self.frame())
        for key in (" ", ENTER, "u"):
            self.keys(app, key)
        self.assertEqual(self.path.read_text(), self.TEXT)
        self.assertIn("nothing written", app.status)

    def test_control_characters_never_reach_the_terminal(self):
        self.env["ORCH_PR_AUTHOR"] = "a\x1b]0;x\x07b"
        self.at("pr.author")
        self.assertNotIn("\x07", self.out.getvalue())

    def test_a_broken_file_shows_the_error_and_edits_nothing(self):
        broken = "[pr\n"
        self.path.write_text(broken)
        app = self.at("herdr.todo_pane")
        self.assertIn("invalid TOML", app.error)
        self.assertIn("ERROR:", self.frame())
        self.keys(app, " ", "u")
        self.assertEqual(self.path.read_text(), broken)

    def test_a_missing_selected_file_is_an_error_and_is_not_created(self):
        self.path.unlink()
        app = self.at("herdr.todo_pane")
        self.keys(app, " ")
        self.assertIn("ORCH_CONFIG", self.frame())
        self.assertFalse(self.path.exists())

    def test_a_missing_default_file_is_created_from_the_example(self):
        home = self.path.parent / "home"
        self.env = {}
        with patch.dict(os.environ, {"HOME": str(home)}):
            self.keys(self.at("herdr.todo_pane"), " ")
        text = (home / ".claude/orchestration.toml").read_text()
        self.assertIn("todo_pane = false", text)
        self.assertIn("# Global settings", text)


class ScreenTest(ViewCase):
    def test_the_selected_row_shows_its_doc_and_when_it_applies(self):
        self.size = Size(200, 30)
        self.at("host.load_max")
        self.assertIn("Host watcher: highest load average", self.frame())
        self.assertIn(self.view.WATCHER, self.frame())

    def test_the_description_does_not_repeat_the_key(self):
        self.size = Size(200, 30)
        self.at("host.load_max")
        doc = s.BY_KEY["host.load_max"].doc
        self.assertIn(doc, self.frame().split("\r\n"))

    def test_the_list_scrolls_to_keep_the_selection_visible(self):
        self.size = Size(100, 14)
        app = self.app()
        for _ in range(len(app.rows) + 5):
            self.keys(app, DOWN)
        self.assertEqual(app.selected, len(app.rows) - 1)
        self.assertRegex(self.frame(), r"> models\.gemini_smart ")
        self.assertLessEqual(len(self.frame().split("\r\n")), 14)

    def test_slash_filters_rows_and_escape_clears_the_filter_before_closing(self):
        app = self.app()
        self.keys(app, "/", *"todo_p", ENTER)
        self.assertEqual(
            [row.key for row in app.rows], ["herdr.todo_popup", "herdr.todo_pane"]
        )
        self.keys(app, ESC)
        self.assertFalse(app.closing)
        self.assertGreater(len(app.rows), 2)

    def test_the_filter_is_bounded(self):
        app = self.app()
        self.keys(app, "/", *("x" * (self.view.FILTER_MAX + 5)))
        self.assertEqual(len(app.filter), self.view.FILTER_MAX)

    def click(self, app, key, column):
        line = next(
            n
            for n, text in enumerate(self.frame().split("\r\n"), 1)
            if f" {key} " in text
        )
        app.feed(f"\x1b[<0;{column};{line}M")

    def test_a_click_selects_a_row_and_a_click_on_its_value_opens_it(self):
        self.size = Size(100, 200)
        app = self.at("herdr.bin")
        self.click(app, "herdr.todo_pane", self.view.VALUE_COLUMN - 1)
        self.assertEqual(app.rows[app.selected].key, "herdr.todo_pane")
        self.assertIsNone(app.field)
        self.click(app, "herdr.todo_pane", self.view.VALUE_COLUMN)
        self.assertIsNotNone(app.field)
        self.assertEqual(self.path.read_text(), self.TEXT)


if __name__ == "__main__":
    unittest.main()
