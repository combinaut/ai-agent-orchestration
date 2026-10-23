import argparse
import json
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
        import subprocess

        self.opener.side_effect = subprocess.TimeoutExpired("close question", 10)
        self.assertEqual(s.wait_answer(q, 0.01), "b")
        self.opener.assert_not_called()
        self.assertTrue(s.read(s.question_path(q["id"]))["cleanup"])
        s.collect(None, None, s.time.time())
        self.assertTrue(s.question_path(q["id"]).exists())
        self.opener.side_effect = None
        self.opener.return_value = 1
        s.collect(None, None, s.time.time())
        self.assertTrue(s.question_path(q["id"]).exists())
        self.opener.return_value = 0
        s.collect(None, None, s.time.time())
        self.assertFalse((directory / (q["id"] + ".md")).exists())
        self.assertFalse(s.question_path(q["id"]).exists())

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
        self.assertEqual(
            s.record_signal(
                ident, "done", outcome="succeeded", report="/r", summary="ok"
            ),
            first,
        )
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
        s.collect([], {}, s.time.time() + 1)
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

    def test_notifier_startup_replays_only_unread_signals(self):
        import importlib.util

        import alert_policy

        spec = importlib.util.spec_from_file_location(
            "startup_inbox", Path(__file__).with_name("notify-inbox.py")
        )
        inbox = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inbox)
        line = "WORKER QUESTION pane:1 owner=owner id=signal-test workspace=worker question=Which?"
        (self.base / "workers.json").write_text(
            json.dumps(
                dict(
                    before={},
                    events=[
                        dict(
                            seq=1,
                            at=100,
                            line=json.dumps(dict(mode="workers", events=[line])),
                        )
                    ],
                )
            )
        )
        cursor, late = {}, []
        history = inbox.history(self.base, cursor, ["workers"], 101, late)
        state, initial = alert_policy.rebuild(history, 101)
        self.assertEqual(initial, [])
        sent = alert_policy.apply(state, late, 101)
        self.assertEqual(len(sent), 1)
        self.assertIn("question=Which?", sent[0])
        again = []
        inbox.history(self.base, cursor, ["workers"], 102, again)
        self.assertEqual(again, [])

    def test_live_pane_survives_agent_registry_exit(self):
        record = s.record_signal(s.identity(), "done", outcome="succeeded")

        def command(*args):
            if args[-2:] == ("agent", "list"):
                return json.dumps(dict(result=dict(agents=[])))
            if args[-2:] == ("workspace", "list"):
                return json.dumps(
                    dict(
                        result=dict(
                            workspaces=[
                                dict(workspace_id="worker", tokens=dict(owner="owner"))
                            ]
                        )
                    )
                )
            raise AssertionError(args)

        with (
            patch.dict(os.environ, HERDR_ENV="1"),
            patch.object(w, "command", side_effect=command),
        ):
            raw = w.collect("workers", None)
        self.assertEqual(raw["signal_events"][0]["id"], record["id"])
        self.assertTrue(s.pane_path("pane:1").exists())

    def test_unreadable_pane_snapshot_preserves_signals(self):
        s.record_signal(s.identity(), "done", outcome="succeeded")
        with patch.object(
            s.discovery, "open_work", side_effect=s.discovery.Unavailable("offline")
        ):
            _, events = s.collect_live(None, s.time.time())
        self.assertEqual(len(events), 1)
        self.assertTrue(s.pane_path("pane:1").exists())

    def test_signal_newer_than_snapshot_is_not_pruned(self):
        with patch.object(s.time, "time", return_value=200):
            s.record_signal(s.identity(), "done", outcome="succeeded")
        _, events = s.collect([], {}, 201, snapshot_at=100)
        self.assertEqual(len(events), 1)
        self.assertTrue(s.pane_path("pane:1").exists())

    def test_expired_completion_not_emitted_but_still_done(self):
        with patch.object(s.time, "time", return_value=100):
            s.record_signal(s.identity(), "done", outcome="succeeded")
        signals, events = s.collect(
            [dict(pane_id="pane:1", workspace_id="worker")], {"worker": "owner"}, 3701
        )
        self.assertEqual(events, [])
        self.assertEqual(signals["pane:1"]["kind"], "done")

    def test_creation_timeout_bounds_contended_answer_lock(self):
        import fcntl
        import time

        ident = s.identity()
        s.questions_directory().mkdir(parents=True)
        with (s.questions_directory() / ".answers.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "timed out"):
                s.new_question(ident, "Which?", "", timeout=0.02)
            self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(list(s.directory().glob("*.json")), [])

    def test_expired_escalation_defers_panel_cleanup_on_new_ask(self):
        q = self.record(timeout=1)
        (s.questions_directory() / (q["id"] + ".md")).write_text("Question")
        with patch.object(s.time, "time", return_value=q["deadline"] + 1):
            with self.assertRaisesRegex(ValueError, "cleanup"):
                self.record(timeout=0.01)
        self.opener.assert_not_called()
        self.assertTrue(s.read(s.question_path(q["id"]))["cleanup"])

    def test_question_cleanup_backlog_global_bound(self):
        ident = s.identity()
        with patch.object(s, "QUESTIONS_MAX", 2):
            s.new_question(ident, "Which?", "", timeout=60)
            s.new_question(dict(ident, pane="pane:2"), "Which?", "", timeout=60)
            with self.assertRaisesRegex(ValueError, "capacity reached"):
                s.new_question(dict(ident, pane="pane:3"), "Which?", "", timeout=60)
        self.assertEqual(len(list(s.directory().glob("signal-*.json"))), 2)

    def test_corrupt_worker_state_still_publishes_explicit_signals(self):
        record = self.record(timeout=60)
        raw = dict(result=dict(agents=[]), signal_events=[record])
        (self.base / "workers.json").write_text("corrupt")
        first = w.cycle(self.base, "workers", lambda before: raw)
        self.assertIn("WORKER QUESTION", first)
        second = w.cycle(self.base, "workers", lambda before: raw)
        self.assertFalse(second and "WORKER QUESTION" in second)

    def test_existing_cursor_replay_preserves_other_events_once(self):
        import importlib.util

        import alert_policy

        spec = importlib.util.spec_from_file_location(
            "existing_cursor_inbox", Path(__file__).with_name("notify-inbox.py")
        )
        inbox = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inbox)
        signal = "WORKER QUESTION pane:1 owner=owner id=signal-existing workspace=worker question=Which?"
        event = dict(
            seq=1,
            at=100,
            line=json.dumps(
                dict(mode="workers", events=[signal, "WORKER pane:2 blocked"])
            ),
        )
        (self.base / "workers.json").write_text(
            json.dumps(dict(before={}, events=[event]))
        )
        (self.base / "workers.heartbeat").touch()
        cursor = dict(workers=dict(seq=0, stale=False, since=100))
        late = []
        history = inbox.history(self.base, cursor, ["workers"], 101, late)
        state, _ = alert_policy.rebuild(history, 101)
        lines = alert_policy.apply(state, late, 101)
        self.assertEqual(sum("WORKER QUESTION" in line for line in lines), 1)
        self.assertTrue(any("pane:2 blocked" in line for line in lines))
        again = inbox.reader.read_once(self.base, cursor, 102, 900, ["workers"])
        self.assertIsNone(again)

    def test_panel_cleanup_budget_rotates_failed_retries(self):
        records = []
        for number in range(3):
            ident = dict(s.identity(), pane=f"pane:{number}")
            record = s.new_question(ident, "Which?", "", 60)
            (s.questions_directory() / (record["id"] + ".md")).write_text("Question")
            record["cleanup"] = True
            s.write(s.question_path(record["id"]), record)
            records.append(record)
        self.opener.return_value = 1
        s.collect(None, None, 100)
        self.assertEqual(self.opener.call_count, 1)
        first = self.opener.call_args.args[1]
        self.assertEqual(self.opener.call_args.kwargs["timeout"], 2)
        s.collect(None, None, 101)
        self.assertEqual(self.opener.call_count, 2)
        self.assertNotEqual(self.opener.call_args.args[1], first)
        self.assertEqual(len(list(s.directory().glob("signal-*.json"))), 3)

    def test_heartbeat_published_during_snapshot_is_fresh(self):
        clock = [100]
        ident = s.identity()

        def snapshot(_command):
            clock[0] = 200
            s.record_signal(ident, "heartbeat")
            return self.work

        def command(*args):
            if args[-2:] == ("agent", "list"):
                return json.dumps(dict(result=dict(agents=[])))
            return json.dumps(
                dict(
                    result=dict(
                        workspaces=[
                            dict(workspace_id="worker", tokens=dict(owner="owner"))
                        ]
                    )
                )
            )

        with (
            patch.object(s.time, "time", side_effect=lambda: clock[0]),
            patch.dict(os.environ, HERDR_ENV="1"),
            patch.object(w, "command", side_effect=command),
        ):
            s.record_signal(ident, "heartbeat")
            with patch.object(s.discovery, "open_work", side_effect=snapshot):
                raw = w.collect("workers", None)
        self.assertTrue(
            s.suppressed(dict(pane_id="pane:1", workspace_id="worker"), raw)
        )

    def test_close_timeout_terminates_nested_close_process(self):
        import signal
        import subprocess
        import sys

        child = self.base / "child.py"
        pidfile = self.base / "child.pid"
        child.write_text(
            "import os,pathlib,time\npathlib.Path("
            + repr(str(pidfile))
            + ").write_text(str(os.getpid()))\ntime.sleep(20)\n"
        )
        helper = self.base / "ask-user-question"
        helper.write_text(
            "#!"
            + sys.executable
            + "\nimport sys\nsys.path.insert(0,"
            + repr(str(w.ROOT))
            + ")\nfrom question_lock import run_group\nrun_group(["
            + repr(sys.executable)
            + ","
            + repr(str(child))
            + "],30)\n"
        )
        helper.chmod(0o755)
        root = self.base / "coordinator/watchers"
        root.mkdir(parents=True)
        with patch.object(w, "ROOT", root):
            with self.assertRaises(subprocess.TimeoutExpired):
                w.open_question("close", "signal-test", timeout=1)
        self.assertTrue(
            pidfile.exists(), "the owned descendant must start before cancellation"
        )
        pid = int(pidfile.read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
        else:
            alive = True
        try:
            self.assertFalse(alive, "nested closer survived cancellation")
        finally:
            if alive:
                os.kill(pid, signal.SIGTERM)

    def test_interrupted_ask_cancels_under_lock_and_can_reask(self):
        import signal
        import subprocess
        import sys
        import time

        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum):
                code = 'import worker_signals as s; s.identity=lambda:dict(pane="pane:1",workspace="worker",owner="owner"); raise SystemExit(s.main())'
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        code,
                        "ask",
                        "--question",
                        "Which?",
                        "--timeout",
                        "30",
                    ],
                    cwd=Path(s.__file__).parent,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    deadline = time.monotonic() + 5
                    while (
                        not list(s.directory().glob("signal-*.json"))
                        and process.poll() is None
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                    self.assertTrue(list(s.directory().glob("signal-*.json")))
                    process.send_signal(signum)
                    process.communicate(timeout=5)
                    self.assertFalse(list(s.directory().glob("signal-*.json")))
                    record = self.record(timeout=60)
                    self.assertEqual(record["asker_pid"], os.getpid())
                    s.remove_question(record)
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.communicate(timeout=5)

    def test_dead_asker_is_replaced_without_waiting_for_deadline(self):
        record = self.record(timeout=60)
        record["asker_pid"] = 999999999
        s.atomic_write(s.questions_directory() / (record["id"] + ".md"), "Question")
        s.write(s.question_path(record["id"]), record)
        replacement = self.record(timeout=60)
        self.assertNotEqual(record["id"], replacement["id"])
        self.assertTrue(s.read(s.question_path(record["id"]))["cleanup"])

    def test_changed_done_is_a_new_completion_and_noop_is_visible(self):
        import contextlib
        import io

        ident = s.identity()
        first = s.record_signal(
            ident, "done", outcome="succeeded", report="/r1", summary="one"
        )
        second = s.record_signal(
            ident, "done", outcome="failed", report="/r2", summary="two"
        )
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["report"], "/r2")
        self.assertEqual(len(second["done_events"]), 2)
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            self.assertEqual(
                s.record_signal(
                    ident, "done", outcome="failed", report="/r2", summary="two"
                ),
                second,
            )
        self.assertEqual(len(stream.getvalue().splitlines()), 1)

    def test_work_after_done_restores_waiting_across_polls(self):
        record = s.record_signal(s.identity(), "done", outcome="succeeded")

        def raw(status, now):
            return dict(
                result=dict(
                    agents=[
                        dict(
                            pane_id="pane:1", workspace_id="worker", agent_status=status
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
                observed_at=now,
                worker_signals={"pane:1": record},
            )

        before, _ = w.reduce_workers(None, raw("idle", record["at"] + 1))
        working, _ = w.reduce_workers(before, raw("working", record["at"] + 2))
        later, _ = w.reduce_workers(working, raw("idle", record["at"] + 3))
        _, events = w.reduce_workers(later, raw("idle", record["at"] + 1000))
        self.assertTrue(any("WORKER WAITING" in line for line in events))

    def test_closed_or_retagged_pane_emits_remaining_done_once(self):
        for rows, owners in (
            ([], {}),
            ([dict(pane_id="pane:1", workspace_id="worker")], {"worker": "other"}),
        ):
            record = s.record_signal(
                s.identity(),
                "done",
                outcome="failed",
                report="/report",
                summary="failed",
            )
            _, events = s.collect(rows, owners, record["at"] + 1)
            self.assertEqual([r["id"] for r in events], [record["id"]])
            self.assertFalse(s.pane_path("pane:1").exists())
            self.assertEqual(s.collect(rows, owners, record["at"] + 2)[1], [])

    def test_worker_brief_retains_execution_and_picker_rules(self):
        root = Path(s.__file__).parents[2]
        for relative in (
            "skills/architect/SKILL.md",
            "skills/architect/references/dispatch.md",
        ):
            text = (root / relative).read_text()
            self.assertIn("run_in_background", text)
            self.assertIn("above `--timeout`", text)
        skill = (root / "skills/architect/SKILL.md").read_text()
        self.assertIn("SLOT: go", skill)
        self.assertIn("may not answer", skill)
        for relative in ("README.md", "docs/architecture.md", "docs/watchers.md"):
            lines = [
                line
                for line in (root / relative).read_text().splitlines()
                if "worker signals](" in line
            ]
            self.assertEqual(len(lines), 1)
            self.assertLess(len(lines[0]), 180)

    def test_resumed_completion_survives_unavailable_signal_snapshot(self):
        record = s.record_signal(s.identity(), "done", outcome="succeeded")
        row = dict(pane_id="pane:1", workspace_id="worker", agent_status="working")
        raw = dict(
            result=dict(agents=[row]),
            workspaces=dict(
                result=dict(
                    workspaces=[dict(workspace_id="worker", tokens=dict(owner="owner"))]
                )
            ),
            observed_at=record["at"] + 1,
            worker_signals={"pane:1": record},
        )
        before, _ = w.reduce_workers(None, raw)
        unavailable = dict(
            raw,
            result=dict(agents=[dict(row, agent_status="idle")]),
            workspaces=None,
            observed_at=record["at"] + 2,
        )
        unavailable.pop("worker_signals")
        missing, _ = w.reduce_workers(before, unavailable)
        self.assertEqual(missing.get("resumed_done_ids"), [record["id"]])
        recovered = dict(
            raw, result=unavailable["result"], observed_at=record["at"] + 3
        )
        later, _ = w.reduce_workers(missing, recovered)
        _, events = w.reduce_workers(
            later, dict(recovered, observed_at=record["at"] + 1000)
        )
        self.assertTrue(any("WORKER WAITING" in line for line in events))
