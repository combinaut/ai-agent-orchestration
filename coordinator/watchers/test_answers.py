import os
import tempfile
import importlib.util
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase
import feed_actions
import watchers as w

spec = importlib.util.spec_from_file_location("question_pane", w.ROOT.parent / "question-pane.py")
question_pane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(question_pane)


class AnswersTest(IsolatedTestCase):
    def test_question_pane_writes_answers_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            answer = Path(tmp) / "deploy.answer"
            answer.write_text("old\n")
            question_pane.atomic_write(answer, "  2  ")
            self.assertEqual(answer.read_text(), "2\n")
            self.assertEqual(list(Path(tmp).glob("deploy.answer.*")), [])

    def test_answer_is_seen_and_matches_the_shared_feed_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            questions = Path(tmp) / "questions"
            questions.mkdir()
            (questions / "ship.answer").write_text("  option one\n")
            with patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(questions)}, clear=False):
                state, events = w.reduce_answers(None, w.collect_answers(None))
            self.assertEqual(state, {"unreadable": []})
            self.assertEqual(events, ["ANSWER ship: option one"])
            self.assertFalse((questions / "ship.answer").exists())
            self.assertEqual((questions / "ship.answer.seen").read_text(), "  option one\n")
            line = "2026-10-21T12:00:00+00:00 answers: ANSWER ship: option one (start)"
            self.assertTrue(feed_actions.is_action(line, feed_actions.action_patterns()))
