"""Independent-review regressions; all subprocesses use fake GitHub."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import audit
import test_github_poll as original
import watchers

g = original.g
ROOT = Path(__file__).resolve().parents[2]


def load_prs(service=False):
    path = ROOT / (
        "coordinator/watchers/prs-copy.py"
        if service
        else "skills/review-prs/scripts/prs.py"
    )
    spec = importlib.util.spec_from_file_location("round2_prs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RoundTwoTests(IsolatedTestCase):
    setUp = original.GithubPollTests.setUp
    advance = original.GithubPollTests.advance
    run_call = original.GithubPollTests.run_call

    def test_all_server_delays_are_capped_and_secondary_reset_is_ignored(self):
        for headers, expected in (
            ({"retry-after": "99999999999"}, 3600),
            ({"retry-after": "1e308"}, 3600),
            ({"retry-after": "Thu, 01 Jan 2099 00:00:00 GMT"}, 3600),
            ({"x-ratelimit-reset": "4102444800", "x-ratelimit-remaining": "0"}, 3600),
            ({"x-ratelimit-reset": "4500", "x-ratelimit-remaining": "4900"}, None),
            ({"x-ratelimit-reset": "4500"}, None),
            ({"retry-after": "-5"}, None),
            ({"retry-after": "inf"}, None),
        ):
            with self.subTest(headers=headers):
                self.assertEqual(g.retry_delay(headers, 1000), expected)

    def test_oversized_saved_cooldown_recovers_without_manual_edit(self):
        self.state.mkdir()
        (self.state / "github.json").write_text(json.dumps({"until": 1e20}))
        self.assertEqual(self.run_call("gh: forbidden", 1).returncode, 1)
        self.assertEqual(
            json.loads((self.state / "github.json").read_text())["until"], 0
        )
        self.assertEqual(self.run_call().returncode, 0)

    def test_idle_calls_have_no_spacing_tax_and_short_calls_share_remainder(self):
        self.run_call()
        self.assertEqual(self.now, 1000)
        self.advance(1)
        self.run_call()
        self.assertEqual(self.now, 1003)
        self.advance(30)
        self.run_call()
        self.assertEqual(self.now, 1033)

    def test_corrupt_state_is_quarantined_then_recovers(self):
        for content in (
            b"broken",
            b"[]",
            b"",
            b"\xff",
            b'{"until":"x"}',
            b'{"until":1e999}',
            b'{"failures":-1}',
            json.dumps(
                {"callers": {Path(sys.argv[0]).name: {"started": "bad", "limited": 0}}}
            ).encode(),
        ):
            with self.subTest(content=content):
                self.state.mkdir(exist_ok=True)
                path = self.state / "github.json"
                path.write_bytes(content)
                with self.assertRaises(OSError) as caught:
                    self.run_call()
                self.assertIn(str(path), str(caught.exception))
                self.assertTrue(list(self.state.glob("github.json.corrupt*")))
                self.advance(66)
                self.assertEqual(self.run_call().returncode, 0)

    def test_permission_error_names_state_path(self):
        self.state.mkdir()
        path = self.state / "github.json"
        path.write_text("{}")
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(OSError) as caught:
                self.run_call()
        self.assertIn(str(path), str(caught.exception))

    def test_gone_pr_follows_misses_while_new_request_is_reported(self):
        gone = "https://github.com/a/b/pull/1"
        new = "https://github.com/a/b/pull/2"
        for service, missing in ((False, False), (False, True), (True, True)):
            prs = load_prs(service)
            before = {gone: {"title": "GONE-PR"}}
            entry = {
                "title": "NEW-PR",
                "author": "author",
                "draft": False,
                "conflict": False,
                "ci": "SUCCESS",
            }
            failure = RuntimeError("GraphQL: Could not resolve to a Repository")
            with patch.object(
                prs,
                "gh",
                side_effect=failure
                if missing
                else lambda *a: '{"data":{"resource":null}}',
            ):
                output = []
                for _ in range(5):
                    lines, kept = prs.changes(
                        before, {new: entry}, *([] if service else ["me"])
                    )
                    output += lines
                    before = {new: entry, **kept}
                self.assertTrue(
                    any("new:" in line and "NEW-PR" in line for line in output)
                )
                self.assertTrue(
                    any("cleared:" in line and "GONE-PR" in line for line in output)
                )
                self.assertNotIn(gone, before)

    def test_busy_grace_keeps_audit_and_pull_evidence_without_first_event(self):
        before = {"sources": {"github": ["saved"]}, "unreadable": {}}
        raw = {
            "sources": {},
            "unreadable": {"github": "READFAIL github: holder pid=7 age=1s"},
            "deferred": ["github"],
        }
        after, events = audit.reduce(before, raw)
        self.assertEqual(events, [])
        self.assertEqual(after["sources"]["github"], ["saved"])
        for _ in range(2):
            after, events = audit.reduce(after, raw)
        self.assertEqual(len(events), 1)
        raw["unreadable"]["github"] = "READFAIL github: holder pid=8 age=9s"
        after, events = audit.reduce(after, raw)
        self.assertEqual(events, [])
        before = {"prs": {}, "unreadable": []}
        raw = {
            "prs": {},
            "departed": {},
            "failed": [],
            "unreadable": ["pr:a/b#1"],
            "deferred": ["pr:a/b#1"],
            "busy": {"pr:a/b#1": "holder pid=7 age=1s"},
        }
        after, events = watchers.reduce_pulls(before, raw)
        self.assertEqual(events, [])
        for _ in range(2):
            after, events = watchers.reduce_pulls(after, raw)
        self.assertEqual(len(events), 1)
        self.assertIn("holder pid=7 age=1s", events[0])
        seeded, _ = watchers.reduce_pulls(None, raw)
        self.assertEqual(seeded["deferred_passes"]["pr:a/b#1"], 1)

    def test_cooldown_details_do_not_refire_audit_alert(self):
        self.assertEqual(
            audit.readfail_events({"github": "until 10"}, {"github": "until 20"}), []
        )

    def fake_cli(self, body):
        folder = Path(self.tmp.name) / "bin"
        folder.mkdir(exist_ok=True)
        cli = folder / "gh"
        cli.write_text(f"#!{sys.executable}\n{body}\n")
        cli.chmod(0o755)
        return dict(os.environ, PATH=f"{folder}:{os.environ['PATH']}")

    def test_shell_cli_exit_status_and_45_second_timeout(self):
        env = self.fake_cli(
            "import sys; print('gh: denied', file=sys.stderr); sys.exit(7)"
        )
        result = subprocess.run(
            [sys.executable, str(Path(g.__file__)), "api", "fixture"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 7)
        self.assertNotIn("Traceback", result.stderr)
        code = (
            "import github_poll as g,subprocess; from unittest.mock import patch\n"
            + "\n".join(
                [
                    "def run(*args, **kwargs):",
                    " assert kwargs['timeout'] == 45, kwargs",
                    " assert '--supervise' in args[0], args",
                    " raise subprocess.TimeoutExpired(args[0], 45)",
                    "with patch.object(g, 'capture', side_effect=run):",
                    " raise SystemExit(g.main())",
                ]
            )
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=dict(env, PYTHONPATH=str(ROOT / "lib")),
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("45", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_list_and_dispatch_errors_are_one_line(self):
        env = dict(
            self.fake_cli(
                "import sys; print('gh: failed read', file=sys.stderr); sys.exit(1)"
            ),
            ORCH_CONFIG=str(Path(self.tmp.name) / "none.toml"),
            ORCH_PROVIDERS="codex",
            ORCH_CODEX_WORKER_MODEL="test-worker",
        )
        for mode in ("list", "dispatch-queue"):
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills/review-prs/scripts/prs.py"),
                    mode,
                    "--repo",
                    "a/b",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_busy_wait_is_bounded_and_cooldown_skips_wait(self):
        self.state.mkdir()
        (self.state / "holder.json").write_text(
            json.dumps({"pid": 123, "since": self.now - 61})
        )
        with patch.object(g.fcntl, "flock", side_effect=BlockingIOError):
            with self.assertRaises(g.Busy) as caught:
                self.run_call()
            self.assertEqual(self.now, 1015)
            self.assertIn("pid=123", str(caught.exception))
            self.assertIn("age=76.0s", str(caught.exception))
            self.assertIn("stuck", str(caught.exception))
            before = self.now
            with self.assertRaises(g.Busy):
                g.run(["gh"], lambda env, fds: None, timeout=2)
            self.assertAlmostEqual(self.now - before, 1.75)
            (self.state / "github.json").write_text(
                json.dumps({"until": self.now + 60})
            )
            with self.assertRaises(g.Cooldown):
                self.run_call()
            self.assertEqual(self.now, 1016.75)

    def test_busy_collectors_preserve_grace_marker(self):
        busy = g.Busy("GitHub poll busy; holder pid=7 age=1s")
        found = {"repos": ["a/b"], "clones": []}
        with (
            patch.object(audit.discovery, "repos", return_value=found),
            patch.object(audit, "workspace_lines", return_value=[]),
            patch.object(audit, "github_lines", side_effect=busy),
            patch.object(audit, "slot_sources", return_value=({}, {})),
        ):
            raw = audit.collect(lambda *args: "")
            self.assertIn("github", raw["deferred"])
        before = {"prs": {"a/b#1": {"title": "saved", "lines": {}}}}
        with (
            patch.object(watchers.discovery, "open_work", return_value={}),
            patch.object(watchers.discovery, "repos", return_value=found),
            patch.object(watchers.audit, "fetch_prs", return_value=[]),
            patch.object(watchers, "pull_owners", return_value={}),
        ):
            raw = watchers.collect_pulls(
                lambda *args: (_ for _ in ()).throw(busy), before
            )
            self.assertIn("pr:a/b#1", raw["deferred"])
            self.assertIn("pid=7", raw["busy"]["pr:a/b#1"])

    def real_clock(self):
        self.sleep.stop()
        self.clock.stop()
        self.monotonic.stop()

    def test_contention_waits_for_release_within_call_timeout(self):
        self.real_clock()
        self.state.mkdir()
        ready = Path(self.tmp.name) / "ready"
        code = f"import fcntl,time; from pathlib import Path; f=open({str(self.state / 'github.lock')!r}, 'w'); fcntl.flock(f,fcntl.LOCK_EX); Path({str(ready)!r}).touch(); time.sleep(0.5)"
        owner = subprocess.Popen([sys.executable, "-c", code])
        try:
            for _ in range(200):
                if ready.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            start = time.monotonic()
            result = g.run(
                ["gh"],
                lambda env, fds: subprocess.CompletedProcess(["gh"], 0, "ok", ""),
                timeout=3,
            )
            self.assertEqual(result.returncode, 0)
            self.assertGreater(time.monotonic() - start, 0.2)
            self.assertLess(time.monotonic() - start, 2)
        finally:
            if owner.poll() is None:
                owner.terminate()
            owner.wait(timeout=2)

    def test_orphan_hard_deadline_survives_wrapper_death(self):
        self.real_clock()
        pidfile = Path(self.tmp.name) / "pid"
        env = self.fake_cli(
            f"import os,time,signal\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)"
        )
        code = "import github_poll as g, subprocess; g.run(['gh'], lambda env,fds: subprocess.run(g.supervised(['gh'],env), env=env,pass_fds=fds,capture_output=True,text=True),timeout=2.2)"
        owner = subprocess.Popen(
            [sys.executable, "-c", code],
            env=dict(env, PYTHONPATH=str(ROOT / "lib")),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child = None
        try:
            for _ in range(200):
                if pidfile.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(pidfile.exists())
            child = int(pidfile.read_text())
            owner.terminate()
            owner.wait(timeout=2)
            time.sleep(2.4)
            import fcntl

            with (self.state / "github.lock").open() as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            holder = json.loads((self.state / "holder.json").read_text())
            self.assertFalse(
                json.loads((self.state / "github.json").read_text()).get(
                    "pending_start"
                )
            )
            self.assertEqual(holder["gh_pid"], child)
            self.assertEqual(
                json.loads((self.state / "github.json").read_text())["last_monotonic"],
                holder["monotonic"],
            )
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)
            child = None
        finally:
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=2)
            if child:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_service_stop_terminates_the_owned_fake_gh(self):
        self.real_clock()
        pidfile = Path(self.tmp.name) / "service.pid"
        env = self.fake_cli(
            f"import os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)"
        )
        owner = subprocess.Popen(
            [sys.executable, "-c", "import watcher_runtime as r; r.run(['gh'])"],
            env=dict(env, PYTHONPATH=str(ROOT / "coordinator/watchers")),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child = None
        try:
            for _ in range(200):
                if pidfile.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(pidfile.exists())
            child = int(pidfile.read_text())
            owner.terminate()
            owner.wait(timeout=2)
            time.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)
            child = None
        finally:
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=2)
            if child:
                try:
                    os.kill(child, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def test_six_callers_complete_three_read_passes_without_starvation(self):
        self.real_clock()
        env = self.fake_cli("import time; time.sleep(0.03)")
        code = "import github_poll as g,subprocess; g.SPACING=0.01\nfor _ in range(3):\n r=g.run(['gh'], lambda env,fds: subprocess.run(g.supervised(['gh'],env), env=env,pass_fds=fds,capture_output=True,text=True),timeout=10)\n assert r.returncode == 0\n"
        clients = [
            subprocess.Popen(
                [sys.executable, "-c", code],
                env=dict(env, PYTHONPATH=str(ROOT / "lib")),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(6)
        ]
        try:
            results = [client.communicate(timeout=20) for client in clients]
            failed = sum(client.returncode != 0 for client in clients)
            self.assertEqual(failed, 0, results)
        finally:
            for client in clients:
                if client.poll() is None:
                    client.terminate()
                    client.wait(timeout=2)

    def test_busy_does_not_clear_an_existing_read_failure(self):
        before = {
            "sources": {"github": ["saved"]},
            "unreadable": {"github": "READFAIL github: offline"},
        }
        raw = {
            "sources": {},
            "unreadable": {"github": "READFAIL github: holder pid=7 age=1s"},
            "deferred": ["github"],
        }
        after, events = audit.reduce(before, raw)
        self.assertEqual(events, [])
        self.assertIn("github", after["reported_unreadable"])
        before = {"prs": {}, "unreadable": ["owners"]}
        raw = {
            "prs": {},
            "departed": {},
            "failed": [],
            "unreadable": ["owners"],
            "deferred": ["owners"],
            "busy": {"owners": "holder pid=7 age=1s"},
        }
        after, events = watchers.reduce_pulls(before, raw)
        self.assertEqual(events, [])
        self.assertIn("owners", after["reported_unreadable"])

    def test_metadata_latency_cannot_start_a_call_past_its_deadline(self):
        original_write = g.write_state

        def slow(path, state):
            if path.name == "github.json":
                self.advance(2)
            original_write(path, state)

        with patch.object(g, "write_state", side_effect=slow):
            with self.assertRaises(g.Busy):
                g.run(
                    ["gh"],
                    lambda env, fds: (_ for _ in ()).throw(
                        AssertionError("late launch")
                    ),
                    timeout=1,
                )

    def test_supervisor_deadline_also_bounds_metadata_io(self):
        self.real_clock()
        pidfile = Path(self.tmp.name) / "metadata.pid"
        env = self.fake_cli(
            f"import os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)"
        )
        holder = Path(self.tmp.name) / "holder.json"
        code = "import github_poll as g,time; g.write_state=lambda *a:time.sleep(3); raise SystemExit(g.supervise(time.monotonic()+0.8,['gh']))"
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                env=dict(
                    env, PYTHONPATH=str(ROOT / "lib"), GITHUB_POLL_HOLDER=str(holder)
                ),
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 124, result.stderr)
        finally:
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def test_unreadable_state_file_is_quarantined_without_chmod(self):
        if os.geteuid() == 0:
            self.skipTest("root can read mode-000 files")
        self.state.mkdir()
        path = self.state / "github.json"
        path.write_text("{}")
        path.chmod(0)
        with self.assertRaises(OSError):
            self.run_call()
        self.assertTrue(list(self.state.glob("github.json.corrupt*")))
        self.advance(66)
        self.assertEqual(self.run_call().returncode, 0)
