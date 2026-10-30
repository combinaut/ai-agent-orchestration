"""Shared GitHub admission and cooldown contracts without network traffic."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import github_poll as g


class GithubPollTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.env = patch.dict(os.environ, GITHUB_POLL_STATE_DIR=str(self.state))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = 1000.0
        self.clock = patch.object(g.time, "time", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.sleep = patch.object(g.time, "sleep", side_effect=self.advance)
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.jitter = patch.object(g.random, "uniform", return_value=2)
        self.jitter.start()
        self.addCleanup(self.jitter.stop)

    def advance(self, seconds):
        self.now += seconds

    def run_call(self, stderr="", code=0):
        return g.run(
            ["gh", "api", "fixture"],
            lambda env: subprocess.CompletedProcess(["gh"], code, "payload", stderr),
        )

    def test_capacity_forty_callers_share_twenty_starts_per_minute(self):
        starts = []
        for _ in range(40):
            g.run(
                ["gh"],
                lambda env: (
                    starts.append(self.now)
                    or subprocess.CompletedProcess(["gh"], 0, "ok", "")
                ),
            )
        self.assertEqual(starts, [1000 + 3 * i for i in range(40)])
        self.assertLessEqual(sum(t < 1060 for t in starts), 20)

    def test_other_process_cannot_enter_during_call(self):
        code = "import github_poll as g; g.run(['gh'], lambda env: (_ for _ in ()).throw(AssertionError('entered')))"

        def invoke(env):
            child_env = dict(os.environ, PYTHONPATH=str(Path(g.__file__).parent))
            child = subprocess.run(
                [sys.executable, "-c", code],
                env=child_env,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("busy", child.stderr)
            self.assertNotIn("AssertionError: entered", child.stderr)
            return subprocess.CompletedProcess(["gh"], 0, "ok", "")

        g.run(["gh"], invoke)

    def test_retry_after_and_reset_set_shared_failed_reads_without_retry(self):
        for header, expected, status, code in (
            ("Retry-After: 120", 1122, 429, 1),
            ("X-RateLimit-Reset: 1300", 1302, 403, 1),
            ("Retry-After: 120\n< X-RateLimit-Reset: 1300", 1122, 429, 1),
            ("Retry-After: Thu, 01 Jan 1970 00:20:00 GMT", 1202, 429, 1),
            ("X-RateLimit-Reset: 1300", 1302, 200, 0),
        ):
            with self.subTest(header=header):
                if self.state.exists():
                    (self.state / "github.json").unlink()
                result = self.run_call(
                    f"< HTTP/2.0 {status} Response\n< {header}\nGraphQL: API rate limit exceeded",
                    code,
                )
                self.assertEqual(result.returncode, 1)
                saved = json.loads((self.state / "github.json").read_text())
                self.assertEqual(saved["until"], expected)
                with self.assertRaisesRegex(OSError, "cooldown"):
                    self.run_call()
                self.assertEqual(self.now, 1000)

    def test_fallback_grows_across_calls_and_success_resets(self):
        for delay in (62, 122, 242, 482, 962, 1922, 3602, 3602):
            self.run_call("gh: API rate limit already exceeded for user ID 1", 1)
            saved = json.loads((self.state / "github.json").read_text())
            self.assertEqual(saved["until"], self.now + delay)
            self.now = saved["until"]
        self.run_call()
        self.run_call("gh: secondary rate limit (HTTP 403)", 1)
        saved = json.loads((self.state / "github.json").read_text())
        self.assertEqual(saved["failures"], 1)

    def test_permission_failure_does_not_start_cooldown_and_debug_is_private(self):
        result = self.run_call(
            "* debug secret\n< HTTP/2.0 403 Forbidden\ngh: Forbidden (HTTP 403)", 1
        )
        self.assertEqual(result.stderr, "gh: Forbidden (HTTP 403)")
        self.assertEqual(
            json.loads((self.state / "github.json").read_text())["until"], 0
        )
        self.assertEqual(self.run_call().stdout, "payload")

    def test_unreadable_state_fails_closed_and_spawn_failure_releases_lock(self):
        self.state.mkdir()
        (self.state / "github.json").write_text("broken")
        with self.assertRaises(OSError):
            self.run_call()
        (self.state / "github.json").unlink()
        with self.assertRaises(TimeoutError):
            g.run(["gh"], lambda env: (_ for _ in ()).throw(TimeoutError()))
        self.assertEqual(self.run_call().returncode, 0)

    def test_prs_failure_keeps_snapshot(self):
        path = Path(__file__).resolve().parents[2] / "skills/review-prs/scripts/prs.py"
        spec = importlib.util.spec_from_file_location("limit_prs", path)
        prs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prs)
        args = type("Args", (), {"interval": 120})()
        before = {"known": "evidence"}
        with (
            patch.object(prs, "load_state", return_value=before),
            patch.object(prs, "fetch", side_effect=OSError("cooldown")),
            patch.object(prs, "save_state") as save,
            patch.object(prs.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                prs.cmd_watch(args, "repo:fixture", "fixture")
        save.assert_not_called()

    def test_service_pr_watch_keeps_snapshot_on_cooldown(self):
        path = Path(__file__).resolve().parent / "prs-copy.py"
        spec = importlib.util.spec_from_file_location("limit_prs_copy", path)
        prs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prs)
        args = type("Args", (), {"interval": 120})()
        with (
            patch.object(prs, "load_state", return_value={"saved": "evidence"}),
            patch.object(prs, "fetch", side_effect=OSError("cooldown")),
            patch.object(prs, "save_state") as save,
            patch.object(prs.runtime, "iterations", return_value=iter([None])),
        ):
            prs.cmd_watch(args, "repo:fixture", "fixture")
        save.assert_not_called()

    def test_polling_entry_points_use_gate_and_keep_timeouts(self):
        import focus_check
        import watcher_runtime

        path = Path(__file__).resolve().parents[2] / "skills/review-prs/scripts/prs.py"
        spec = importlib.util.spec_from_file_location("entry_prs", path)
        prs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prs)
        result = subprocess.CompletedProcess(["gh"], 0, "ok", "")
        env = {"GH_DEBUG": "api"}
        with patch.object(
            g, "run", side_effect=lambda argv, invoke: invoke(env)
        ) as gate:
            with patch.object(watcher_runtime, "_run", return_value=result) as call:
                watcher_runtime.run(["gh", "api", "fixture"], timeout=10)
                call.assert_called_once_with(["gh", "api", "fixture"], 10, env)
            for read, timeout in (
                (prs.gh, 45),
                (lambda *a: focus_check.command("gh", *a), 30),
            ):
                with patch.object(subprocess, "run", return_value=result) as call:
                    self.assertEqual(read("api", "fixture"), "ok")
                    self.assertEqual(call.call_args.kwargs["timeout"], timeout)
                    self.assertEqual(call.call_args.kwargs["env"], env)
            self.assertEqual(gate.call_count, 3)

    def test_unprefixed_diagnostics_preserve_large_diff_fallback(self):
        for message in ("diff too_large", "EOF"):
            self.assertEqual(self.run_call(message, 1).stderr, message)
