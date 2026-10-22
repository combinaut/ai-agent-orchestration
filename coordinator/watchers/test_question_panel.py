import importlib.util
import io
import os
import pty
import subprocess
import tempfile
import time
import tty
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_support import IsolatedTestCase
import feed_actions
import question_lock
import question_remind
import reminder_picker
import watchers as w

spec = importlib.util.spec_from_file_location("question_pane", w.ROOT.parent / "question-pane.py")
pane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pane)

NOW = 1_800_000_000.0


class PanelCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.question = self.root / "release.md"
        self.question.write_text("Ship it?\n1. yes\n2. no\n")
        self.feed = self.root / "feed.log"
        self.launched, self.out = [], io.StringIO()
        self.panel = pane.Panel(self.question, self.out, columns=lambda: 80, clock=lambda: NOW,
                                launcher=self.launch, feed=self.feed)
        self.exit_code = 0

    def launch(self, *args):
        self.launched.append(args[1:])
        return SimpleNamespace(wait=lambda timeout: self.exit_code)

    def press(self, *keys):
        for key in keys:
            self.panel.press(key)

    def feed_lines(self):
        return self.feed.read_text().splitlines() if self.feed.exists() else []

    def remind_file(self):
        return self.question.with_suffix(".remind")


class DismissTest(PanelCase):
    def test_d_writes_the_dismissed_line_and_closes_the_question(self):
        self.press("d")
        [line] = self.feed_lines()
        self.assertRegex(line, r"^\S+ answers: DISMISSED release \(start\)$")
        self.assertTrue(feed_actions.is_action(line, feed_actions.action_patterns()))
        self.assertEqual(self.launched, [("close", "release")])
        self.assertTrue(self.panel.done)

    def test_a_feed_that_cannot_be_written_keeps_the_panel_open(self):
        with patch.object(pane.feed_attach, "append_line", side_effect=OSError("read-only")):
            self.press("d")
        self.assertEqual((self.launched, self.panel.done), ([], False))
        self.assertIn("nothing changed", self.out.getvalue())

    def test_a_failed_close_keeps_the_panel_interactive_with_the_reason(self):
        self.exit_code = 3
        self.press("d")
        self.assertFalse(self.panel.done)
        self.assertIn("exit 3", self.out.getvalue())

    def test_a_helper_still_running_after_the_wait_counts_as_closing(self):
        def slow(timeout):
            raise pane.subprocess.TimeoutExpired("x", timeout)
        self.panel.launcher = lambda *args: SimpleNamespace(wait=slow)
        self.press("d")
        self.assertTrue(self.panel.done)

    def test_d_is_not_a_dismiss_inside_the_picker(self):
        self.press("r", "d")
        self.assertEqual((self.feed_lines(), self.launched), ([], []))


