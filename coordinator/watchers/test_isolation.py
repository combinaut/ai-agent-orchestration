import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_support import IsolatedTestCase, guard_popen


class IsolationTests(IsolatedTestCase):
    def assert_spawn_blocked(self, args, message, **kwargs):
        # Even a mutant that removes the guard can only reach this mock.
        spawn = Mock()
        guarded = guard_popen(spawn, Path(os.environ["TEST_TOOL_LOG"]).parent)
        with self.assertRaisesRegex(AssertionError, message):
            guarded(args, **kwargs)
        spawn.assert_not_called()

    def test_tools_resolve_to_logging_fakes(self):
        base = Path(os.environ["TEST_TOOL_LOG"]).parent
        for tool in ("launchctl", "systemctl", "herdr"):
            self.assertEqual(shutil.which(tool), str(base / tool))
            subprocess.run([tool, "fixture"], check=True)
        self.assertEqual((base / "calls").read_text().splitlines(), ["fixture"] * 3)

    def test_inherited_alert_settings_are_cleared(self):
        self.assertEqual(
            [key for key in os.environ if key.startswith("ORCH_ALERT_")], []
        )

    def test_absolute_host_tool_is_blocked_before_spawn(self):
        for tool in ("launchctl", "systemctl", "herdr"):
            self.assert_spawn_blocked(
                ["/usr/bin/" + tool, "fixture"], "Host tool blocked"
            )

    def test_path_reset_is_blocked_before_spawn(self):
        for env in ({"PATH": "/usr/bin:/bin"}, {}):
            self.assert_spawn_blocked(
                ["launchctl", "fixture"], "Host tool blocked", env=env
            )

    def test_environment_wrappers_are_blocked_before_spawn(self):
        for executable in ("env", "/usr/bin/env"):
            for options in (["-i"], ["-u", "PATH"], ["PATH=/usr/bin:/bin"]):
                self.assert_spawn_blocked(
                    [executable, *options, "launchctl", "fixture"],
                    "Command wrapper blocked",
                )

    def test_shell_wrappers_are_blocked_before_spawn(self):
        self.assert_spawn_blocked(
            "launchctl fixture", "explicit subprocess argv", shell=True
        )
        for executable in ("sh", "/bin/bash"):
            self.assert_spawn_blocked(
                [executable, "-c", "launchctl fixture"], "Command wrapper blocked"
            )

    def test_allowed_command_forwards_arguments(self):
        spawn = Mock()
        base = Path(os.environ["TEST_TOOL_LOG"]).parent
        guarded = guard_popen(spawn, base)
        argv = [str(base / "herdr"), "fixture"]
        self.assertIs(guarded(argv, cwd="/tmp"), spawn.return_value)
        spawn.assert_called_once_with(argv, cwd="/tmp")

    def test_discovery_requires_isolated_test_cases(self):
        suite = unittest.defaultTestLoader.discover(".", pattern="test_*.py")
        pending = [suite]
        while pending:
            item = pending.pop()
            if isinstance(item, unittest.TestCase):
                self.assertIsInstance(item, IsolatedTestCase)
            else:
                pending.extend(item)
