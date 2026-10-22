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
import feed_attach
import question_lock
import question_remind
import reminder_picker
import watchers as w

spec = importlib.util.spec_from_file_location(
    "question_pane", w.ROOT.parent / "question-pane.py"
)
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
        self.panel = pane.Panel(
            self.question,
            self.out,
            columns=lambda: 80,
            clock=lambda: NOW,
            launcher=self.launch,
            feed=self.feed,
        )
        self.exit_code = 0

    def launch(self, *args):
        self.launched.append(args[1:])
        return SimpleNamespace(wait=lambda timeout: self.exit_code)

    def press(self, *keys):
        for key in keys:
            self.panel.press(key)

    def slow_helper(self, codes):
        """A helper that outlasts the settle wait, then reports each of `codes` from poll()."""
        results = iter(codes)

        def slow(timeout):
            raise pane.subprocess.TimeoutExpired("x", timeout)

        helper = SimpleNamespace(wait=slow, poll=lambda: next(results))
        self.panel.launcher = lambda *args: helper

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
        with patch.object(
            pane.feed_attach, "append_line", side_effect=OSError("read-only")
        ):
            self.press("d")
        self.assertEqual((self.launched, self.panel.done), ([], False))
        self.assertIn("nothing changed", self.out.getvalue())

    def test_a_failed_close_keeps_the_panel_interactive_with_the_reason(self):
        self.exit_code = 3
        self.press("d")
        self.assertFalse(self.panel.done)
        self.assertIn("exit 3", self.out.getvalue())

    def test_a_helper_still_running_after_the_wait_keeps_the_panel_alive(self):
        self.slow_helper([None, 0])
        self.press("d")
        self.assertEqual((self.panel.done, bool(self.panel.pending)), (False, True))
        self.panel.poll()
        self.assertFalse(self.panel.done)
        self.panel.poll()
        self.assertEqual((self.panel.done, self.panel.pending), (True, None))

    def test_a_failure_after_the_wait_reopens_the_panel_with_the_reason(self):
        self.slow_helper([None, 1])
        self.press("d")
        self.panel.poll()
        self.panel.poll()
        self.assertEqual((self.panel.done, self.panel.pending), (False, None))
        self.assertIn("exit 1", self.out.getvalue())

    def test_keys_are_ignored_while_the_helper_is_pending(self):
        self.slow_helper([None])
        self.press("d", "1")
        self.assertFalse(self.question.with_suffix(".answer").exists())

    def test_d_is_not_a_dismiss_inside_the_picker(self):
        self.press("r", "d")
        self.assertEqual((self.feed_lines(), self.launched), ([], []))


