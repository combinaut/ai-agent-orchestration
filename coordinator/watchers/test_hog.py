import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import watchers as w
from watcher_platform import LinuxProcesses, MacProcesses

FIXTURES = Path(__file__).parent / "fixtures"


class HogTests(IsolatedTestCase):
    def proc(self, **values):
        return dict(
            dict(
                pid="9",
                ppid="8",
                rss=6 * 1048576,
                age=180,
                start="100",
                comm="worker",
                command="worker " + "x" * 400,
            ),
            **values,
        )

    def test_forensic_details_and_five_parents(self):
        raw = [self.proc()]
        raw += [
            self.proc(pid=str(n), ppid=str(n - 1), rss=0, command=f"parent {n}")
            for n in range(8, 1, -1)
        ]
        line = next(iter(w.conditions("hog", raw).values()))
        self.assertIn("rss=6.0GB age=180s", line)
        self.assertIn("cmd=[" + ("worker " + "x" * 400)[:300] + "]", line)
        self.assertEqual(line.count(" <- "), 5)
        self.assertIn(" <- 4 parent 4", line)
        self.assertNotIn(" <- 3 parent 3", line)

    def test_parent_cycle_stops(self):
        raw = [self.proc(), self.proc(pid="8", ppid="9", rss=0)]
        self.assertEqual(next(iter(w.conditions("hog", raw).values())).count(" <- "), 1)

    def test_every_pass_logged_fsynced_and_restart_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            durable_lines = []
            real_fsync = os.fsync

            def verify_durable_bytes(fd):
                if (base / "hog.log").exists() and os.fstat(fd).st_ino == (
                    base / "hog.log"
                ).stat().st_ino:
                    data = (base / "hog.log").read_text()
                    durable_lines.append(data)
                    self.assertEqual(len(data.splitlines()), len(durable_lines))
                    self.assertIn("HOG pid=9 rss=6.0GB", data.splitlines()[-1])
                real_fsync(fd)

            with patch.object(w.os, "fsync", side_effect=verify_durable_bytes):
                self.assertIsNotNone(w.cycle(base, "hog", lambda _: [self.proc()]))
                self.assertIsNone(w.cycle(base, "hog", lambda _: [self.proc()]))
            self.assertEqual(len(durable_lines), 2)
            self.assertEqual(len((base / "hog.log").read_text().splitlines()), 2)
            self.assertEqual(
                len(json.loads((base / "hog.json").read_text())["events"]), 1
            )

    def test_rotation_keeps_one_bounded_backup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(w, "HOG_LOG_BYTES", 80):
            base = Path(tmp)
            for n in range(4):
                w.append_hog_log(base, [str(n) * 40])
            self.assertLessEqual((base / "hog.log").stat().st_size, 80)
            self.assertLessEqual((base / "hog.log.1").stat().st_size, 80)
            self.assertIn("2" * 40, (base / "hog.log.1").read_text())
            self.assertEqual(len(list(base.glob("hog.log*"))), 2)

    def test_linux_fixture_processes(self):
        rows = LinuxProcesses(FIXTURES / "hog-proc", ticks=100).collect(None)
        by_pid = {row["pid"]: row for row in rows}
        self.assertEqual(
            by_pid["9"],
            dict(
                pid="9",
                ppid="8",
                rss=2097152,
                age=180,
                start="2000",
                comm="/opt/Git Tools/bin/git",
                command="/opt/Git Tools/bin/git gc --auto",
            ),
        )
        self.assertEqual(by_pid["8"]["command"], "parent (worker)")
        self.assertEqual(by_pid["8"]["rss"], 0)
        self.assertIn("GITBIG", next(iter(w.conditions("hog", rows).values())))

    def test_linux_disappearing_process_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "uptime").write_text("200 0")
            (base / "9").mkdir()
            self.assertEqual(LinuxProcesses(base, ticks=100).collect(None), [])

    def test_log_failure_preserves_alert_and_failure_streak(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_open = Path.open

            def deny_log(path, *args, **kwargs):
                if path == base / "hog.log":
                    # State and alert publication must precede log attempts.
                    state = json.loads((base / "hog.json").read_text())
                    self.assertTrue(state["before"])
                    self.assertIn("HOG pid=9", (base / "hog.alerts").read_text())
                    raise PermissionError("unwritable hog.log")
                return real_open(path, *args, **kwargs)

            with patch.object(Path, "open", deny_log):
                events = json.loads(w.cycle(base, "hog", lambda _: [self.proc()]))[
                    "events"
                ]
                self.assertTrue(any(e.startswith("HOG pid=9") for e in events))
                self.assertEqual(events.count("hog LOG WRITE FAILED"), 1)
                for _ in range(3):
                    self.assertIsNone(w.cycle(base, "hog", lambda _: [self.proc()]))
            state = json.loads((base / "hog.json").read_text())
            self.assertEqual(state["fails"], 0)
            self.assertNotIn("read failed", (base / "hog.alerts").read_text())
            self.assertEqual(
                json.loads(w.cycle(base, "hog", lambda _: [self.proc()]))["events"],
                ["hog LOG WRITE RECOVERED"],
            )
            self.assertIsNone(w.cycle(base, "hog", lambda _: [self.proc()]))
            with patch.object(Path, "open", deny_log):
                events = json.loads(w.cycle(base, "hog", lambda _: [self.proc()]))[
                    "events"
                ]
                self.assertEqual(events, ["hog LOG WRITE FAILED"])

    def test_log_fsync_oserror_does_not_become_read_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_fsync = os.fsync

            def full_disk(fd):
                log = base / "hog.log"
                if log.exists() and os.fstat(fd).st_ino == log.stat().st_ino:
                    raise OSError("disk full while syncing log")
                real_fsync(fd)

            with patch.object(w.os, "fsync", side_effect=full_disk):
                events = json.loads(w.cycle(base, "hog", lambda _: [self.proc()]))[
                    "events"
                ]
                self.assertTrue(any(e.startswith("HOG pid=9") for e in events))
                self.assertIn("hog LOG WRITE FAILED", events)
                self.assertIsNone(w.cycle(base, "hog", lambda _: [self.proc()]))
            self.assertEqual(json.loads((base / "hog.json").read_text())["fails"], 0)

    def test_linux_permission_denied_skips_only_that_pid(self):
        adapter = LinuxProcesses(FIXTURES / "hog-proc", ticks=100)
        read = Path.read_text

        def deny_parent(path, *args, **kwargs):
            if path.parent.name == "8":
                raise PermissionError("restricted pid")
            return read(path, *args, **kwargs)

        with patch.object(Path, "read_text", deny_parent):
            self.assertEqual([p["pid"] for p in adapter.collect(None)], ["9"])

    def test_linux_ticks_from_sysconf(self):
        with patch("watcher_platform.os.sysconf", return_value=200):
            rows = LinuxProcesses(FIXTURES / "hog-proc").collect(None)
        self.assertEqual(next(p["age"] for p in rows if p["pid"] == "9"), 190)

    def test_linux_bad_pid_skipped_and_counted(self):
        for stat in (
            "",
            "12 (bad) S 1",
            "12 (bad) " + " ".join(["S", "1"] + ["0"] * 17 + ["bad"]),
        ):
            with self.subTest(stat=stat), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp) / "proc"
                shutil.copytree(FIXTURES / "hog-proc", base)
                bad = base / "12"
                bad.mkdir()
                (bad / "stat").write_text(stat)
                adapter = LinuxProcesses(base, ticks=100)
                self.assertEqual({p["pid"] for p in adapter.collect(None)}, {"8", "9"})
                self.assertEqual(adapter.skipped_processes, 1)
                (bad / "stat").write_text((base / "9/stat").read_text())
                (bad / "status").write_text("VmRSS: invalid kB")
                self.assertEqual({p["pid"] for p in adapter.collect(None)}, {"8", "9"})
                self.assertEqual(adapter.skipped_processes, 1)
                shutil.rmtree(bad)
                adapter.collect(None)
                self.assertEqual(adapter.skipped_processes, 0)

    def test_parent_command_cap_and_pid_one_stop(self):
        raw = [
            self.proc(),
            self.proc(pid="8", ppid="1", rss=0, command="x" * 150),
            self.proc(pid="1", ppid="0", rss=0, command="init"),
        ]
        line = next(iter(w.conditions("hog", raw).values()))
        self.assertEqual(line.split("parents:")[1], " <- 8 " + "x" * 90)

    def test_thresholds_compare_raw_kb_before_display_rounding(self):
        self.assertEqual(w.conditions("hog", [self.proc(rss=int(5.96 * 1048576))]), {})
        self.assertEqual(
            w.conditions(
                "hog", [self.proc(comm="git", age=1, rss=int(1.96 * 1048576))]
            ),
            {},
        )

    def test_mac_fixture_processes(self):
        def command(*args):
            return (
                FIXTURES
                / (
                    "hog-mac-commands.txt"
                    if args[-1] == "pid=,command="
                    else "hog-mac-ps.txt"
                )
            ).read_text()

        rows = MacProcesses().collect(command)
        self.assertEqual(rows[0]["age"], 86400 + 3600 + 120 + 3)
        self.assertEqual(rows[0]["ppid"], "8")
        self.assertEqual(rows[0]["command"], "/usr/bin/git gc --auto")
        self.assertEqual(rows[0]["start"], "Mon Oct 12 10:00:00 2026")

    def test_growth_is_one_hit_and_departure_is_one_clear(self):
        proc = dict(
            pid=340,
            ppid=1,
            start="s",
            comm="/usr/sbin/fseventsd",
            command="/usr/sbin/fseventsd",
            rss=6 * 1048576,
            age=5,
        )
        before, events = w.reduce("hog", None, [proc])
        self.assertEqual(len(events), 1)
        proc["rss"] = 20 * 1048576
        before, events = w.reduce("hog", before, [proc])
        self.assertEqual(events, [])
        before, events = w.reduce("hog", before, [])
        self.assertEqual(events, ["HOG pid=340 comm=[fseventsd] cleared"])

    def test_state_keyed_by_step_upgrades_without_alerts(self):
        proc = dict(
            pid=9,
            ppid=1,
            start="s",
            comm="node",
            command="node",
            rss=7 * 1048576,
            age=5,
        )
        after, _ = w.reduce("hog", None, [proc])
        old = {key + ":1": line for key, line in after.items()}
        self.assertEqual(w.reduce("hog", old, [proc])[1], [])

    def test_reused_pid_with_the_same_name_does_not_clear_the_live_process(self):
        proc = dict(
            pid=9,
            ppid=1,
            start="a",
            comm="node",
            command="node",
            rss=7 * 1048576,
            age=5,
        )
        before, _ = w.reduce("hog", None, [proc])
        before, events = w.reduce("hog", before, [dict(proc, start="b")])
        self.assertEqual(len(events), 1)
        self.assertNotIn("cleared", events[0])

    def test_old_format_state_still_clears(self):
        old = {
            "340:s:HOG:2": "HOG pid=340 rss=7.8GB age=5s cmd=[/System/Library/fseventsd] parents:"
        }
        self.assertEqual(
            w.reduce("hog", old, [])[1], ["HOG pid=340 comm=[fseventsd] cleared"]
        )

    def test_a_process_that_changes_name_clears_the_old_name(self):
        proc = dict(
            pid=9,
            ppid=1,
            start="a",
            comm="bash",
            command="bash",
            rss=7 * 1048576,
            age=5,
        )
        before, _ = w.reduce("hog", None, [proc])
        _, events = w.reduce("hog", before, [dict(proc, comm="node", command="node")])
        self.assertEqual(events[0], "HOG pid=9 comm=[bash] cleared")
        self.assertTrue(events[1].startswith("HOG pid=9 rss=7.0GB age=5s comm=[node]"))


if __name__ == "__main__":
    unittest.main()
