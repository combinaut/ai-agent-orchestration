"""Session-scoped waiting lifecycle, including unreadable ownership and native routing."""

import argparse
import importlib.util
import tempfile
import json
import os
import subprocess
from unittest.mock import patch
from pathlib import Path

from test_support import IsolatedTestCase
import alert_policy
import watchers as w

spec = importlib.util.spec_from_file_location(
    "waiting_inbox", w.ROOT / "notify-inbox.py"
)
inbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inbox)


class WaitingTests(IsolatedTestCase):
    def setUp(self):
        self.state = None
        self.row = dict(
            pane_id="p1",
            workspace_id="w1",
            name="worker",
            agent_status="done",
            agent_session={"value": "s1"},
            tokens={},
        )

    def scan(self, now, rows=None, owners=True):
        raw = dict(
            result={"agents": [self.row] if rows is None else rows},
            observed_at=now,
            workspaces=[dict(workspace_id="w1", tokens={"owner": "architect"})]
            if owners
            else [],
        )
        if owners is None:
            raw["workspaces"] = None
        self.state, events = w.reduce_workers(self.state, raw)
        return [e for e in events if e.startswith("WORKER WAITING ")]

    def start(self):
        self.assertEqual(self.scan(0), [])
        self.assertEqual(self.scan(600), [])
        events = self.scan(601)
        self.assertEqual(len(events), 1)
        self.assertTrue(
            events[0].startswith("WORKER WAITING worker pane=p1 owner=architect 10m")
        )
        self.assertIn("session=s1", events[0])
        return events[0]

    def test_own_background_jobs_are_explicitly_uncertain_without_a_reliable_sensor(
        self,
    ):
        line = self.start()
        self.assertTrue(line.endswith("(may be waiting on its own job)"))
        self.row["name"] = "long-worker-name-" * 20
        self.scan(1800)
        line = self.scan(3600)[0]
        self.assertLessEqual(len(line), alert_policy.LINE_LIMIT)
        self.assertTrue(line.endswith("(may be waiting on its own job)"))
        fact = alert_policy.worker_facts(line)[0]
        self.assertTrue(fact["key"].endswith(":ESCALATE"))
        self.assertTrue(fact["text"].endswith("(may be waiting on its own job)"))

    def test_threshold_and_no_extra_repeats(self):
        self.start()
        self.assertEqual(self.scan(1799), [])
        self.assertIn("REPEAT", self.scan(1800)[0])
        self.assertEqual(self.scan(3599), [])
        self.assertIn("ESCALATE", self.scan(3600)[0])
        self.assertEqual(self.scan(99999), [])

    def test_clears_and_prunes_on_working_closed_or_untagged(self):
        for cause in ("working", "closed", "untagged"):
            with self.subTest(cause=cause):
                self.state = None
                self.row["agent_status"] = "done"
                self.start()
                if cause == "working":
                    self.row["agent_status"] = "working"
                events = self.scan(
                    700,
                    rows=[] if cause == "closed" else None,
                    owners=cause != "untagged",
                )
                self.assertTrue(
                    events[0].endswith("cleared" + alert_policy.WORKER_WAIT_NOTE)
                )
                self.assertEqual(self.state["waiting"], {})

    def test_successor_in_same_pane_and_name_starts_fresh(self):
        self.start()
        self.row["agent_session"] = {"value": "s2"}
        self.assertTrue(
            self.scan(700)[0].endswith("cleared" + alert_policy.WORKER_WAIT_NOTE)
        )
        self.assertEqual(set(self.state["waiting"]), {"s2"})
        self.assertEqual(self.scan(1300), [])
        self.assertIn("session=s2", self.scan(1301)[0])

    def test_role_panes_and_missing_sessions_never_alert(self):
        for role in ("architect", "coordinator"):
            self.state = None
            self.row["tokens"] = {"role": role}
            self.scan(0)
            self.assertEqual(self.scan(9999), [])
        self.row.update(tokens={}, agent_session=None)
        self.state = None
        self.scan(0)
        self.assertEqual(self.scan(9999), [])

    def test_unreadable_ownership_or_status_keeps_state(self):
        self.start()
        saved = self.state["waiting"].copy()
        self.assertEqual(self.scan(900, owners=None), [])
        self.assertEqual(self.state["waiting"], saved)
        self.row["agent_status"] = "unknown"
        self.assertEqual(self.scan(1000), [])
        self.assertEqual(self.state["waiting"], saved)
        self.row["agent_status"] = "working"
        self.assertTrue(
            self.scan(1100)[0].endswith("cleared" + alert_policy.WORKER_WAIT_NOTE)
        )

    def test_session_metadata_gap_preserves_state_until_real_successor(self):
        self.start()
        saved = self.state["waiting"].copy()
        self.row["agent_session"] = None
        self.assertEqual(self.scan(700), [])
        self.assertEqual(self.state["waiting"], saved)
        self.row["agent_session"] = {"value": "s2"}
        self.assertTrue(
            self.scan(800)[0].endswith("cleared" + alert_policy.WORKER_WAIT_NOTE)
        )
        self.assertEqual(self.scan(1400), [])
        self.assertIn("session=s2", self.scan(1401)[0])

    def test_owner_change_clears_and_restarts(self):
        self.start()
        raw = dict(
            result={"agents": [self.row]},
            observed_at=700,
            workspaces=[dict(workspace_id="w1", tokens={"owner": "new-owner"})],
        )
        self.state, events = w.reduce_workers(self.state, raw)
        self.assertTrue(events[0].endswith("cleared" + alert_policy.WORKER_WAIT_NOTE))
        self.assertEqual(self.state["waiting"]["s1"]["since"], 700)
        self.assertEqual(self.state["waiting"]["s1"]["owner"], "new-owner")

    def test_new_work_resets_timer_even_with_same_session(self):
        self.start()
        self.row["agent_status"] = "working"
        self.scan(700)
        self.row["agent_status"] = "idle"
        self.assertEqual(self.scan(800), [])
        self.assertEqual(self.scan(1400), [])
        self.assertEqual(len(self.scan(1401)), 1)

    def test_policy_delivers_each_stage_and_immediate_clear(self):
        line = self.start()
        policy = {}
        for now, text in (
            (601, line),
            (1800, self.scan(1800)[0]),
            (3600, self.scan(3600)[0]),
        ):
            lines = alert_policy.apply(
                policy, [dict(mode="workers", at=now, events=[text])], now
            )
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].endswith("(start)"))
        self.row["agent_status"] = "working"
        clear = self.scan(3700)[0]
        lines = alert_policy.apply(
            policy, [dict(mode="workers", at=3700, events=[clear])], 3700
        )
        self.assertTrue(any(line.endswith("(clear)") for line in lines))

    def test_monitor_cannot_swallow_native_coordinator_escalation(self):
        self.start()
        self.scan(1800)
        line = "time workers: " + self.scan(3600)[0] + " (start)"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = argparse.Namespace(
                bindings_dir=base,
                binding=base / "coordinator.json",
                cursor=base / "cursor",
                feed=base / "feed",
            )
            cursor = {
                inbox.PENDING_KEY: dict(
                    lines=[line], fed=True, coordinator="watch", inode=1, offset=0
                )
            }
            with patch.object(inbox, "flush_outbox", return_value=False):
                inbox.finish(args, cursor, 3600, inbox.action_patterns())
            self.assertEqual(len(cursor[inbox.OUTBOX_KEY]), 1)
            self.assertNotIn("watch", cursor[inbox.OUTBOX_KEY][0])
            with patch.object(inbox, "send_line", return_value=False) as send:
                inbox.flush_outbox(args, cursor, 3600)
                self.assertEqual(send.call_count, 1)
                inbox.flush_outbox(args, cursor, 3620)
                self.assertEqual(send.call_count, 1)
            with patch.object(inbox, "send_line", return_value=True):
                inbox.flush_outbox(args, cursor, 3660)
            self.assertNotIn(inbox.OUTBOX_KEY, cursor)

    def test_threshold_settings_are_used(self):
        with patch.dict(os.environ, ORCH_WAIT_MIN="2", ORCH_WAIT_REPEAT_MIN="5"):
            self.scan(0)
            self.assertEqual(self.scan(2), [])
            self.assertEqual(len(self.scan(3)), 1)
            self.assertIn("REPEAT", self.scan(5)[0])
            self.assertIn("ESCALATE", self.scan(10)[0])
            self.assertEqual(self.scan(99999), [])

    def test_native_owner_route_and_coordinator_only_for_escalation(self):
        line = self.start()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            owner = base / "architect-architect.json"
            owner.touch()
            args = argparse.Namespace(
                bindings_dir=base, binding=base / "coordinator.json"
            )
            for text in (line, self.scan(1800)[0]):
                stamped = ["time workers: " + text + " (start)"]
                messages = inbox.native_messages(
                    args, stamped, True, inbox.action_patterns()
                )
                self.assertEqual([path for path, _ in messages], [str(owner)])
            stamped = ["time workers: " + self.scan(3600)[0] + " (start)"]
            messages = inbox.native_messages(
                args, stamped, False, inbox.action_patterns()
            )
            self.assertEqual([path for path, _ in messages], [str(args.binding)])