class RemindTest(PanelCase):
    def test_r_opens_the_shared_picker(self):
        self.press("r")
        for _, _, name in reminder_picker.PICKS:
            self.assertIn(name, self.out.getvalue())
        self.assertIn("Remind me in:", self.out.getvalue())
        self.assertEqual(
            (self.feed_lines(), self.launched, self.panel.done), ([], [], False)
        )

    def test_a_pick_stores_the_time_writes_the_feed_line_and_hides_the_panel(self):
        self.press("r", "2")
        self.assertEqual(float(self.remind_file().read_text()), NOW + 3600)
        [line] = self.feed_lines()
        self.assertRegex(
            line, r"^\S+ answers: REMIND release \d{4}-\d\d-\d\d \d\d:\d\d \(start\)$"
        )
        self.assertIn(time.strftime("%Y-%m-%d %H:%M", time.localtime(NOW + 3600)), line)
        self.assertTrue(feed_actions.is_action(line, feed_actions.action_patterns()))
        self.assertEqual(self.launched, [("hide", "release")])
        self.assertTrue(self.question.is_file())

    def test_tomorrow_pick_and_typed_value_use_the_shared_rules(self):
        self.press("r", "5")
        self.assertEqual(
            float(self.remind_file().read_text()),
            reminder_picker.remind_until("pick5", NOW),
        )
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
        self.assertEqual(
            (
                self.feed_lines(),
                self.launched,
                self.remind_file().exists(),
                self.panel.done,
            ),
            ([], [], False, False),
        )

    def test_a_failed_hide_keeps_the_panel_open_and_drops_the_reminder_file(self):
        self.exit_code = 1
        self.press("r", "1")
        self.assertEqual((self.panel.done, self.remind_file().exists()), (False, False))
        self.assertIn("still open", self.out.getvalue())

    def test_a_hide_that_fails_after_the_wait_drops_the_reminder_file(self):
        self.slow_helper([None, 1])
        self.press("r", "1")
        self.assertTrue(self.remind_file().exists())
        self.panel.poll()
        self.panel.poll()
        self.assertEqual((self.panel.done, self.remind_file().exists()), (False, False))

    def test_a_feed_that_cannot_be_written_leaves_no_reminder(self):
        with patch.object(
            pane.feed_attach, "append_line", side_effect=TimeoutError("lock")
        ):
            self.press("r", "1")
        self.assertEqual(
            (self.remind_file().exists(), self.launched, self.panel.done),
            (False, [], False),
        )

    def test_number_keys_still_answer_when_no_picker_is_open(self):
        self.press("2")
        self.assertEqual(self.question.with_suffix(".answer").read_text(), "2\n")
        self.assertEqual((self.feed_lines(), self.launched), ([], []))

    def test_number_keys_choose_a_time_instead_of_answering_while_the_picker_is_open(
        self,
    ):
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
        self.assertEqual(
            (self.opened, (self.dir / "later.remind").exists()), ([], True)
        )

    def test_a_due_reminder_reopens_its_question_once(self):
        self.make("due", NOW)
        self.assertEqual(
            question_remind.reopen_due(self.dir, NOW, self.opener()), ["due"]
        )
        self.assertEqual(
            question_remind.reopen_due(self.dir, NOW + 60, self.opener()), []
        )
        self.assertEqual(
            (self.opened, (self.dir / "due.remind").exists()),
            ([("due", "due.md")], False),
        )

    def test_an_already_open_pane_counts_as_reopened(self):
        self.make("due", NOW)
        self.assertEqual(
            question_remind.reopen_due(self.dir, NOW, self.opener(73)), ["due"]
        )
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

    def test_no_open_starts_after_the_budget_and_failures_go_to_the_back(self):
        for slug in ("aaa", "bbb", "ccc"):
            self.make(slug, NOW)
        ticks = iter(range(0, 1000, 10))
        order = []

        def opener(slug, question):
            order.append(slug)
            return 1

        question_remind.reopen_due(
            self.dir, NOW, opener, budget=20, clock=lambda: next(ticks)
        )
        self.assertEqual(len(order), 1)
        first = list(order)
        order.clear()
        ticks = iter(range(0, 1000, 10))
        question_remind.reopen_due(
            self.dir, NOW, opener, budget=20, clock=lambda: next(ticks)
        )
        self.assertNotEqual(order[0], first[0])

    def test_slow_reopens_never_keep_a_pending_answer_from_publishing(self):
        for slug in ("r1", "r2", "r3", "r4", "r5"):
            self.make(slug, 1)
        (self.dir / "ship.md").touch()
        (self.dir / "ship.answer").write_text("yes\n")
        base = self.dir / "state"
        attempts = []

        def slow(slug, question):
            attempts.append(slug)
            raise subprocess.TimeoutExpired("ask-user-question", 10)

        with (
            patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(self.dir)}),
            patch.object(w, "open_question", slow),
            patch.object(question_remind, "REOPEN_BUDGET", 0),
        ):
            line = w.cycle(base, "answers", lambda before: w.collect_answers(before))
        self.assertIn("ANSWER ship: yes", line)
        self.assertEqual(attempts, [])

    def test_open_question_runs_through_the_owned_process_group_runner(self):
        with patch.object(
            w.runtime, "run", return_value=SimpleNamespace(returncode=73)
        ) as run:
            self.assertEqual(w.open_question("due", Path("/q/due.md")), 73)
        self.assertEqual(
            run.call_args.args[0][1:3],
            [f"{question_lock.HELD}=1", str(w.ROOT.parents[1] / "ask-user-question")],
        )
        self.assertEqual(run.call_args.args[0][3:], ["due", "/q/due.md"])
        self.assertEqual(run.call_args.kwargs["timeout"], question_remind.OPEN_TIMEOUT)

    def test_the_answers_pass_reopens_but_a_preview_does_not(self):
        self.make("due", 1)
        calls = []
        with (
            patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(self.dir)}),
            patch.object(w, "open_question", lambda *args: calls.append(args) or 0),
        ):
            w.collect_answers(None, consume=False)
            self.assertEqual(calls, [])
            w.collect_answers(None)
        self.assertEqual([slug for slug, _ in calls], ["due"])

    def test_a_reopen_that_raises_does_not_stop_the_answers_pass(self):
        self.make("due", 1)
        (self.dir / "ship.md").touch()
        (self.dir / "ship.answer").write_text("yes\n")
        with (
            patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(self.dir)}),
            patch.object(w, "open_question", side_effect=OSError("no herdr")),
        ):
            raw = w.collect_answers(None)
        self.assertEqual([answer[:2] for answer in raw["answers"]], [("ship", "yes")])

    def test_closing_a_question_removes_its_reminder(self):
        self.make("done", NOW)
        with patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(self.dir)}):
            question_lock.close_question("done", "true", "")
        self.assertEqual(list(self.dir.glob("done.*")), [])


