import importlib.util
import json
import tempfile
import warnings
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import watchers as w

spec = importlib.util.spec_from_file_location("stale_inbox", w.ROOT / "notify-inbox.py")
inbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inbox)


class StaleBindingTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bindings = self.base / "bindings"
        self.bindings.mkdir()
        self.path = self.bindings / "architect-wA.json"
        self.write_binding(1234, "architect-session")
        self.sent = []

    def write_binding(self, pid, session):
        self.path.write_text(
            json.dumps(
                dict(
                    pid=pid,
                    session_id=session,
                    token="never-display-this",
                    socket=f"/tmp/cc-socks/{pid}.sock",
                    proc_start="start",
                )
            )
        )
        self.path.chmod(0o600)
        sessions = self.base / ".claude/sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / f"{pid}.json").write_text(
            json.dumps(
                dict(
                    sessionId=session,
                    procStart="start",
                    messagingSocketPath=f"/tmp/cc-socks/{pid}.sock",
                )
            )
        )

    def deliver(self, dead=True, preview=False):
        argv = [
            "notify-inbox.py",
            "--state-dir",
            str(self.base),
            "--cursor",
            str(self.base / "cursor.json"),
            "--bindings-dir",
            str(self.bindings),
            "--binding",
            str(self.bindings / "coordinator.json"),
            "--modes",
            "workers",
        ]
        if not preview:
            argv += ["--deliver", "file"]
        with (
            warnings.catch_warnings(),
            patch("sys.argv", argv),
            patch.object(
                inbox.os, "kill", side_effect=ProcessLookupError if dead else None
            ),
            patch.object(Path, "home", return_value=self.base),
            patch.object(
                inbox,
                "binding",
                return_value=(self.bindings / "coordinator.json", "token"),
            ),
            patch.object(
                inbox,
                "send_to_socket",
                side_effect=lambda path, token, text: self.sent.append(
                    (path.name, text)
                ),
            ),
        ):
            warnings.simplefilter("ignore", ResourceWarning)
            inbox.main()

    def test_dead_binding_alerts_coordinator_once_and_rebind_can_alert_again(self):
        self.deliver()
        feed = self.base / inbox.FEED_NAME
        self.assertTrue(feed.exists(), "dead binding must produce an alert")
        first = feed.read_text()
        self.assertIn("BINDING STALE architect-session wA", first)
        self.assertEqual([name for name, _ in self.sent], ["coordinator.json"])
        self.assertNotIn("never-display-this", first + str(self.sent))
        self.deliver()
        self.assertEqual(feed.read_text(), first)
        self.assertEqual(len(self.sent), 1)
        self.write_binding(2345, "successor-session")
        self.deliver(dead=False)
        self.assertEqual(len(self.sent), 1)
        self.deliver()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("BINDING STALE successor-session wA", self.sent[-1][1])

    def test_unreadable_binding_retains_its_notification_fingerprint(self):
        self.deliver()
        self.path.chmod(0o644)
        self.deliver()
        self.path.chmod(0o600)
        self.deliver()
        self.assertEqual(len(self.sent), 1)
        cursor = json.loads((self.base / "cursor.json").read_text())
        before = dict(cursor)
        with patch.object(Path, "iterdir", side_effect=PermissionError):
            self.assertEqual(
                inbox.stale_binding_alerts(
                    type("Args", (), {"bindings_dir": self.bindings})(), cursor
                ),
                ([], False),
            )
        self.assertEqual(cursor, before)

    def test_live_unreadable_and_preview_bindings_do_not_raise_stale_alerts(self):
        self.deliver(dead=False)
        self.path.chmod(0o644)
        self.deliver()
        self.path.chmod(0o600)
        self.deliver(preview=True)
        self.assertEqual(self.sent, [])
        self.assertFalse((self.base / inbox.FEED_NAME).exists())


class ReusedPidTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bindings = self.base / "bindings"
        self.bindings.mkdir()
        self.sessions = self.base / ".claude/sessions"
        self.sessions.mkdir(parents=True)
        self.path = self.bindings / "architect-wA.json"
        self.cursor = {}
        self.alive = False
        self.write_binding("architect-session")

    def write_binding(self, session, pid=1234):
        self.path.write_text(
            json.dumps(
                dict(
                    pid=pid,
                    session_id=session,
                    token="t",
                    socket=f"/tmp/cc-socks/{pid}.sock",
                    proc_start="start",
                )
            )
        )
        self.path.chmod(0o600)

    def write_session(self, **override):
        data = dict(
            sessionId="architect-session",
            procStart="start",
            messagingSocketPath="/tmp/cc-socks/1234.sock",
        )
        data.update(override)
        (self.sessions / "1234.json").write_text(json.dumps(data))

    def lines(self):
        def kill(pid, sig):
            if not self.alive:
                raise ProcessLookupError

        args = type("Args", (), {"bindings_dir": self.bindings})()
        with (
            patch.object(inbox.os, "kill", side_effect=kill),
            patch.object(Path, "home", return_value=self.base),
        ):
            events, _ = inbox.stale_binding_alerts(args, self.cursor)
        return events

    def test_pid_reused_by_unrelated_process_does_not_alert_again(self):
        self.assertEqual(len(self.lines()), 1)
        self.alive = True
        self.assertEqual(self.lines(), [])
        self.alive = False
        self.assertEqual(self.lines(), [])

    def test_live_pid_with_matching_session_is_not_stale(self):
        self.write_session()
        self.assertEqual(len(self.lines()), 1)
        self.alive = True
        self.assertEqual(self.lines(), [])
        self.assertNotIn("stale_bindings", self.cursor)

    def test_live_pid_with_other_session_alerts_once(self):
        self.alive = True
        self.write_session(sessionId="someone-else")
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.lines(), [])

    def test_live_pid_without_session_file_alerts_once(self):
        self.alive = True
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.lines(), [])

    def test_live_pid_with_non_object_session_file_alerts_once(self):
        self.alive = True
        for payload in ("[]", "null"):
            with self.subTest(payload=payload):
                self.cursor.clear()
                (self.sessions / "1234.json").write_text(payload)
                self.assertEqual(len(self.lines()), 1)
                self.assertEqual(self.lines(), [])

    def test_torn_session_read_does_not_alert(self):
        self.alive = True
        self.write_session()
        self.assertEqual(self.lines(), [])
        for torn in (b"", b'{"sessionId": "', b"\xff\xfe"):
            with self.subTest(torn=torn):
                (self.sessions / "1234.json").write_bytes(torn)
                self.assertEqual(self.lines(), [])
                self.assertNotIn("stale_bindings", self.cursor)

    def test_deeply_nested_session_file_does_not_raise(self):
        self.alive = True
        (self.sessions / "1234.json").write_text("[" * 100000)
        self.assertEqual(self.lines(), [])

    def test_pid_too_large_for_kill_does_not_raise(self):
        self.write_binding("architect-session", pid=2**70)
        args = type("Args", (), {"bindings_dir": self.bindings})()
        self.assertEqual(inbox.stale_binding_alerts(args, self.cursor), ([], False))

    def test_each_session_field_mismatch_makes_a_live_binding_stale(self):
        self.alive = True
        for field in ("procStart", "messagingSocketPath"):
            with self.subTest(field=field):
                self.cursor.clear()
                self.write_session(**{field: "other"})
                self.assertEqual(len(self.lines()), 1)

    def test_changed_binding_file_alerts_again(self):
        self.assertEqual(len(self.lines()), 1)
        self.write_binding("successor-session")
        self.assertEqual(len(self.lines()), 1)
