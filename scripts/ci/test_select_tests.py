#!/usr/bin/env python3
"""Regression cases for CI selection and changed-file collection."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "select_tests", Path(__file__).with_name("select-tests.py")
)
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class SelectionTests(unittest.TestCase):
    def test_sweep_python_and_its_documentation(self):
        self.assertEqual(
            ci.select(
                [
                    "lib/sweep_record.py",
                    "lib/sweep_run.py",
                    "tests/sweep-run.bats",
                    "docs/sweep-script.md",
                ]
            ),
            {"full": False, "macos": False, "suites": sorted(ci.SWEEP)},
        )

    def test_extension_ui(self):
        self.assertEqual(
            ci.select(["chrome/pr-tabs/extension/src/App.tsx"]),
            {"full": False, "macos": False, "suites": ["pr-tabs"]},
        )

    def test_native_host_and_manifest_consumers(self):
        host = ci.select(["chrome/pr-tabs/host/pr_tabs_host.py"])
        self.assertTrue(host["macos"])
        self.assertEqual(
            host["suites"], ["bats:chrome-host", "pr-tabs", "pr-tabs-host"]
        )
        manifest = ci.select(["chrome/pr-tabs/extension/manifest.json"])
        self.assertIn("bats:chrome-host", manifest["suites"])
        self.assertFalse(manifest["macos"])

    def test_shell_source_and_its_integration(self):
        plan = ci.select(["coordinator/watchers/coord-hog-watch.sh"])
        self.assertTrue(plan["macos"])
        self.assertEqual(plan["suites"], ["bats:install", "hog", "units", "watchers"])

    def test_unknown_and_shared_sources_fall_back(self):
        for path in ["new-component/file", "lib/orch_settings.py", "install.sh"]:
            with self.subTest(path=path):
                plan = ci.select([path])
                self.assertTrue(plan["full"])
                self.assertEqual(set(plan["suites"]), ci.HEAVY | {"all"})
        self.assertTrue(ci.select(["new-component/file"])["macos"])
        self.assertFalse(ci.select(["lib/orch_settings.py"])["macos"])

    def test_ci_and_isolation_helpers_select_everything_on_both_platforms(self):
        for path in [
            ".github/workflows/validate.yml",
            ".github/actions/validate/action.yml",
            "scripts/ci/select-tests.py",
            "tests/run.sh",
            "tests/run-inner.sh",
            "tests/platform/linux.sh",
            "tests/test_helper.bash",
            "tests/setup_suite.bash",
        ]:
            with self.subTest(path=path):
                plan = ci.select([path])
                self.assertTrue(plan["full"])
                self.assertTrue(plan["macos"])

    def test_documentation_keeps_readers_on_linux(self):
        plan = ci.select(["docs/settings.md", "skills/architect/SKILL.md"])
        self.assertFalse(plan["macos"])
        self.assertEqual(set(plan["suites"]), ci.DOC_READERS)

    def test_shell_bats_and_installer_fixture_coverage(self):
        self.assertTrue(ci.select(["tests/install.bats"])["macos"])
        plan = ci.select(["coordinator/watchers/fixtures/audit-pr.json"])
        self.assertIn("bats:install", plan["suites"])
        self.assertIn("audit", plan["suites"])

    def test_union_preserves_dependencies(self):
        plan = ci.select(["lib/sweep_run.py", "coordinator/watchers/test_workers.py"])
        self.assertEqual(set(plan["suites"]), {"bats:sweep-run", "workers", "watchers"})

    def test_dispatch_runs_everything(self):
        self.assertEqual(
            ci.select([], everything=True),
            {"full": True, "macos": True, "suites": sorted(ci.HEAVY | {"all"})},
        )

    def test_push_uses_previous_main_commit(self):
        with patch.object(
            ci, "changed_files", return_value=["lib/sweep_run.py"]
        ) as diff:
            plan = ci.event_plan({"before": "old-main", "after": "new-main"}, "push")
        diff.assert_called_once_with("old-main", "new-main")
        self.assertFalse(plan["macos"])
        self.assertEqual(plan["suites"], ["bats:sweep-run"])

    def test_pull_request_uses_merge_base(self):
        event = {"pull_request": {"base": {"sha": "base"}, "head": {"sha": "head"}}}
        with (
            patch.object(
                ci.subprocess, "check_output", return_value="ancestor\n"
            ) as git,
            patch.object(
                ci, "changed_files", return_value=["lib/sweep_run.py"]
            ) as diff,
        ):
            plan = ci.event_plan(event, "pull_request")
        self.assertEqual(git.call_args.args[0], ["git", "merge-base", "base", "head"])
        diff.assert_called_once_with("ancestor", "head")
        self.assertFalse(plan["macos"])

    def test_unavailable_history_and_dispatch_fail_open(self):
        with patch.object(
            ci, "changed_files", side_effect=subprocess.CalledProcessError(1, "git")
        ):
            self.assertTrue(
                ci.event_plan({"before": "missing", "after": "head"}, "push")["macos"]
            )
        self.assertTrue(ci.event_plan({}, "workflow_dispatch")["full"])

    def test_changed_file_collection_keeps_deletions_and_both_rename_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=root, text=True
                ).strip()

            git("init", "-q")
            git("config", "user.email", "ci@example.invalid")
            git("config", "user.name", "CI")
            (root / "before.py").write_text("source\n")
            (root / "deleted.sh").write_text("source\n")
            git("add", ".")
            git("commit", "-qm", "before")
            base = git("rev-parse", "HEAD")
            git("mv", "before.py", "after.py")
            git("rm", "deleted.sh")
            git("commit", "-qm", "after")
            with patch.object(ci, "ROOT", root):
                self.assertEqual(
                    set(ci.changed_files(base, "HEAD")),
                    {"before.py", "after.py", "deleted.sh"},
                )


if __name__ == "__main__":
    unittest.main()