class FeedWriteTest(IsolatedTestCase):
    def test_a_short_write_is_continued_until_the_whole_record_is_written(self):
        real = os.write
        with tempfile.TemporaryDirectory() as tmp:
            feed = Path(tmp) / "feed.log"
            with patch.object(
                feed_attach.os, "write", lambda fd, data: real(fd, bytes(data[:3]))
            ):
                feed_attach.append_line(feed, "abcdefghij\n")
            self.assertEqual(feed.read_text(), "abcdefghij\n")

    def test_a_write_that_makes_no_progress_fails(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(feed_attach.os, "write", lambda fd, data: 0),
        ):
            with self.assertRaises(OSError):
                feed_attach.append_line(Path(tmp) / "feed.log", "x\n")


class CloseHelperTest(IsolatedTestCase):
    def test_a_timed_out_helper_is_killed_with_its_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "child.pid"
            helper = Path(tmp) / "helper.sh"
            helper.write_text(f"#!/bin/sh\nsleep 60 &\necho $! > {child}\nwait\n")
            helper.chmod(0o755)
            with self.assertRaises(subprocess.TimeoutExpired):
                question_lock.run_group([str(helper), "pane"], 1)
            pid = int(child.read_text())
            time.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_an_action_that_stalls_under_the_lock_is_killed_and_the_lock_released(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": tmp}),
            patch.object(question_lock, "ACTION_TIMEOUT", 1),
        ):
            child = Path(tmp) / "child.pid"
            helper = Path(tmp) / "helper.sh"
            helper.write_text(f"#!/bin/sh\nsleep 60 &\necho $! > {child}\nwait\n")
            helper.chmod(0o755)
            argv = [str(helper)]
            self.assertEqual(question_lock.run_locked(argv), question_lock.TIMED_OUT)
            pid = int(child.read_text())
            time.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            with question_lock.answers_lock(tmp):
                pass

    def test_an_action_that_finishes_returns_its_status(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": tmp}),
        ):
            helper = Path(tmp) / "helper.sh"
            helper.write_text("#!/bin/sh\nexit 7\n")
            helper.chmod(0o755)
            self.assertEqual(question_lock.run_locked([str(helper)]), 7)

    def test_a_failing_helper_raises_and_a_passing_one_returns(self):
        self.assertEqual(question_lock.run_group(["true"], 5), 0)
        with self.assertRaises(subprocess.CalledProcessError):
            question_lock.run_group(["false"], 5)

    def test_a_process_started_by_the_holder_with_the_marker_does_not_lock_again(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {question_lock.HELD: "1"}),
        ):
            with question_lock.answers_lock(tmp), question_lock.answers_lock(tmp):
                pass
            self.assertFalse((Path(tmp) / ".answers.lock").exists())

    def test_the_lock_is_not_inherited_by_unrelated_children(self):
        with tempfile.TemporaryDirectory() as tmp, question_lock.answers_lock(tmp):
            self.assertNotIn(question_lock.HELD, os.environ)


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
        with (
            patch.object(pane, "Panel", return_value=panel),
            patch.object(pane, "read_key", lambda *_: next(keys)),
            patch.object(panel, "show"),
            patch.object(panel, "press", pressed.append),
        ):
            pane.follow(Path("x.md"))
        self.assertEqual(pressed, [pane.ESCAPE])

    def test_follow_keeps_polling_a_pending_helper_until_it_ends(self):
        panel = pane.Panel(Path("x.md"), io.StringIO())
        panel.pending = (SimpleNamespace(poll=iter([None, 0]).__next__), None)
        waits = []
        with (
            patch.object(pane, "Panel", return_value=panel),
            patch.object(panel, "show"),
            patch.object(pane, "read_key", lambda wait=None: waits.append(wait)),
        ):
            pane.follow(Path("x.md"))
        self.assertEqual((waits, panel.done), ([pane.POLL_SECONDS] * 2, True))

    def test_columns_default_is_the_terminal_width(self):
        with patch.object(
            pane.shutil, "get_terminal_size", return_value=SimpleNamespace(columns=33)
        ):
            self.assertEqual(pane.Panel(Path("x.md"), io.StringIO()).columns(), 33)

    def test_launcher_runs_ask_user_question_detached_against_the_question_directory(
        self,
    ):
        with patch.object(pane.subprocess, "Popen") as popen:
            pane.launch(Path("/tmp/qs/release.md"), "hide", "release")
        args, kwargs = popen.call_args
        self.assertEqual(
            args[0], [str(pane.ROOT / "ask-user-question"), "hide", "release"]
        )
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["env"]["ORCH_USER_QUESTIONS_DIR"], "/tmp/qs")


if __name__ == "__main__":
    unittest.main()
