import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

from watcher_platform import LinuxHost, MacHost, herdr_bin, optional


class PlatformTests(IsolatedTestCase):
    def test_herdr_falls_back_to_the_configured_binary(self):
        with patch.dict(os.environ, {"ORCH_HERDR_BIN": "/opt/herdr/bin/herdr"}):
            os.environ.pop("HERDR_BIN_PATH", None)
            self.assertEqual(herdr_bin(), "/opt/herdr/bin/herdr")
            with patch.dict(os.environ, {"HERDR_BIN_PATH": "/svc/herdr"}):
                self.assertEqual(herdr_bin(), "/svc/herdr")

    def test_linux_proc_fixture(self):
        raw = LinuxHost(Path(__file__).parent / "fixtures/proc").collect(None)
        self.assertEqual(
            raw, dict(free=19, swap=2048, procs=2, files=160000, load=41.5)
        )

    def test_linux_missing_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = LinuxHost(tmp).collect(None)
            self.assertEqual(
                raw, dict(free=None, swap=None, procs=0, files=None, load=None)
            )

    def test_mac_missing_metric_and_headerless_processes(self):
        def command(*args):
            return {
                ("memory_pressure",): "no percentage",
                ("ps", "-A", "-o", "pid="): "12\n34\n",
                ("sysctl", "-n", "vm.swapusage"): "used = 2048.00M",
                ("sysctl", "-n", "kern.num_files"): "160000",
                ("sysctl", "-n", "vm.loadavg"): "{ 41.5 2.0 1.0 }",
            }[args]

        self.assertEqual(
            MacHost().collect(command),
            dict(free=None, swap=2048, procs=2, files=160000, load=41.5),
        )

    def test_mac_failed_commands_isolated(self):
        def command(*args):
            if args == ("sysctl", "-n", "kern.num_files"):
                return "160000"
            raise subprocess.CalledProcessError(1, args)

        self.assertEqual(
            MacHost().collect(command),
            dict(free=None, swap=None, procs=None, files=160000, load=None),
        )

    def test_optional_parser_bugs_raise(self):
        for error in (
            TypeError("bug"),
            IndexError("bug"),
            ZeroDivisionError("bug"),
            ValueError("bug"),
            RuntimeError("bug"),
        ):
            with self.subTest(error=type(error).__name__):

                def read():
                    raise error

                with self.assertRaises(type(error)):
                    optional(read)


if __name__ == "__main__":
    unittest.main()
