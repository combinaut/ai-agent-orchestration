"""Watch-loop deadlines, child cleanup, and outage recovery with fake GitHub."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_support import IsolatedTestCase

from test_github_poll_round2 import ROOT, load_prs


class PrsWatchTests(IsolatedTestCase):
    def test_successive_cycles_allow_calls_longer_than_the_alarm_tick(self):
        prs = load_prs()
        waits = []

        def fetch(_scope):
            for duration in (2, 0.3):

                def invoke(_env, _fds):
                    time.sleep(duration)
                    return subprocess.CompletedProcess(["gh"], 0, "", "")

                prs.github_poll.run(["gh"], invoke, timeout=prs.GH_TIMEOUT_S)
            return [], "reviewer"

        def sleep(delay):
            waits.append(delay)
            if len(waits) == 3:
                raise KeyboardInterrupt
            time.sleep(0.3)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, GITHUB_POLL_STATE_DIR=directory),
            patch.object(prs.github_poll, "SPACING", 0),
            patch.object(prs, "load_state", return_value={}),
            patch.object(prs, "fetch", side_effect=fetch),
            patch.object(prs, "save_state") as save,
            patch.object(prs, "time", SimpleNamespace(sleep=sleep)),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaises(KeyboardInterrupt):
                prs.cmd_watch(SimpleNamespace(interval=1), "", "")
        self.assertEqual(save.call_count, 3, output.getvalue())
        self.assertEqual(waits, [1, 1, 1])
        self.assertNotIn("failing", output.getvalue())

    def test_hung_child_is_killed_and_next_cycle_saves_a_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "gh"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import os, sys, time\n"
                "from pathlib import Path\n"
                f"pid = Path({str(root / 'hung.pid')!r})\n"
                # Cross an alarm tick before readiness, regardless of spawn speed.
                "time.sleep(0.4)\n"
                "if not pid.exists():\n"
                "    pid.write_text(str(os.getpid()))\n"
                "    time.sleep(60)\n"
                "elif 'viewer { login }' in ' '.join(sys.argv):\n"
                "    print('reviewer')\n"
                "else:\n"
                '    print(\'[{"data": {"search": {"nodes": []}}}]\')\n'
            )
            fake.chmod(0o755)
            driver = """
import json, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import prs
prs.GH_TIMEOUT_S = float(sys.argv[3])
prs.WATCH_TIMEOUT_S = float(sys.argv[4])
prs.github_poll.SPACING = 0
prs.STATE_DIR = Path(sys.argv[2]) / 'snapshots'
scope = 'repo:example/project'
prs.save_state(scope, {})
saved = prs.state_file(scope)
original = saved.stat().st_mtime_ns
sleeps = []
def sleep(delay):
    sleeps.append(delay)
    if len(sleeps) == 1:
        assert saved.stat().st_mtime_ns == original, 'failure replaced snapshot'
    else:
        assert saved.stat().st_mtime_ns != original, 'recovery did not save'
        assert json.loads(saved.read_text()) == {}
        raise KeyboardInterrupt
with patch.object(prs, 'time', SimpleNamespace(sleep=sleep)):
    try:
        prs.cmd_watch(SimpleNamespace(interval=1), scope, '')
    except KeyboardInterrupt:
        pass
assert sleeps == [1, 1], sleeps
"""
            for call_budget, cycle_budget in ((3, 120), (45, 3)):
                with self.subTest(call=call_budget, cycle=cycle_budget):
                    pidfile = root / "hung.pid"
                    pidfile.unlink(missing_ok=True)
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            driver,
                            str(ROOT / "skills/review-prs/scripts"),
                            directory,
                            str(call_budget),
                            str(cycle_budget),
                        ],
                        env=dict(
                            os.environ,
                            PATH=f"{directory}:{os.environ['PATH']}",
                            GITHUB_POLL_STATE_DIR=str(root / f"poll-{call_budget}"),
                        ),
                        capture_output=True,
                        text=True,
                        timeout=12,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("PR fetch failing 1x", result.stdout)
                    self.assertIn("recovered: PR fetch works again", result.stdout)
                    self.assertTrue(pidfile.exists())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(pidfile.read_text()), 0)

    def test_backoff_caps_reports_every_failure_and_resets_after_recovery(self):
        prs = load_prs()
        waits = []
        output = io.StringIO()

        def sleep(delay):
            waits.append(delay)
            if len(waits) == 15:
                raise KeyboardInterrupt

        with (
            patch.object(prs, "load_state", return_value={}),
            patch.object(
                prs,
                "fetch",
                side_effect=[
                    *[OSError("offline")] * 13,
                    ([], "reviewer"),
                    OSError("offline"),
                ],
            ),
            patch.object(prs, "save_state") as save,
            patch.object(prs, "time", SimpleNamespace(sleep=sleep)),
            contextlib.redirect_stdout(output),
        ):
            with self.assertRaises(KeyboardInterrupt):
                prs.cmd_watch(SimpleNamespace(interval=120), "", "")
        self.assertEqual(waits, [120, 240, 480, *[900] * 10, 120, 120])
        self.assertEqual(output.getvalue().count("PR fetch failing"), 14)
        self.assertEqual(output.getvalue().count("recovered:"), 1)
        save.assert_called_once_with("", {})

    def test_cycle_deadline_bypasses_departure_read_error_handlers(self):
        prs = load_prs()
        before = {
            f"https://github.com/example/project/pull/{number}": {"title": "Saved"}
            for number in (1, 2)
        }

        def departure(*args):
            return prs.github_poll.run(["gh"], lambda *args: None, timeout=45)

        def stop(_delay):
            raise KeyboardInterrupt

        with (
            patch.object(prs, "WATCH_TIMEOUT_S", 0.05),
            patch.object(prs, "load_state", return_value=before),
            patch.object(prs, "fetch", return_value=([], "reviewer")),
            patch.object(prs, "departure", side_effect=departure) as lookup,
            patch.object(
                prs.github_poll, "_run", side_effect=lambda *a, **kw: time.sleep(5)
            ),
            patch.object(prs, "save_state") as save,
            patch.object(prs, "time", SimpleNamespace(sleep=stop)),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaises(KeyboardInterrupt):
                prs.cmd_watch(SimpleNamespace(interval=1), "", "")
        save.assert_not_called()
        self.assertEqual(lookup.call_count, 1)
        self.assertIn("cycle timed out", output.getvalue())
