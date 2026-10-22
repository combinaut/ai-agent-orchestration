"""Pane-observed background work suppresses due waiting alerts without resetting them."""

import io
import json
import os
import subprocess
import tempfile
from pathlib import Path
from contextlib import redirect_stderr
from unittest.mock import patch

from test_support import IsolatedTestCase
import test_worker_waiting as waiting
import watchers as w


class WaitingJobTests(IsolatedTestCase):
    def setUp(self):
        self.worker = waiting.WaitingTests()
        self.worker.setUp()
        self.worker.scan(0)
        self.calls = []

    def poll(self, now, text="Done.\n› Ask Codex to do anything", error=None):
        def command(*args, **kwargs):
            self.calls.append(args)
            if args[-2:] == ("agent", "list"):
                return json.dumps({"result": {"agents": [self.worker.row]}})
            if args[-2:] == ("workspace", "list"):
                return json.dumps(waiting.workspace_snapshot("architect"))
            if error:
                raise error
            return text

        with (
            patch.dict(os.environ, HERDR_ENV="1"),
            patch.object(w.time, "time", return_value=now),
            patch.object(w, "command", side_effect=command),
        ):
            raw = w.collect("workers", self.worker.state)
        self.worker.state, events = w.reduce_workers(self.worker.state, raw)
        return events

    def test_live_background_texts_suppress_all_stages_without_resetting(self):
        for text in (
            "  1 monitor still running",
            "  2 monitors still running",
            "  1 shell still running",
            "  2 shells still running",
            "  ⏵⏵ accept edits on · 1 monitor · ctrl+t to hide tasks",
            "  1 background terminal running · /ps to view · /stop to close",
            "• Working (20s • esc to interrupt) · 2 background terminals running · /ps to view · /stop to close",
        ):
            for stage in (0, 1, 2):
                with self.subTest(text=text, stage=stage):
                    self.worker.setUp()
                    self.worker.scan(0)
                    if stage:
                        self.worker.scan(601)
                    if stage == 2:
                        self.worker.scan(2401)
                    before = dict(self.worker.state["waiting"]["s1"])
                    self.calls.clear()
                    self.assertEqual(self.poll(10000, text), [])
                    self.assertEqual(self.worker.state["waiting"]["s1"], before)
                    self.assertEqual(
                        self.calls[-1][1:],
                        (
                            "pane",
                            "read",
                            "p1",
                            "--source",
                            "recent-unwrapped",
                            "--lines",
                            "16",
                        ),
                    )
                    events = self.poll(10060)
                    self.assertEqual(len(events), 1)
                    self.assertNotIn("cleared", events[0])

    def test_plain_or_zero_job_pane_alerts(self):
        for text in (
            "Done.",
            "0 monitors still running",
            "0 background terminals running · /ps to view · /stop to close",
        ):
            with self.subTest(text=text):
                self.worker.setUp()
                self.worker.scan(0)
                self.assertEqual(len(self.poll(601, text)), 1)

    def test_reads_only_due_worker_panes(self):
        self.assertEqual(self.poll(600), [])
        self.assertEqual(len(self.calls), 2)
        self.calls.clear()
        self.assertEqual(len(self.poll(601)), 1)
        self.assertEqual(len(self.calls), 3)
        self.calls.clear()
        self.assertEqual(self.poll(602), [])
        self.assertEqual(len(self.calls), 2)
        self.worker.row["agent_status"] = "working"
        self.calls.clear()
        self.poll(10000)
        self.assertEqual(len(self.calls), 2)

    def test_read_failure_alerts_and_logs_once_until_a_successful_read(self):
        for error in (OSError("no pane"), w.MissingMetric("empty pane")):
            with self.subTest(error=error):
                self.worker.setUp()
                self.worker.scan(0)
                output = io.StringIO()
                with redirect_stderr(output):
                    self.assertEqual(len(self.poll(601, error=error)), 1)
                    self.assertEqual(len(self.poll(10000, error=error)), 1)
                self.assertEqual(
                    output.getvalue().count("background-job read failed"), 1
                )
                self.assertIn("p1", output.getvalue())
                with redirect_stderr(output):
                    self.assertEqual(self.poll(11800, "1 monitor still running"), [])
                    self.assertEqual(len(self.poll(11860, error=error)), 1)
                self.assertEqual(
                    output.getvalue().count("background-job read failed"), 2
                )

    def test_stalled_reads_are_bounded_and_worker_state_is_committed(self):
        for budget in (120,):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as tmp:
                rows = [
                    dict(
                        self.worker.row,
                        pane_id=f"p{i}",
                        agent_session={"value": f"s{i}"},
                    )
                    for i in range(4)
                ]
                raw = dict(
                    result={"agents": rows},
                    observed_at=0,
                    workspaces=waiting.workspace_snapshot("architect"),
                )
                before, _ = w.reduce_workers(None, raw)
                rows.append(
                    dict(
                        self.worker.row,
                        pane_id="blocker",
                        agent_status="blocked",
                        agent_session={"value": "blocked"},
                    )
                )
                base = Path(tmp)
                (base / "workers.json").write_text(
                    json.dumps(dict(before=before, fails=0, events=[]))
                )
                elapsed, reads = [0.0], []

                def command(*args, **kwargs):
                    if args[-2:] == ("agent", "list"):
                        return json.dumps({"result": {"agents": rows}})
                    if args[-2:] == ("workspace", "list"):
                        return json.dumps(waiting.workspace_snapshot("architect"))
                    timeout = kwargs.get("timeout", 45)
                    reads.append(timeout)
                    elapsed[0] += timeout
                    if elapsed[0] >= budget:
                        w.runtime._SOFT_DEADLINES[-1]["expired"] = True
                    raise subprocess.TimeoutExpired(args, timeout)

                with (
                    patch.dict(os.environ, HERDR_ENV="1"),
                    patch.object(w, "command", side_effect=command),
                    patch.object(w.time, "time", return_value=4000),
                    patch.object(w.time, "monotonic", side_effect=lambda: elapsed[0]),
                    redirect_stderr(io.StringIO()),
                ):
                    w.cycle(
                        base,
                        "workers",
                        lambda state: w.collect("workers", state),
                        budget=budget,
                    )
                saved = json.loads((base / "workers.json").read_text())
                self.assertEqual(saved["before"]["bad"]["blocked"]["state"], "blocked")
                self.assertEqual(saved["fails"], 0)
                self.assertTrue(
                    all(
                        entry["stage"] == 1
                        for entry in saved["before"]["waiting"].values()
                    )
                )
                self.assertLessEqual(elapsed[0], 10)
                self.assertTrue(all(timeout <= 5 for timeout in reads))
                self.assertEqual(len(reads), 2 if budget == 120 else 0)
                self.assertEqual(
                    len(saved["before"]["waiting_read_failures"]), len(reads)
                )

    def test_low_remaining_pass_budget_skips_reads_and_allows_delivery(self):
        with patch("signal.getitimer", return_value=(2, 0)):
            self.assertEqual(len(self.poll(601)), 1)
        self.assertEqual(len(self.calls), 2)

    def test_laptop_sleep_keeps_repeat_a_full_interval_after_first_send(self):
        self.worker.scan(300)
        self.assertEqual(len(self.worker.scan(4000)), 1)
        self.assertEqual(self.worker.scan(4060), [])
        self.assertEqual(self.worker.scan(5799), [])
        self.assertIn("REPEAT", self.worker.scan(5800)[0])
        self.assertEqual(self.worker.scan(5860), [])
        self.assertIn("ESCALATE", self.worker.scan(7600)[0])

    def test_repeat_gap_is_at_least_one_minute_even_with_zero_setting(self):
        with patch.dict(os.environ, ORCH_WAIT_REPEAT_S="0"):
            self.worker.scan(601)
            self.assertEqual(self.worker.scan(602), [])
            self.assertIn("REPEAT", self.worker.scan(661)[0])
