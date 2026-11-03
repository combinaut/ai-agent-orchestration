"""GitHub write retry contracts, using a fake gh rather than the network."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import github_poll
import github_write


class GithubWriteTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.count = self.root / "count"
        self.fake = self.root / "gh"
        self.env = patch.dict(
            os.environ,
            PATH=f"{self.root}:{os.environ['PATH']}",
            GITHUB_POLL_STATE_DIR=str(self.state),
            GITHUB_WRITE_BACKOFF_BASE="0.02",
            GITHUB_WRITE_JITTER_MAX="0",
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.spacing = patch.object(github_poll, "SPACING", 0.001)
        self.spacing.start()
        self.addCleanup(self.spacing.stop)

    def fake_gh(self, body):
        self.fake.write_text("#!/bin/sh\n" + body)
        self.fake.chmod(0o755)

    def calls(self):
        return int(self.count.read_text()) if self.count.exists() else 0

    def test_secondary_limit_retries_once_after_the_configured_delay(self):
        self.fake_gh(
            f'n=$(cat "{self.count}" 2>/dev/null || echo 0)\n'
            'n=$((n + 1)); printf "%s" "$n" >"' + str(self.count) + '"\n'
            'if [ "$n" = 1 ]; then echo "GraphQL: secondary rate limit" >&2; exit 1; fi\n'
            "printf ok\n"
        )
        started = time.monotonic()
        result = github_write.run(["gh", "api", "-X", "POST", "fixture"], timeout=5)
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(self.calls(), 2)
        self.assertGreaterEqual(time.monotonic() - started, 0.015)

    def test_permission_403_is_not_retried(self):
        self.fake_gh(
            f'printf 1 >"{self.count}"\n'
            'echo "gh: HTTP 403: Resource not accessible by integration" >&2\nexit 1\n'
        )
        result = github_write.run(["gh", "api", "-X", "POST", "fixture"], timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), 1)

    def test_unknown_outcome_does_not_resend_a_write(self):
        self.fake_gh(
            f'n=$(cat "{self.count}" 2>/dev/null || echo 0)\n'
            'n=$((n + 1)); printf "%s" "$n" >"' + str(self.count) + '"\n'
            'echo "network connection lost after request" >&2\nexit 1\n'
        )
        result = github_write.run(["gh", "api", "-X", "POST", "fixture"], timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), 1)

    def test_unrelated_shared_cooldown_does_not_replay_an_uncertain_write(self):
        result = subprocess.CompletedProcess(["gh"], 1, "", "connection lost")
        with (
            patch.object(github_poll, "run", return_value=result) as run,
            patch.object(github_poll, "active_cooldown", return_value=True),
        ):
            actual = github_write.run(["gh", "api", "-X", "POST", "fixture"], timeout=5)
        self.assertIs(actual, result)
        run.assert_called_once()

    def test_oversized_saved_cooldown_reaches_state_recovery(self):
        self.fake_gh("printf ok\n")
        self.state.mkdir()
        github_poll.write_state(
            self.state / "github.json",
            {"until": time.time() + github_poll.MAX_DELAY + 6},
        )
        result = github_write.run(["gh", "workflow", "run", "fixture"], timeout=5)
        self.assertEqual(result.stdout, "ok")

    def test_limited_non_single_request_command_is_not_replayed(self):
        result = subprocess.CompletedProcess(["gh"], 1, "", "secondary rate limit")
        result.github_limited = True
        with patch.object(github_poll, "run", return_value=result) as run:
            actual = github_write.run(["gh", "pr", "create", "--title", "x"], timeout=5)
        self.assertIs(actual, result)
        self.assertIn("not replayed", actual.stderr)
        run.assert_called_once()

    def test_stdin_forms_are_buffered_before_a_retry(self):
        for argv in (
            ["api", "--input", "-"],
            ["pr", "create", "--body-file=-"],
            ["api", "-F", "body=@-"],
            ["api", "-f", "@-"],
            ["api", "--raw-field", "body=@-"],
            ["workflow", "run", "validate.yml", "--json"],
        ):
            with self.subTest(argv=argv):
                self.assertTrue(github_write.reads_stdin(argv))

    def test_second_process_waits_for_the_shared_cooldown(self):
        self.fake_gh("printf ok\n")
        self.state.mkdir()
        github_poll.write_state(
            self.state / "github.json", {"until": time.time() + 0.08}
        )
        started = time.monotonic()
        result = subprocess.run(
            [
                sys.executable,
                str(Path(github_write.__file__)),
                "--timeout",
                "5",
                "workflow",
                "run",
                "fixture",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok")
        self.assertGreaterEqual(time.monotonic() - started, 0.06)
