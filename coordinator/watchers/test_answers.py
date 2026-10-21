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
                raw = w.collect_answers(None)
                state, events = w.reduce_answers(None, raw)
                w.acknowledge_answers(raw)
            self.assertEqual(state["unreadable"], [])
            self.assertEqual(events, ["ANSWER ship: option one"])
            self.assertFalse((questions / "ship.answer").exists())
            [seen] = questions.glob("ship.*.answer.seen")
            self.assertEqual(seen.read_text(), "  option one\n")
            line = "2026-10-21T12:00:00+00:00 answers: ANSWER ship: option one (start)"
            self.assertTrue(feed_actions.is_action(line, feed_actions.action_patterns()))

    def test_processing_answer_replays_after_publish_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            questions, base = Path(tmp) / "questions", Path(tmp) / "state"
            questions.mkdir()
            (questions / "ship.answer").write_text("one\n")
            with patch.dict(os.environ, {"ORCH_USER_QUESTIONS_DIR": str(questions)}, clear=False):
                with patch.object(w, "publish_events", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        w.cycle(base, "answers", lambda before: w.collect_answers(before))
                self.assertEqual(len(list(questions.glob("ship.*.answer.processing"))), 1)
                line = w.cycle(base, "answers", lambda before: w.collect_answers(before))
            self.assertIn("ANSWER ship: one", line)
            self.assertEqual(len(list(questions.glob("ship.*.answer.seen"))), 1)

    def test_long_answer_is_not_truncated(self):
        answer = "x" * 300 + " LAST IMPORTANT WORDS"
        facts = feed_actions
        import alert_policy
        self.assertEqual(alert_policy.answer_facts("ANSWER ship: " + answer)[0]["text"], "ANSWER ship: " + answer)
