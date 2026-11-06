"""Server gate tests; no GitHub writes or local record cache."""

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import sweep_ci as ci
import sweep_record as gate

HEAD, BASE = "a" * 40, "b" * 40
REPO = "owner/repo"


class SharedGateTests(unittest.TestCase):
    def setUp(self):
        self.pr = {
            "head": {"sha": HEAD},
            "base": {"ref": "main"},
            "title": "Title",
            "body": "Body",
        }
        self.record = {
            "tool": "orch-sweep",
            "version": 1,
            "pr": f"https://github.com/{REPO}/pull/1",
            "head": HEAD,
            "base": BASE,
            "clean": True,
            "mode": "agents",
            "slices": 1,
            "sweep_agents": 3,
            "validators": 0,
            "findings": 0,
            "validator_ids": [],
            "verdicts": {},
            "runtime": "codex",
            "model": "test",
            "kind": "full",
            "tree": {"head": HEAD, **gate.CLEAN_TREE},
            "plan": [
                {"name": "lib", "lines": 1, "files": [{"path": "lib/a.py", "lines": 1}]}
            ],
            "pr_text": 1,
            "sweep_agent_ids": ["a", "b", "c"],
            "items": [],
            "dropped": [],
            "open_fix": 0,
            "deviation": None,
            "pr_text_sha256": gate.text_sha(self.pr),
        }
        self.git = patch.object(gate, "since", return_value=(None, ""))
        self.git.start()
        self.addCleanup(self.git.stop)

    def comment(self, record=None, author="author"):
        return {
            "user": {"login": author},
            "author_association": "MEMBER",
            "body": gate.MARKER
            + "\n```json\n"
            + json.dumps(self.record if record is None else record)
            + "\n```",
        }

    def check(self, comments=None):
        return gate.comments_problem(
            self.pr,
            [self.comment()] if comments is None else comments,
            REPO,
            1,
            HEAD,
            BASE,
            {"author", "review-bot"},
            local=False,
        )

    def test_valid(self):
        self.assertIsNone(self.check())

    def test_wrong_head(self):
        self.record["head"] = "c" * 40
        self.assertIn("no full", self.check())

    def test_stale_base(self):
        self.record["base"] = "c" * 40
        self.assertIn("PR base changed", self.check() or "")

    def test_changed_body(self):
        self.pr["body"] = "Edited"
        self.assertIn("title or body changed", self.check() or "")

    def test_untrusted_author(self):
        self.assertIn("no full", self.check([self.comment(author="stranger")]) or "")

    def test_malformed(self):
        del self.record["clean"]
        self.assertIn("missing field clean", self.check() or "")

    def test_counts_and_verdicts(self):
        self.record.update(findings=1, validators=1, validator_ids=["v"])
        self.assertIn("verdicts add up", self.check() or "")
        self.record["verdicts"] = {"UNKNOWN": 1}
        self.assertIn("unknown verdict", self.check() or "")

    def test_ci_never_reads_local_cache(self):
        with patch.object(
            gate, "cache_path", side_effect=AssertionError("local cache read")
        ):
            self.assertIsNone(self.check())

    def test_outside_fork_author_cannot_self_attest(self):
        for login in ("author", "review-bot"):
            for association in (
                None,
                "NONE",
                "CONTRIBUTOR",
                "FIRST_TIMER",
                "FIRST_TIME_CONTRIBUTOR",
                "MANNEQUIN",
            ):
                comment = self.comment(author=login)
                comment["author_association"] = association
                with self.subTest(login=login, association=association):
                    self.assertIn("no full", self.check([comment]) or "")
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            comment = self.comment()
            comment["author_association"] = association
            self.assertIsNone(self.check([comment]))

    def test_configured_author(self):
        self.assertIsNone(self.check([self.comment(author="review-bot")]))

    def text_record(self):
        record = copy.deepcopy(self.record)
        record.update(
            kind="text",
            slices=0,
            plan=[],
            sweep_agents=1,
            sweep_agent_ids=["text"],
            pr_text_sha256=gate.text_sha(self.pr),
        )
        return record

    def test_text_refresh(self):
        self.pr["body"] = "Edited"
        self.assertIsNone(
            self.check([self.comment(), self.comment(self.text_record())])
        )

    def test_newest_full_supersedes_text(self):
        self.pr["body"] = "Edited"
        self.assertIn(
            "title or body changed",
            self.check([self.comment(self.text_record()), self.comment()]),
        )

    def test_newest_malformed_not_older_valid(self):
        old = self.comment()
        self.record["findings"] = 1
        self.assertIn("no validator", self.check([old, self.comment()]))

    def test_git_failure(self):
        with patch.object(gate, "since", return_value=("unresolvable base", "")):
            self.assertEqual("unresolvable base", self.check())

    def test_open_code_fix(self):
        self.record.update(
            findings=1,
            validators=1,
            validator_ids=["v"],
            verdicts={"CONFIRMED_NEW": 1},
            open_fix=1,
            items=[
                {
                    "id": "F1",
                    "file": "lib/a.py",
                    "line": 1,
                    "category": "real-bug",
                    "summary": "bug",
                    "class": "fix",
                    "verdict": "CONFIRMED_NEW",
                }
            ],
        )
        self.assertIn("open fix", self.check())
        text = self.text_record()
        text.update(
            findings=0,
            validators=0,
            validator_ids=[],
            verdicts={},
            open_fix=0,
            items=[],
        )
        self.assertIn(
            "outside the PR text", self.check([self.comment(), self.comment(text)])
        )


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.pr = {
            "state": "open",
            "head": {"sha": HEAD},
            "base": {"ref": "main"},
            "title": "T",
            "body": "B",
            "user": {"login": "author"},
        }

    def evaluate(self, live=None, why=None, comments=None, live_comments=None):
        comments = [] if comments is None else comments
        live_comments = comments if live_comments is None else live_comments
        with (
            patch.object(ci, "write_status") as status,
            patch.object(ci.subprocess, "run") as run,
            patch.object(
                gate,
                "gh_json",
                side_effect=[self.pr, [comments], live or self.pr, [live_comments]],
            ),
            patch.object(gate, "git", return_value=BASE),
            patch.object(gate, "comments_problem", return_value=why) as shared,
        ):
            ci.evaluate(REPO, 1, ["bot"])
        return status, run, shared

    def test_publishes_shared_result_at_exact_head(self):
        status, run, shared = self.evaluate()
        self.assertEqual((REPO, HEAD, "success"), status.call_args.args[:3])
        self.assertEqual({"author", "bot"}, shared.call_args.args[-1])
        self.assertEqual({"local": False}, shared.call_args.kwargs)
        self.assertIn("refs/sweep/base", run.call_args.args[0][-1])
        self.assertNotIn("checkout", run.call_args.args[0])

    def test_shared_rejection_fails_status(self):
        status, _, _ = self.evaluate(why="missing record")
        self.assertEqual(
            (REPO, HEAD, "failure", "missing record"), status.call_args.args
        )

    def test_concurrent_edit_fails_status(self):
        live = {**self.pr, "body": "changed"}
        status, _, _ = self.evaluate(live=live)
        self.assertEqual("failure", status.call_args.args[2])

    def test_unrelated_repository_metadata_keeps_success(self):
        self.pr["head"]["repo"] = {"pushed_at": "before"}
        self.pr["base"]["repo"] = {"pushed_at": "before"}
        live = copy.deepcopy(self.pr)
        live["head"]["repo"]["pushed_at"] = "after"
        live["base"]["repo"]["pushed_at"] = "after"
        status, _, _ = self.evaluate(live=live)
        self.assertEqual("success", status.call_args.args[2])

    def test_trusted_pr_event_discovers_without_pr_workflow(self):
        # A PR may delete its refresh workflow; trusted metadata events still work.
        event = {"number": 1, "pull_request": {"number": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "event.json")
            path.write_text(json.dumps(event))
            output = io.StringIO()
            with (
                patch.dict(
                    ci.os.environ,
                    {"GITHUB_EVENT_PATH": str(path), "GITHUB_REPOSITORY": REPO},
                ),
                patch.object(sys, "argv", ["sweep_ci.py", "--discover"]),
                patch.object(gate, "gh_json") as api,
                contextlib.redirect_stdout(output),
            ):
                ci.main()
            self.assertEqual([1], json.loads(output.getvalue()))
            api.assert_not_called()

    def test_comment_change_during_validation_fails_status(self):
        comment = {
            "id": 1,
            "updated_at": "before",
            "body": "record",
            "user": {"login": "author"},
            "author_association": "MEMBER",
        }
        for live in (
            [],
            [{**comment, "body": "edited"}],
            [{**comment, "updated_at": "after"}],
            [{**comment, "author_association": "NONE"}],
        ):
            with self.subTest(live=live):
                status, _, _ = self.evaluate(comments=[comment], live_comments=live)
                self.assertEqual("failure", status.call_args.args[2])

    def test_fetch_failure_reports_error(self):
        with (
            patch.object(gate, "gh_json", return_value=self.pr),
            patch.object(ci, "write_status") as status,
            patch.object(ci.subprocess, "run", side_effect=OSError("fetch failed")),
        ):
            with self.assertRaises(OSError):
                ci.evaluate(REPO, 1, [])
        self.assertEqual("error", status.call_args.args[2])


class WorkflowTrustTests(unittest.TestCase):
    def setUp(self):
        path = (
            Path(__file__).resolve().parents[1] / ".github/workflows/sweep-record.yml"
        )
        parsed = subprocess.check_output(
            [
                "ruby",
                "--disable-gems",
                "-r",
                "yaml",
                "-r",
                "json",
                "-e",
                "puts JSON.generate(ARGV.map { |p| YAML.safe_load(File.read(p)) })",
                str(path),
                str(path.with_name("sweep-record-refresh.yml")),
            ],
            text=True,
        )
        self.workflow, self.refresh = json.loads(parsed)

    def assert_trusted(self, workflow):
        events = workflow.get("on", workflow.get("true"))
        self.assertEqual(
            ["opened", "synchronize", "reopened", "edited", "ready_for_review"],
            events["pull_request_target"]["types"],
        )
        allowed = {"contents": "read", "pull-requests": "read", "statuses": "write"}
        self.assertIsInstance(workflow["permissions"], dict)
        self.assertNotIn("statuses", workflow["permissions"])
        for name, job in workflow["jobs"].items():
            permissions = job.get("permissions", workflow["permissions"])
            self.assertIsInstance(permissions, dict)
            if name != "validate":
                self.assertNotIn("statuses", permissions)
            self.assertLessEqual(permissions.items(), allowed.items())
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertEqual(
                        "${{ github.repository }}", step["with"]["repository"]
                    )
                    self.assertEqual(
                        "${{ github.event.repository.default_branch }}",
                        step["with"]["ref"],
                    )
                    self.assertIs(False, step["with"]["persist-credentials"])
                run = step.get("run", "")
                for tail in run.split("${{")[1:]:
                    expression, end, _ = tail.partition("}}")
                    self.assertTrue(end)
                    self.assertRegex(
                        expression.strip(),
                        r"^(?:(?:matrix|needs|steps)\.[\w.]+|github\.token)$",
                    )
                self.assertNotRegex(
                    run,
                    r"\bgit\b[^\n;|&]*\b(?:checkout|switch|worktree)\b|\bgh\b[^\n;|&]*\b(?:pr\s+checkout|run\s+download)\b|refs/pull/",
                )
                self.assertNotIn("download-artifact", step.get("uses", ""))

    def assert_refresh(self, refresh):
        self.assertEqual({}, refresh["permissions"])
        for job in refresh["jobs"].values():
            self.assertEqual({}, job.get("permissions", {}))
            for step in job["steps"]:
                self.assertNotIn("uses", step)

    def test_trusted_workflow(self):
        self.assert_trusted(self.workflow)
        self.assert_refresh(self.refresh)

    def test_workflow_shape_blocks_public_event_escape_routes(self):
        runs = [
            "echo '${{ github.event.comment.body }}'",
            "echo '${{ github.event.issue.title }}'",
            "echo '${{ github.head_ref }}'",
            "echo '${{ github.event.workflow_run.head_branch }}'",
            "echo '${{ github.event.pull_request.title }}'",
            "echo '${{ github.repository }}'",
            "git checkout FETCH_HEAD",
            "git -C /tmp/pr checkout FETCH_HEAD",
            "git switch other",
            "git worktree add /tmp/pr",
            'gh pr checkout "$PR_NUMBER"',
            'gh --repo owner/repo pr checkout "$PR_NUMBER"',
            "git fetch origin refs/pull/1/head",
            'gh run download "$RUN_ID"',
        ]
        for run in runs:
            workflow = copy.deepcopy(self.workflow)
            workflow["jobs"]["validate"]["steps"][-1]["run"] = run
            with self.subTest(run=run), self.assertRaises(AssertionError):
                self.assert_trusted(workflow)
        for guard in (
            "head-checkout",
            "extra-permission",
            "root-string",
            "job-string",
            "discovery-status",
            "root-status",
            "artifact",
        ):
            workflow = copy.deepcopy(self.workflow)
            job = workflow["jobs"]["validate"]
            if guard == "head-checkout":
                job["steps"][0]["with"]["ref"] = (
                    "${{ github.event.pull_request.head.sha }}"
                )
            elif guard == "extra-permission":
                job["permissions"]["contents"] = "write"
            elif guard == "root-string":
                workflow["permissions"] = "write-all"
            elif guard == "job-string":
                job["permissions"] = "write-all"
            elif guard == "discovery-status":
                workflow["jobs"]["discover"]["permissions"] = {"statuses": "write"}
            elif guard == "root-status":
                workflow["permissions"]["statuses"] = "write"
            else:
                job["steps"].append({"uses": "actions/download-artifact@v6"})
            with self.subTest(guard=guard), self.assertRaises(AssertionError):
                self.assert_trusted(workflow)
        for permissions in ({"contents": "read"}, "write-all"):
            refresh = copy.deepcopy(self.refresh)
            refresh["permissions"] = permissions
            with self.assertRaises(AssertionError):
                self.assert_refresh(refresh)
        refresh = copy.deepcopy(self.refresh)
        refresh["jobs"]["refresh"]["permissions"] = {"statuses": "write"}
        with self.assertRaises(AssertionError):
            self.assert_refresh(refresh)
        for expression in (
            "matrix.number",
            "needs.discover.outputs.numbers",
            "steps.prs.outputs.numbers",
            "github.token",
        ):
            workflow = copy.deepcopy(self.workflow)
            workflow["jobs"]["validate"]["steps"][-1]["run"] = (
                "echo '${{ " + expression + " }}'"
            )
            self.assert_trusted(workflow)


if __name__ == "__main__":
    unittest.main()
