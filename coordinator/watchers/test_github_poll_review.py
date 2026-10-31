"""Review-cycle regressions for admission races, interruption, and source grace."""

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
import test_github_poll_round2 as fixtures
import watchers

g = fixtures.g
ROOT = fixtures.ROOT


class ReviewTests(IsolatedTestCase):
    setUp = fixtures.RoundTwoTests.setUp
    advance = fixtures.RoundTwoTests.advance
    run_call = fixtures.RoundTwoTests.run_call
    real_clock = fixtures.RoundTwoTests.real_clock
    fake_cli = fixtures.RoundTwoTests.fake_cli

    def test_cooldown_published_before_lock_acquisition_is_respected(self):
        self.state.mkdir()
        path = self.state / "github.json"

        def publication(_path):
            path.write_text(json.dumps({"until": self.now + 60}))
            return False

        with patch.object(g, "active_cooldown", side_effect=publication):
            with self.assertRaises(g.Cooldown):
                self.run_call()
        self.assertEqual(json.loads(path.read_text())["until"], 1060)

    def test_ctrl_c_stops_the_private_cli_group_in_every_capture_adapter(self):
        self.real_clock()
        pidfile = Path(self.tmp.name) / "interrupt.pid"
        env = dict(
            self.fake_cli(
                f"import os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)"
            ),
            PYTHONPATH=str(ROOT / "coordinator/watchers"),
        )
        commands = (
            [sys.executable, str(Path(g.__file__)), "api", "fixture"],
            [
                sys.executable,
                str(ROOT / "skills/review-prs/scripts/prs.py"),
                "list",
                "--repo",
                "a/b",
            ],
            [sys.executable, "-c", "import focus_check as f; f.command('gh')"],
        )
        for command in commands:
            with self.subTest(command=command):
                pidfile.unlink(missing_ok=True)
                owner = subprocess.Popen(
                    command,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                child = None
                try:
                    for _ in range(500):
                        if pidfile.exists():
                            break
                        time.sleep(0.01)
                    self.assertTrue(pidfile.exists())
                    child = int(pidfile.read_text())
                    owner.send_signal(signal.SIGINT)
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

    def test_legacy_busy_read_preserves_saved_evidence_through_audit_grace(self):
        pr = json.loads((watchers.ROOT / "fixtures/audit-pr.json").read_text())
        before = {
            "sources": {"github": [], "herdr": []},
            "legacy": {"saved": {"line": "LEGACY saved", "head": "old"}},
            "unreadable": {},
        }
        with (
            patch.object(
                audit.discovery, "repos", return_value={"repos": ["a/b"], "clones": []}
            ),
            patch.object(audit, "fetch_prs", return_value=[pr]),
            patch.object(
                audit, "repo_settings", side_effect=g.Busy("holder pid=7 age=1s")
            ),
            patch.object(audit, "pr_lines", return_value=[]),
            patch.object(audit, "workspace_lines", return_value=[]),
            patch.object(audit, "slot_sources", return_value=({}, {})),
        ):
            raw = audit.collect(lambda *a: "", before=before)
        after, events = audit.reduce(before, raw)
        self.assertEqual(after["legacy"], before["legacy"])
        self.assertEqual(events, [])
        self.assertIn("github", raw["deferred"])

    def test_stacked_settings_contention_uses_the_settings_source_identity(self):
        from test_pulls import pr

        row = pr(7)
        row["baseRefName"] = "release"
        key = row["repository"]["nameWithOwner"] + "#7"
        with (
            patch.object(watchers.discovery, "open_work", return_value={}),
            patch.object(
                watchers.discovery,
                "repos",
                return_value={
                    "repos": [row["repository"]["nameWithOwner"]],
                    "clones": [],
                },
            ),
            patch.object(watchers.audit, "fetch_prs", return_value=[row]),
            patch.object(watchers, "pull_owners", return_value={}),
            patch.object(
                watchers.audit,
                "repo_settings",
                side_effect=g.Busy("holder pid=7 age=1s"),
            ),
        ):
            raw = watchers.collect_pulls(lambda *a: "", {"prs": {}})
        self.assertIn("settings:" + key, raw["unreadable"])
        self.assertIn("settings:" + key, raw["deferred"])
        after, events = watchers.reduce_pulls({"prs": {}, "unreadable": []}, raw)
        self.assertFalse(any("READFAIL" in line for line in events), events)

    def test_entire_admission_and_final_state_write_obey_caller_deadline(self):
        self.real_clock()
        for phase in ("admission", "result"):
            with self.subTest(phase=phase):
                invoked = False
                original_write = g.write_state

                def slow(path, state):
                    if (phase == "admission" and not invoked) or (
                        phase == "result" and invoked
                    ):
                        time.sleep(3)
                    original_write(path, state)

                def invoke(env, fds):
                    nonlocal invoked
                    invoked = True
                    return subprocess.CompletedProcess(["gh"], 0, "ok", "")

                started = time.monotonic()
                with patch.object(g, "write_state", side_effect=slow):
                    with self.assertRaises(g.Deadline):
                        g.run(["gh"], invoke, timeout=2.1 if phase == "result" else 0.2)
                self.assertLess(
                    time.monotonic() - started, 2.6 if phase == "result" else 0.6
                )

    def test_parent_deadline_preserves_earlier_and_later_watcher_alarms(self):
        self.real_clock()
        for outer, expected_fired in ((0.05, True), (0.8, False)):
            with self.subTest(outer=outer):
                fired = []

                def previous(signum, frame):
                    fired.append(True)
                    raise TimeoutError("outer deadline")

                original_handler = signal.signal(signal.SIGALRM, previous)
                original_timer = signal.setitimer(signal.ITIMER_REAL, outer)
                try:
                    with patch.object(
                        g, "write_state", side_effect=lambda *a: time.sleep(0.5)
                    ):
                        with self.assertRaises(
                            TimeoutError if expected_fired else g.Deadline
                        ):
                            g.run(["gh"], lambda env, fds: None, timeout=0.1)
                    self.assertEqual(bool(fired), expected_fired)
                    self.assertIs(signal.getsignal(signal.SIGALRM), previous)
                    if not expected_fired:
                        self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0.4)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, original_handler)
                    if original_timer[0]:
                        signal.setitimer(signal.ITIMER_REAL, *original_timer)

    def test_signals_during_spawn_clean_up_before_process_assignment(self):
        self.real_clock()
        env = self.fake_cli("import time; time.sleep(60)")
        real_spawn = subprocess.Popen
        for number, expected in (
            (signal.SIGINT, KeyboardInterrupt),
            (signal.SIGALRM, g.Deadline),
        ):
            with self.subTest(signal=number):
                spawned = []
                (self.state / "github.json").unlink(missing_ok=True)

                def interrupted(*args, **kwargs):
                    process = real_spawn(*args, **kwargs)
                    spawned.append(process)
                    signal.raise_signal(number)
                    return process

                try:
                    with (
                        patch.dict(os.environ, PATH=env["PATH"]),
                        patch.object(g.subprocess, "Popen", side_effect=interrupted),
                    ):
                        with self.assertRaises(expected):
                            g.run(
                                ["gh"],
                                lambda env, fds: g.capture(
                                    g.supervised(["gh"], env),
                                    env=env,
                                    pass_fds=fds,
                                    timeout=5,
                                ),
                                timeout=5,
                            )
                    self.assertTrue(spawned)
                    self.assertIsNotNone(spawned[0].poll())
                finally:
                    for process in spawned:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=2)

    def test_admission_legacy_rechecks_never_clear_saved_hits(self):
        pr = json.loads((watchers.ROOT / "fixtures/audit-pr.json").read_text())
        key = pr["repository"]["nameWithOwner"] + "#" + str(pr["number"])
        errors = (g.Busy("busy"), g.Cooldown("cooldown"))
        for error, saved_key in (
            (error, saved_key)
            for error in errors
            for saved_key in (
                key,
                pr["repository"]["nameWithOwner"] + "@" + pr["baseRefName"],
            )
        ):
            with self.subTest(error=type(error).__name__, saved_key=saved_key):
                before = {
                    "sources": {"github": [], "herdr": []},
                    "legacy": {saved_key: {"line": "LEGACY saved", "head": "old"}},
                    "unreadable": {},
                }
                with (
                    patch.object(
                        audit.discovery,
                        "repos",
                        return_value={"repos": ["a/b"], "clones": []},
                    ),
                    patch.object(audit, "fetch_prs", return_value=[pr]),
                    patch.object(audit, "repo_settings", side_effect=error),
                    patch.object(audit, "pr_lines", return_value=[]),
                    patch.object(audit, "workspace_lines", return_value=[]),
                    patch.object(audit, "slot_sources", return_value=({}, {})),
                ):
                    raw = audit.collect(lambda *a: "", before=before)
                after, events = audit.reduce(before, raw)
                self.assertEqual(after["legacy"], before["legacy"])
                self.assertIn("github", raw["unreadable"])
                self.assertFalse(any("CLEARED" in line for line in events), events)
        for error in (g.Busy("busy"), g.Cooldown("cooldown")):
            with patch.object(audit, "repo_settings", side_effect=error):
                with self.assertRaises(type(error)):
                    audit.legacy_entries(lambda *a: "", [pr], {})

    def test_readfail_diagnostics_preserve_one_source_and_holder_text(self):
        state = {"prs": {}, "unreadable": []}
        raw = {
            "prs": {},
            "departed": {},
            "failed": [],
            "unreadable": ["pr:a/b#1"],
            "deferred": ["pr:a/b#1"],
            "busy": {"pr:a/b#1": "GitHub poll busy; holder pid=7 age=5s"},
        }
        for _ in range(3):
            state, events = watchers.reduce_pulls(state, raw)
        facts = watchers.alert_policy.readfail_facts("pulls", events[0])
        alerts = [fact for fact in facts if fact["kind"] == "alert"]
        self.assertEqual([fact["key"] for fact in alerts], ["pulls:readfail:pr:a/b#1"])
        self.assertIn("pid=7", alerts[0]["text"])
        self.assertIn("age=5s", alerts[0]["text"])
        self.assertEqual(facts[-1]["keep"], {"pulls:readfail:pr:a/b#1"})

    def test_readfail_recovery_has_no_diagnostic_suffix_during_other_grace(self):
        before = {
            "prs": {},
            "unreadable": ["owners"],
            "reported_unreadable": ["owners"],
        }
        raw = {
            "prs": {},
            "departed": {},
            "failed": [],
            "unreadable": ["settings:a/b#1"],
            "deferred": ["settings:a/b#1"],
            "busy": {"settings:a/b#1": "holder pid=7 age=1s"},
        }
        after, events = watchers.reduce_pulls(before, raw)
        self.assertEqual(events, ["PULLS READFAIL cleared"])
        self.assertEqual(
            watchers.alert_policy.readfail_facts("pulls", "PULLS READFAIL cleared "),
            [watchers.alert_policy.clear("pulls:readfail:")],
        )
        self.assertEqual(
            watchers.alert_policy.readfail_facts("pulls", events[0]),
            [watchers.alert_policy.clear("pulls:readfail:")],
        )

    def test_runtime_stop_during_spawn_terminates_its_owned_command(self):
        import watcher_runtime as runtime

        self.real_clock()
        real_spawn = subprocess.Popen
        for number, expected in (
            (signal.SIGTERM, SystemExit),
            (signal.SIGINT, KeyboardInterrupt),
        ):
            with self.subTest(signal=number):
                spawned = []

                def interrupted(*args, **kwargs):
                    process = real_spawn(*args, **kwargs)
                    spawned.append(process)
                    signal.raise_signal(number)
                    return process

                try:
                    with patch.object(
                        runtime.subprocess, "Popen", side_effect=interrupted
                    ):
                        with self.assertRaises(expected):
                            runtime.run(
                                [sys.executable, "-c", "import time;time.sleep(60)"],
                                timeout=2,
                            )
                    self.assertIsNotNone(spawned[0].poll())
                finally:
                    for process in spawned:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=2)

    def test_malformed_counters_cannot_bypass_rate_limit_persistence(self):
        for field, bad in (
            ("limited", "bad"),
            ("limited", None),
            ("limited", float("inf")),
            ("limited", -1),
            ("started", -1),
        ):
            with self.subTest(field=field, value=bad):
                self.state.mkdir(exist_ok=True)
                counter = {"started": 0, "limited": 0}
                counter[field] = bad
                path = self.state / "github.json"
                path.write_text(
                    json.dumps({"callers": {Path(sys.argv[0]).name: counter}})
                )
                calls = []

                def invoke(env, fds):
                    calls.append(True)
                    return subprocess.CompletedProcess(
                        ["gh"],
                        1,
                        "",
                        "< HTTP/2.0 429 Too Many Requests\n< Retry-After: 120\ngh: rate limit exceeded",
                    )

                with self.assertRaises(g.Cooldown):
                    g.run(["gh"], invoke)
                self.assertEqual(calls, [])
                self.assertTrue(list(self.state.glob("github.json.corrupt*")))
                self.advance(66)
                self.assertEqual(g.run(["gh"], invoke).returncode, 1)
                self.assertEqual(calls, [True])
                self.assertEqual(json.loads(path.read_text())["until"], self.now + 122)
                with self.assertRaises(g.Cooldown):
                    g.run(["gh"], invoke)
                self.assertEqual(calls, [True])

    def test_supervisor_spawn_signals_reap_the_owned_child(self):
        self.real_clock()
        real_spawn = subprocess.Popen

        def terminated(signum, frame):
            raise SystemExit(128 + signum)

        def expired(signum, frame):
            raise subprocess.TimeoutExpired(["gh"], 0)

        previous_alarm = signal.signal(signal.SIGALRM, expired)
        previous_term = signal.signal(signal.SIGTERM, terminated)
        try:
            for number, expected in (
                (signal.SIGTERM, SystemExit),
                (signal.SIGINT, KeyboardInterrupt),
                (signal.SIGALRM, None),
            ):
                with self.subTest(signal=number):
                    spawned = []

                    def interrupted(*args, **kwargs):
                        process = real_spawn(*args, **kwargs)
                        spawned.append(process)
                        signal.raise_signal(number)
                        return process

                    try:
                        with patch.object(
                            g.subprocess, "Popen", side_effect=interrupted
                        ):
                            command = [
                                sys.executable,
                                "-c",
                                "import time;time.sleep(60)",
                            ]
                            if expected is None:
                                self.assertEqual(
                                    g.supervise(time.monotonic() + 2, command), 124
                                )
                            else:
                                with self.assertRaises(expected):
                                    g.supervise(time.monotonic() + 2, command)
                        self.assertIsNotNone(spawned[0].poll())
                    finally:
                        for process in spawned:
                            if process.poll() is None:
                                os.kill(process.pid, signal.SIGKILL)
                                process.wait(timeout=2)
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGALRM, previous_alarm)

    def test_unpublished_spawn_uses_completion_or_pending_recovery(self):
        for abandoned in (False, True):
            with self.subTest(abandoned=abandoned):
                path = self.state / "github.json"
                path.unlink(missing_ok=True)

                def invoke(env, fds):
                    self.advance(2.5)
                    if abandoned:
                        raise OSError("supervisor exited before timestamp publication")
                    return subprocess.CompletedProcess(["gh"], 124, "", "deadline")

                if abandoned:
                    with self.assertRaises(OSError):
                        g.run(["gh"], invoke)
                    self.assertTrue(json.loads(path.read_text()).get("pending_start"))
                else:
                    g.run(["gh"], invoke)
                    self.assertFalse(json.loads(path.read_text()).get("pending_start"))
                completed = self.elapsed
                self.run_call()
                self.assertGreaterEqual(self.elapsed - completed, 3)
                self.assertFalse(json.loads(path.read_text()).get("pending_start"))

    def test_wait_deadline_crossing_remains_a_busy_read(self):
        sleeps = []

        def wait(seconds):
            if seconds < 0:
                raise ValueError("sleep length must be non-negative")
            sleeps.append(seconds)

        with (
            patch.object(g.fcntl, "flock", side_effect=BlockingIOError),
            patch.object(g.time, "monotonic", side_effect=[1000.74, 1000.76, 1000.76]),
            patch.object(g.time, "sleep", side_effect=wait),
        ):
            with self.assertRaises(g.Busy):
                g._run(["gh"], lambda *args: self.fail("invoked"), 1, 1000)
        self.assertEqual(sleeps, [0])

    def test_short_remaining_budget_is_busy_without_invoking_cli(self):
        original = g.write_state

        def slow(path, state):
            original(path, state)
            if path.name == "github.json":
                self.advance(8.5)

        with patch.object(g, "write_state", side_effect=slow):
            with self.assertRaises(g.Busy):
                g.run(["gh"], lambda *args: self.fail("CLI started"), timeout=10)

    def test_call_deadline_is_an_oserror_but_pass_deadline_stays_timeout(self):
        self.real_clock()
        with patch.object(g, "write_state", side_effect=lambda *args: time.sleep(1)):
            with self.assertRaises(OSError) as caught:
                g.run(["gh"], lambda *args: None, timeout=0.05)
        self.assertNotIsInstance(caught.exception, TimeoutError)

        def command(*args):
            if args[0] == "gh":
                raise caught.exception
            return "feature"

        with (
            patch.object(watchers, "command", side_effect=command),
            patch.object(watchers, "stopped_operation", return_value=False),
            patch.object(watchers, "github_remotes", return_value=["a/b"]),
        ):
            self.assertIsNone(watchers.open_pr({"cwd": "/repo"}))
        previous_handler = signal.signal(
            signal.SIGALRM, lambda *args: (_ for _ in ()).throw(TimeoutError("pass"))
        )
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        try:
            with self.assertRaises(TimeoutError):
                g.run(["gh"], lambda *args: time.sleep(1), timeout=5)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)

    def test_unreached_departures_share_busy_grace(self):
        keys = [f"a/b#{n}" for n in range(8)]
        before = {
            "prs": {key: {"misses": 1, "title": key, "lines": {}} for key in keys},
            "unreadable": [],
        }
        elapsed = [0]

        def command(*args):
            elapsed[0] += 15
            raise g.Busy("holder pid=7 age=1s")

        with (
            patch.object(watchers.discovery, "open_work", return_value={}),
            patch.object(
                watchers.discovery,
                "repos",
                return_value={"repos": ["a/b"], "clones": []},
            ),
            patch.object(watchers.audit, "fetch_prs", return_value=[]),
            patch.object(watchers, "pull_owners", return_value={}),
        ):
            raw = watchers.collect_pulls(command, before, clock=lambda: elapsed[0])
        self.assertEqual(set(raw["deferred"]), {"pr:" + key for key in keys})
        after, events = watchers.reduce_pulls(before, raw)
        self.assertFalse(any("READFAIL" in line for line in events), events)
        self.assertEqual(set(after["prs"]), set(keys))

    def test_alarm_during_cleanup_restores_outer_handler_and_timer(self):
        self.real_clock()
        original_timer = signal.setitimer
        previous_handler = signal.signal(signal.SIGALRM, lambda *args: None)
        previous_timer = original_timer(signal.ITIMER_REAL, 50)
        fired = False

        def timer(which, seconds, *args):
            nonlocal fired
            if seconds == 0 and not fired:
                fired = True
                signal.raise_signal(signal.SIGALRM)
            return original_timer(which, seconds, *args)

        outer = signal.getsignal(signal.SIGALRM)
        try:
            with patch.object(g.signal, "setitimer", side_effect=timer):
                try:
                    with g.call_deadline(5):
                        pass
                except g._Expired:
                    pass
            self.assertIs(signal.getsignal(signal.SIGALRM), outer)
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 45)
        finally:
            original_timer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0]:
                original_timer(signal.ITIMER_REAL, *previous_timer)
