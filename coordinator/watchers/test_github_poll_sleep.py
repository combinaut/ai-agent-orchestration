"""Deadlines and cleanup when monotonic time pauses across machine sleep."""

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import github_poll as g


class SleepDeadlineTests(IsolatedTestCase):
    def test_sleep_expires_call_and_next_pass_retries(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "child.pid"
            command = [
                sys.executable,
                "-c",
                "import os,time; from pathlib import Path; "
                f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)",
            ]
            real_monotonic = time.monotonic
            started = real_monotonic()
            wall = time.time()

            def sleeping_clock():
                return wall + (3600 if real_monotonic() - started > 0.15 else 0)

            with (
                patch.dict(os.environ, GITHUB_POLL_STATE_DIR=directory),
                patch.object(g.time, "monotonic", return_value=started),
                patch.object(g.time, "time", side_effect=sleeping_clock),
            ):
                with self.assertRaises(g.Deadline):
                    g.run(
                        command,
                        lambda env, fds: g.capture(
                            g.supervised(command, env),
                            env=env,
                            pass_fds=fds,
                            timeout=10,
                        ),
                        timeout=3,
                    )
                self.assertLess(real_monotonic() - started, 1.5)
                self.assertTrue(pidfile.exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), 0)
                with patch.object(g, "SPACING", 0):
                    result = g.run(
                        ["gh"],
                        lambda env, fds: subprocess.CompletedProcess(
                            ["gh"], 0, "retried", ""
                        ),
                    )
                self.assertEqual(result.stdout, "retried")

    def test_supervisor_sleep_deadline_kills_child_group(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "child.pid"
            command = [
                sys.executable,
                "-c",
                "import os,signal,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "child=os.fork(); "
                f"Path({str(pidfile)!r}+('.descendant' if child == 0 else '')).write_text(str(os.getpid())); "
                "time.sleep(60)",
            ]
            real_monotonic = time.monotonic
            started = real_monotonic()
            wall = time.time()
            real_spawn = subprocess.Popen
            read_fd, write_fd = os.pipe()

            def bounded_spawn(*args, **kwargs):
                process = real_spawn(*args, pass_fds=(write_fd,), **kwargs)
                os.close(write_fd)
                wait = process.wait
                process.wait = lambda timeout=None: wait(
                    timeout=3 if timeout is None else timeout
                )
                return process

            try:
                with (
                    patch.object(g.subprocess, "Popen", side_effect=bounded_spawn),
                    patch.object(g.time, "monotonic", return_value=started),
                    patch.object(
                        g.time,
                        "time",
                        side_effect=lambda: (
                            wall + (3600 if real_monotonic() - started > 0.15 else 0)
                        ),
                    ),
                ):
                    self.assertEqual(g.supervise(wall + 3, command), 124)
                self.assertLess(real_monotonic() - started, 1.5)
                for path in (pidfile, Path(str(pidfile) + ".descendant")):
                    self.assertTrue(path.exists())
                # Both processes inherit the writer; EOF proves neither remains alive.
                self.assertTrue(select.select([read_fd], [], [], 1)[0])
                self.assertEqual(os.read(read_fd, 1), b"")
            finally:
                os.close(read_fd)
                for path in (pidfile, Path(str(pidfile) + ".descendant")):
                    if path.exists():
                        try:
                            os.kill(int(path.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_backward_clock_cannot_extend_parent_or_supervisor_deadline(self):
        real_time = time.time
        for supervisor in (False, True):
            with self.subTest(supervisor=supervisor):
                started = time.monotonic()
                wall = real_time()
                command = [sys.executable, "-c", "import time; time.sleep(1)"]
                with patch.object(
                    g.time,
                    "time",
                    side_effect=lambda: (
                        wall - (3600 if time.monotonic() - started > 0.03 else 0)
                    ),
                ):
                    if supervisor:
                        self.assertEqual(g.supervise(wall + 0.3, command), 124)
                    else:
                        with self.assertRaises(g._Expired):
                            with g.call_deadline(0.15):
                                time.sleep(1)
                self.assertLess(time.monotonic() - started, 0.8)

    def test_expiry_disarms_ticks_while_capture_reaps(self):
        spawned, finished = [], []
        real_spawn = subprocess.Popen
        real_cleanup = g.kill_capture

        def spawn(*args, **kwargs):
            process = real_spawn(*args, **kwargs)
            spawned.append(process)
            return process

        def slow_cleanup(process):
            time.sleep(0.4)
            real_cleanup(process)
            finished.append(True)

        try:
            with (
                patch.object(g.subprocess, "Popen", side_effect=spawn),
                patch.object(g, "kill_capture", side_effect=slow_cleanup),
            ):
                with self.assertRaises(g._Expired):
                    with g.call_deadline(0.1):
                        g.capture(
                            [sys.executable, "-c", "import time; time.sleep(60)"],
                            env=None,
                            pass_fds=(),
                            timeout=10,
                        )
            self.assertEqual(finished, [True])
            self.assertIsNotNone(spawned[0].poll())
        finally:
            for process in spawned:
                real_cleanup(process)
                process.wait(timeout=1)
