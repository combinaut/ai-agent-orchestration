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
                patch.object(g.time, "monotonic", return_value=1000),
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
                    patch.object(g.time, "monotonic", return_value=1000),
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
