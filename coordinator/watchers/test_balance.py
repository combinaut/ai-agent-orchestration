import contextlib
import io
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

from watcher_platform import (
    LinuxBalance,
    MacBalance,
    MissingMetric,
    balance_metrics,
    worktree_totals,
)
from watchers import BALANCE_LIMITS, READ_FAILURES, cycle, reduce


def stuck(before, raw, passes=READ_FAILURES):
    """Reduce `passes` identical unreadable scans; return the state and the last scan's events."""
    for _ in range(passes):
        before, events = reduce("balance", before, raw)
    return before, events


def own_ps(text):
    return "\n".join(row + " " + str(os.getuid()) for row in text.splitlines())


class BalanceTests(IsolatedTestCase):
    def healthy(self):
        return dict(load5=20, slots=4, heavy=4, free=30, top=[])

    def test_each_boundary(self):
        self.assertEqual(reduce("balance", None, self.healthy())[1], [])
        for key, value, label in [
            ("load5", 20.01, "LOAD5"),
            ("slots", 5, "SLOTS"),
            ("heavy", 5, "HEAVYWT"),
            ("free", 29.99, "MEMFREE"),
        ]:
            raw = self.healthy()
            raw[key] = value
            self.assertIn(label, reduce("balance", None, raw)[1][0])

    def test_steps_slots_dedup_and_recovery(self):
        raw = self.healthy()
        raw.update(load5=21, slots=5, top=[dict(name="busy", cpu=123, mb=456)])
        before, events = reduce("balance", None, raw)
        self.assertIn("busy(123%,456MB)", events[0])
        raw["load5"] = 29.99
        self.assertEqual(reduce("balance", before, raw)[1], [])
        raw["load5"] = 30
        before, events = reduce("balance", before, raw)
        self.assertIn("LOAD5(30.00)", events[0])
        raw["slots"] = 6
        before, events = reduce("balance", before, raw)
        self.assertIn("SLOTS(6)", events[0])
        self.assertEqual(
            reduce("balance", before, self.healthy())[1], ["BALANCE RECOVERED"]
        )

    def test_missing_active_metrics_and_processes_prevent_recovery(self):
        for key in ("load5", "slots", "heavy", "free"):
            raw = self.healthy()
            raw[key] = 29 if key == "free" else 30
            before, _ = reduce("balance", None, raw)
            raw = self.healthy()
            raw[key] = None
            before, events = stuck(before, raw)
            self.assertIn("BALANCE READFAIL " + key, events)
            self.assertNotIn("BALANCE RECOVERED", events)
            self.assertEqual(reduce("balance", before, raw)[1], [])
            other = "slots" if key != "slots" else "load5"
            raw[other] = 31
            changed, events = reduce("balance", before, raw)
            self.assertTrue(any("unavailable" in event for event in events))
            events = reduce("balance", before, self.healthy())[1]
            self.assertIn("BALANCE RECOVERED", events)
        before, _ = reduce("balance", None, dict(self.healthy(), load5=21))
        before, events = stuck(
            before, dict(self.healthy(), heavy=None, unreadable=["pid:7"])
        )
        self.assertIn("BALANCE READFAIL heavy pid:7", events)
        self.assertNotIn("BALANCE RECOVERED", events)

    def test_all_missing_counts_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = dict.fromkeys(("load5", "slots", "heavy", "free"))
            base = Path(tmp)
            cycle(base, "balance", lambda _: raw)
            cycle(base, "balance", lambda _: raw)
            self.assertIn("read failed 3 times", cycle(base, "balance", lambda _: raw))

    def test_totals_sort_cpu_and_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "worktrees"
            busy = base / "webapp/busy"
            (busy / "sub").mkdir(parents=True)
            (busy / ".git").write_text("gitdir: /fixture/common/worktrees/busy\n")
            rows = [
                dict(cwd=str(busy / "sub"), cpu=40, rss=1024),
                dict(cwd=str(busy), cpu=40, rss=2048),
                dict(cwd=str(base / "other"), cpu=10, rss=1024),
            ]
            totals = worktree_totals(rows)
            self.assertEqual(
                totals,
                [dict(name="busy", cpu=80, mb=3), dict(name="other", cpu=10, mb=1)],
            )

    def test_linux_proc_cpu_cwd_and_isolated_bad_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 300 kB\n")
            (proc / "loadavg").write_text("1 21 3")
            (proc / "uptime").write_text("100 0")
            slots = proc / "slots"
            slots.mkdir()
            values = ["0"] * 22
            values[0], values[11], values[19], values[21] = "R", "8000", "0", "256"
            for pid in ("1", "2"):
                path = proc / pid
                path.mkdir()
                (path / "stat").write_text(
                    pid + " (worker with spaces) " + " ".join(values)
                )
                (path / "cwd").symlink_to("/repo/worktrees/busy/sub")
            adapter = LinuxBalance(proc, ticks=100)
            raw = balance_metrics(None, BALANCE_LIMITS, slots, adapter)
            self.assertEqual(raw["load5"], 21)
            self.assertEqual(raw["free"], 30)
            self.assertEqual(raw["heavy"], 1)
            before, _ = reduce("balance", None, raw)
            (proc / "uptime").write_text("101 0")
            for pid in ("1", "2"):
                values[11] = "8080"
                (proc / pid / "stat").write_text(pid + " (worker) " + " ".join(values))
            raw = balance_metrics(None, BALANCE_LIMITS, slots, adapter, before=before)
            self.assertEqual(raw["heavy"], 1)
            self.assertEqual(
                raw["top"],
                [
                    dict(
                        name="busy",
                        cpu=160,
                        mb=512 * os.sysconf("SC_PAGE_SIZE") / 1048576,
                    )
                ],
            )
            (proc / "2/stat").write_text("bad")
            before, _ = reduce("balance", before, raw)
            (proc / "uptime").write_text("102 0")
            values[11] = "8160"
            (proc / "1/stat").write_text("1 (worker) " + " ".join(values))
            raw = balance_metrics(None, BALANCE_LIMITS, slots, adapter, before=before)
            self.assertEqual(raw["heavy"], None)
            self.assertEqual(raw["unreadable"], ["pid:2"])
            self.assertEqual(raw["top"][0]["cpu"], 80)
            (proc / "meminfo").write_text("MemTotal: 0 kB\nMemAvailable: 0 kB\n")
            self.assertIsNone(
                balance_metrics(None, BALANCE_LIMITS, slots, adapter)["free"]
            )
            (proc / "meminfo").unlink()
            raw = balance_metrics(None, BALANCE_LIMITS, proc / "absent", adapter)
            self.assertIsNone(raw["free"])
            self.assertEqual(raw["slots"], 0)
            self.assertEqual(raw["load5"], 21)

    def test_mac_cwd_mapping_without_command_path(self):
        def command(*args):
            return {
                ("memory_pressure",): "no metric",
                ("sysctl", "-n", "vm.loadavg"): "{ 1 21 3 }",
                (
                    "ps",
                    "-U",
                    str(os.getuid()),
                    "-o",
                    "pid=,%cpu=,rss=,stat=,uid=",
                ): own_ps("7 80 1024 R\n8 20 2048 S\n9 1 10 R"),
                (
                    "lsof",
                    "-n",
                    "-d",
                    "cwd",
                    "-Fpn",
                ): "p7\nn/repo/worktrees/busy\np8\nn/repo/worktrees/busy/sub",
            }[args]

        adapter = MacBalance()
        self.assertEqual(adapter.metrics(command), dict(free=None, load5=21))
        with patch("watcher_platform.process_alive", return_value=True):
            rows, errors = adapter.processes(command)
        self.assertEqual(errors, ["pid:9"])
        self.assertEqual(worktree_totals(rows), [dict(name="busy", cpu=100, mb=3)])

    def test_partial_process_scan_still_alerts_known_heavy_breach(self):
        class Adapter:
            def metrics(self, command):
                return dict(free=50, load5=1)

            def processes(self, command):
                return (
                    [
                        dict(cwd="/worktrees/job" + str(i), cpu=80, rss=1024)
                        for i in range(5)
                    ],
                    ["pid:bad"],
                )

        with tempfile.TemporaryDirectory() as tmp:
            raw = balance_metrics(None, BALANCE_LIMITS, Path(tmp), Adapter())
        self.assertEqual(raw["heavy"], 5)
        before, events = reduce("balance", None, raw)
        self.assertNotIn("BALANCE READFAIL pid:bad", events)
        self.assertIn("HEAVYWT(5)", events[-1])
        self.assertEqual(
            stuck(before, raw, READ_FAILURES - 1)[1], ["BALANCE READFAIL pid:bad"]
        )

    def test_linux_kernel_threads_excluded_and_global_proc_failure_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            (proc / "uptime").write_text("100 0")
            path = proc / "7"
            path.mkdir()
            values = ["0"] * 22
            values[0], values[6] = "S", str(0x00200000)
            (path / "stat").write_text("7 (kernel) " + " ".join(values))
            adapter = LinuxBalance(proc, ticks=100)
            sample = adapter.sample(None)
            self.assertEqual((sample["rows"], sample["errors"]), ([], []))
            values[0], values[6] = "Z", "0"
            (path / "stat").write_text("7 (zombie) " + " ".join(values))
            sample = adapter.sample(None)
            self.assertEqual((sample["rows"], sample["errors"]), ([], []))
            (proc / "uptime").write_text("bad")
            sample = adapter.sample(None)
            self.assertEqual((sample["rows"], sample["errors"]), ([], ["processes"]))

    def test_mac_process_command_failure_keeps_metrics(self):
        import subprocess

        def command(*args):
            if args[0] == "sysctl":
                return "{ 1 21 3 }"
            if args[0] == "memory_pressure":
                return "free percentage: 50%"
            raise subprocess.CalledProcessError(1, args)

        with (
            tempfile.TemporaryDirectory() as tmp,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            raw = balance_metrics(command, BALANCE_LIMITS, Path(tmp), MacBalance())
        before, events = reduce("balance", None, raw)
        self.assertIn("LOAD5(21.00)", events[-1])
        self.assertEqual(
            stuck(before, raw, READ_FAILURES - 1)[1],
            ["BALANCE READFAIL heavy processes"],
        )

    def test_mac_exited_pid_skipped_permission_denied_pid_reported(self):
        def command(*args):
            if args[0] == "ps":
                return own_ps("7 80 1024 R\n8 20 2048 S\n9 1 10 R")
            return "p7\nn/repo/worktrees/busy"

        def signal(pid, sig):
            self.assertEqual(sig, 0)
            if pid == 8:
                raise ProcessLookupError()
            if pid == 9:
                raise PermissionError()

        with patch("watcher_platform.os.kill", side_effect=signal):
            rows, errors = MacBalance().processes(command)
        self.assertEqual(errors, ["pid:9"])
        self.assertEqual(worktree_totals(rows), [dict(name="busy", cpu=80, mb=1)])

    def test_slots_live_stale_invalid_and_partial_breach(self):
        from watcher_platform import live_slots

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "directory").mkdir()
            (directory / "live").write_text("101")
            (directory / "stale").write_text("102")
            (directory / "restricted").write_text("103")

            def signal(pid, sig):
                self.assertEqual(sig, 0)
                self.assertGreater(pid, 0)
                if pid > 2**31 - 1:
                    raise OverflowError()
                if pid == 102:
                    raise ProcessLookupError()
                if pid == 103:
                    raise PermissionError()

            with patch("watcher_platform.os.kill", side_effect=signal):
                self.assertEqual(live_slots(directory), (2, []))
                for value in ("garbage", "0", "-1", "9" * 100):
                    (directory / "bad").write_text(value)
                    self.assertEqual(live_slots(directory), (2, ["slot:bad"]))
                for i in range(3):
                    (directory / str(i)).write_text(str(200 + i))
                self.assertEqual(live_slots(directory), (5, ["slot:bad"]))

    def test_unreadable_slot_preserves_previous_slot_pressure(self):
        from watcher_platform import live_slots

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            slot = directory / "denied"
            slot.write_text("101")
            with patch.object(Path, "read_text", side_effect=PermissionError()):
                count, errors = live_slots(directory)
            raw = dict(self.healthy(), slots=None, unreadable=errors)
            self.assertEqual(count, 0)
            before, _ = reduce("balance", None, dict(self.healthy(), slots=5))
            before, events = stuck(before, raw)
            self.assertIn("BALANCE READFAIL slots slot:denied", events)
            self.assertNotIn("BALANCE RECOVERED", events)
            events = reduce("balance", before, self.healthy())[1]
            self.assertIn("BALANCE RECOVERED", events)

    def test_mac_zombie_skipped_before_liveness_probe(self):
        def command(*args):
            if args[0] == "ps":
                return own_ps("7 80 1024 R\n8 0 0 Z+\n9 20 2048 S")
            return "p7\nn/repo/worktrees/busy"

        adapter = MacBalance()
        with patch("watcher_platform.process_alive", return_value=True) as alive:
            rows, errors = adapter.processes(command)
        alive.assert_called_once_with(9)
        self.assertEqual(errors, ["pid:9"])
        self.assertEqual(worktree_totals(rows), [dict(name="busy", cpu=80, mb=1)])


