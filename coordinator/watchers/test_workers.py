import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase
import watchers as w


def agent(status="working", sid="one", role=None):
    return dict(pane_id="pane-" + sid, agent_session={"value": sid},
                agent_status=status, tokens={"role": role} if role else {})


class WorkersTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        young = patch.object(w, "YOUNG_PANE_SECONDS", 0)
        young.start()
        self.addCleanup(young.stop)

    def scan(self, *rows):
        # Fake Herdr at the command boundary exercises collection and persisted dedupe.
        with patch.dict(os.environ, {"HERDR_ENV": "1"}), \
             patch.object(w, "command", return_value=json.dumps({"result": {"agents": rows}})):
            line = w.cycle(self.base, "workers", lambda before: w.collect("workers", before))
        return json.loads(line)["events"] if line else []

    def test_role_tagged_blocked_and_error(self):
        self.assertEqual(self.scan(agent("blocked", role="architect"), agent("error", "two", "coordinator")),
                         ["WORKER pane-one blocked", "WORKER pane-two error"])
        self.assertEqual(self.scan(agent("blocked", role="architect"), agent("error", "two", "coordinator")), [])

    def test_unknown_is_isolated_and_set_deduped(self):
        bad = agent("unknown")
        self.assertEqual(self.scan(bad, agent("blocked", "two")),
                         ["WORKER READFAIL pane-one(unknown)", "WORKER pane-two blocked"])
        self.assertEqual(self.scan(agent("blocked", "two"), bad), [])
        self.assertEqual(self.scan(bad, agent(None, "three"), agent("blocked", "two")),
                         ["WORKER READFAIL pane-one(unknown) pane-three(None)"])
        self.assertEqual(self.scan(agent("idle"), agent("blocked", "two")), ["WORKER READFAIL cleared"])
        self.assertEqual(json.loads((self.base / "workers.json").read_text())["fails"], 0)

    def test_state_transition_and_recovery(self):
        self.assertEqual(self.scan(agent("blocked")), ["WORKER pane-one blocked"])
        self.assertEqual(self.scan(agent("error")), ["WORKER pane-one RECOVERED blocked", "WORKER pane-one error"])
        self.assertEqual(self.scan(agent("error")), [])
        self.assertEqual(self.scan(agent("idle")), ["WORKER pane-one RECOVERED error"])
        self.assertEqual(self.scan(agent()), [])
        self.assertEqual(self.scan(agent("idle")), [])
        self.assertEqual(self.scan(agent("blocked")), ["WORKER pane-one blocked"])

    def test_disappearance_alerts_once_without_recovery_on_reappearance(self):
        self.scan(agent(role="architect"))
        self.assertEqual(self.scan(), ["WORKER pane-one dead/disappeared"])
        self.assertEqual(self.scan(), [])
        self.assertEqual(self.scan(agent("idle")), [])
        self.assertEqual(self.scan(), [])

    def test_only_active_disappearances_alert(self):
        for status in ("working", "waiting", "blocked", "error", "idle", "done"):
            with self.subTest(status=status):
                self.scan(agent(status))
                events = self.scan()
                self.assertEqual(any(line.endswith("dead/disappeared") and "RECOVERED" not in line for line in events),
                                 status in ("working", "waiting", "blocked", "error"))
                self.scan(agent("idle"))

    def test_unknown_preserves_alert_until_readable(self):
        self.scan(agent("blocked"))
        self.assertEqual(self.scan(agent("unknown")), ["WORKER READFAIL pane-one(unknown)"])
        self.assertEqual(self.scan(agent("unknown")), [])
        self.assertEqual(self.scan(agent("blocked")), ["WORKER READFAIL cleared"])
        self.assertEqual(self.scan(agent("idle")), ["WORKER pane-one RECOVERED blocked"])

    def test_session_identity_and_restart(self):
        self.scan(agent("blocked"))
        self.assertEqual(self.scan(agent("blocked")), [])
        self.assertIn("WORKER pane-two blocked", self.scan(agent("blocked", "two")))

    def test_missing_session_uses_pane(self):
        row = agent("blocked")
        row["agent_session"] = None
        self.assertEqual(self.scan(row), ["WORKER pane-one blocked"])
        self.assertEqual(self.scan(row), [])

    def test_unknown_then_disappearance_uses_last_known_status(self):
        self.scan(agent("blocked"))
        self.scan(agent("unknown"))
        self.assertEqual(self.scan(), ["WORKER READFAIL cleared", "WORKER pane-one dead/disappeared"])

    def test_healthy_jitter_and_initial_unknown_disappearance_are_silent(self):
        for status in ("working", "idle", "done", "waiting", "working"):
            self.assertEqual(self.scan(agent(status)), [])
        self.scan(agent("unknown", "two"))
        self.assertEqual(self.scan(), ["WORKER READFAIL cleared"])

    def test_pane_move_keeps_session_state_deduped(self):
        row = agent("blocked")
        self.scan(row)
        row["pane_id"] = "moved-pane"
        self.assertEqual(self.scan(row), ["WORKER pane-one MOVED moved-pane"])
        self.assertEqual(self.scan(row), [])
        row["agent_status"] = "idle"
        self.assertEqual(self.scan(row), ["WORKER moved-pane RECOVERED blocked"])

    def test_direct_pane_move_and_recovery_use_current_pane(self):
        for next_status in ("idle", "error"):
            with self.subTest(next_status=next_status):
                row = agent("blocked")
                self.scan(row)
                row.update(pane_id="current-pane", agent_status=next_status)
                events = ["WORKER pane-one MOVED current-pane", "WORKER current-pane RECOVERED blocked"]
                if next_status == "error":
                    events.append("WORKER current-pane error")
                self.assertEqual(self.scan(row), events)
                self.scan(agent("idle"))

    def test_unknown_move_then_disappearance_uses_latest_pane(self):
        row = agent("blocked")
        self.scan(row)
        row.update(pane_id="latest-pane", agent_status="unknown")
        self.assertEqual(self.scan(row), ["WORKER READFAIL latest-pane(unknown)", "WORKER pane-one MOVED latest-pane"])
        self.assertEqual(self.scan(), ["WORKER READFAIL cleared", "WORKER latest-pane dead/disappeared"])

    def test_session_id_availability_changes_are_silent(self):
        for status in ("working", "blocked"):
            with self.subTest(status=status):
                self.base = Path(self.tmp.name) / status
                row = agent(status, "identity-" + status)
                session = row.pop("agent_session")
                first = self.scan(row)
                self.assertEqual(first, [f"WORKER {row['pane_id']} blocked"] if status == "blocked" else [])
                row["agent_session"] = session
                self.assertEqual(self.scan(row), [])
                row["agent_session"] = None
                self.assertEqual(self.scan(row), [])
                row["agent_session"] = session
                self.assertEqual(self.scan(row), [])
                self.scan(agent("idle", "identity-" + status))

    def test_real_session_replacement_in_same_pane_silently_replaces_old(self):
        for metadata_gap in (False, True):
            with self.subTest(metadata_gap=metadata_gap):
                self.base = Path(self.tmp.name) / str(metadata_gap)
                old = agent("blocked")
                self.scan(old)
                if metadata_gap:
                    missing = dict(old, agent_session=None)
                    self.assertEqual(self.scan(missing), [])
                new = agent("blocked", "two")
                new["pane_id"] = old["pane_id"]
                self.assertEqual(self.scan(new), ["WORKER pane-one blocked"])
                saved = json.loads((self.base / "workers.json").read_text())["before"]
                self.assertNotIn("one", saved["bad"])
                self.assertNotIn("one", saved["present"])
                new["agent_status"] = "idle"
                self.scan(new)
                replacement = agent("idle", "three")
                replacement["pane_id"] = new["pane_id"]
                self.assertEqual(self.scan(replacement), [])

    def saved(self):
        return json.loads((self.base / "workers.json").read_text())["before"]

    def test_closed_workers_are_pruned_after_one_alert(self):
        self.scan(*(agent(sid=str(n)) for n in range(100)))
        events = self.scan()
        self.assertEqual(len(events), 100)
        self.assertTrue(all(line.endswith("dead/disappeared") for line in events))
        self.assertEqual(self.saved()["bad"], {})
        self.assertEqual(self.scan(), [])
        self.assertEqual(self.saved()["bad"], {})
        self.assertEqual(self.saved()["present"], {})

    def test_disappearance_does_not_emit_recovered(self):
        for status in ("blocked", "error"):
            with self.subTest(status=status):
                self.base = Path(self.tmp.name) / status
                self.scan(agent(status))
                self.assertEqual(self.scan(), ["WORKER pane-one dead/disappeared"])

    def test_existing_session_moving_to_fallback_pane_keeps_its_alert(self):
        known = agent("blocked", "X")
        known["pane_id"] = "p1"
        fallback = dict(agent("working"), pane_id="p2", agent_session=None)
        self.scan(known, fallback)
        known.update(pane_id="p2", agent_status="working")
        self.assertEqual(self.scan(known), ["WORKER p1 MOVED p2", "WORKER p2 RECOVERED blocked"])
        self.assertEqual(set(self.saved()["present"]), {"X"})

    def test_departed_fallback_does_not_transfer_alert_to_new_agent(self):
        row = dict(agent("blocked"), agent_session=None)
        self.scan(row)
        self.scan()
        row["agent_status"] = "idle"
        self.assertEqual(self.scan(row), [])
        self.assertEqual(self.saved()["bad"], {})

    def test_nonpane_key_is_not_promoted_without_session_metadata(self):
        row = dict(agent("blocked"), agent_session=None)
        before = dict(present={"old-session": row}, bad={"old-session": {"pane": row["pane_id"], "state": "blocked"}})
        replacement = dict(row, agent_session={"value": "new-session"})
        after, events = w.reduce("workers", before, {"result": {"agents": [replacement]}})
        self.assertEqual(events, ["WORKER pane-one blocked"])
        self.assertEqual(set(after["bad"]), {"new-session"})

    def test_alert_state_is_structured_and_tracks_current_pane(self):
        row = agent("blocked")
        self.scan(row)
        self.assertEqual(self.saved()["bad"], {"one": {"pane": "pane-one", "state": "blocked"}})
        row.update(pane_id="moved-pane", agent_status="unknown")
        self.scan(row)
        self.assertEqual(self.saved()["bad"], {"one": {"pane": "moved-pane", "state": "blocked"}})

    def test_readfail_set_change_line_for_each_prefix(self):
        for prefix in ("WORKER", "HOST", "BALANCE"):
            with self.subTest(prefix=prefix):
                self.assertEqual(w.readfail_changes(prefix, [], ["missing"]), [prefix + " READFAIL missing"])
                self.assertEqual(w.readfail_changes(prefix, ["missing"], ["missing"]), [])
                self.assertEqual(w.readfail_changes(prefix, ["missing"], []), [prefix + " READFAIL cleared"])

    def test_existing_unreadable_session_is_not_overwritten_by_fallback(self):
        known = dict(agent("blocked", "X"), pane_id="p1")
        fallback = dict(agent("working"), pane_id="p2", agent_session=None)
        self.scan(known, fallback)
        known.update(pane_id="p2", agent_status="unknown")
        self.assertEqual(self.scan(known), ["WORKER READFAIL p2(unknown)", "WORKER p1 MOVED p2"])
        self.assertEqual(self.saved()["present"]["X"]["agent_status"], "blocked")

    def test_live_sessions_swapping_panes_are_not_replacements(self):
        first, second = agent("blocked", "first"), agent("error", "second")
        self.scan(first, second)
        first["pane_id"], second["pane_id"] = second["pane_id"], first["pane_id"]
        self.assertEqual(self.scan(first, second), [f"WORKER {second['pane_id']} MOVED {first['pane_id']}",
                                                    f"WORKER {first['pane_id']} MOVED {second['pane_id']}"])
        self.assertEqual(set(self.saved()["bad"]), {"first", "second"})

    def seed(self, before):
        w.atomic(self.base / "workers.json", json.dumps(dict(before=before, fails=0, events=[])))

    def test_existing_string_alerts_upgrade_without_replay_or_read_failure(self):
        blocked, error = agent("blocked"), agent("error", "two")
        self.seed(dict(present={"one": blocked, "two": error},
                       bad={"one": "WORKER pane-one blocked", "two": "WORKER pane-two error"}))
        self.assertEqual(self.scan(blocked, error), [])
        self.assertEqual(self.saved()["bad"], {"one": {"pane": "pane-one", "state": "blocked"},
                                              "two": {"pane": "pane-two", "state": "error"}})
        self.assertEqual(json.loads((self.base / "workers.json").read_text())["fails"], 0)
        blocked["agent_status"] = "unknown"
        error["agent_status"] = "idle"
        self.assertEqual(self.scan(blocked, error), ["WORKER READFAIL pane-one(unknown)", "WORKER pane-two RECOVERED error"])

    def test_existing_departed_string_alerts_are_pruned(self):
        self.seed(dict(present={}, bad={str(n): f"WORKER pane-{n} dead/disappeared" for n in range(100)}))
        self.assertEqual(self.scan(), [])
        self.assertEqual(self.saved()["bad"], {})
        self.assertEqual(json.loads((self.base / "workers.json").read_text())["fails"], 0)

    def test_moved_live_session_and_metadata_less_replacement_keep_both_rows(self):
        for replacement_first in (False, True):
            with self.subTest(replacement_first=replacement_first):
                self.base = Path(self.tmp.name) / str(replacement_first)
                old = agent("blocked")
                self.scan(old)
                replacement = dict(old, agent_session=None)
                moved = dict(old, pane_id="moved-pane", agent_status="idle")
                rows = (replacement, moved) if replacement_first else (moved, replacement)
                self.assertEqual(self.scan(*rows), ["WORKER pane-one MOVED moved-pane", "WORKER moved-pane RECOVERED blocked",
                                            "WORKER pane-one blocked"])
                self.assertEqual(set(self.saved()["present"]), {"one", "pane-one"})

    def test_young_pane_readfail_is_marked_until_two_minutes(self):
        with patch.object(w, "YOUNG_PANE_SECONDS", 120):
            raw = {"result": {"agents": [agent("unknown")]}, "observed_at": 1000}
            state, events = w.reduce("workers", None, raw)
            self.assertEqual(events, ["WORKER READFAIL pane-one(unknown,new)"])
            state, events = w.reduce("workers", state, dict(raw, observed_at=1119))
            self.assertEqual(events, [])
            state, events = w.reduce("workers", state, dict(raw, observed_at=1120))
            self.assertEqual(events, ["WORKER READFAIL pane-one(unknown)"])

    def test_pane_closed_on_purpose_is_not_a_death(self):
        state, _ = w.reduce("workers", None, {"result": {"agents": [agent("working"), agent("working", "two")]}})
        _, events = w.reduce("workers", state, {"result": {"agents": []}, "closed": ["pane-one"]})
        self.assertEqual(events, ["WORKER pane-one closed", "WORKER pane-two dead/disappeared"])

    def scan_panes(self, rows, panes):
        def herdr(*argv):
            if argv[1:3] == ("pane", "list"):
                return json.dumps({"result": {"panes": panes}})
            return json.dumps({"result": {"agents": rows}})
        with patch.dict(os.environ, {"HERDR_ENV": "1"}), patch.object(w, "command", side_effect=herdr):
            line = w.cycle(self.base, "workers", lambda before: w.collect("workers", before, base=self.base))
        return json.loads(line)["events"] if line else []

    def test_finished_then_closed_is_silent(self):
        row = dict(agent("working"), tab_id="tab-a")
        self.assertEqual(self.scan_panes([row], []), [])
        self.assertEqual(self.scan_panes([dict(row, agent_status="done")], []), [])
        self.assertEqual(self.scan_panes([], [dict(pane_id="lead", tab_id="tab-a")]), [])

    def test_working_pane_closed_while_its_tab_lives_is_closed(self):
        row = dict(agent("working"), tab_id="tab-a")
        self.assertEqual(self.scan_panes([row], []), [])
        self.assertEqual(self.scan_panes([], [dict(pane_id="lead", tab_id="tab-a")]), ["WORKER pane-one closed"])

    def test_working_agent_gone_from_a_live_pane_is_dead(self):
        row = dict(agent("working"), tab_id="tab-a")
        self.assertEqual(self.scan_panes([row], []), [])
        self.assertEqual(self.scan_panes([], [dict(pane_id="pane-one", tab_id="tab-a", agent_status="unknown")]),
                         ["WORKER pane-one dead/disappeared"])

    def test_working_pane_gone_with_its_tab_is_dead(self):
        row = dict(agent("working"), tab_id="tab-a")
        self.assertEqual(self.scan_panes([row], []), [])
        self.assertEqual(self.scan_panes([], [dict(pane_id="lead", tab_id="tab-b")]), ["WORKER pane-one dead/disappeared"])

    def test_blocked_pane_closed_while_its_tab_lives_is_dead(self):
        row = dict(agent("blocked"), tab_id="tab-a")
        self.assertEqual(self.scan_panes([row], []), ["WORKER pane-one blocked"])
        self.assertEqual(self.scan_panes([], [dict(pane_id="lead", tab_id="tab-a")]), ["WORKER pane-one dead/disappeared"])

    def test_unreadable_pane_list_keeps_a_disappearance_dead(self):
        row = dict(agent("working"), tab_id="tab-a")
        self.assertEqual(self.scan_panes([row], []), [])
        self.assertEqual(self.scan_panes([], None), ["WORKER pane-one dead/disappeared"])

    def test_malformed_pane_list_keeps_a_disappearance_dead(self):
        for panes in ([None], [dict(pane_id="lead")], [dict(pane_id=None, tab_id="tab-a")]):
            with self.subTest(panes=panes):
                self.scan_panes([dict(agent("working"), tab_id="tab-a")], [])
                self.assertEqual(self.scan_panes([], panes), ["WORKER pane-one dead/disappeared"])

    def test_missing_tab_id_keeps_a_disappearance_dead(self):
        self.assertEqual(self.scan_panes([agent("working")], []), [])
        self.assertEqual(self.scan_panes([], [dict(pane_id="lead", tab_id="tab-a")]), ["WORKER pane-one dead/disappeared"])

    def test_close_pane_mark_is_closed_without_a_pane_list(self):
        (self.base / "closed").mkdir()
        self.assertEqual(self.scan_panes([dict(agent("blocked"), tab_id="tab-a")], []), ["WORKER pane-one blocked"])
        (self.base / "closed" / "pane-one").touch()
        self.assertEqual(self.scan_panes([], None), ["WORKER pane-one closed"])

    def test_closed_markers_are_read_and_consumed_once_the_pane_is_gone(self):
        (self.base / "closed").mkdir()
        (self.base / "closed" / "pane-one").touch()
        self.assertEqual(self.scan(agent("working")), [])
        self.assertTrue((self.base / "closed" / "pane-one").exists())
        with patch.dict(os.environ, {"HERDR_ENV": "1"}), \
             patch.object(w, "command", return_value=json.dumps({"result": {"agents": []}})):
            line = w.cycle(self.base, "workers", lambda before: w.collect("workers", before, base=self.base))
        self.assertEqual(json.loads(line)["events"], ["WORKER pane-one closed"])
        self.assertTrue((self.base / "closed" / "pane-one").exists())
        with patch.dict(os.environ, {"HERDR_ENV": "1"}), \
             patch.object(w, "command", return_value=json.dumps({"result": {"agents": []}})):
            self.assertIsNone(w.cycle(self.base, "workers", lambda before: w.collect("workers", before, base=self.base)))
        self.assertFalse((self.base / "closed" / "pane-one").exists())

    def test_a_close_survives_a_failed_state_write(self):
        (self.base / "closed").mkdir()
        self.assertEqual(self.scan(agent("working")), [])
        (self.base / "closed" / "pane-one").touch()
        with patch.dict(os.environ, {"HERDR_ENV": "1"}), \
             patch.object(w, "command", return_value=json.dumps({"result": {"agents": []}})):
            with patch.object(w, "publish_events", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    w.cycle(self.base, "workers", lambda before: w.collect("workers", before, base=self.base))
            line = w.cycle(self.base, "workers", lambda before: w.collect("workers", before, base=self.base))
        self.assertEqual(json.loads(line)["events"], ["WORKER pane-one closed"])


    def close(self, herdr_exit):
        fake = self.base / "fakeherdr"
        fake.write_text(f"#!/bin/sh\nexit {herdr_exit}\n")
        fake.chmod(0o755)
        (self.base / "orchestration.toml").write_text(f'[herdr]\nbin = "{fake}"\n')
        env = dict(os.environ, ORCH_CONFIG=str(self.base / "orchestration.toml"), ORCH_CLOSED_DIR=str(self.base / "closed"))
        env.pop("HERDR_BIN_PATH", None)
        env.pop("ORCH_HERDR_BIN", None)
        return subprocess.run([str(w.ROOT / "close-pane.sh"), "wA:p9"], env=env, capture_output=True).returncode

    def test_close_pane_refuses_a_path_for_a_pane_id(self):
        fake = self.base / "fakeherdr"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)
        victim = self.base / "victim"
        victim.write_text("keep")
        (self.base / "closed").mkdir()
        (self.base / "none.toml").touch()
        env = dict(os.environ, HERDR_BIN_PATH=str(fake), ORCH_CLOSED_DIR=str(self.base / "closed"),
                   ORCH_CONFIG=str(self.base / "none.toml"))
        result = subprocess.run([str(w.ROOT / "close-pane.sh"), "../victim"], env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(victim.read_text(), "keep")

    def test_close_pane_marks_a_closed_pane_and_unmarks_a_failed_close(self):
        self.assertEqual(self.close(0), 0)
        self.assertTrue((self.base / "closed" / "wA:p9").exists())
        (self.base / "closed" / "wA:p9").unlink()
        self.assertEqual(self.close(1), 1)
        self.assertFalse((self.base / "closed" / "wA:p9").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