class CollectionTests(IsolatedTestCase):
    def test_workspace_failures_keep_waiting_but_successful_untagging_clears(self):
        test = WaitingTests()
        test.setUp()
        test.start()

        def command(*args):
            if args[-2:] == ("agent", "list"):
                return json.dumps({"result": {"agents": [test.row]}})
            raise subprocess.CalledProcessError(1, args)

        with (
            patch.dict(os.environ, HERDR_ENV="1"),
            patch.object(w, "command", side_effect=command),
        ):
            raw = w.collect("workers", test.state)
        self.assertIsNone(raw["workspaces"])
        after, events = w.reduce_workers(test.state, raw)
        self.assertEqual(after["waiting"], test.state["waiting"])
        self.assertFalse(any("cleared" in event for event in events))

    def test_snapshot_keeps_all_stages_and_policy_keys_session_scoped(self):
        test = WaitingTests()
        test.setUp()
        test.start()
        test.scan(1800)
        test.scan(3600)
        lines = inbox.snapshot_lines("workers", test.state)
        self.assertEqual(len(lines), 3)
        keys = [alert_policy.worker_facts(line)[0]["key"] for line in lines]
        self.assertEqual(
            set(keys),
            {
                "workers:waiting:s1:start",
                "workers:waiting:s1:REPEAT",
                "workers:waiting:s1:ESCALATE",
            },
        )
