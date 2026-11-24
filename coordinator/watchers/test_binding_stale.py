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
