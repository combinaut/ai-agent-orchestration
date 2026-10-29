import io
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

    def test_replacement_terminates_recorded_child_without_closing_reused_popup_slot(
        self,
    ):
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
        rpc.assert_not_called()
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

    def test_unknown_identity_registers_startup_without_authorizing_replacement(self):
        with (
            patch.object(self.popup, "pidfile", return_value=self.path),
            patch.object(self.popup, "process_identity", return_value=None),
        ):
            self.path.unlink()
            registration = self.popup.register()
            record = self.popup.read_record(self.path)
            self.assertIsNone(record["identity"])
            self.assertFalse(self.popup.owns(record))
            self.assertIsNotNone(registration)
            self.popup.unregister(registration)
        self.assertFalse(self.path.exists())

    def test_narrow_columns_do_not_truncate_command_identity(self):
        output = f"Thu Oct 29 10:00:00 2026 /usr/bin/python3 {self.popup.VIEW} --popup"

        def ps(args, **kwargs):
            width = len(output) if "-ww" in args else int(os.environ["COLUMNS"])
            return subprocess.CompletedProcess(args, 0, stdout=output[:width])

        with (
            patch.dict(os.environ, {"COLUMNS": "80"}),
            patch.object(self.popup.subprocess, "run", side_effect=ps),
        ):
            identity = self.popup.process_identity(12345)
        self.assertIsNotNone(identity)
        self.assertIn(str(self.popup.VIEW), identity["command"])

    def test_open_terminates_recorded_child_before_launch_and_registration(self):
        events = []
        with (
            patch.object(self.popup, "pidfile", return_value=self.path),
            patch.object(
                self.popup,
                "process_identity",
                side_effect=[self.identity, self.identity, None],
            ),
            patch.object(
                self.popup, "rpc", side_effect=lambda *args: events.append("close")
            ) as rpc,
            patch.object(
                self.popup.os,
                "kill",
                side_effect=lambda *args: events.append("terminate"),
            ) as kill,
            patch.object(self.popup.subprocess, "run") as run,
            patch.object(self.popup.uuid, "uuid4") as token,
            patch.object(
                self.popup,
                "read_record",
                side_effect=[self.record, {**self.record, "token": "new"}],
            ),
        ):
            token.return_value.hex = "new"
            run.side_effect = lambda *a, **k: (
                events.append("open")
                or subprocess.CompletedProcess([], 0, stdout="", stderr="")
            )
            self.assertEqual(self.popup.open_popup(), 0)
        self.assertEqual(events, ["terminate", "open"])
        rpc.assert_not_called()
        kill.assert_called_once_with(12345, self.popup.signal.SIGTERM)
        self.assertIn("ORCH_TODO_POPUP_TOKEN=new", run.call_args.args[0])

    def test_open_retries_busy_during_previous_popup_process_exit(self):
        busy = subprocess.CompletedProcess(
            [], 1, stdout=json.dumps({"error": {"code": "ui_busy"}}), stderr=""
        )
        opened = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            patch.object(self.popup, "pidfile", return_value=self.path),
            patch.object(self.popup, "replace") as replace,
            patch.object(self.popup.uuid, "uuid4") as token,
            patch.object(
                self.popup, "read_record", return_value={**self.record, "token": "new"}
            ),
            patch.object(
                self.popup.subprocess, "run", side_effect=[busy, opened]
            ) as run,
            patch.object(self.popup.time, "sleep"),
        ):
            token.return_value.hex = "new"
            self.assertEqual(self.popup.open_popup(), 0)
        replace.assert_called_once_with(self.path)
        self.assertEqual(run.call_count, 2)

    def test_unowned_busy_popup_is_not_closed_and_retries_are_bounded(self):
        busy = subprocess.CompletedProcess(
            [], 1, stdout=json.dumps({"error": {"code": "ui_busy"}}), stderr=""
        )
        with (
            patch.object(self.popup, "pidfile", return_value=self.path),
            patch.object(self.popup, "read_record", return_value=None),
            patch.object(self.popup, "rpc") as rpc,
            patch.object(self.popup.os, "kill") as kill,
            patch.object(self.popup.subprocess, "run", return_value=busy) as run,
            patch.object(self.popup.time, "monotonic", side_effect=[0, 0, 4]),
            patch.object(self.popup.time, "sleep"),
            patch.object(self.popup.sys, "stdout", io.StringIO()),
        ):
            self.assertEqual(self.popup.open_popup(), 1)
        self.assertEqual(run.call_count, 2)
        rpc.assert_not_called()
        kill.assert_not_called()

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
