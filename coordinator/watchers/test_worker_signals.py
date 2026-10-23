import argparse
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import watchers as w
import worker_signals as s


class SignalTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = patch.dict(
            os.environ,
            {
                "HOME": str(self.base),
                "HERDR_PANE_ID": "pane:1",
                "HERDR_WORKSPACE_ID": "worker",
                "ORCH_USER_QUESTIONS_DIR": str(self.base / "questions"),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.work = dict(
            workspaces=[dict(id="worker", owner="owner"), dict(id="owner")],
            panes=[
                dict(pane="pane:1", workspace="worker"),
                dict(pane="architect", workspace="owner"),
            ],
        )
        adapter = patch.object(s.discovery, "open_work", return_value=self.work)
        adapter.start()
        self.addCleanup(adapter.stop)
        opener = patch.object(s, "open_user_question", return_value=0)
        self.opener = opener.start()
        self.addCleanup(opener.stop)

    def record(self, **kw):
        return s.new_question(s.identity(), "Which?", "a,b", **kw)

    def test_pending_bound(self):
        self.record(timeout=60)
        with self.assertRaisesRegex(ValueError, "pending question"):
            self.record(timeout=60)

    def test_expired_pending_replaced_and_pruned(self):
        q = self.record(timeout=1)
        with patch.object(s.time, "time", return_value=q["deadline"] + 1):
            other = self.record(timeout=10)
        self.assertNotEqual(other["id"], q["id"])
        self.assertFalse(s.question_path(q["id"]).exists())

    def test_wait_timeout_and_poll_bound(self):
        q = self.record(timeout=2)
        elapsed = [0]

        def advance(seconds):
            elapsed[0] += seconds

        with (
            patch.object(s.time, "monotonic", side_effect=lambda: elapsed[0]),
            patch.object(s.time, "sleep", side_effect=advance) as sleep,
        ):
            with self.assertRaisesRegex(ValueError, "timed out"):
                s.wait_answer(q, 2)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 1])

    def test_invalid_timeout(self):
        for timeout in [0, -1, float("inf"), float("nan")]:
            with self.assertRaises(ValueError):
                self.record(timeout=timeout)

    def test_owner_answer_and_atomic_wait(self):
        q = self.record(timeout=60)
        with patch.dict(
            os.environ, HERDR_WORKSPACE_ID="owner", HERDR_PANE_ID="architect"
        ):
            s.answer(q["id"], "a")
        with self.assertRaisesRegex(ValueError, "pending question"):
            self.record(timeout=60)
        self.assertEqual(s.wait_answer(q, 60), "a")
        self.assertFalse(s.question_path(q["id"]).exists())
        self.assertFalse(s.answer_path(q["id"]).exists())

    def test_foreign_answer_refused(self):
        q = self.record(timeout=60)
        with self.assertRaisesRegex(ValueError, "owner architect"):
            s.answer(q["id"], "a")
        self.assertFalse(s.answer_path(q["id"]).exists())

    def test_duplicate_answer_refused(self):
        q = self.record(timeout=60)
        with patch.dict(
            os.environ, HERDR_WORKSPACE_ID="owner", HERDR_PANE_ID="architect"
        ):
            s.answer(q["id"], "a")
            with self.assertRaisesRegex(ValueError, "already answered"):
                s.answer(q["id"], "b")

    def test_escalation_and_answers_watcher_bridge(self):
        q = self.record(timeout=60)
        with (
            patch.dict(
                os.environ, HERDR_WORKSPACE_ID="owner", HERDR_PANE_ID="architect"
            ),
            patch.object(s, "open_user_question", return_value=0),
        ):
            s.answer(q["id"], None, to_user=True)
        directory = s.questions_directory()
        self.assertTrue((directory / (q["id"] + ".md")).exists())
        (directory / (q["id"] + ".answer")).write_text("b")
        raw = w.collect_answers(None)
        w.acknowledge_answers(raw)
        self.assertEqual(s.wait_answer(q, 0.01), "b")
        self.assertFalse((directory / (q["id"] + ".md")).exists())

    def test_heartbeat_rate_and_done_dedupe(self):
        ident = s.identity()
        with patch.object(s.time, "time", return_value=100):
            first = s.record_signal(ident, "heartbeat")
        with patch.object(s.time, "time", return_value=101):
            self.assertEqual(s.record_signal(ident, "heartbeat"), first)
        with patch.object(s.time, "time", return_value=130):
            self.assertEqual(s.record_signal(ident, "heartbeat")["at"], 130)
        first = s.record_signal(
            ident, "done", outcome="succeeded", report="/r", summary="ok"
        )
        self.assertEqual(s.record_signal(ident, "done", outcome="failed"), first)
        self.assertEqual(s.record_signal(ident, "heartbeat")["kind"], "done")
        self.assertNotEqual(
            s.record_signal(ident, "heartbeat", new_job=True)["job"], first["job"]
        )

    def test_prune_closed_and_answered_records(self):
        s.record_signal(s.identity(), "done", outcome="succeeded")
        q = self.record(timeout=60)
        with patch.dict(
            os.environ, HERDR_WORKSPACE_ID="owner", HERDR_PANE_ID="architect"
        ):
            s.answer(q["id"], "a")
        s.collect([], {}, 0)
        self.assertEqual(list(s.directory().glob("*.json")), [])
        self.assertFalse(s.answer_path(q["id"]).exists())

    def test_done_and_fresh_heartbeat_suppress_waiting(self):
        for kind, age, suppressed in [
            ("done", 1000, True),
            ("heartbeat", 100, True),
            ("heartbeat", 301, False),
        ]:
            raw = dict(
                result=dict(
                    agents=[
                        dict(
                            pane_id="pane:1", workspace_id="worker", agent_status="idle"
                        )
                    ]
                ),
                workspaces=dict(
                    result=dict(
                        workspaces=[
                            dict(workspace_id="worker", tokens=dict(owner="owner"))
                        ]
                    )
                ),
                observed_at=1000,
                worker_signals={
                    "pane:1": dict(
                        kind=kind, at=1000 - age, owner="owner", workspace="worker"
                    )
                },
            )
            old, _ = w.reduce_workers(None, dict(raw, observed_at=0, worker_signals={}))
            _, lines = w.reduce_workers(old, raw)
            self.assertEqual(any("WORKER WAITING" in x for x in lines), not suppressed)

    def test_signals_native_without_binding(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "signal_inbox", Path(__file__).with_name("notify-inbox.py")
        )
        inbox = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inbox)
        args = argparse.Namespace(bindings_dir=self.base, binding=None)
        line = (
            "workers: WORKER QUESTION pane:1 owner=owner id=signal-abc question=Which?"
        )
        routed = inbox.native_messages(args, ["stamp " + line], False, [])
        self.assertEqual(routed[0][0], str(self.base / "architect-owner.json"))
        self.assertIn("Which?", routed[0][1])

    def test_signal_events_dedupe_and_full_payload(self):
        signal = dict(
            id="signal-abc",
            kind="question",
            pane="pane:1",
            workspace="worker",
            owner="owner",
            question="Which?",
        )
        raw = dict(result=dict(agents=[]), signal_events=[signal])
        after, events = w.reduce_workers(None, raw)
        self.assertIn("question=Which?", "\n".join(events))
        self.assertEqual(w.reduce_workers(after, raw)[1], [])

    def test_invalid_id_cannot_escape_directory(self):
        with self.assertRaises(ValueError):
            s.question_path("../other")

    def test_identity_requires_environment_and_owner(self):
        with patch.dict(os.environ, HERDR_PANE_ID=""):
            with self.assertRaises(ValueError):
                s.worker_identity(s.identity())
        self.work["workspaces"][0]["owner"] = None
        with self.assertRaises(ValueError):
            s.worker_identity(s.identity())

    def test_completion_backlog_bound_and_expiry(self):
        ident = s.identity()
        with patch.object(s.time, "time", return_value=100):
            for _ in range(s.DONE_EVENTS_MAX + 1):
                s.record_signal(ident, "heartbeat", new_job=True)
                s.record_signal(ident, "done", outcome="succeeded")
            record = s.record_signal(ident, "heartbeat", new_job=True)
        self.assertEqual(len(record["done_events"]), s.DONE_EVENTS_MAX)
        rows = [dict(pane_id=ident["pane"], workspace_id=ident["workspace"])]
        _, events = s.collect(rows, {"worker": "owner"}, 101)
        self.assertEqual(len(events), s.DONE_EVENTS_MAX)
        _, events = s.collect(rows, {"worker": "owner"}, 3701)
        self.assertEqual(events, [])
        self.assertEqual(s.read(s.pane_path(ident["pane"]))["done_events"], [])
        s.write(s.pane_path(ident["pane"]), record)
        with patch.object(s.time, "time", return_value=3701):
            self.assertEqual(s.record_signal(ident, "heartbeat")["done_events"], [])

    def test_question_retention_bound(self):
        q = self.record(timeout=1)
        rows = [dict(pane_id="pane:1", workspace_id="worker")]
        s.collect(rows, {"worker": "owner"}, q["deadline"] + 3601)
        self.assertFalse(s.question_path(q["id"]).exists())

    def test_full_signal_payload_survives_policy(self):
        import alert_policy

        text = "x" * 300
        line = s.event_line(
            dict(
                id="signal-abc",
                kind="question",
                pane="pane:1",
                owner="owner",
                workspace="worker",
                question=text,
            )
        )
        self.assertIn(text, alert_policy.worker_facts(line)[0]["text"])

    def test_wait_timeout_bounds_contended_answer_lock(self):
        import fcntl
        import time

        q = self.record(timeout=60)
        with (s.questions_directory() / ".answers.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "timed out"):
                s.wait_answer(q, 0.02)
            self.assertLess(time.monotonic() - started, 1)