class BalanceReadGraceTests(IsolatedTestCase):
    def healthy(self):
        return dict(load5=1, slots=0, heavy=0, free=50, top=[])

    def bad(self):
        return dict(self.healthy(), load5=None)

    def test_one_and_two_bad_passes_give_no_event(self):
        before = None
        for _ in range(READ_FAILURES - 1):
            before, events = reduce("balance", before, self.bad())
            self.assertEqual(events, [])
        self.assertEqual(before["unreadable"], [])

    def test_third_bad_pass_reports_the_readfail_once(self):
        before, events = stuck(None, self.bad())
        self.assertEqual(events, ["BALANCE READFAIL load5"])
        self.assertEqual(reduce("balance", before, self.bad())[1], [])

    def test_recovery_after_a_reported_readfail_gives_cleared(self):
        before, _ = stuck(None, self.bad())
        self.assertEqual(
            reduce("balance", before, self.healthy())[1], ["BALANCE READFAIL cleared"]
        )

    def test_recovery_before_the_report_gives_nothing(self):
        before, _ = stuck(None, self.bad(), READ_FAILURES - 1)
        before, events = reduce("balance", before, self.healthy())
        self.assertEqual(events, [])
        self.assertEqual(before["unreadable_passes"], {})

    def test_bad_passes_must_be_in_a_row(self):
        before = None
        for raw in (self.bad(), self.bad(), self.healthy(), self.bad(), self.bad()):
            before, events = reduce("balance", before, raw)
            self.assertEqual(events, [])

    def test_each_source_counts_its_own_passes(self):
        before, events = stuck(None, dict(self.healthy(), unreadable=["pid:1"]), 2)
        before, events = reduce(
            "balance", before, dict(self.healthy(), unreadable=["pid:1", "pid:2"])
        )
        self.assertEqual(events, ["BALANCE READFAIL pid:1"])
        before, events = stuck(
            before, dict(self.healthy(), unreadable=["pid:1", "pid:2"]), 2
        )
        self.assertEqual(events, ["BALANCE READFAIL pid:1 pid:2"])

    def test_grace_keeps_the_last_good_value_and_the_pressure_flag(self):
        before, _ = reduce("balance", None, dict(self.healthy(), load5=45))
        for slots in (6, 7):
            before, events = reduce("balance", before, dict(self.bad(), slots=slots))
            self.assertEqual(len(events), 1)
            self.assertIn(f"LOAD5(unavailable) SLOTS({slots})", events[0])
            self.assertIn("load5", before["flags"])
        before, events = reduce("balance", before, dict(self.bad(), slots=8))
        self.assertEqual(events[0], "BALANCE READFAIL load5")
        self.assertIn("LOAD5(unavailable) SLOTS(8)", events[1])

    def test_a_kept_value_does_not_recover_or_alert_by_itself(self):
        before, _ = reduce("balance", None, dict(self.healthy(), load5=45))
        before, events = reduce("balance", before, self.bad())
        self.assertEqual(events, [])
        self.assertEqual(
            reduce("balance", before, self.healthy())[1], ["BALANCE RECOVERED"]
        )

    def old_state(self):
        return {
            "flags": {},
            "pending_recovery": False,
            "unreadable": ["heavy", "processes"],
        }

    def test_state_saved_before_the_grace_keeps_its_reported_sources(self):
        raw = dict(self.healthy(), heavy=None, unreadable=["processes"])
        before = self.old_state()
        for _ in range(READ_FAILURES):
            before, events = reduce("balance", before, raw)
            self.assertEqual(events, [])
            self.assertEqual(before["unreadable"], ["heavy", "processes"])

    def test_state_saved_before_the_grace_clears_on_a_good_scan(self):
        self.assertEqual(
            reduce("balance", self.old_state(), self.healthy())[1],
            ["BALANCE READFAIL cleared"],
        )

    def test_state_survives_between_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cycle(base, "balance", lambda _: self.healthy())
            self.assertIsNone(cycle(base, "balance", lambda _: self.bad()))
            self.assertIsNone(cycle(base, "balance", lambda _: self.bad()))
            self.assertIn(
                "BALANCE READFAIL load5", cycle(base, "balance", lambda _: self.bad())
            )

    def test_other_modes_report_the_first_bad_pass(self):
        host = dict(free=50, swap=0, procs=1, files=1, load=1)
        self.assertEqual(
            reduce("host", None, dict(host, swap=None))[1], ["HOST READFAIL swap"]
        )


