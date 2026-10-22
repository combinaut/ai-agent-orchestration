"""Session-scoped waiting lifecycle, including unreadable ownership and native routing."""

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import alert_policy
import watchers as w

spec = importlib.util.spec_from_file_location(
    "waiting_inbox", w.ROOT / "notify-inbox.py"
)
inbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inbox)


def workspace_snapshot(owner):
    return {
        "result": {
            "workspaces": [dict(workspace_id="w1", tokens={"owner": owner})]
            if owner
            else []
        }
    }


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
            workspaces=workspace_snapshot("architect" if owners else None),
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

    def test_waiting_protocol_keeps_the_uncertainty_caveat(self):
        line = self.start()
        self.assertTrue(line.endswith("(may be waiting on its own job)"))
        self.row["name"] = "long-worker-name-" * 20
        self.scan(2401)
        line = self.scan(4201)[0]
        self.assertLessEqual(len(line), alert_policy.LINE_LIMIT)
        self.assertTrue(line.endswith("(may be waiting on its own job)"))
        fact = alert_policy.worker_facts(line)[0]
        self.assertTrue(fact["key"].endswith(":ESCALATE"))
        self.assertTrue(fact["text"].endswith("(may be waiting on its own job)"))

    def test_long_ids_survive_policy_and_route_escalation_to_coordinator(self):
        self.row.update(
            pane_id="p" * 36, name="worker-name" * 30, agent_session={"value": "s" * 36}
        )
        state, policy = None, {}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            owner = base / ("architect-" + "a" * 36 + ".json")
            owner.touch()
            args = argparse.Namespace(
                bindings_dir=base, binding=base / "coordinator.json"
            )
            for now in (0, 601, 2401, 4201):
                raw = dict(
                    result={"agents": [self.row]},
                    observed_at=now,
                    workspaces=workspace_snapshot("a" * 36),
                )
                state, events = w.reduce_workers(state, raw)
                lines = alert_policy.apply(
                    policy, [dict(mode="workers", at=now, events=events)], now
                )
                if now == 0:
                    continue
                self.assertEqual(len(lines), 1)
                self.assertIn("(may be waiting on its own job)", lines[0])
                messages = inbox.native_messages(
                    args, ["time " + lines[0]], False, inbox.action_patterns()
                )
                self.assertEqual(
                    [path for path, _ in messages],
                    [str(args.binding if now == 4201 else owner)],
                )

    def test_known_working_status_resets_during_ownership_outage(self):
        self.start()
        self.row["agent_status"] = "working"
        self.assertIn("cleared", self.scan(700, owners=None)[0])
        self.assertEqual(self.state["waiting"], {})
        self.row["agent_status"] = "done"
        self.assertEqual(self.scan(800), [])
        self.assertEqual(self.scan(1400), [])
        self.assertIn("session=s1", self.scan(1401)[0])

    def test_empty_session_id_uses_pane_and_preserves_later_metadata_gaps(self):
        self.row["agent_session"] = {"value": ""}
        self.scan(0)
        self.assertIn("session=p1", self.scan(601)[0])
        self.assertEqual(set(self.state["waiting"]), {"p1"})
        self.state = None
        self.row["agent_session"] = {"value": "s1"}
        self.start()
        saved = self.state["waiting"].copy()
        self.row["agent_session"] = {"value": ""}
        self.assertEqual(self.scan(700), [])
        self.assertEqual(self.state["waiting"], saved)
        self.assertEqual(set(self.state["waiting"]), {"s1"})

    def test_worker_name_cannot_override_generated_owner_or_stage(self):
        for name in ("x pane=p owner=other", "x ESCALATE (start)"):
            with self.subTest(name=name):
                self.state = None
                self.row["name"] = name
                self.scan(0)
                events = self.scan(601)
                lines = alert_policy.apply(
                    {}, [dict(mode="workers", at=601, events=events)], 601
                )
                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    owner = base / "architect-architect.json"
                    owner.touch()
                    (base / "architect-other.json").touch()
                    args = argparse.Namespace(
                        bindings_dir=base, binding=base / "coordinator.json"
                    )
                    messages = inbox.native_messages(
                        args, ["time " + lines[0]], False, inbox.action_patterns()
                    )
                    self.assertEqual([path for path, _ in messages], [str(owner)])

    def test_readable_owner_change_clears_during_status_or_session_gap(self):
        for gap in ("status", "session"):
            with self.subTest(gap=gap):
                self.state = None
                self.row.update(agent_status="done", agent_session={"value": "s1"})
                self.start()
                self.row.update(
                    agent_status="unknown" if gap == "status" else "done",
                    agent_session=None if gap == "session" else {"value": "s1"},
                )
                raw = dict(
                    result={"agents": [self.row]},
                    observed_at=700,
                    workspaces=workspace_snapshot("new-owner"),
                )
                self.state, events = w.reduce_workers(self.state, raw)
                self.assertEqual(len([e for e in events if "cleared" in e]), 1)
                self.assertEqual(self.state["waiting"], {})
                self.row.update(agent_status="done", agent_session={"value": "s1"})
                self.state, events = w.reduce_workers(
                    self.state, dict(raw, observed_at=800)
                )
                self.assertEqual(self.state["waiting"]["s1"]["since"], 800)
                self.assertEqual(self.state["waiting"]["s1"]["stage"], 0)

    def test_move_during_unreadable_snapshot_then_session_gap_keeps_timer(self):
        for gap in ("owner", "status"):
            with self.subTest(gap=gap):
                self.state = None
                self.row.update(
                    pane_id="p1", agent_status="done", agent_session={"value": "s1"}
                )
                self.start()
                self.row.update(
                    pane_id="p2", agent_status="unknown" if gap == "status" else "done"
                )
                self.assertEqual(
                    self.scan(700, owners=None if gap == "owner" else True), []
                )
                self.assertEqual(self.state["waiting"]["s1"]["row"]["pane_id"], "p2")
                self.row.update(agent_status="done", agent_session=None)
                self.assertEqual(self.scan(800), [])
                self.assertEqual(self.state["waiting"]["s1"]["since"], 0)
                self.assertEqual(self.state["waiting"]["s1"]["stage"], 1)
                self.row["agent_session"] = {"value": "s1"}
                self.assertIn("REPEAT", self.scan(2401)[0])

    def test_only_done_or_idle_workers_start_waiting_timers(self):
        for status in ("working", "waiting", "blocked", "error"):
            with self.subTest(status=status):
                self.state = None
                self.row["agent_status"] = status
                self.assertEqual(self.scan(0), [])
                self.assertEqual(self.scan(99999), [])
                self.assertEqual(self.state["waiting"], {})

    def test_threshold_and_no_extra_repeats(self):
        self.start()
        self.assertEqual(self.scan(2400), [])
        self.assertIn("REPEAT", self.scan(2401)[0])
        self.assertEqual(self.scan(4200), [])
        self.assertIn("ESCALATE", self.scan(4201)[0])
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

    def test_role_panes_never_alert(self):
        for role in ("architect", "coordinator"):
            self.state = None
            self.row["tokens"] = {"role": role}
            self.scan(0)
            self.assertEqual(self.scan(9999), [])

    def test_sessionless_codex_worker_alerts_repeats_escalates_and_resets(self):
        self.row.update(agent="codex", agent_session=None)
        self.assertEqual(self.scan(0), [])
        self.assertIn("session=p1", self.scan(601)[0])
        self.assertIn("REPEAT", self.scan(2401)[0])
        self.assertIn("ESCALATE", self.scan(4201)[0])
        self.row["agent_status"] = "working"
        self.assertIn("cleared", self.scan(4301, owners=None)[0])
        self.assertEqual(self.state["waiting"], {})

    def test_session_appearing_after_pane_timer_starts_fresh(self):
        self.row["agent_session"] = None
        self.scan(0)
        self.scan(601)
        self.row["agent_session"] = {"value": "s1"}
        self.assertIn("cleared", self.scan(700)[0])
        self.assertEqual(set(self.state["waiting"]), {"s1"})
        self.assertEqual(self.scan(1300), [])
        self.assertIn("session=s1", self.scan(1301)[0])

    def test_architect_names_and_titles_never_alert(self):
        for fields in (
            dict(name="Architect::Tooling"),
            dict(name="worker", terminal_title_stripped="busy Architect::Tooling"),
            dict(name=None, terminal_title_stripped="Architect::Tooling"),
        ):
            with self.subTest(fields=fields):
                self.state = None
                self.row.update(fields)
                self.scan(0)
                self.assertEqual(self.scan(9999), [])
                self.assertEqual(self.state["waiting"], {})

    def test_whitespace_label_falls_back_and_routes_with_own_policy_key(self):
        for fields in (
            dict(name="   "),
            dict(name=None, terminal_title_stripped=" \t"),
        ):
            with self.subTest(fields=fields):
                self.state = None
                self.row.update(fields)
                self.scan(0)
                line = self.scan(601)[0]
                self.assertTrue(line.startswith("WORKER WAITING worker pane="))
                fact = alert_policy.worker_facts(line)[0]
                self.assertEqual(fact["key"], "workers:waiting:s1:start")
                self.assertIsNotNone(inbox.WAIT_OWNER.search("workers: " + line))

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
            workspaces=workspace_snapshot("new-owner"),
        )
        self.state, events = w.reduce_workers(self.state, raw)
        self.assertTrue(events[0].endswith("cleared" + alert_policy.WORKER_WAIT_NOTE))
        self.assertEqual(self.state["waiting"]["s1"]["since"], 700)
        self.assertEqual(self.state["waiting"]["s1"]["owner"], "new-owner")

    def test_state_change_between_polls_clears_and_restarts_timer(self):
        self.row["state_change_seq"] = 10
        self.start()
        self.row["state_change_seq"] = 12
        self.assertIn("cleared", self.scan(700)[0])
        self.assertEqual(self.state["waiting"]["s1"]["since"], 700)
        self.assertEqual(self.scan(1300), [])
        self.assertIn("session=s1", self.scan(1301)[0])

    def test_state_change_resets_during_ownership_outage(self):
        self.row["state_change_seq"] = 10
        self.start()
        self.row["state_change_seq"] = 12
        events = self.scan(700, owners=None)
        self.assertEqual(len(events), 1)
        self.assertIn("cleared", events[0])
        self.assertEqual(self.state["waiting"], {})
        self.assertEqual(self.scan(800), [])
        self.assertEqual(self.state["waiting"]["s1"]["since"], 800)

    def test_first_sequence_during_outage_becomes_reset_baseline(self):
        for outage in ("ownership", "status"):
            with self.subTest(outage=outage):
                self.state = None
                self.row.pop("state_change_seq", None)
                self.row["agent_status"] = "done"
                self.start()
                self.row.update(
                    state_change_seq=10,
                    agent_status="unknown" if outage == "status" else "done",
                )
                self.assertEqual(
                    self.scan(700, owners=None if outage == "ownership" else True), []
                )
                self.assertEqual(self.state["waiting"]["s1"]["since"], 0)
                self.assertEqual(self.state["waiting"]["s1"]["stage"], 1)
                self.row.pop("state_change_seq")
                self.assertEqual(
                    self.scan(750, owners=None if outage == "ownership" else True), []
                )
                self.row.update(state_change_seq=12, agent_status="done")
                events = self.scan(800)
                self.assertEqual(len(events), 1)
                self.assertIn("cleared", events[0])
                self.assertEqual(self.state["waiting"]["s1"]["since"], 800)
                self.assertEqual(self.scan(1400), [])
                self.assertIn("session=s1", self.scan(1401)[0])

    def test_missing_sequence_preserves_baseline_for_later_transition(self):
        for missing in ("absent", "null"):
            with self.subTest(missing=missing):
                self.state = None
                self.row["state_change_seq"] = 10
                self.start()
                if missing == "absent":
                    self.row.pop("state_change_seq")
                else:
                    self.row["state_change_seq"] = None
                self.assertEqual(self.scan(700), [])
                self.assertEqual(self.state["waiting"]["s1"]["since"], 0)
                self.row["state_change_seq"] = 12
                events = self.scan(800)
                self.assertEqual(len(events), 1)
                self.assertIn("cleared", events[0])
                self.assertEqual(self.state["waiting"]["s1"]["since"], 800)
                self.assertEqual(self.scan(1400), [])
                self.assertIn("session=s1", self.scan(1401)[0])

    def test_unchanged_sequence_or_missing_sequence_does_not_reset(self):
        for seq in (10, None):
            with self.subTest(seq=seq):
                self.state = None
                self.row["state_change_seq"] = seq
                self.start()
                self.row.update(agent_status="idle", revision=99)
                self.assertEqual(self.scan(700), [])
                self.assertEqual(self.state["waiting"]["s1"]["since"], 0)
                self.assertIn("REPEAT", self.scan(2401)[0])

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
            (2401, self.scan(2401)[0]),
            (4201, self.scan(4201)[0]),
        ):
            lines = alert_policy.apply(
                policy, [dict(mode="workers", at=now, events=[text])], now
            )
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].endswith("(start)"))
        self.row["agent_status"] = "working"
        clear = self.scan(4301)[0]
        lines = alert_policy.apply(
            policy, [dict(mode="workers", at=4301, events=[clear])], 4301
        )
        self.assertTrue(any(line.endswith("(clear)") for line in lines))

    def test_monitor_cannot_swallow_native_coordinator_escalation(self):
        self.start()
        self.scan(2401)
        line = "time workers: " + self.scan(4201)[0] + " (start)"
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
                inbox.finish(args, cursor, 4201, inbox.action_patterns())
            self.assertEqual(len(cursor[inbox.OUTBOX_KEY]), 1)
            self.assertNotIn("watch", cursor[inbox.OUTBOX_KEY][0])
            with patch.object(inbox, "send_line", return_value=False) as send:
                inbox.flush_outbox(args, cursor, 4201)
                self.assertEqual(send.call_count, 1)
                inbox.flush_outbox(args, cursor, 4221)
                self.assertEqual(send.call_count, 1)
            with patch.object(inbox, "send_line", return_value=True):
                inbox.flush_outbox(args, cursor, 4261)
            self.assertNotIn(inbox.OUTBOX_KEY, cursor)

    def test_threshold_settings_are_used(self):
        with patch.dict(os.environ, ORCH_WAIT_S="2", ORCH_WAIT_REPEAT_S="95"):
            self.scan(0)
            self.assertEqual(self.scan(2), [])
            self.assertEqual(len(self.scan(3)), 1)
            self.assertIn("REPEAT", self.scan(98)[0])
            self.assertIn("ESCALATE", self.scan(193)[0])
            self.assertEqual(self.scan(99999), [])

    def test_missing_owner_binding_still_queues_native_waiting_message(self):
        line = self.start()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = argparse.Namespace(
                bindings_dir=base, binding=base / "coordinator.json"
            )
            messages = inbox.native_messages(
                args,
                ["time workers: " + line + " (start)"],
                False,
                inbox.action_patterns(),
            )
            self.assertEqual(
                [path for path, _ in messages], [str(base / "architect-architect.json")]
            )
            self.assertIn(alert_policy.WORKER_WAIT_NOTE, messages[0][1])

    def test_native_owner_route_and_coordinator_only_for_escalation(self):
        line = self.start()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            owner = base / "architect-architect.json"
            owner.touch()
            args = argparse.Namespace(
                bindings_dir=base, binding=base / "coordinator.json"
            )
            for text in (line, self.scan(2401)[0]):
                stamped = ["time workers: " + text + " (start)"]
                messages = inbox.native_messages(
                    args, stamped, True, inbox.action_patterns()
                )
                self.assertEqual([path for path, _ in messages], [str(owner)])
            stamped = ["time workers: " + self.scan(4201)[0] + " (start)"]
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

    def test_empty_workspace_output_still_resets_working_and_reports_blockers(self):
        test = WaitingTests()
        test.setUp()
        test.start()
        test.row["agent_status"] = "working"
        blocked = dict(
            pane_id="p2",
            workspace_id="w1",
            agent_status="blocked",
            agent_session={"value": "s2"},
            tokens={},
        )

        def command(*args):
            if args[-2:] == ("agent", "list"):
                return json.dumps({"result": {"agents": [test.row, blocked]}})
            raise w.MissingMetric("herdr empty response")

        with (
            patch.dict(os.environ, HERDR_ENV="1"),
            patch.object(w, "command", side_effect=command),
        ):
            raw = w.collect("workers", test.state)
        self.assertIsNone(raw["workspaces"])
        after, events = w.reduce_workers(test.state, raw)
        self.assertEqual(after["waiting"], {})
        self.assertIn("WORKER p2 blocked", events)
        self.assertEqual(len([line for line in events if "cleared" in line]), 1)

    def test_snapshot_keeps_all_stages_and_policy_keys_session_scoped(self):
        test = WaitingTests()
        test.setUp()
        test.start()
        test.scan(2401)
        test.scan(4201)
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


