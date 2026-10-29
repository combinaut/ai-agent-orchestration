import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase


class PopupTest(IsolatedTestCase):
    def setUp(self):
        import todo_popup

        self.popup = todo_popup
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "popup.json"
        self.identity = {
            "started": "Thu Oct 29 10:00:00 2026",
            "command": "python view --popup",
        }
        self.record = {
            "pid": 12345,
            "identity": self.identity,
            "socket": "/test/herdr.sock",
        }
        self.path.write_text(json.dumps(self.record))
        self.env = patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/test/herdr.sock"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_replacement_closes_and_terminates_only_the_recorded_popup(self):
        with (
            patch.object(
                self.popup,
                "process_identity",
                side_effect=[self.identity, self.identity, None],
            ),
            patch.object(
                self.popup, "rpc", return_value={"result": {"type": "ok"}}
            ) as rpc,
            patch.object(self.popup.os, "kill") as kill,
        ):
            self.popup.replace(self.path)
        rpc.assert_called_once_with("popup.close", {})
        kill.assert_called_once_with(12345, self.popup.signal.SIGTERM)

    def test_stale_dead_reused_or_foreign_pidfiles_never_signal_or_close(self):
        for identity, socket in (
            (None, "/test/herdr.sock"),
            ({"started": "new", "command": "other"}, "/test/herdr.sock"),
            (self.identity, "/other.sock"),
        ):
            with self.subTest(identity=identity, socket=socket):
                self.path.write_text(json.dumps({**self.record, "socket": socket}))
                with (
                    patch.object(self.popup, "process_identity", return_value=identity),
                    patch.object(self.popup, "rpc") as rpc,
                    patch.object(self.popup.os, "kill") as kill,
                ):
                    self.popup.replace(self.path)
                rpc.assert_not_called()
                kill.assert_not_called()

    def test_command_identity_rejects_a_reused_pid_with_a_different_command(self):
        for command in (
            "/usr/bin/python3 other.py --popup",
            "/bin/sh -c python3 view.py --popup",
            "/usr/bin/python3 -c view.py --popup",
        ):
            with (
                self.subTest(command=command),
                patch.object(self.popup.subprocess, "run") as run,
            ):
                run.return_value.stdout = "Thu Oct 29 10:00:00 2026 " + command
                self.assertIsNone(self.popup.process_identity(12345))

    def test_identity_accepts_the_installed_symlink_and_real_script(self):
        link = Path(self.tmp.name) / "installed view.py"
        link.symlink_to(self.popup.VIEW)
        for script in (link, self.popup.VIEW):
            command = f"/usr/bin/python3 {script} --demo --popup"
            with (
                self.subTest(script=script),
                patch.object(self.popup.subprocess, "run") as run,
            ):
                run.return_value.stdout = "Thu Oct 29 10:00:00 2026 " + command
                self.assertEqual(
                    self.popup.process_identity(12345),
                    {"started": "Thu Oct 29 10:00:00 2026", "command": command},
                )

    def test_open_action_replaces_before_launch_and_waits_for_registration(self):
        events = []
        with (
            patch.object(self.popup, "pidfile", return_value=self.path),
            patch.object(
                self.popup, "replace", side_effect=lambda _: events.append("replace")
            ),
            patch.object(self.popup.subprocess, "run") as run,
            patch.object(self.popup.uuid, "uuid4") as token,
            patch.object(
                self.popup, "read_record", return_value={**self.record, "token": "new"}
            ),
            patch.object(self.popup, "owns", return_value=True),
        ):
            token.return_value.hex = "new"
            run.side_effect = lambda *a, **k: (
                events.append("open") or subprocess.CompletedProcess([], 0)
            )
            self.assertEqual(self.popup.open_popup(), 0)
        self.assertEqual(events, ["replace", "open"])
        self.assertIn("ORCH_TODO_POPUP_TOKEN=new", run.call_args.args[0])

    def test_pane_check_distinguishes_gone_from_transient_errors(self):
        for reply, expected in (
            ({"result": {}}, True),
            ({"error": {"code": "pane_not_found"}}, False),
            ({"error": {"code": "internal_error"}}, True),
        ):
            with (
                self.subTest(reply=reply),
                patch.object(self.popup, "rpc", return_value=reply),
            ):
                self.assertEqual(self.popup.pane_exists("w1:p9"), expected)
        with patch.object(self.popup, "rpc", side_effect=OSError("offline")):
            self.assertTrue(self.popup.pane_exists("w1:p9"))


if __name__ == "__main__":
    unittest.main()