class RemindTest(PanelCase):
    def test_r_opens_the_shared_picker(self):
        self.press("r")
        for _, _, name in reminder_picker.PICKS:
            self.assertIn(name, self.out.getvalue())
        self.assertIn("Remind me in:", self.out.getvalue())
        self.assertEqual((self.feed_lines(), self.launched, self.panel.done), ([], [], False))

    def test_a_pick_stores_the_time_writes_the_feed_line_and_hides_the_panel(self):
        self.press("r", "2")
        self.assertEqual(float(self.remind_file().read_text()), NOW + 3600)
        [line] = self.feed_lines()
        self.assertRegex(line, r"^\S+ answers: REMIND release \d{4}-\d\d-\d\d \d\d:\d\d \(start\)$")
        self.assertIn(time.strftime("%Y-%m-%d %H:%M", time.localtime(NOW + 3600)), line)
        self.assertTrue(feed_actions.is_action(line, feed_actions.action_patterns()))
        self.assertEqual(self.launched, [("hide", "release")])
        self.assertTrue(self.question.is_file())

    def test_tomorrow_pick_and_typed_value_use_the_shared_rules(self):
        self.press("r", "5")
        self.assertEqual(float(self.remind_file().read_text()), reminder_picker.remind_until("pick5", NOW))
        self.panel.done, self.panel.picker = False, None
        self.press("r", "t", "3", "0", "m", "\r")
        self.assertEqual(float(self.remind_file().read_text()), NOW + 1800)

    def test_a_typed_value_that_is_not_a_duration_asks_again(self):
        self.press("r", "t", "x", "\r")
        self.assertIn(reminder_picker.TYPE_HINT, self.out.getvalue())
        self.assertEqual((self.remind_file().exists(), self.launched), (False, []))
        self.press("\x7f", "5", "m", "\r")
        self.assertEqual(float(self.remind_file().read_text()), NOW + 300)

    def test_esc_cancels_the_picker_and_the_type_prompt(self):
        self.press("r", pane.ESCAPE)
        self.assertIsNone(self.panel.picker)
        self.press("r", "t", "5", pane.ESCAPE)
        self.assertIsNone(self.panel.picker)
        self.assertEqual((self.feed_lines(), self.launched, self.remind_file().exists(), self.panel.done),
                         ([], [], False, False))

    def test_a_failed_hide_keeps_the_panel_open_and_drops_the_reminder_file(self):
        self.exit_code = 1
        self.press("r", "1")
        self.assertEqual((self.panel.done, self.remind_file().exists()), (False, False))
        self.assertIn("still open", self.out.getvalue())

    def test_a_feed_that_cannot_be_written_leaves_no_reminder(self):
        with patch.object(pane.feed_attach, "append_line", side_effect=TimeoutError("lock")):
            self.press("r", "1")
        self.assertEqual((self.remind_file().exists(), self.launched, self.panel.done), (False, [], False))

    def test_number_keys_still_answer_when_no_picker_is_open(self):
        self.press("2")
        self.assertEqual(self.question.with_suffix(".answer").read_text(), "2\n")
        self.assertEqual((self.feed_lines(), self.launched), ([], []))

    def test_number_keys_choose_a_time_instead_of_answering_while_the_picker_is_open(self):
        self.press("r", "1")
        self.assertFalse(self.question.with_suffix(".answer").exists())


class ReopenTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.opened = []

    def opener(self, status=0):
        def run(slug, question):
            self.opened.append((slug, question.name))
            return status
        return run

    def make(self, slug, until, question=True):
        if question:
            (self.dir / f"{slug}.md").write_text("q")
        question_remind.write_remind(self.dir / f"{slug}.md", until)

    def test_nothing_returns_before_its_time(self):
        self.make("later", NOW + 1)
        self.assertEqual(question_remind.reopen_due(self.dir, NOW, self.opener()), [])
        self.assertEqual((self.opened, (self.dir / "later.remind").exists()), ([], True))

    def test_a_due_reminder_reopens_its_question_once(self):
        self.make("due", NOW)
        self.assertEqual(question_remind.reopen_due(self.dir, NOW, self.opener()), ["due"])
        self.assertEqual(question_remind.reopen_due(self.dir, NOW + 60, self.opener()), [])
        self.assertEqual((self.opened, (self.dir / "due.remind").exists()), ([("due", "due.md")], False))

    def test_an_already_open_pane_counts_as_reopened(self):
        self.make("due", NOW)
        self.assertEqual(question_remind.reopen_due(self.dir, NOW, self.opener(73)), ["due"])
        self.assertFalse((self.dir / "due.remind").exists())

    def test_a_failed_open_keeps_the_reminder_for_the_next_pass(self):
        self.make("due", NOW)
        self.assertEqual(question_remind.reopen_due(self.dir, NOW, self.opener(1)), [])
        self.assertTrue((self.dir / "due.remind").exists())

    def test_a_reminder_without_a_question_or_a_time_is_dropped(self):
        self.make("gone", NOW, question=False)
        (self.dir / "junk.md").write_text("q")
        (self.dir / "junk.remind").write_text("soon")
        question_remind.reopen_due(self.dir, NOW, self.opener())
        self.assertEqual((self.opened, sorted(self.dir.glob("*.remind"))), ([], []))

    def test_a_raising_open_does_not_stop_the_later_reminders(self):
        self.make("aaa", NOW)
        self.make("bbb", NOW)
        def opener(slug, question):
            if slug == "aaa":
                raise subprocess.TimeoutExpired("ask-user-question", 30)
            self.opened.append(slug)
            return 0
        self.assertEqual(question_remind.reopen_due(self.dir, NOW, opener), ["bbb"])
        self.assertEqual((self.dir / "aaa.remind").exists(), True)

    def test_open_question_runs_through_the_owned_process_group_runner(self):
        with patch.object(w.runtime, "run", return_value=SimpleNamespace(returncode=73)) as run:
            self.assertEqual(w.open_question("due", Path("/q/due.md")), 73)
        self.assertEqual(run.call_args.args[0][1:], ["due", "/q/due.md"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_the_answers_pass_reopens_but_a_preview_does_not(self):
        self.make("due", 1)
        calls = []
        with patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(self.dir)}), \
                patch.object(w, "open_question", lambda *args: calls.append(args) or 0):
            w.collect_answers(None, consume=False)
            self.assertEqual(calls, [])
            w.collect_answers(None)
        self.assertEqual([slug for slug, _ in calls], ["due"])

    def test_a_reopen_that_raises_does_not_stop_the_answers_pass(self):
        self.make("due", 1)
        (self.dir / "ship.md").touch()
        (self.dir / "ship.answer").write_text("yes\n")
        with patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(self.dir)}), \
                patch.object(w, "open_question", side_effect=OSError("no herdr")):
            raw = w.collect_answers(None)
        self.assertEqual([answer[:2] for answer in raw["answers"]], [("ship", "yes")])

    def test_closing_a_question_removes_its_reminder(self):
        self.make("done", NOW)
        with patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(self.dir)}):
            question_lock.close_question("done", "true", "")
        self.assertEqual(list(self.dir.glob("done.*")), [])


class KeyReaderTest(IsolatedTestCase):
    def read(self, data):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        tty.setcbreak(slave)
        with os.fdopen(slave, "r") as stdin, patch.object(pane.sys, "stdin", stdin):
            os.write(master, data)
            return pane.read_key()

    def test_a_lone_esc_is_the_escape_key(self):
        self.assertEqual(self.read(b"\x1b"), pane.ESCAPE)

    def test_the_start_of_an_escape_sequence_is_not_a_lone_esc(self):
        self.assertEqual(self.read(b"\x1b[A"), "\x1b")

    def test_a_plain_key_is_returned_as_typed(self):
        self.assertEqual(self.read(b"r"), "r")

    def test_follow_sends_a_lone_esc_to_the_open_picker_and_skips_arrow_keys(self):
        panel = pane.Panel(Path("x.md"), io.StringIO())
        panel.picker = {"mode": "pick", "entry": ""}
        keys = iter([pane.ESCAPE, "\x1b", "[", "A", ""])
        pressed = []
        with patch.object(pane, "Panel", return_value=panel), patch.object(pane, "read_key", lambda: next(keys)), \
                patch.object(panel, "show"), patch.object(panel, "press", pressed.append):
            pane.follow(Path("x.md"))
        self.assertEqual(pressed, [pane.ESCAPE])

    def test_columns_default_is_the_terminal_width(self):
        with patch.object(pane.shutil, "get_terminal_size", return_value=SimpleNamespace(columns=33)):
            self.assertEqual(pane.Panel(Path("x.md"), io.StringIO()).columns(), 33)

    def test_launcher_runs_ask_user_question_detached_against_the_question_directory(self):
        with patch.object(pane.subprocess, "Popen") as popen:
            pane.launch(Path("/tmp/qs/release.md"), "hide", "release")
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [str(pane.ROOT / "ask-user-question"), "hide", "release"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["env"]["ORCH_USER_QUESTIONS_DIR"], "/tmp/qs")


if __name__ == "__main__":
    unittest.main()