class WaitingOutboxTests(IsolatedTestCase):
    def line(self, sid="s1", stage="", clear=False):
        suffix = " cleared" if clear else (" " + stage if stage else "")
        return f"time workers: WORKER WAITING worker pane=p1 owner=architect 11m session={sid}{suffix}{alert_policy.WORKER_WAIT_NOTE} ({'clear' if clear else 'start'})"

    def finish(self, args, cursor, lines, now):
        cursor[inbox.PENDING_KEY] = dict(
            lines=lines, fed=True, coordinator="native", inode=1, offset=0
        )
        inbox.finish(args, cursor, now, inbox.action_patterns())

    def test_clear_cancels_failed_waiting_retries_but_preserves_other_messages(self):
        for stage in ("", "REPEAT", "ESCALATE"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                args = argparse.Namespace(
                    bindings_dir=base,
                    binding=base / "coordinator.json",
                    cursor=base / "cursor",
                    feed=base / "feed",
                )
                cursor = {}
                with patch.object(inbox, "send_line", return_value=False):
                    self.finish(
                        args,
                        cursor,
                        [self.line(stage=stage), self.line(sid="s2")],
                        1000,
                    )
                self.assertIn(inbox.OUTBOX_KEY, cursor)
                # Outbox compaction can merge unrelated messages into the same entry.
                next(
                    entry
                    for entry in cursor[inbox.OUTBOX_KEY]
                    if "session=s1" in entry["text"]
                )["text"] += "\nunrelated message"
                with patch.object(inbox, "send_line", return_value=True) as send:
                    self.finish(args, cursor, [self.line(clear=True)], 1060)
                delivered = "\n".join(call.args[1] for call in send.call_args_list)
                self.assertNotIn("session=s1", delivered)
                self.assertIn("session=s2", delivered)
                self.assertIn("unrelated message", delivered)
                self.assertNotIn(inbox.OUTBOX_KEY, cursor)

    def test_clear_filters_earlier_same_batch_starts_but_keeps_later_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = argparse.Namespace(
                bindings_dir=base,
                binding=base / "coordinator.json",
                cursor=base / "cursor",
                feed=base / "feed",
            )
            cursor = {}
            with patch.object(inbox, "send_line", return_value=True) as send:
                self.finish(args, cursor, [self.line(), self.line(clear=True)], 1000)
                send.assert_not_called()
                self.finish(args, cursor, [self.line(clear=True), self.line()], 1060)
            self.assertEqual(send.call_count, 1)
            self.assertIn("session=s1", send.call_args.args[1])
