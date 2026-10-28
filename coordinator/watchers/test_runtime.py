"""Regression proofs for bounded watcher execution, without host tools."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import watchers as w


class RuntimeTests(IsolatedTestCase):
    def module(self, stem):
        spec = importlib.util.spec_from_file_location(
            stem.replace("-", "_"), w.ROOT / stem
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_standalone_loops_use_default_iteration_deadlines(self):
        """Each standalone loop must retain iterations()' per-pass watchdog."""
        for stem, extra, expected in (
            ("read-alerts.py", ["--once"], "alert reader"),
            ("notify-inbox.py", [], "notifier"),
        ):
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as tmp:
                module = self.module(stem)
                calls = []
                state, cursor = Path(tmp) / "state", Path(tmp) / "cursor"
                state.mkdir()

                def iterations(*args, **kwargs):
                    calls.append((args, kwargs))
                    return iter(())

                with (
                    patch.object(module.runtime, "iterations", side_effect=iterations),
                    patch.object(
                        sys,
                        "argv",
                        [
                            stem,
                            "--state-dir",
                            str(state),
                            "--cursor",
                            str(cursor),
                            *extra,
                        ],
                    ),
                ):
                    module.main()
                self.assertEqual(calls, [((5, False, expected), {})])

    def test_pr_watch_and_follower_keep_per_pass_deadlines(self):
        prs = self.module("prs-copy.py")
        follower = self.module("feed_follow.py")
        pr_calls, follow_calls = [], []
        args = type("Args", (), {"interval": 1})()
        with (
            patch.object(
                prs.runtime,
                "deadline",
                return_value=__import__("contextlib").nullcontext(),
            ),
            patch.object(
                prs.runtime,
                "iterations",
                side_effect=lambda *a, **kw: pr_calls.append((a, kw)) or iter(()),
            ),
        ):
            prs.cmd_watch(args, "repo:fixture", "fixture")
        self.assertEqual(pr_calls, [((1,), {"label": "PR watch"})])
        with tempfile.TemporaryDirectory() as tmp:
            feed, beat = Path(tmp) / "feed", Path(tmp) / "beat"
            feed.touch()
            stream = feed.open("rb")
            try:
                with (
                    patch.object(follower, "attach", return_value=stream),
                    patch.object(
                        follower.runtime,
                        "iterations",
                        side_effect=lambda *a, **kw: (
                            follow_calls.append((a, kw)) or iter(())
                        ),
                    ),
                ):
                    follower.follow(feed, beat)
            finally:
                stream.close()
        self.assertEqual(follow_calls, [((0,), {"label": "feed follower"})])

    def test_prs_copy_gh_uses_bounded_runner(self):
        prs = self.module("prs-copy.py")
        completed = subprocess.CompletedProcess(["gh"], 0, "ok", "")
        with patch.object(prs.runtime, "run", return_value=completed) as run:
            self.assertEqual(prs.gh("api", "fixture"), "ok")
        run.assert_called_once_with(["gh", "api", "fixture"])

    def test_timeout_stops_descendant_with_inherited_pipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker, forked, armed = (
                Path(tmp) / name for name in ("survived", "forked", "armed")
            )
            script = (
                "import os,time,pathlib; time.sleep(0.2); child=os.fork()\n"
                "if child == 0:\n"
                f"    pathlib.Path({str(forked)!r}).touch()\n"
                f"    while not pathlib.Path({str(armed)!r}).exists(): time.sleep(0.01)\n"
                "    time.sleep(0.2)\n"
                f"    pathlib.Path({str(marker)!r}).touch()\n"
                "else: time.sleep(2)\n"
            )
            probe = subprocess.Popen([sys.executable, "-c", "pass"])
            probe.wait(timeout=2)
            process_class = type(probe)
            communicate = process_class.communicate

            def after_fork(process, *args, **kwargs):
                end = time.monotonic() + 3
                while not forked.exists() and time.monotonic() < end:
                    time.sleep(0.01)
                self.assertTrue(
                    forked.exists(), "fixture must fork before timeout starts"
                )
                armed.touch()
                return communicate(process, *args, **kwargs)

            with (
                patch.object(w.runtime, "COMMAND_TIMEOUT", 0.05),
                patch.object(process_class, "communicate", after_fork),
            ):
                with self.assertRaises(subprocess.TimeoutExpired):
                    w.command(sys.executable, "-c", script)
            time.sleep(0.5)
            self.assertFalse(
                marker.exists(), "timed-out command left its descendant running"
            )

    def test_command_timeout_publishes_failure_and_heartbeat(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(w.runtime, "COMMAND_TIMEOUT", 0.05),
        ):
            base = Path(tmp)

            def collector(before):
                return w.command(sys.executable, "-c", "import time; time.sleep(1)")

            for _ in range(3):
                line = w.cycle(base, "context", collector)
            self.assertIn("TimeoutExpired", line)
            self.assertTrue((base / "context.heartbeat").exists())

    def child(self, script, env=None):
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=2,
            env=env,
        )

    def test_iteration_watchdog_exits_on_fifo_read_without_children(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / ".claude/context-usage"
            usage.mkdir(parents=True)
            os.mkfifo(usage / "blocked.tokens")
            env = dict(os.environ, HOME=tmp)
            result = self.child(
                "import watchers as w; import watcher_runtime as r\n"
                "for _ in r.iterations(5, False, 'context', budget=0.1):\n"
                "    w.session_usage('blocked')\n",
                env,
            )
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertIn("watcher deadline exceeded: context", result.stderr)
        self.assertIn("session_usage", result.stderr)

    def test_sleep_uses_its_clock_and_chunks_waits(self):
        values = iter([100, 50, 108, 111])
        waits = []
        with (
            patch.object(
                w.runtime.time, "time", side_effect=AssertionError("wall clock")
            ),
            patch.object(w.runtime.time, "sleep", side_effect=waits.append),
        ):
            w.runtime.sleep(10, clock=lambda: next(values))
        self.assertEqual(waits, [5, 2])

    def test_loop_clock_uses_boottime_on_linux(self):
        with (
            patch.object(w.runtime.sys, "platform", "linux"),
            patch.object(w.runtime.time, "CLOCK_BOOTTIME", 7, create=True),
            patch.object(w.runtime.time, "clock_gettime", return_value=100) as clock,
        ):
            self.assertEqual(w.runtime.loop_clock(), 100)
        clock.assert_called_once_with(7)

    def test_sleep_uses_suspend_aware_clock_and_returns_after_sleep(self):
        values = iter([100, 105, 305])
        waits = []
        with (
            patch.object(w.runtime.sys, "platform", "darwin"),
            patch.object(
                w.runtime.time,
                "clock_gettime",
                side_effect=lambda clock_id: next(values),
            ) as clock_gettime,
            patch.object(
                w.runtime.time, "time", side_effect=AssertionError("wall clock")
            ),
            patch.object(w.runtime.time, "sleep", side_effect=waits.append),
        ):
            w.runtime.sleep(10)
        self.assertEqual(waits, [5])
        self.assertEqual(clock_gettime.call_args_list[0].args, (time.CLOCK_MONOTONIC,))

    def test_feed_follower_heartbeat_uses_suspend_aware_clock(self):
        follower = self.module("feed_follow.py")
        with tempfile.TemporaryDirectory() as tmp:
            feed, beat = Path(tmp) / "feed", Path(tmp) / "beat"
            feed.touch()
            beat.touch()
            stream = feed.open("rb")
            try:
                with (
                    patch.object(follower, "attach", return_value=stream),
                    patch.object(follower, "action_patterns", return_value=[]),
                    patch.object(
                        follower.runtime, "loop_clock", side_effect=[0, 60, 60]
                    ),
                    patch.object(
                        follower.runtime, "iterations", return_value=iter([None])
                    ),
                    patch.object(follower.runtime, "sleep"),
                    patch.object(follower.os, "utime") as utime,
                ):
                    follower.follow(feed, beat)
            finally:
                stream.close()
        utime.assert_called_once_with(beat)

    def test_feed_lock_has_deadline(self):
        import fcntl

        import feed_attach

        with tempfile.TemporaryDirectory() as tmp:
            feed = Path(tmp) / "feed"
            with feed.with_name(feed_attach.LOCK_NAME).open("w") as holder:
                fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(w.runtime, "LOCK_TIMEOUT", 0.05):
                    with self.assertRaisesRegex(TimeoutError, "feed lock deadline"):
                        with feed_attach.locked(feed):
                            self.fail("acquired held lock")

    def test_deadline_cancelled_after_success_and_nested_deadline_preserved(self):
        import signal

        with w.runtime.deadline(1, "outer"):
            with w.runtime.deadline(2, "inner"):
                self.assertLessEqual(signal.getitimer(signal.ITIMER_REAL)[0], 1)
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_every_service_restarts_after_watchdog_exit(self):
        import plistlib

        for path in (w.ROOT / "service/macos").glob("*.template"):
            self.assertTrue(plistlib.loads(path.read_bytes())["KeepAlive"], path.name)
        for path in (w.ROOT / "service/linux").glob("*.service.template"):
            body = path.read_text()
            unit, service = body.split("[Service]", 1)
            self.assertIn("Restart=always", body, path.name)
            self.assertIn("RestartSec=10", body, path.name)
            self.assertIn("StartLimitIntervalSec=0", unit, path.name)
            self.assertNotIn("StartLimitIntervalSec=", service, path.name)

    def test_macos_services_cap_stderr_at_restart(self):
        import plistlib

        wrapper = w.ROOT / "watcher-log-exec.sh"
        self.assertTrue(wrapper.exists())
        self.assertIn("WATCHER_STDERR_MAX_BYTES", wrapper.read_text())
        for path in (w.ROOT / "service/macos").glob("*.template"):
            values = plistlib.loads(path.read_bytes())
            args = values["ProgramArguments"]
            self.assertEqual(
                args[0],
                "@ORCH_CLAUDE_DIR@/coordinator/watchers/watcher-log-exec.sh",
                path.name,
            )
            self.assertEqual(args[1], values["StandardErrorPath"], path.name)

    def test_macos_stderr_cap_truncates_before_restarting_watcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "watcher.stderr"
            log.write_bytes(b"x" * (1024 * 1024))
            result = self.child(
                "import subprocess,sys\n"
                f"subprocess.run(['bash', {str(w.ROOT / 'watcher-log-exec.sh')!r}, {str(log)!r}, "
                "sys.executable, '-c', \"import sys; sys.stderr.write('restart')\"], check=True)\n"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_bytes(), b"restart")

    def test_macos_stderr_size_probe_failure_does_not_block_watcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, tools = Path(tmp) / "watcher.stderr", Path(tmp) / "tools"
            tools.mkdir()
            (tools / "wc").write_text("#!/bin/sh\nexit 1\n")
            (tools / "wc").chmod(0o755)
            log.write_text("previous stderr")
            env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}")
            result = self.child(
                "import subprocess,sys\n"
                f"subprocess.run(['bash', {str(w.ROOT / 'watcher-log-exec.sh')!r}, {str(log)!r}, "
                "sys.executable, '-c', \"import sys; sys.stderr.write('restart')\"], check=True, capture_output=True, text=True)\n",
                env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(log.read_text().endswith("restart"))

    def test_producer_entrypoints_arm_watchdog(self):
        for mode in w.MODES + ("all",):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                script = (
                    "import sys,time; import watchers as w; import watcher_runtime as r\n"
                    "guard=r.deadline\n"
                    "r.deadline=lambda seconds,label,**kwargs: guard(0.05,label)\n"
                    "w.collect=lambda *args: time.sleep(1)\n"
                    f"sys.argv=['watchers.py',{mode!r},'--state-dir',{tmp!r},'--scope','fixture']\n"
                    "w.main()\n"
                )
                result = self.child(script)
                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertIn("watcher deadline exceeded", result.stderr)

    def test_watchdog_can_exit_with_full_stderr_pipe(self):
        # The child fills its own stderr pipe while the parent deliberately does
        # not read it. The watchdog must still exit rather than block on its note.
        script = (
            "import os,time; import watcher_runtime as r\n"
            "os.set_blocking(2, False)\n"
            "try:\n"
            "    while True: os.write(2, b'x'*4096)\n"
            "except BlockingIOError: pass\n"
            "os.set_blocking(2, True)\n"
            "with r.deadline(0.05, 'full stderr'): time.sleep(1)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script], stderr=subprocess.PIPE
        )
        try:
            self.assertEqual(process.wait(timeout=2), 70)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            process.stderr.close()

    def test_nested_watchdog_reports_selected_deadline(self):
        for outer, inner, expected in ((0.05, 0.5, "outer"), (0.5, 0.05, "inner")):
            with self.subTest(expected=expected):
                result = self.child(
                    "import time; import watcher_runtime as r\n"
                    f"with r.deadline({outer}, 'outer'):\n"
                    f"    with r.deadline({inner}, 'inner'): time.sleep(1)\n"
                )
                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertIn(f"watcher deadline exceeded: {expected}", result.stderr)

    def test_command_error_replaces_invalid_stderr(self):
        script = "import sys; sys.stdout.buffer.write(b'out\\rline'); sys.stderr.buffer.write(bytes([255])); sys.exit(1)"
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            w.command(sys.executable, "-c", script)
        self.assertEqual(caught.exception.output, "out\rline")
        self.assertEqual(caught.exception.stderr, "\ufffd")

    def test_slow_command_pass_publishes_timeout_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Model serial command cost and deliver the OS deadline signal at a
            # fixed call boundary. No child startup/reap scheduling or short
            # wall-clock margin decides whether the soft or hard handler wins.
            result = self.child(
                "import signal,subprocess; from unittest.mock import patch; from pathlib import Path; import watchers as w; import watcher_runtime as r\n"
                "elapsed=0; calls=0; budget=w.WATCH_TIMING['audit']['soft_budget']\n"
                "def slow_command(argv):\n"
                "    global elapsed,calls\n"
                "    calls+=1; elapsed+=budget/60\n"
                "    if elapsed>=budget: signal.raise_signal(signal.SIGALRM)\n"
                "    return subprocess.CompletedProcess(argv,0,'fixture','')\n"
                "def collector(before):\n"
                "    global elapsed\n"
                "    elapsed=0\n"
                "    for _ in range(100): w.command('gh','api','fixture')\n"
                f"base=Path({tmp!r})\n"
                "with patch.object(r,'run',side_effect=slow_command):\n"
                "    for _ in range(3): line=w.cycle(base,'audit',collector)\n"
                "print(line,flush=True); print('commands',calls,flush=True)\n"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("audit read failed", result.stdout)
            self.assertIn("TimeoutError", result.stdout)
            self.assertIn("commands 180", result.stdout)
            self.assertTrue((Path(tmp) / "audit.heartbeat").exists())

    def test_swallowed_soft_timeout_still_reaches_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Deliver the soft alarm at a known point, leaving the normal hard
            # budget for failure publication even on a busy host.
            result = self.child(
                "import signal; import watchers as w; from pathlib import Path\n"
                "def collector(before):\n"
                "    try: signal.raise_signal(signal.SIGALRM)\n"
                "    except OSError: pass\n"
                "    return {'sources':{},'unreadable':{},'legacy':{}}\n"
                f"print(w.cycle(Path({tmp!r}),'audit',collector,failures=1),flush=True)\n"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("audit read failed", result.stdout)
            import json

            self.assertIsNone(
                json.loads((Path(tmp) / "audit.json").read_text())["before"]
            )

    def test_watchdog_restores_shared_stderr_blocking(self):
        import os

        with tempfile.TemporaryFile() as stream:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import time; import watcher_runtime as r\n"
                    "with r.deadline(0.05,'restore'): time.sleep(1)",
                ],
                stderr=stream,
                timeout=2,
            )
            self.assertEqual(result.returncode, 70)
            self.assertTrue(os.get_blocking(stream.fileno()))

    def test_successful_command_leaves_group_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "helper-finished"
            script = (
                "import os,time,pathlib; child=os.fork()\n"
                "if child == 0:\n"
                "    fd=os.open(os.devnull,os.O_WRONLY); os.dup2(fd,1); os.dup2(fd,2); os.close(fd)\n"
                "    time.sleep(0.2)\n"
                f"    pathlib.Path({str(marker)!r}).touch()\n"
                "else: print('ok',flush=True)\n"
            )
            self.assertEqual(
                w.runtime.run([sys.executable, "-c", script]).returncode, 0
            )
            end = time.monotonic() + 1
            while not marker.exists() and time.monotonic() < end:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "successful command helper must survive")

    def test_failed_command_stops_detached_pipe_descendant(self):
        import os
        import select
        import signal

        reader, writer = os.pipe()
        spawned = []
        original = w.runtime.subprocess.Popen

        def spawn(*args, **kwargs):
            process = original(*args, **kwargs, pass_fds=(writer,))
            spawned.append(process)
            return process

        # The descendant releases capture pipes so communicate() completes;
        # its private witness pipe stays open until group cleanup kills it.
        script = (
            "import os,signal,sys; child=os.fork()\n"
            "if child == 0:\n"
            "    fd=os.open(os.devnull,os.O_WRONLY); os.dup2(fd,1); os.dup2(fd,2); os.close(fd)\n"
            "    signal.pause()\n"
            "else:\n"
            "    sys.stdout.buffer.write(b'failed\\routput'); sys.stdout.flush()\n"
            "    sys.stderr.write('diagnostic'); sys.stderr.flush(); sys.exit(7)\n"
        )
        try:
            try:
                with patch.object(w.runtime.subprocess, "Popen", side_effect=spawn):
                    result = w.runtime.run([sys.executable, "-c", script])
            finally:
                os.close(writer)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, "failed\routput")
            self.assertEqual(result.stderr, "diagnostic")
            self.assertTrue(
                select.select([reader], [], [], 1)[0],
                "failed group leader left its descendant alive",
            )
            self.assertEqual(os.read(reader, 1), b"")
        finally:
            os.close(reader)
            for process in spawned:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    def test_group_permission_error_does_not_escape_cleanup(self):
        from types import SimpleNamespace

        with patch.object(w.runtime.os, "killpg", side_effect=PermissionError):
            w.runtime.kill_command(SimpleNamespace(pid=123))

    def test_c_backstop_runs_without_python_signal_handler(self):
        result = self.child(
            "import signal,time; import watcher_runtime as r\n"
            "r.TRACEBACK_GRACE=0.05\n"
            "with r.deadline(0.05,'C backstop'):\n"
            "    signal.setitimer(signal.ITIMER_REAL,0)\n"
            "    time.sleep(1)\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Timeout", result.stderr)
        self.assertIn("<module>", result.stderr)

    def test_spawn_alarm_cleans_every_registered_process_group_member(self):
        import os
        import select
        import signal

        for soft in (False, True):
            with self.subTest(soft=soft), tempfile.TemporaryDirectory() as tmp:
                pidfile = Path(tmp) / "spawned.pid"
                forked = Path(tmp) / "forked"
                reader, writer = os.pipe()
                command = (
                    "import os,signal; from pathlib import Path; "
                    f"forked=Path({str(forked)!r}); child=os.fork(); "
                    "signal.pause() if child else (os.fork() or (forked.touch(),signal.pause()))"
                )
                script = (
                    "import os,signal,sys,time; from pathlib import Path; from unittest.mock import patch; import watcher_runtime as r\n"
                    "original=r.subprocess.Popen\n"
                    "def spawn(*args,**kwargs):\n"
                    f"    process=original(*args,**kwargs,pass_fds=({writer},))\n"
                    f"    Path({str(pidfile)!r}).write_text(str(process.pid))\n"
                    "    deadline=time.monotonic()+1\n"
                    f"    while not Path({str(forked)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                    f"    assert Path({str(forked)!r}).exists(), 'fixture did not fork'\n"
                    "    signal.raise_signal(signal.SIGALRM)\n"
                    "    return process\n"
                    "try:\n"
                    "    with patch.object(r.subprocess,'Popen',side_effect=spawn):\n"
                    f"        with r.deadline(30,'spawn alarm',soft={soft!r}):\n"
                    f"            r.run([sys.executable,'-c',{command!r}])\n"
                    "except TimeoutError: print('soft timeout',flush=True)\n"
                )
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", script],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        pass_fds=(writer,),
                    )
                except BaseException:
                    if pidfile.exists():
                        try:
                            os.killpg(int(pidfile.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    os.close(reader)
                    raise
                finally:
                    os.close(writer)
                pid = int(pidfile.read_text())
                try:
                    self.assertEqual(
                        result.returncode, 0 if soft else 70, result.stderr
                    )
                    # EOF proves the command released its inherited witness
                    # pipe; no host process inspection or zombie polling.
                    self.assertTrue(
                        select.select([reader], [], [], 1)[0],
                        "alarm during spawn leaked its child",
                    )
                    self.assertEqual(os.read(reader, 1), b"")
                    if soft:
                        self.assertIn("soft timeout", result.stdout)
                    else:
                        self.assertIn("active command:", result.stderr)
                finally:
                    os.close(reader)
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_watchdog_logs_active_command_args(self):
        result = self.child(
            "import sys; import watcher_runtime as r\n"
            "with r.deadline(0.1,'active command'):\n"
            "    r.run([sys.executable,'-c','import time; time.sleep(1) # command-evidence'])\n"
        )
        self.assertEqual(result.returncode, 70)
        self.assertIn("command-evidence", result.stderr)

    def test_notifier_preview_flushes_before_cursor_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.child(
                "import sys,importlib.util,os; from pathlib import Path; import watchers as w\n"
                "spec=importlib.util.spec_from_file_location('inbox',w.ROOT/'notify-inbox.py')\n"
                "inbox=importlib.util.module_from_spec(spec); spec.loader.exec_module(inbox)\n"
                f"base=Path({tmp!r})\n"
                "inbox.reader.read_once=lambda *args: 'preview-event'\n"
                "w.atomic=lambda *args,**kwargs: os._exit(70)\n"
                "sys.argv=['notify-inbox.py','--state-dir',str(base),'--cursor',str(base/'cursor')]\n"
                "inbox.main()\n"
            )
        self.assertEqual(result.returncode, 70)
        self.assertIn("preview-event", result.stdout)

    def test_soft_timeout_keeps_hard_backstop_armed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.child(
                "import time; import watchers as w; from pathlib import Path\n"
                "def collector(before):\n"
                "    try: time.sleep(1)\n"
                "    except OSError: time.sleep(1)\n"
                f"w.cycle(Path({tmp!r}),'audit',collector,budget=0.05)\n"
            )
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertIn("collector", result.stderr)

    def test_no_new_command_starts_after_swallowed_soft_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Catch the soft alarm before attempting the next command.
            result = self.child(
                "import signal,sys; import watchers as w; import watcher_runtime as r; from pathlib import Path\n"
                "def collector(before):\n"
                "    try: signal.raise_signal(signal.SIGALRM)\n"
                "    except OSError: pass\n"
                "    print(r.run([sys.executable,'-c',\"print('should-not-start')\"]).stdout,flush=True)\n"
                f"print(w.cycle(Path({tmp!r}),'audit',collector,failures=1),flush=True)\n"
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("audit read failed", result.stdout)
        self.assertNotIn("should-not-start", result.stdout)

    def test_feed_pattern_read_is_inside_startup_deadline(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            pattern = Path(tmp) / "patterns"
            os.mkfifo(pattern)
            result = self.child(
                "import feed_follow as f; import feed_actions as a; import watcher_runtime as r; from pathlib import Path\n"
                f"f.action_patterns=lambda: a.action_patterns(Path({str(pattern)!r}))\n"
                "guard=r.deadline\n"
                "r.deadline=lambda seconds,label: guard(0.05,label)\n"
                f"f.follow(Path({tmp!r})/'feed',Path({tmp!r})/'beat')\n"
            )
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertIn("feed startup", result.stderr)
        self.assertIn("action_patterns", result.stderr)

    def test_looping_all_is_rejected_before_any_collector(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.child(
                "import sys; import watchers as w\n"
                "def unexpected(*args): raise RuntimeError('collector-entered')\n"
                "w.cycle=unexpected\n"
                f"sys.argv=['watchers.py','all','--loop','--scope','fixture','--state-dir',{tmp!r}]\n"
                "w.main()\n"
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("run one loop per mode", result.stderr)
        self.assertNotIn("collector-entered", result.stderr)
