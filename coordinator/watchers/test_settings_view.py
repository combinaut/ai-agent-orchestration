import importlib.util
import io
import os
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

WATCHERS = Path(__file__).resolve().parent
sys.path.insert(0, str(WATCHERS.parents[1] / "lib"))
import orch_settings as s  # noqa: E402
from reminder_picker import SGR  # noqa: E402

Size = namedtuple("Size", "columns lines")
UP, DOWN, RIGHT, LEFT = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"
ENTER, ESC, BACKSPACE, CLEAR = "\r", "\x1b", "\x7f", "\x15"


def load_view():
    spec = importlib.util.spec_from_file_location(
        "settings_view", WATCHERS.parent / "settings-view.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def app(self):
        self.out = io.StringIO()
        app = self.view.App(self.env, self.out, lambda: self.size, popup=True)
        app.tick()
        return app

    def select(self, app, key):
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

    def saved(self, key):
        return s.read_file(self.path, "global").get(s.BY_KEY[key])


class CoverageTest(ViewCase):
    def test_every_global_setting_has_a_row_grouped_by_table(self):
        app = self.app()
        self.assertEqual(
            sorted(row.key for row in app.rows),
            sorted(x.key for x in s.listed("global")),
        )
        tables = [row.table for row in app.rows]
        self.assertEqual(
            tables, sorted(tables, key=tables.index), "a table is split in two"
        )

    def test_every_global_setting_says_when_it_takes_effect(self):
        self.assertEqual(
            sorted(self.view.APPLIES), sorted(x.key for x in s.listed("global"))
        )

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


class EditTest(ViewCase):
    def test_space_toggles_a_boolean_and_keeps_the_rest_of_the_file(self):
        app = self.at("herdr.todo_pane")
        self.keys(app, " ")
        self.assertIs(self.saved("herdr.todo_pane"), False)
        self.assertTrue(self.path.read_text().startswith(self.TEXT))
        self.keys(app, " ")
        self.assertIs(self.saved("herdr.todo_pane"), True)

    def test_arrows_cycle_fixed_choices_both_ways(self):
        app = self.at("models.worker_effort")
        self.keys(app, RIGHT)
        self.assertEqual(self.saved("models.worker_effort"), "low")
        self.keys(app, LEFT, LEFT)
        self.assertEqual(self.saved("models.worker_effort"), "max")

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

    def test_q_closes_the_popup(self):
        app = self.app()
        self.keys(app, "q")
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

    def test_the_list_scrolls_to_keep_the_selection_visible(self):
        self.size = Size(100, 14)
        app = self.app()
        for _ in range(len(app.rows) + 5):
            self.keys(app, DOWN)
        self.assertEqual(app.selected, len(app.rows) - 1)
        self.assertRegex(self.frame(), r"> finalize_comment ")
        self.assertLessEqual(len(self.frame().split("\r\n")), 14)

    def test_slash_filters_rows_and_escape_clears_the_filter_before_closing(self):
        app = self.app()
        self.keys(app, "/", *"todo_p", ENTER)
        self.assertEqual(
            [row.key for row in app.rows], ["herdr.todo_popup", "herdr.todo_pane"]
        )
        self.keys(app, ESC)
        self.assertFalse(app.closing)
        self.assertEqual(len(app.rows), len(s.listed("global")))

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

    def test_a_click_selects_a_row_and_a_click_on_its_value_changes_it(self):
        self.size = Size(100, 200)
        app = self.at("herdr.bin")
        self.click(app, "todo_pane", 5)
        self.assertEqual(app.rows[app.selected].key, "herdr.todo_pane")
        self.assertEqual(self.path.read_text(), self.TEXT)
        self.click(app, "todo_pane", self.view.VALUE_COLUMN + 1)
        self.assertIs(self.saved("herdr.todo_pane"), False)


if __name__ == "__main__":
    unittest.main()