class MacReadReasonTests(IsolatedTestCase):
    def processes(self, failing, error):
        def command(*args):
            if args[0] == failing:
                raise error
            return own_ps("7 1 1024 R") if args[0] == "ps" else "p7\nn/repo"

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = MacBalance().processes(command)
        return result, stderr.getvalue().splitlines()

    def test_non_zero_exit_names_command_time_exit_code_and_first_stderr_line(self):
        error = subprocess.CalledProcessError(
            2, ["lsof"], stderr="lsof: no permission\nsecond line\n"
        )
        result, lines = self.processes("lsof", error)
        self.assertEqual(result, ([], ["processes"]))
        self.assertEqual(len(lines), 1)
        self.assertRegex(
            lines[0],
            r"^balance read failed: lsof after \d+\.\ds: exit 2: lsof: no permission$",
        )

    def test_timeout_names_command_and_exception(self):
        error = subprocess.TimeoutExpired(["ps"], 45)
        result, lines = self.processes("ps", error)
        self.assertEqual(result, ([], ["processes"]))
        self.assertEqual(len(lines), 1)
        self.assertRegex(
            lines[0],
            r"^balance read failed: ps after \d+\.\ds: TimeoutExpired: Command '\['ps'\]' timed out after 45",
        )

    def test_empty_output_and_two_failures_write_one_line_each(self):
        def command(*args):
            raise MissingMetric(args[0] + " empty response")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            MacBalance().processes(command)
        lines = stderr.getvalue().splitlines()
        self.assertEqual(
            [line.split(" after ")[0] for line in lines],
            ["balance read failed: ps", "balance read failed: lsof"],
        )
        self.assertTrue(all("MissingMetric: " in line for line in lines))

    def test_elapsed_seconds_come_from_the_failing_call(self):
        def command(*args):
            raise subprocess.TimeoutExpired(args, 45)

        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            patch(
                "watcher_platform.time.monotonic", side_effect=[0.0, 2.5, 10.0, 55.0]
            ),
        ):
            MacBalance().processes(command)
        self.assertEqual(
            [line.split(": ")[1] for line in stderr.getvalue().splitlines()],
            ["ps after 2.5s", "lsof after 45.0s"],
        )

    def test_reason_is_cut_to_200_characters(self):
        error = subprocess.CalledProcessError(1, ["ps"], stderr="x" * 500)
        _, lines = self.processes("ps", error)
        self.assertEqual(len(lines[0].split(": ", 2)[2]), 200)
        _, lines = self.processes("ps", MissingMetric("y" * 500))
        self.assertEqual(len(lines[0].split(": ", 2)[2]), 200)

    def test_unlabelled_reads_write_nothing(self):
        def command(*args):
            raise subprocess.CalledProcessError(1, args, stderr="boom")

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(stderr):
            self.assertEqual(MacBalance().metrics(command), dict(free=None, load5=None))
            self.assertEqual(
                LinuxBalance(Path(tmp), ticks=100).metrics(command),
                dict(free=None, load5=None),
            )
        self.assertEqual(stderr.getvalue(), "")

    def test_a_good_read_writes_nothing(self):
        result, lines = self.processes("none", None)
        self.assertEqual((result[1], lines), ([], []))
