"""Shared GitHub admission and cooldown contracts without network traffic."""

import importlib.util
import io
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
        bytecode = patch.object(sys, "dont_write_bytecode", True)
        bytecode.start()
        self.addCleanup(bytecode.stop)
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
            lambda env, pass_fds: subprocess.CompletedProcess(
                ["gh"], code, "payload", stderr
            ),
        )

    def test_capacity_forty_callers_share_twenty_starts_per_minute(self):
        starts = []
        for _ in range(40):
            g.run(
                ["gh"],
                lambda env, pass_fds: (
                    starts.append(self.now)
                    or subprocess.CompletedProcess(["gh"], 0, "ok", "")
                ),
            )
        self.assertEqual(starts, [1003 + 3 * i for i in range(40)])
        self.assertLessEqual(sum(t < starts[0] + 60 for t in starts), 20)

    def test_other_process_cannot_enter_during_call(self):
        code = "import github_poll as g; g.run(['gh'], lambda env, pass_fds: (_ for _ in ()).throw(AssertionError('entered')))"

        def invoke(env, pass_fds):
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
            ("Retry-After: 120", 1125, 429, 1),
            ("X-RateLimit-Reset: 1300", 1302, 403, 1),
            ("Retry-After: 120\n< X-RateLimit-Reset: 1300", 1125, 429, 1),
            ("Retry-After: Thu, 01 Jan 1970 00:20:00 GMT", 1202, 429, 1),
            ("X-RateLimit-Reset: 1300", 1302, 200, 0),
        ):
            with self.subTest(header=header):
                self.now = 1000
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
                self.assertEqual(self.now, 1003)

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
            g.run(["gh"], lambda env, pass_fds: (_ for _ in ()).throw(TimeoutError()))
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
            g, "run", side_effect=lambda argv, invoke: invoke(env, (77,))
        ) as gate:
            with patch.object(watcher_runtime, "_run", return_value=result) as call:
                watcher_runtime.run(["gh", "api", "fixture"], timeout=10)
                call.assert_called_once_with(["gh", "api", "fixture"], 10, env, (77,))
            with patch.object(subprocess, "Popen") as spawn:
                spawn.return_value.communicate.return_value = (b"ok", b"")
                spawn.return_value.returncode = 0
                watcher_runtime.run(["gh", "api", "fixture"], timeout=10)
                self.assertEqual(spawn.call_args.kwargs["pass_fds"], (77,))
            for read, timeout in (
                (prs.gh, 45),
                (lambda *a: focus_check.command("gh", *a), 30),
            ):
                with patch.object(subprocess, "run", return_value=result) as call:
                    self.assertEqual(read("api", "fixture"), "ok")
                    self.assertEqual(call.call_args.kwargs["timeout"], timeout)
                    self.assertEqual(call.call_args.kwargs["env"], env)
                    self.assertEqual(call.call_args.kwargs["pass_fds"], (77,))
            self.assertEqual(gate.call_count, 4)

    def test_unprefixed_diagnostics_preserve_large_diff_fallback(self):
        for message in ("diff too_large", "EOF"):
            self.assertEqual(self.run_call(message, 1).stderr, message)

    def test_persistence_latency_cannot_compress_actual_starts(self):
        starts = []
        original = g.write_state
        writes = 0

        def persist(path, state):
            nonlocal writes
            writes += 1
            if writes == 1:
                self.advance(1)
            original(path, state)

        with patch.object(g, "write_state", side_effect=persist):
            for _ in range(2):
                g.run(
                    ["gh"],
                    lambda env, pass_fds: (
                        starts.append(self.now)
                        or subprocess.CompletedProcess(["gh"], 0, "ok", "")
                    ),
                )
        self.assertGreaterEqual(starts[1] - starts[0], 3)

    def test_legacy_spacing_state_has_bounded_admission_wait(self):
        self.state.mkdir()
        (self.state / "github.json").write_text(
            json.dumps({"pending": True, "next": 1e20})
        )
        self.run_call()
        self.assertEqual(self.now, 1003)

    def test_orphaned_cli_keeps_admission_locked_until_child_exits(self):
        import signal
        import threading
        import time

        stub = Path(self.tmp.name) / "bin"
        stub.mkdir()
        pidfile = Path(self.tmp.name) / "child.pid"
        cli = stub / "gh"
        cli.write_text(
            f"#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n"
        )
        cli.chmod(0o755)
        env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}")
        owner = subprocess.Popen(
            [sys.executable, str(Path(g.__file__)), "api", "fixture"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child = None
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            self.assertTrue(pidfile.exists())
            child = int(pidfile.read_text())
            owner.terminate()
            owner.wait(timeout=2)
            self.now = 10000
            with self.assertRaisesRegex(OSError, "busy"):
                self.run_call()
            os.kill(child, signal.SIGTERM)
            for _ in range(100):
                try:
                    result = self.run_call()
                    break
                except OSError:
                    threading.Event().wait(0.01)
            else:
                self.fail("child exit did not release admission")
            self.assertEqual(result.returncode, 0)
            child = None
        finally:
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=2)
            if child is not None:
                try:
                    os.kill(child, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def test_failed_departure_reads_never_clear_or_save_in_either_pr_watch(self):
        root = Path(__file__).resolve().parents[2]
        for relative, fetched in (
            ("skills/review-prs/scripts/prs.py", ([], "reviewer")),
            ("coordinator/watchers/prs-copy.py", []),
        ):
            for error_type in (RuntimeError, OSError, ValueError):
                with self.subTest(relative=relative, error_type=error_type):
                    spec = importlib.util.spec_from_file_location(
                        "departure_prs", root / relative
                    )
                    prs = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(prs)
                    before = {
                        "https://github.com/acme/app/pull/7": {
                            "title": "t",
                            "misses": 4,
                        }
                    }
                    args = type("Args", (), {"interval": 120})()
                    with (
                        patch.object(prs, "load_state", return_value=before),
                        patch.object(prs, "fetch", return_value=fetched),
                        patch.object(
                            prs, "gh", side_effect=error_type("failed departure")
                        ),
                        patch.object(prs, "save_state") as save,
                        patch.object(
                            prs.time,
                            "sleep",
                            side_effect=[None, None, None, None, KeyboardInterrupt],
                        ),
                        patch.object(sys, "stdout", new_callable=io.StringIO) as output,
                    ):
                        if isinstance(fetched, tuple):
                            with self.assertRaises(KeyboardInterrupt):
                                prs.cmd_watch(args, "repo:fixture", "fixture")
                        else:
                            with patch.object(
                                prs.runtime, "iterations", return_value=iter([None] * 5)
                            ):
                                prs.cmd_watch(args, "repo:fixture", "fixture")
                    save.assert_not_called()
                    self.assertIn("PR fetch failing 3x", output.getvalue())
                    self.assertNotIn("cleared:", output.getvalue())

    def test_missing_departure_state_is_a_failed_read(self):
        root = Path(__file__).resolve().parents[2]
        for relative, response in (
            (
                "skills/review-prs/scripts/prs.py",
                json.dumps({"data": {"resource": {"headRefOid": "h"}}}),
            ),
            ("coordinator/watchers/prs-copy.py", ""),
        ):
            with self.subTest(relative=relative):
                spec = importlib.util.spec_from_file_location(
                    "invalid_departure", root / relative
                )
                prs = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(prs)
                before = {
                    "https://github.com/acme/app/pull/7": {"title": "t", "misses": 4}
                }
                with patch.object(prs, "gh", return_value=response):
                    with self.assertRaises(RuntimeError):
                        if relative.startswith("skills"):
                            prs.changes(before, {}, "reviewer")
                        else:
                            prs.changes(before, {})

    def test_startup_admission_failure_cannot_expand_repository_scope(self):
        root = Path(__file__).resolve().parents[2]
        args = type("Args", (), {"all": False, "org": None, "repo": None})()
        for relative in (
            "skills/review-prs/scripts/prs.py",
            "coordinator/watchers/prs-copy.py",
        ):
            with self.subTest(relative=relative):
                spec = importlib.util.spec_from_file_location(
                    "scope_prs", root / relative
                )
                prs = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(prs)
                with (
                    patch.object(
                        prs, "gh", side_effect=RuntimeError("GitHub poll busy")
                    ),
                    patch.object(
                        prs, "setting", return_value="org:configured", create=True
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "busy"):
                        prs.resolve_scope(args)
                with (
                    patch.object(
                        prs,
                        "gh",
                        side_effect=g.NoRepository("fatal: not a git repository"),
                    ),
                    patch.object(
                        prs, "setting", return_value="org:configured", create=True
                    ),
                ):
                    scope, _label = prs.resolve_scope(args)
                    self.assertEqual(
                        scope, "org:configured" if relative.startswith("skills") else ""
                    )

    def test_wall_clock_changes_cannot_shorten_elapsed_spacing(self):
        for jump in (3600, -3600):
            with self.subTest(jump=jump):
                if self.state.exists():
                    (self.state / "github.json").unlink()
                self.now = 10000
                elapsed = 0
                starts = []

                def wait(seconds):
                    nonlocal elapsed
                    elapsed += seconds
                    self.advance(seconds)

                def invoke(env, pass_fds):
                    starts.append(elapsed)
                    return subprocess.CompletedProcess(["gh"], 0, "ok", "")

                with patch.object(g.time, "sleep", side_effect=wait):
                    g.run(["gh"], invoke)
                    self.now += jump
                    g.run(["gh"], invoke)
                self.assertGreaterEqual(starts[1] - starts[0], 3)

    def test_only_confirmed_local_repository_absence_can_use_scope_fallback(self):
        for diagnostic in (
            "fatal: not a git repository",
            "error: no git remotes found",
            "none of the git remotes point to a known GitHub host",
        ):
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaises(g.NoRepository):
                    g.run(
                        ["gh", "repo", "view"],
                        lambda env, pass_fds: subprocess.CompletedProcess(
                            ["gh"], 1, "", diagnostic
                        ),
                    )
        result = g.run(
            ["gh", "api", "fixture"],
            lambda env, pass_fds: subprocess.CompletedProcess(
                ["gh"], 1, "", "fatal: not a git repository"
            ),
        )
        self.assertEqual(result.returncode, 1)
