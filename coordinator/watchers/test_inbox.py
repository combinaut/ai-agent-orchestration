import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import watchers as w

spec = importlib.util.spec_from_file_location("inbox", w.ROOT / "notify-inbox.py")
inbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inbox)


class InboxTest(IsolatedTestCase):
    def setUp(self):
        config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(config_dir.cleanup)
        config = Path(config_dir.name) / "no-config.toml"
        config.touch()
        env = patch.dict(os.environ, {"ORCH_CONFIG": str(config)})
        env.start()
        self.addCleanup(env.stop)

    def test_native_frame_only_to_owned_test_socket(self):
        # Short /tmp path is required by the macOS Unix socket path-length cap.
        with tempfile.TemporaryDirectory(prefix="coord-test-") as tmp:
            path = Path(tmp) / "test.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                server.listen(1)
                received = []

                def read():
                    conn, _ = server.accept()
                    with conn:
                        data = b""
                        while True:
                            part = conn.recv(4096)
                            if not part:
                                break
                            data += part
                        received.extend(json.loads(line) for line in data.splitlines())

                thread = threading.Thread(target=read)
                thread.start()
                inbox.send_to_socket(path, "test-token-only", "one batch")
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(
                    received,
                    [
                        {"type": "auth", "token": "test-token-only"},
                        {
                            "type": "user",
                            "message": {"role": "user", "content": "one batch"},
                        },
                    ],
                )

    def test_workers_delivery_commits_cursor_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = {
                "result": {
                    "agents": [dict(pane_id="blocked-pane", agent_status="blocked")]
                }
            }
            w.cycle(base, "workers", lambda before: raw)
            cursor = base / "deliver.cursor"
            argv = [
                "notify-inbox.py",
                "--state-dir",
                str(base),
                "--cursor",
                str(cursor),
                "--deliver",
                "file",
                "--modes",
                "workers",
            ]
            with (
                warnings.catch_warnings(),
                patch("sys.argv", argv),
                patch.object(inbox, "send_to_socket") as send,
            ):
                warnings.simplefilter("ignore", ResourceWarning)
                inbox.main()
                feed = (base / inbox.FEED_NAME).read_text()
                self.assertIn("workers: WORKER blocked-pane blocked (start)", feed)
                self.assertEqual(json.loads(cursor.read_text())["workers"]["seq"], 1)
                inbox.main()
                self.assertEqual((base / inbox.FEED_NAME).read_text(), feed)
                send.assert_not_called()

    def test_symlink_socket_refused(self):
        with tempfile.TemporaryDirectory(dir=w.ROOT) as tmp:
            path = Path(tmp) / "link"
            path.symlink_to("/tmp/cc-socks/43617.sock")
            with self.assertRaises(ValueError):
                inbox.send_to_socket(path, "test", "test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
