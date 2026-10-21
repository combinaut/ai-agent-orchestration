import json
import subprocess
import sys
import time
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
            (questions / "ship.md").touch()
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
            (questions / "ship.md").touch()
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
        import alert_policy
        self.assertEqual(alert_policy.answer_facts("ANSWER ship: " + answer)[0]["text"], "ANSWER ship: " + answer)

    def test_input_keys_and_blank_typed_answer(self):
        with patch.object(question_pane, 'read_key', side_effect=['x', 't', 't', *'123456789', KeyboardInterrupt]), \
                patch('builtins.input', side_effect=['  ', ' typed answer ']), \
                patch.object(question_pane, 'show'), patch.object(question_pane.time, 'sleep'), \
                patch.object(question_pane, 'atomic_write') as write:
            with self.assertRaises(KeyboardInterrupt):
                question_pane.follow(Path('ship.md'))
        self.assertEqual([call.args[1] for call in write.call_args_list], [' typed answer ', *'123456789'])

    def test_main_rejects_missing_question_before_follow(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(question_pane, 'follow') as follow:
            with self.assertRaises(SystemExit) as error:
                question_pane.main([str(Path(tmp) / 'missing.md')])
            self.assertEqual(error.exception.code, 2)
            follow.assert_not_called()

    def test_atomic_write_preserves_destination_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            answer = Path(tmp) / 'ship.answer'
            answer.write_text('old')
            real_replace = os.replace
            def interrupted(source, destination):
                self.assertEqual(Path(source).parent, answer.parent)
                self.assertEqual(answer.read_text(), 'old')
                self.assertEqual(Path(source).read_text(), 'new\n')
                raise OSError('interrupted')
            with patch.object(question_pane.os, 'replace', side_effect=interrupted), \
                    patch.object(question_pane.os, 'fsync', wraps=os.fsync) as fsync:
                with self.assertRaises(OSError):
                    question_pane.atomic_write(answer, 'new')
                fsync.assert_called_once()
            self.assertEqual(answer.read_text(), 'old')
            self.assertEqual(list(answer.parent.iterdir()), [answer])
            with patch.object(question_pane.os, 'replace', wraps=real_replace), \
                    patch.object(question_pane.os, 'fsync', wraps=os.fsync) as fsync:
                question_pane.atomic_write(answer, 'new')
                fsync.assert_called_once()

    def test_feed_pattern_rejects_unrelated_answer_text(self):
        patterns = feed_actions.action_patterns()
        for line in ['ANSWER ship: yes', '2026-10-21 other: ANSWER ship: yes (start)',
                     '2026-10-21 answers: ANSWER ship: yes (stop)',
                     '2026-10-21 answers: ANSWER ship: yes (start) trailing']:
            self.assertFalse(feed_actions.is_action(line, patterns), line)

    def test_blank_answer_is_discarded_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ORCH_USER_QUESTIONS_DIR=tmp):
            (Path(tmp) / 'ship.md').touch()
            (Path(tmp) / 'ship.answer').write_text(' \n ')
            raw = w.collect_answers(None)
            _, events = w.reduce_answers(None, raw)
            self.assertEqual(events, ['ANSWERS blank ship'])
            self.assertEqual(list(Path(tmp).glob('*.answer*')), [])

    def test_failed_reduction_and_soft_deadline_replay(self):
        for failure in ('error', 'deadline'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                questions, base = Path(tmp) / 'questions', Path(tmp) / 'state'
                questions.mkdir()
                (questions / 'ship.md').touch()
                (questions / 'ship.answer').write_text('yes')
                original = w.reduce_answers
                def reduce(before, raw):
                    if failure == 'error':
                        raise ValueError('bad reduction')
                    time.sleep(.3)
                    return original(before, raw)
                with patch.dict(os.environ, ORCH_USER_QUESTIONS_DIR=str(questions)):
                    with patch.object(w, 'reduce_answers', side_effect=reduce):
                        w.cycle(base, 'answers', w.collect_answers, budget=.2)
                    self.assertEqual(len(list(questions.glob('*.processing'))), 1)
                    self.assertEqual(list(questions.glob('*.seen')), [])
                    self.assertIn('ANSWER ship: yes', w.cycle(base, 'answers', w.collect_answers))
                    self.assertIsNone(w.cycle(base, 'answers', w.collect_answers))

    def test_committed_claims_survive_restart_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as tmp:
            questions, base = Path(tmp) / 'questions', Path(tmp) / 'state'
            questions.mkdir()
            (questions / 'ship.md').touch()
            claims = ['00000000000040008000000000000000'] + [f'ffffffffffff4fff8fff{i:012x}' for i in range(1000)]
            for claim in claims:
                (questions / f'ship.{claim}.answer.processing').write_text('yes')
            with patch.dict(os.environ, ORCH_USER_QUESTIONS_DIR=str(questions)):
                with patch.object(w, 'acknowledge_answers', side_effect=OSError('restart')):
                    with self.assertRaises(OSError):
                        w.cycle(base, 'answers', w.collect_answers)
                saved = json.loads((base / 'answers.json').read_text())
                self.assertEqual(set(saved['before']['committed']), set(claims))
                self.assertIsNone(w.cycle(base, 'answers', w.collect_answers))
                self.assertIsNone(w.cycle(base, 'answers', w.collect_answers))
                self.assertEqual(json.loads((base / 'answers.json').read_text())['before']['committed'], [])

    def test_two_pending_identical_submissions_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            questions, base = Path(tmp) / 'questions', Path(tmp) / 'state'
            questions.mkdir()
            (questions / 'ship.md').touch()
            with patch.dict(os.environ, ORCH_USER_QUESTIONS_DIR=str(questions)):
                for _ in range(2):
                    (questions / 'ship.answer').write_text('yes')
                    with patch.object(w, 'publish_events', side_effect=OSError('disk full')):
                        with self.assertRaises(OSError):
                            w.cycle(base, 'answers', w.collect_answers)
                self.assertEqual(len(list(questions.glob('*.processing'))), 2)
                line = w.cycle(base, 'answers', w.collect_answers)
                self.assertEqual(json.loads(line)['events'], ['ANSWER ship: yes'] * 2)
                (questions / 'ship.answer').write_text('yes')
                self.assertIn('ANSWER ship: yes', w.cycle(base, 'answers', w.collect_answers))

    def test_close_waits_for_answer_pass_and_drops_closed_question_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            questions, base = Path(tmp) / 'questions', Path(tmp) / 'state'
            questions.mkdir()
            (questions / 'ship.md').touch()
            (questions / 'ship.answer').write_text('yes')
            started = Path(tmp) / 'started'
            code = ('import sys; from pathlib import Path; from question_lock import close_question; '
                    'Path(sys.argv[1]).touch(); close_question("ship", "", "")')
            original = w.reduce_answers
            children = []
            def reduce(before, raw):
                child = subprocess.Popen([sys.executable, '-c', code, str(started)], cwd=w.ROOT)
                children.append(child)
                limit = time.monotonic() + 5
                while not started.exists() and time.monotonic() < limit:
                    time.sleep(.01)
                self.assertTrue(started.exists(), 'close child did not start')
                with self.assertRaises(subprocess.TimeoutExpired):
                    child.wait(timeout=.2)
                return original(before, raw)
            with patch.dict(os.environ, ORCH_USER_QUESTIONS_DIR=str(questions)):
                try:
                    with patch.object(w, 'reduce_answers', side_effect=reduce):
                        self.assertIn('ANSWER ship: yes', w.cycle(base, 'answers', w.collect_answers))
                    self.assertEqual(children[0].wait(timeout=5), 0)
                    self.assertFalse((questions / 'ship.md').exists())
                    (questions / 'ship.answer').write_text('stale')
                    self.assertIsNone(w.cycle(base, 'answers', w.collect_answers))
                    self.assertEqual([p for p in questions.glob('*.answer*') if not p.name.startswith('.')], [])
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.terminate()
                            child.wait(timeout=5)

    def test_unreadable_claim_does_not_forget_other_committed_claims(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ORCH_USER_QUESTIONS_DIR=tmp):
            directory = Path(tmp)
            (directory / 'ship.md').touch()
            (directory / 'ship.first.answer.processing').write_text('one')
            (directory / 'ship.second.answer.processing').write_text('two')
            before = {'committed': ['first', 'second']}
            with patch.object(Path, 'read_text', side_effect=OSError('unreadable')):
                raw = w.collect_answers(before)
            after, _ = w.reduce_answers(before, raw)
            self.assertEqual(set(after['committed']), {'first', 'second'})
            _, events = w.reduce_answers(after, w.collect_answers(after))
            self.assertEqual(events, [])
