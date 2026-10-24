#!/usr/bin/env python3
import copy
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import call, patch

from test_support import IsolatedTestCase

import watchers as w

spec = importlib.util.spec_from_file_location("reader", w.ROOT / "read-alerts.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def fixture(mode):
    return json.loads((w.ROOT / "fixtures" / (mode + ".json")).read_text())


def healthy(mode):
    raw = fixture(mode)
    if mode == "context":
        for value in raw["usage"].values():
            value.update(tokens=1000)
    elif mode == "workers":
        for agent in w.agents(raw):
            agent["agent_status"] = "working"
    elif mode == "hog":
        for row in raw:
            row.update(rss=1000, age=1)
    elif mode == "host":
        raw.update(free=50, swap=0, procs=10, files=10, load=1)
    elif mode == "balance":
        raw.update(free=50, slots=0, heavy=0, load5=1)
    elif mode == "pulls":
        for pr in raw["prs"].values():
            pr["lines"] = {}
    elif mode == "prs":
        for pr in raw:
            pr["reviewDecision"] = "APPROVED"
            for review in pr.get("latestReviews", {}).get("nodes", []):
                review["state"] = "APPROVED"
            pr["state"] = "OPEN"
            for t in pr["reviewThreads"]["nodes"]:
                t["isResolved"] = True
            pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["state"] = (
                "SUCCESS"
            )
    return raw


def worker_context(tokens, owner="wArch"):
    """The recorded context read with its worker session at `tokens` and the worker's workspace tagged `owner`
    (None: no tag)."""
    raw = fixture("context")
    raw["usage"]["session-worker-001"] = {"tokens": tokens, "window": 1000000}
    for row in raw["workspaces"]["result"]["workspaces"]:
        if row["workspace_id"] == "workspace-task":
            row["tokens"] = {"owner": owner} if owner else {}
    return raw


def worker_line(amount, owner="wArch", pane="workspace-task:p1"):
    return f"RECYCLE-WORKER-SOFT {pane} owner={owner} fixture-worker {amount}"


class WatcherTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=w.ROOT)
        self.base = Path(self.tmp.name)
        self.signal_directory = patch.object(
            w.worker_signals, "directory", return_value=self.base / "worker-signals"
        )
        self.signal_directory.start()
        self.addCleanup(self.signal_directory.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, mode, raw):
        return w.cycle(self.base, mode, lambda before: copy.deepcopy(raw))

    def one(self, mode, raw, word):
        line = self.cycle(mode, raw)
        self.assertIsNotNone(line)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn(word, line)
        self.assertIsNone(self.cycle(mode, raw))

    def test_recorded_replay_every_watcher(self):
        for mode in w.MODES:
            with self.subTest(mode=mode):
                raw = fixture(mode)
                self.cycle(mode, raw)
                self.assertIsNone(self.cycle(mode, raw))
                if mode == "prs":
                    raw.reverse()
                    for pr in raw:
                        pr["reviewThreads"]["nodes"].reverse()
                        pr["updatedAt"] = "2099-01-01T00:00:00Z"
                        pr["ack"] = True
                elif mode == "context":
                    raw["herdr"]["result"]["agents"].reverse()
                elif mode == "workers":
                    raw["result"]["agents"].reverse()
                elif mode == "hog":
                    raw.reverse()
                self.assertIsNone(self.cycle(mode, raw))

    def test_read_failure_and_recovery_every_watcher(self):
        for mode in w.MODES:
            with self.subTest(mode=mode):
                raw = healthy(mode)
                self.cycle(mode, raw)

                def failed(before):
                    raise RuntimeError("recorded failed read")

                self.assertIsNone(w.cycle(self.base, mode, failed))
                self.assertIsNone(w.cycle(self.base, mode, failed))
                line = w.cycle(self.base, mode, failed)
                self.assertIn("read failed 3 times", line)
                self.assertIsNone(w.cycle(self.base, mode, failed))
                self.one(mode, raw, "READ RECOVERED")

    def test_context_crossing_and_session_recycle(self):
        raw = healthy("context")
        self.cycle("context", raw)
        a = next(
            a
            for a in w.agents(raw["herdr"])
            if a.get("agent") == "claude"
            and (
                (a.get("tokens") or {}).get("role") in ("architect", "coordinator")
                or (a.get("name") or "").startswith("Architect::")
            )
        )
        sid = w.identity(a)
        raw["usage"][sid]["tokens"] = 300000
        self.one("context", raw, "RECYCLE")
        raw["usage"][sid]["tokens"] += 10000
        self.assertIsNone(self.cycle("context", raw))
        a["agent_session"]["value"] = "new-session"
        raw["usage"]["new-session"] = {"tokens": 300000}
        self.one("context", raw, "RECYCLE")

    def context_pair(self):
        raw = healthy("context")
        selected = [
            a
            for a in w.agents(raw["herdr"])
            if a.get("agent") == "claude"
            and (
                (a.get("tokens") or {}).get("role") in ("architect", "coordinator")
                or (a.get("name") or "").startswith("Architect::")
            )
        ][:2]
        selected = [selected[0], copy.deepcopy(selected[0])]
        selected[1].update(
            pane_id="second-pane", agent_session={"value": "second-session"}
        )
        raw["herdr"]["result"]["agents"] = selected
        raw["usage"][w.identity(selected[1])] = {"tokens": 300000}
        return raw, selected

    def test_context_session_moving_panes_clears_its_old_subject(self):
        agent = {
            "agent": "claude",
            "name": "Architect::x",
            "pane_id": "p1",
            "agent_session": {"value": "s1"},
        }
        raw = {
            "herdr": {"result": {"agents": [agent]}},
            "usage": {"s1": {"tokens": 310000}},
        }
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            before, _ = w.reduce("context", None, raw)
            agent["pane_id"] = "p2"
            _, events = w.reduce("context", before, raw)
        self.assertEqual(
            events, ["RECYCLE p1 Architect::x cleared", "RECYCLE p2 Architect::x 310k"]
        )

    def test_context_missing_sensor_moving_panes_clears_its_old_pane(self):
        agent = {
            "agent": "claude",
            "name": "Architect::x",
            "pane_id": "p1",
            "agent_session": {"value": "s1"},
        }
        raw = {"herdr": {"result": {"agents": [agent]}}, "usage": {}}
        before, _ = w.reduce("context", None, raw)
        agent["pane_id"] = "p2"
        _, events = w.reduce("context", before, raw)
        self.assertEqual(events, ["NOSENSOR CLEARED p1", "NOSENSOR p2 Architect::x"])

    def test_context_sensor_recovery_clears_nosensor(self):
        agent = {
            "agent": "claude",
            "name": "Architect::x",
            "pane_id": "p1",
            "agent_session": {"value": "s1"},
        }
        raw = {"herdr": {"result": {"agents": [agent]}}, "usage": {}}
        before, events = w.reduce("context", None, raw)
        self.assertEqual(events, ["NOSENSOR p1 Architect::x"])
        raw["usage"] = {"s1": {"tokens": 1000}}
        _, events = w.reduce("context", before, raw)
        self.assertEqual(events, ["NOSENSOR CLEARED p1"])

    def test_context_missing_sensor_isolated(self):
        raw, rows = self.context_pair()
        del raw["usage"][w.identity(rows[0])]
        line = self.cycle("context", raw)
        self.assertIn("NOSENSOR", line)
        self.assertIn("300k", line)
        self.assertIsNone(self.cycle("context", raw))

    def test_context_missing_id_isolated(self):
        raw, rows = self.context_pair()
        rows[0]["agent_session"] = None
        line = self.cycle("context", raw)
        self.assertIn("NOSENSOR", line)
        self.assertIn("RECYCLE", line)

    def test_context_missing_sensor_new_session_same_pane(self):
        raw, rows = self.context_pair()
        raw["usage"] = {}
        self.cycle("context", raw)
        rows[0]["agent_session"]["value"] = "replacement-session"
        line = self.cycle("context", raw)
        self.assertIsNotNone(line)
        self.assertIn("NOSENSOR", line)
        self.assertIsNone(self.cycle("context", raw))

    def test_context_percentage_amount(self):
        raw, rows = self.context_pair()
        raw["usage"][w.identity(rows[1])] = {"remaining": 45}
        self.assertIn("55%", self.cycle("context", raw))

    def test_context_once_per_session_after_sensor_gap(self):
        raw, rows = self.context_pair()
        self.cycle("context", raw)
        sid = w.identity(rows[1])
        del raw["usage"][sid]
        self.cycle("context", raw)
        raw["usage"][sid] = {"tokens": 400000}
        self.assertEqual(
            json.loads(self.cycle("context", raw))["events"],
            [f"NOSENSOR CLEARED {rows[1]['pane_id']}"],
        )

    def test_host_steps_combined_and_recovery(self):
        raw = healthy("host")
        raw.update(swap=1600, files=150001, load=41)
        _, events = w.reduce("host", None, raw)
        self.assertEqual(events, ["HOST SWAP(1600.00MB) FILES(150001) LOAD(41.00)"])
        self.cycle("host", raw)
        raw.update(swap=1999, files=159999)
        self.assertIsNone(self.cycle("host", raw))
        raw["swap"] = 2000
        self.assertIn("HOST SWAP", self.cycle("host", raw))
        raw["files"] = 160000
        self.assertIn("FILES(160000)", self.cycle("host", raw))
        raw["load"] = 1
        line = self.cycle("host", raw)
        self.assertIn("HOST SWAP", line)
        self.assertNotIn("RECOVERED", line)
        self.assertEqual(
            json.loads(self.cycle("host", healthy("host")))["events"],
            ["HOST RECOVERED"],
        )

    def test_host_unavailable_active_metric_preserves_pressure(self):
        raw = healthy("host")
        raw["swap"] = 1500
        self.cycle("host", raw)
        raw["swap"] = None
        self.assertIn("HOST READFAIL swap", self.cycle("host", raw))
        raw["load"] = 41
        line = self.cycle("host", raw)
        self.assertIn("SWAP(unavailable)", line)
        self.assertIn("LOAD(41.00)", line)
        raw["load"] = 1
        self.assertNotIn("RECOVERED", self.cycle("host", raw))
        del raw["swap"]
        self.assertIsNone(self.cycle("host", raw))
        raw["swap"] = 0
        self.assertEqual(
            json.loads(self.cycle("host", raw))["events"],
            ["HOST READFAIL cleared", "HOST RECOVERED"],
        )

    def test_host_missing_metrics_and_boundaries(self):
        self.assertEqual(
            w.conditions(
                "host",
                {
                    "free": None,
                    "swap": 1024,
                    "procs": 1300,
                    "files": 150000,
                    "load": 40,
                },
            ),
            {},
        )
        self.assertEqual(w.conditions("host", {"swap": 1025}), {"swap": "swap=1025"})

    def test_host_readfail_set_changes(self):
        raw = healthy("host")
        raw["swap"] = None
        self.assertEqual(
            json.loads(self.cycle("host", raw))["events"], ["HOST READFAIL swap"]
        )
        self.assertIsNone(self.cycle("host", raw))
        raw["load"] = None
        self.assertEqual(
            json.loads(self.cycle("host", raw))["events"], ["HOST READFAIL swap load"]
        )
        state = json.loads((self.base / "host.json").read_text())
        self.assertEqual(state["before"]["unreadable"], ["swap", "load"])
        raw.update(swap=0, load=1)
        self.assertEqual(
            json.loads(self.cycle("host", raw))["events"], ["HOST READFAIL cleared"]
        )

    def test_host_all_unreadable_counts_failed_cycles(self):
        raw = dict.fromkeys(healthy("host"))
        self.cycle("host", raw)
        self.assertEqual(json.loads((self.base / "host.json").read_text())["fails"], 1)
        self.assertIsNone(self.cycle("host", raw))
        self.assertIn("host read failed 3 times", self.cycle("host", raw))
        self.assertIsNone(self.cycle("host", raw))
        self.assertIn("host READ RECOVERED", self.cycle("host", healthy("host")))

    def test_context_sensor_read_errors_are_isolated(self):
        raw, rows = self.context_pair()
        bad = w.identity(rows[0])
        good = w.identity(rows[1])
        for error in (
            ValueError("empty"),
            PermissionError("denied"),
            OSError("read"),
            RuntimeError("sensor"),
        ):
            with self.subTest(error=type(error).__name__):

                def read(path):
                    if path.name == bad + ".tokens":
                        raise error
                    if path.name == good + ".tokens":
                        return "400000"
                    raise FileNotFoundError()

                with (
                    patch.dict(os.environ, {"HERDR_ENV": "1"}),
                    patch.object(w, "command", return_value=json.dumps(raw["herdr"])),
                    patch.object(Path, "read_text", read),
                ):
                    collected = w.collect("context", None)
                events = list(w.conditions("context", collected).values())
                self.assertTrue(any(line.startswith("NOSENSOR") for line in events))
                self.assertTrue(any(line.endswith("400k") for line in events))

    def test_context_empty_and_corrupt_sensor_files_are_isolated(self):
        raw, rows = self.context_pair()
        bad, good = map(w.identity, rows)
        sensor_dir = self.base / ".claude/context-usage"
        sensor_dir.mkdir(parents=True)
        (sensor_dir / (good + ".tokens")).write_text("400000")
        for content in ("", "not-a-number"):
            with self.subTest(content=content):
                (sensor_dir / (bad + ".tokens")).write_text(content)
                with (
                    patch.dict(os.environ, {"HERDR_ENV": "1"}),
                    patch.object(w, "command", return_value=json.dumps(raw["herdr"])),
                    patch.object(Path, "home", return_value=self.base),
                ):
                    collected = w.collect("context", None)
                events = list(w.conditions("context", collected).values())
                self.assertTrue(any(line.startswith("NOSENSOR") for line in events))
                self.assertTrue(any(line.endswith("400k") for line in events))

    def test_context_percentage_read_error_is_isolated(self):
        raw, rows = self.context_pair()
        bad, good = map(w.identity, rows)

        def read(path):
            if path.name == good + ".tokens":
                return "400000"
            if path.name == bad + ".tokens":
                raise FileNotFoundError()
            raise PermissionError("percentage denied")

        with (
            patch.dict(os.environ, {"HERDR_ENV": "1"}),
            patch.object(w, "command", return_value=json.dumps(raw["herdr"])),
            patch.object(Path, "read_text", read),
        ):
            collected = w.collect("context", None)
        self.assertEqual(collected["usage"][good], {"tokens": 400000})
        self.assertNotIn(bad, collected["usage"])

    def test_context_prunes_departed_sessions(self):
        raw, rows = self.context_pair()
        old = w.identity(rows[1])
        self.cycle("context", raw)
        raw["herdr"]["result"]["agents"] = [rows[0]]
        self.cycle("context", raw)
        remembered = json.loads((self.base / "context.json").read_text())["before"]
        self.assertNotIn(old, remembered)

    def test_context_nosensor_refires_after_sensor_returns(self):
        raw, rows = self.context_pair()
        sid = w.identity(rows[0])
        del raw["usage"][sid]
        self.assertIn("NOSENSOR", self.cycle("context", raw))
        raw["usage"][sid] = {"tokens": 1}
        self.assertEqual(
            json.loads(self.cycle("context", raw))["events"],
            [f"NOSENSOR CLEARED {rows[0]['pane_id']}"],
        )
        del raw["usage"][sid]
        self.assertIn("NOSENSOR", self.cycle("context", raw))

    def test_context_fractional_percentage_format(self):
        raw, rows = self.context_pair()
        raw["usage"][w.identity(rows[1])] = {"remaining": 44.3}
        self.assertTrue(
            any(line.endswith("56%") for line in w.conditions("context", raw).values())
        )

    def events(self, raw):
        line = self.cycle("context", raw)
        return json.loads(line)["events"] if line else []

    def test_context_worker_over_the_threshold_alerts_its_owner_once_per_crossing(self):
        raw = worker_context(299999)
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            self.assertEqual(self.events(raw), [])
            raw["usage"]["session-worker-001"]["tokens"] = 300000
            self.assertEqual(self.events(raw), [worker_line("300k")])
            self.assertEqual(self.events(raw), [])
            raw["usage"]["session-worker-001"]["tokens"] = 440000
            self.assertEqual(self.events(raw), [])

    def test_worker_stage_never_downgrades_before_owner_or_session_changes(self):
        raw = worker_context(300000)
        before, events = w.reduce("context", None, raw)
        self.assertEqual(events, [worker_line("300k")])
        for tokens in (450000, 450000, 320000, 450000, 1000):
            raw["usage"]["session-worker-001"]["tokens"] = tokens
            before, events = w.reduce("context", before, raw)
            if tokens == 450000 and not getattr(self, "hard_seen", False):
                self.assertEqual(
                    events,
                    [
                        worker_line("cleared"),
                        worker_line("450k").replace("-SOFT", "-HARD"),
                    ],
                )
                self.hard_seen = True
            else:
                self.assertEqual(events, [])
            self.assertIn("RECYCLE-WORKER-HARD", before["session-worker-001"])
        raw["workspaces"]["result"]["workspaces"][1]["tokens"]["owner"] = "new-owner"
        middle, events = w.reduce("context", before, raw)
        self.assertEqual(events, [worker_line("cleared").replace("-SOFT", "-HARD")])
        self.assertNotIn("session-worker-001", middle)
        raw["usage"]["session-worker-001"]["tokens"] = 320000
        _, events = w.reduce("context", before, raw)
        self.assertEqual(
            events,
            [
                worker_line("cleared").replace("-SOFT", "-HARD"),
                worker_line("320k", owner="new-owner"),
            ],
        )

    def test_context_worker_successor_alerts_again_and_the_old_pane_clears(self):
        raw = worker_context(310000)
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            self.assertEqual(self.events(raw), [worker_line("310k")])
            worker = w.agents(raw["herdr"])[1]
            worker.update(
                pane_id="workspace-task:p2",
                agent_session={"value": "session-worker-002"},
            )
            raw["usage"]["session-worker-002"] = {"tokens": 300000, "window": 1000000}
            self.assertEqual(
                self.events(raw),
                [worker_line("cleared"), worker_line("300k", pane="workspace-task:p2")],
            )
            raw["herdr"]["result"]["agents"].remove(worker)
            self.assertEqual(
                self.events(raw), [worker_line("cleared", pane="workspace-task:p2")]
            )

    def test_context_worker_without_an_owner_tag_stays_quiet(self):
        untagged = worker_context(310000, owner=None)
        unlisted = worker_context(310000)
        unlisted["workspaces"]["result"]["workspaces"].pop()
        unread = dict(worker_context(310000), workspaces=None)
        absent = {
            key: value
            for key, value in worker_context(310000).items()
            if key != "workspaces"
        }
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            for name, raw in (
                ("untagged", untagged),
                ("unlisted", unlisted),
                ("unread", unread),
                ("absent", absent),
            ):
                with self.subTest(name):
                    after, events = w.reduce("context", None, raw)
                    self.assertEqual(
                        (events, [key for key in after if key != w.WORKSPACE_FAILS]),
                        ([], []),
                    )

    def test_context_worker_owner_change_moves_its_alert(self):
        raw = worker_context(310000)
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            before, _ = w.reduce("context", None, raw)
            raw["workspaces"]["result"]["workspaces"][1]["tokens"]["owner"] = "wOther"
            _, events = w.reduce("context", before, raw)
        self.assertEqual(
            events, [worker_line("cleared"), worker_line("310k", owner="wOther")]
        )

    def test_context_worker_untagged_by_a_successful_read_clears_and_alerts_again_when_retagged(
        self,
    ):
        raw = worker_context(310000)
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            before, _ = w.reduce("context", None, raw)
            untagged = worker_context(310000, owner=None)
            middle, events = w.reduce("context", before, untagged)
            self.assertEqual(events, [worker_line("cleared")])
            self.assertEqual(w.reduce("context", middle, raw)[1], [worker_line("310k")])

    def test_context_worker_alert_survives_unreadable_owner_tags(self):
        raw = worker_context(310000)
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            before, _ = w.reduce("context", None, raw)
            after, events = w.reduce("context", before, dict(raw, workspaces=None))
            self.assertEqual(
                (after, events), (dict(before, **{w.WORKSPACE_FAILS: "1"}), [])
            )
            self.assertEqual(w.reduce("context", after, raw), (before, []))

    def test_context_worker_without_a_sensor_or_off_claude_stays_quiet(self):
        sensorless = worker_context(310000)
        del sensorless["usage"]["session-worker-001"]
        codex = worker_context(310000)
        w.agents(codex["herdr"])[1]["agent"] = "codex"
        unnamed = worker_context(310000)
        w.agents(unnamed["herdr"])[1]["agent_session"] = None
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            for name, raw in (
                ("sensorless", sensorless),
                ("codex", codex),
                ("no session", unnamed),
            ):
                with self.subTest(name):
                    self.assertEqual(w.reduce("context", None, raw), ({}, []))

    def test_context_worker_line_names_the_agent_never_the_terminal_title(self):
        raw = worker_context(310000)
        del w.agents(raw["herdr"])[1]["name"]
        raw["usage"]["session-worker-001"] = {"remaining": 40}
        with patch.dict(os.environ, {"ORCH_RECYCLE_PCT": "55"}):
            self.assertEqual(
                list(w.conditions("context", raw).values()),
                ["RECYCLE-WORKER-HARD workspace-task:p1 owner=wArch 60%"],
            )

    def test_context_junk_workspace_rows_never_stop_the_architect_recycle(self):
        raw = worker_context(310000)
        raw["usage"]["session-architect-001"] = {"tokens": 310000}
        raw["workspaces"]["result"]["workspaces"] += [
            "junk",
            None,
            {"workspace_id": "workspace-odd", "tokens": "x"},
        ]
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            self.assertEqual(
                sorted(w.conditions("context", raw).values()),
                [
                    "RECYCLE workspace-demo:p1 Architect::Example 310k",
                    worker_line("310k"),
                ],
            )

    def test_context_the_coordinator_is_never_a_worker(self):
        raw = worker_context(310000)
        raw["usage"]["session-worker-001"] = {"tokens": 310000}
        pane = w.agents(raw["herdr"])[1]
        pane.update(
            name="Coordinator",
            tokens={"role": "coordinator"},
            terminal_title_stripped="Coordinator",
        )
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            self.assertEqual(
                list(w.conditions("context", raw).values()),
                ["RECYCLE workspace-task:p1 Coordinator 310k"],
            )

    def test_context_a_lasting_unreadable_workspace_list_reaches_the_feed_once_and_recovers(
        self,
    ):
        raw = worker_context(310000)
        unread = dict(raw, workspaces=None)
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "300000"}):
            self.assertEqual(
                [self.events(unread) for _ in range(4)],
                [[], [], ["context read failed 3 times (WorkspaceList)"], []],
            )
            self.assertEqual(
                self.events(raw), [worker_line("310k"), "context READ RECOVERED"]
            )
            self.assertEqual(self.events(raw), [])
            self.assertEqual(
                [self.events(unread) for _ in range(2)] + [self.events(raw)],
                [[], [], []],
            )

    def herdr_reads(self, raw, workspaces):
        def read(*argv):
            if argv[1:] != ("workspace", "list"):
                return json.dumps(raw["herdr"])
            if isinstance(workspaces, Exception):
                raise workspaces
            return workspaces

        return read

    def test_context_collect_reads_the_workspace_owner_tags(self):
        raw = worker_context(310000)
        with (
            patch.dict(os.environ, {"HERDR_ENV": "1", "ORCH_RECYCLE_TOKENS": "300000"}),
            patch.object(
                w,
                "command",
                side_effect=self.herdr_reads(raw, json.dumps(raw["workspaces"])),
            ) as command,
            patch.object(w, "session_usage", side_effect=lambda sid: raw["usage"][sid]),
        ):
            collected = w.collect("context", None)
        command.assert_any_call(os.environ["HERDR_BIN_PATH"], "workspace", "list")
        self.assertEqual(collected["workspaces"], raw["workspaces"])
        self.assertEqual(
            list(w.conditions("context", collected).values()), [worker_line("310k")]
        )

    def test_context_collect_survives_an_unreadable_workspace_list(self):
        raw = worker_context(310000)
        raw["usage"]["session-architect-001"] = {"tokens": 310000}
        for failure in (
            subprocess.CalledProcessError(1, ["herdr"]),
            "not json",
            json.dumps({"result": {}}),
            json.dumps({"result": {"workspaces": {}}}),
        ):
            with (
                self.subTest(failure=failure),
                patch.dict(
                    os.environ, {"HERDR_ENV": "1", "ORCH_RECYCLE_TOKENS": "300000"}
                ),
                patch.object(w, "command", side_effect=self.herdr_reads(raw, failure)),
                patch.object(
                    w, "session_usage", side_effect=lambda sid: raw["usage"][sid]
                ),
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                collected = w.collect("context", None)
                self.assertIsNone(collected["workspaces"])
                self.assertIn("herdr workspace list", stderr.getvalue())
                self.assertEqual(
                    list(w.conditions("context", collected).values()),
                    ["RECYCLE workspace-demo:p1 Architect::Example 310k"],
                )

    def test_host_fractional_number_format(self):
        for free, swap, load, expected in (
            (
                15.6,
                1500.126,
                41.236,
                "HOST PRESSURE(16% free) SWAP(1500.13MB) PROCS(1301) FILES(150001) LOAD(41.24)",
            ),
            (
                15.2,
                1500.1,
                41.2,
                "HOST PRESSURE(15% free) SWAP(1500.10MB) PROCS(1301) FILES(150001) LOAD(41.20)",
            ),
        ):
            with self.subTest(swap=swap):
                raw = dict(free=free, swap=swap, procs=1301, files=150001, load=load)
                self.assertEqual(w.reduce("host", None, raw)[1], [expected])

    def test_worker_blocked_dead_and_idle(self):
        raw = healthy("workers")
        raw["result"]["agents"] = [
            a for a in w.agents(raw) if not (a.get("tokens") or {}).get("role")
        ][:1]
        self.assertTrue(w.agents(raw))
        self.cycle("workers", raw)
        a = w.agents(raw)[0]
        a["agent_status"] = "blocked"
        self.one("workers", raw, "blocked")
        raw["result"]["agents"] = []
        self.one("workers", raw, "dead/disappeared")
        a["agent_status"] = "idle"
        raw["result"]["agents"] = [a]
        self.assertIsNone(self.cycle("workers", raw))

    def test_pi_worker_leaving_working_is_reported(self):
        raw = healthy("workers")
        pi = [a for a in w.agents(raw) if a.get("agent") == "pi"]
        self.assertEqual(len(pi), 1)
        raw["result"]["agents"] = pi
        self.cycle("workers", raw)
        pi[0]["agent_status"] = "blocked"
        self.one("workers", raw, "blocked")
        pi[0]["agent_status"] = "idle"
        self.one("workers", raw, "RECOVERED blocked")
        pi[0]["agent_status"] = "working"
        self.assertIsNone(self.cycle("workers", raw))
        raw["result"]["agents"] = []
        self.one("workers", raw, "dead/disappeared")

    def test_hog_thresholds_pid_reuse(self):
        raw = [{"pid": "42", "rss": 1000, "age": 1, "start": "one", "comm": "node"}]
        self.cycle("hog", raw)
        raw[0]["rss"] = 6 * 1048576
        self.one("hog", raw, "HOG")
        raw[0]["rss"] += 1000
        self.assertIsNone(self.cycle("hog", raw))
        raw[0]["start"] = "two"
        self.one("hog", raw, "HOG")

    def test_git_big_and_long(self):
        raw = [
            {
                "pid": "42",
                "rss": 1000,
                "age": 179,
                "start": "one",
                "comm": "/usr/bin/git",
            }
        ]
        self.cycle("hog", raw)
        raw[0]["age"] = 180
        self.one("hog", raw, "GITLONG")
        raw[0]["rss"] = 2 * 1048576
        self.one("hog", raw, "GITBIG")

    def test_machine_all_limits_jitter_and_recovery(self):
        bad_values = {
            "host": {
                "free": 19,
                "swap": 1025,
                "procs": 1301,
                "files": 150001,
                "load": 41,
            },
            "balance": {"free": 29, "slots": 5, "heavy": 5, "load5": 21},
        }
        for mode, values in bad_values.items():
            raw = healthy(mode)
            self.cycle(mode, raw)
            raw.update(values)
            self.one(mode, raw, mode)
            raw["free"] -= 1
            self.assertIsNone(self.cycle(mode, raw))
            self.one(mode, healthy(mode), "RECOVERED")

    def test_configured_context_host_and_hog_thresholds(self):
        context = healthy("context")
        with patch.dict(os.environ, {"ORCH_RECYCLE_TOKENS": "1000"}, clear=False):
            self.assertTrue(w.conditions("context", context))
        host = healthy("host")
        with patch.dict(os.environ, {"ORCH_HOST_PRESSURE_MIN_PCT": "60"}, clear=False):
            self.assertIn("free", w.conditions("host", host))
        hog = fixture("hog")
        with patch.dict(os.environ, {"ORCH_HOG_GB": "0.001"}, clear=False):
            self.assertTrue(w.conditions("hog", hog))

    def test_collect_uses_herdr_bin_path_with_fallback(self):
        response = '{"result": {"agents": []}}'
        with (
            patch.dict(
                os.environ,
                {"HERDR_ENV": "1", "HERDR_BIN_PATH": "/opt/example/herdr"},
                clear=True,
            ),
            patch.object(
                w, "command", side_effect=[response, '{"result": {"workspaces": []}}']
            ) as command,
        ):
            self.assertEqual(w.collect("workers", None)["result"]["agents"], [])
            self.assertEqual(
                command.call_args_list,
                [
                    call("/opt/example/herdr", "agent", "list"),
                    call("/opt/example/herdr", "workspace", "list"),
                ],
            )
        with (
            patch.dict(os.environ, {"HERDR_ENV": "1"}, clear=True),
            patch.object(
                w, "command", side_effect=[response, '{"result": {"workspaces": []}}']
            ) as command,
        ):
            w.collect("workers", None)
            self.assertEqual(
                command.call_args_list,
                [call("herdr", "agent", "list"), call("herdr", "workspace", "list")],
            )

    def test_pr_new_thread_same_count_and_ack(self):
        raw = healthy("prs")[:1]
        self.assertTrue(raw)
        self.cycle("prs", raw)
        raw[0]["reviewThreads"]["nodes"] = [{"id": "thread-A", "isResolved": False}]
        self.one("prs", raw, "thread-A")
        raw[0]["reviewThreads"]["nodes"] = [{"id": "thread-B", "isResolved": False}]
        self.one("prs", raw, "thread-B")
        raw[0]["updatedAt"] = "2099-01-01T00:00:00Z"
        raw[0]["ack"] = True
        self.assertIsNone(self.cycle("prs", raw))

    def test_pr_requested_changes_red_head_and_terminal(self):
        raw = healthy("prs")[:1]
        self.cycle("prs", raw)
        raw[0]["reviewDecision"] = "CHANGES_REQUESTED"
        raw[0]["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["state"] = (
            "FAILURE"
        )
        self.one("prs", raw, "red checks")
        raw[0]["headRefOid"] = "newhead"
        self.one("prs", raw, "newhead")
        raw[0]["state"] = "MERGED"
        self.one("prs", raw, "MERGED")
        raw[0]["state"] = "CLOSED"
        self.one("prs", raw, "CLOSED")

    def test_pr_fresh_requested_review_same_decision(self):
        raw = healthy("prs")[:1]
        self.cycle("prs", raw)
        raw[0]["reviewDecision"] = "CHANGES_REQUESTED"
        raw[0]["latestReviews"] = {
            "nodes": [{"id": "review-A", "state": "CHANGES_REQUESTED"}]
        }
        self.one("prs", raw, "CHANGES_REQUESTED")
        raw[0]["latestReviews"]["nodes"][0]["id"] = "review-B"
        self.one("prs", raw, "CHANGES_REQUESTED")

    def test_pr_unknown_check_preserves_state(self):
        raw = healthy("prs")[:1]
        self.cycle("prs", raw)
        before = json.loads((self.base / "prs.json").read_text())["before"]
        raw[0]["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
        for _ in range(2):
            self.assertIsNone(self.cycle("prs", raw))
        self.assertIn("read failed", self.cycle("prs", raw))
        self.assertEqual(
            json.loads((self.base / "prs.json").read_text())["before"], before
        )

    def test_pr_missing_is_not_closed(self):
        raw = healthy("prs")[:1]
        self.cycle("prs", raw)
        for _ in range(2):
            self.assertIsNone(self.cycle("prs", []))
        self.assertIn("read failed", self.cycle("prs", []))

    def test_pr_unread_page_is_failure(self):
        raw = healthy("prs")[:1]
        raw[0]["reviewThreads"]["pageInfo"]["hasNextPage"] = True
        for _ in range(2):
            self.assertIsNone(self.cycle("prs", raw))
        self.assertIn("read failed", self.cycle("prs", raw))

    def test_reader_batch_stale_and_recovery(self):
        for mode in w.MODES:
            self.cycle(mode, healthy(mode))
        cursor = {}
        now = time.time()
        reader.read_once(self.base, cursor, now, 900)  # consume the audit baseline
        self.assertIsNone(reader.read_once(self.base, cursor, now, 900))
        self.assertIn(
            "HEARTBEAT STALE", reader.read_once(self.base, cursor, now + 901, 900)
        )
        self.assertIsNone(reader.read_once(self.base, cursor, now + 902, 900))
        self.assertIn(
            "HEARTBEAT RECOVERED", reader.read_once(self.base, cursor, now, 900)
        )
        raw = healthy("host")
        raw["swap"] = 1025
        self.cycle("host", raw)
        raw = healthy("balance")
        raw["slots"] = 5
        self.cycle("balance", raw)
        line = reader.read_once(self.base, cursor, now, 900)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(len(json.loads(line)["alerts"]), 2)
        self.assertIsNone(reader.read_once(self.base, cursor, now, 900))

    def test_reader_gives_a_new_producer_its_heartbeat_grace(self):
        cursor = {}
        now = time.time()
        self.assertIsNone(reader.read_once(self.base, cursor, now, 900, ("pulls",)))
        line = reader.read_once(self.base, cursor, now + 901, 900, ("pulls",))
        self.assertIn("HEARTBEAT STALE", line)

    def test_reader_grace_also_covers_an_old_heartbeat_file(self):
        self.cycle("host", healthy("host"))
        cursor = {}
        old = time.time() - 5000
        os.utime(self.base / "host.heartbeat", (old, old))
        now = time.time()
        self.assertIsNone(reader.read_once(self.base, cursor, now, 900, ("host",)))
        self.assertIn(
            "HEARTBEAT STALE",
            reader.read_once(self.base, cursor, now + 901, 900, ("host",)),
        )

    def test_one_shot_reader_keeps_its_start_grace_across_runs(self):
        cursor = self.base / "once.cursor"
        argv = [
            "read-alerts.py",
            "--state-dir",
            str(self.base),
            "--cursor",
            str(cursor),
            "--once",
            "--modes",
            "pulls",
        ]
        now = time.time()
        for at in (now, now + 901):
            with (
                patch("sys.argv", argv),
                patch.object(reader.time, "time", return_value=at),
                patch("builtins.print") as printed,
            ):
                reader.main()
        self.assertIn("HEARTBEAT STALE", printed.call_args.args[0])

    def test_reader_at_zero_behind_a_capped_history_resumes_from_the_snapshot(self):
        events = [
            {"seq": n, "line": json.dumps({"mode": "pulls", "events": ["X"]})}
            for n in range(200, 300)
        ]
        (self.base / "pulls.json").write_text(
            json.dumps(
                {"before": {"prs": {}, "unreadable": []}, "events": events, "fails": 0}
            )
        )
        (self.base / "pulls.heartbeat").touch()
        cursor = {"pulls": {"seq": 0, "stale": False, "since": 0}}
        alerts = json.loads(
            reader.read_once(self.base, cursor, time.time(), 900, ("pulls",))
        )["alerts"]
        self.assertEqual(alerts, [{"mode": "pulls", "gap": True}])
        self.assertEqual(cursor["pulls"]["seq"], 299)

    def test_reader_restart_cursor_no_duplicates(self):
        self.cycle("host", fixture("host"))
        cursor = {}
        line = reader.read_once(self.base, cursor, time.time(), 900, ["host"])
        self.assertIsNotNone(line)
        cursor = json.loads(json.dumps(cursor))
        self.assertIsNone(
            reader.read_once(self.base, cursor, time.time(), 900, ["host"])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
