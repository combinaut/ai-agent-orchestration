import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_support import IsolatedTestCase

SCRIPT = Path(__file__).with_name("bind-self.py")
BINDINGS = ".claude/coordinator/watchers/bindings"


class BindSelfTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / "home"
        self.bindings = self.home / BINDINGS
        self.bindings.mkdir(parents=True)
        sessions = self.home / ".claude/sessions"
        sessions.mkdir()
        self.socket = str(self.home / "4242.sock")
        (sessions / "4242.json").write_text(
            json.dumps(
                {
                    "messagingSocketPath": self.socket,
                    "sessionId": "sess",
                    "procStart": "start",
                }
            )
        )
        self.outside = self.home / "outside.json"

    def run_bind(self, *outputs):
        env = dict(
            os.environ,
            HOME=str(self.home),
            CLAUDE_CODE_MESSAGING_SOCKET=self.socket,
            CLAUDE_CODE_MESSAGING_TOKEN="secret",
        )
        argv = [sys.executable, str(SCRIPT)]
        for output in outputs:
            argv += ["--output", str(output)]
        return subprocess.run(argv, env=env, capture_output=True, text=True)

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--output must be", result.stderr)
        self.assertEqual(list(self.bindings.iterdir()), [])

    def test_coordinator_and_architect_names_are_written_with_mode_0600(self):
        for name in ("coordinator.json", "architect-wJ.json", "architect-w1T_2.json"):
            result = self.run_bind(self.bindings / name)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.bindings / name).stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                json.loads((self.bindings / name).read_text())["token"], "secret"
            )

    def test_dot_dot_path_that_ends_outside_is_refused(self):
        result = self.run_bind(f"{self.bindings}/../../../../outside.json")
        self.assert_refused(result)
        self.assertFalse(self.outside.exists())

    def test_dot_dot_path_that_returns_inside_is_written(self):
        result = self.run_bind(f"{self.bindings}/../bindings/coordinator.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.bindings / "coordinator.json").exists())

    def test_last_of_two_output_flags_decides(self):
        result = self.run_bind(self.bindings / "coordinator.json", self.outside)
        self.assert_refused(result)
        self.assertFalse(self.outside.exists())

    def test_existing_0600_file_outside_stays_unchanged(self):
        self.outside.write_text("private key")
        self.outside.chmod(0o600)
        result = self.run_bind(self.outside)
        self.assert_refused(result)
        self.assertEqual(self.outside.read_text(), "private key")

    def test_existing_binding_with_an_unsafe_mode_is_refused_and_unchanged(self):
        binding = self.bindings / "coordinator.json"
        binding.write_text("keep me")
        for mode in (0o644, 0o640, 0o666, 0o700):
            with self.subTest(mode=oct(mode)):
                binding.chmod(mode)
                result = self.run_bind(binding)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("mode-0600", result.stderr)
                self.assertEqual(binding.read_text(), "keep me")
                self.assertEqual(binding.stat().st_mode & 0o777, mode)
        binding.chmod(0o600)
        self.assertEqual(self.run_bind(binding).returncode, 0)

    def test_symlink_in_the_directory_to_a_file_outside_is_refused(self):
        self.outside.write_text("private key")
        self.outside.chmod(0o600)
        (self.bindings / "coordinator.json").symlink_to(self.outside)
        result = self.run_bind(self.bindings / "coordinator.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.outside.read_text(), "private key")

    def test_bad_names_are_refused(self):
        for name in (
            "other.json",
            "architect-.json",
            "architect-a.b.json",
            "coordinator.json.bak",
            "xcoordinator.json",
            "architect-w1.json.tmp",
            ".coordinator.json",
        ):
            with self.subTest(name=name):
                self.assert_refused(self.run_bind(self.bindings / name))

    def test_architect_name_must_use_the_callers_workspace_id(self):
        os.environ["HERDR_WORKSPACE_ID"] = "w2M"
        self.addCleanup(os.environ.pop, "HERDR_WORKSPACE_ID")
        result = self.run_bind(self.bindings / "architect-w9X.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("architect-w2M.json", result.stderr)
        self.assertEqual(list(self.bindings.iterdir()), [])
        for name in ("architect-w2M.json", "coordinator.json"):
            self.assertEqual(self.run_bind(self.bindings / name).returncode, 0, name)

    def test_nested_directory_is_refused(self):
        (self.bindings / "sub").mkdir()
        result = self.run_bind(self.bindings / "sub" / "coordinator.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list((self.bindings / "sub").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
