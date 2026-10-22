import copy
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase, open_work

import audit
import watchers as w
from test_watchers import reader

PR = json.loads((w.ROOT / "fixtures/audit-pr.json").read_text())
LABEL = "owner/product#7 Fix menu"


class AuditTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.pr = copy.deepcopy(PR)
        self.env = patch.dict(
            os.environ,
            dict(
                ORCH_PR_AUTHOR="author",
                ORCH_PR_SINCE="2026-10-01",
                ORCH_MERGE_GATE_CHECKS="",
                ORCH_BOT_REVIEWER_LOGIN="robot",
                ORCH_PACKS_MAX="50",
                ORCH_WORKTREES_MAX="40",
                ORCH_AUDIT_PAGE_PRS="80",
                ORCH_AUDIT_PAGE_NODES="100",
            ),
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.work = open_work("owner/product owner/other")
        self.work.start()
        self.addCleanup(self.work.stop)
        settings = patch.object(
            audit,
            "repo_settings",
            lambda command, repo, ref, cache: audit.orch_settings.merged(
                [], os.environ
            ),
        )
        settings.start()
        self.addCleanup(settings.stop)

    def lines(self):
        return audit.pr_lines(self.pr)

    def checks(self):
        return self.pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]

    def test_red_latest_per_name_and_status_contexts(self):
        self.checks()["nodes"] = [
            dict(name="unit", conclusion="FAILURE", startedAt="1"),
            dict(name="unit", conclusion="SUCCESS", startedAt="2"),
            dict(name="lint", conclusion="TIMED_OUT", startedAt="1"),
            dict(context="external", state="ERROR"),
        ]
        self.assertEqual(self.lines(), [f"RED {LABEL} [external,lint]"])
        self.checks()["nodes"][1]["conclusion"] = "FAILURE"
        self.assertEqual(self.lines(), [f"RED {LABEL} [external,lint,unit]"])

    def test_threads_count_unresolved(self):
        self.pr["reviewThreads"]["nodes"] = [
            dict(isResolved=False),
            dict(isResolved=True),
        ]
        self.assertEqual(self.lines(), [f"THREADS {LABEL} [1 unresolved]"])

    def test_copilot_latest_configured_bot(self):
        self.pr["reviews"]["nodes"] = [
            dict(author=dict(login="other"), body="Needs a closer look\nIgnore"),
            dict(author=dict(login="robot"), body="Needs a closer look\nFix this"),
        ]
        self.assertEqual(self.lines(), [f"COPILOT {LABEL} [Fix this]"])
        self.pr["reviews"]["nodes"].append(
            dict(author=dict(login="robot"), body="Looks good")
        )
        self.assertEqual(self.lines(), [])

    def test_copilot_no_summary_and_deleted_author(self):
        self.pr["reviews"]["nodes"] = [
            dict(author=None, body="Needs a closer look"),
            dict(author=dict(login="robot"), body="Needs a closer look"),
        ]
        self.assertEqual(self.lines(), [f"COPILOT {LABEL} [see overview]"])

    def test_conflict(self):
        self.pr["mergeable"] = "CONFLICTING"
        self.assertEqual(self.lines(), [f"CONFLICT {LABEL} [conflicts with main]"])
        self.pr["mergeable"] = "UNKNOWN"
        self.assertEqual(self.lines(), [])

    def test_changes_requested(self):
        self.pr["reviewDecision"] = "CHANGES_REQUESTED"
        self.pr["reviewRequests"]["totalCount"] = 0
        self.assertEqual(self.lines(), [f"CHANGES {LABEL}"])

    def test_noreviewer_ready_only(self):
        self.pr["reviewRequests"]["totalCount"] = 0
        self.assertEqual(self.lines(), [f"NOREVIEWER {LABEL}"])
        self.pr["isDraft"] = True
        self.assertEqual(self.lines(), [])

    def approve(self):
        self.pr["reviewDecision"] = "APPROVED"
        self.pr["latestOpinionatedReviews"]["nodes"] = [
            dict(state="APPROVED", commit=dict(oid="head"))
        ]

    def test_approved_current_head(self):
        self.approve()
        self.assertEqual(self.lines(), [f"APPROVED {LABEL}"])

    def test_approved_old_head(self):
        self.approve()
        self.pr["latestOpinionatedReviews"]["nodes"][0]["commit"]["oid"] = "old"
        self.assertEqual(
            self.lines(),
            [f"APPROVED-OLDHEAD {LABEL} [approval is not on the current head]"],
        )

    def test_stacked_full_ci_ran_and_not_run(self):
        self.approve()
        self.pr["baseRefName"] = "feature"
        with patch.dict(os.environ, {"ORCH_MERGE_GATE_CHECKS": "unit|integration"}):
            self.assertEqual(
                self.lines(), [f"APPROVED-STACKED {LABEL} [base feature, full CI ran]"]
            )
            self.checks()["nodes"][0]["name"] = "quick"
            self.assertEqual(
                self.lines(),
                [f"APPROVED-STACKED {LABEL} [base feature, full CI not run]"],
            )
        self.assertEqual(self.lines(), [f"APPROVED-STACKED {LABEL} [base feature]"])

    def test_nested_truncation_is_visible(self):
        self.checks()["pageInfo"]["hasNextPage"] = True
        self.pr["reviewThreads"]["pageInfo"]["hasNextPage"] = True
        self.assertEqual(
            self.lines(),
            [
                f"TRUNCATED {LABEL} [more than 100 review threads]",
                f"TRUNCATED {LABEL} [more than 100 checks]",
            ],
        )

    def test_no_checks_is_per_pr(self):
        for rollup in (
            None,
            dict(contexts=dict(nodes=[], pageInfo=dict(hasNextPage=False))),
        ):
            self.pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = rollup
            self.assertEqual(
                self.lines(), [f"NOCHECKS {LABEL} [no checks on the current head]"]
            )

    def fake(self, *args):
        if args[0] == "gh":
            return json.dumps(
                dict(
                    data=dict(
                        search=dict(nodes=[self.pr], pageInfo=dict(hasNextPage=False))
                    )
                )
            )
        return json.dumps(
            dict(
                result=dict(
                    workspaces=[
                        dict(
                            workspace_id="w1", label="Architect", agent_status="blocked"
                        )
                    ]
                )
            )
        )

    def scan(self, command=None):
        return w.cycle(
            self.base,
            "audit",
            lambda before: audit.collect(command or self.fake, self.base / "slots"),
        )

    def saved(self):
        return json.loads((self.base / "audit.json").read_text())

    def test_first_run_summary_then_new_lines_and_restart_dedupe(self):
        self.pr["mergeable"] = "CONFLICTING"
        self.assertEqual(
            json.loads(self.scan())["events"],
            [
                "AUDIT BASELINE github [1 active lines; see audit.json before.sources]",
                "AUDIT BASELINE herdr [1 active lines; see audit.json before.sources]",
                "AUDIT BASELINE slots [0 active lines; see audit.json before.sources]",
                "AUDIT BASELINE legacy [0 active lines; see audit.json before.legacy]",
            ],
        )
        self.assertIsNone(self.scan())
        self.pr["reviewDecision"] = "CHANGES_REQUESTED"
        self.assertEqual(json.loads(self.scan())["events"], [f"CHANGES {LABEL}"])
        self.assertIsNone(self.scan())
        self.assertEqual(self.saved()["fails"], 0)

    def test_no_checks_does_not_fail_collection(self):
        self.pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
        raw = audit.collect(self.fake, self.base / "slots")
        self.assertEqual(raw["unreadable"], {})
        self.assertIn(
            f"NOCHECKS {LABEL} [no checks on the current head]",
            raw["sources"]["github"],
        )
        self.assertIn("BLOCKED worker w1 Architect", raw["sources"]["herdr"])

    def test_workspace_role_agnostic_blocked(self):
        self.assertEqual(
            audit.workspace_lines(self.fake), ["BLOCKED worker w1 Architect"]
        )

    def test_failed_sources_named_isolated_preserved_and_recovered(self):
        self.pr["mergeable"] = "CONFLICTING"
        self.scan()

        def failed(*args):
            if args[0] == "gh":
                raise ValueError("offline")
            return self.fake(*args)

        self.assertEqual(
            json.loads(self.scan(failed))["events"], ["READFAIL github: offline"]
        )
        self.assertIsNone(self.scan(failed))
        self.assertIn(
            f"CONFLICT {LABEL} [conflicts with main]",
            self.saved()["before"]["sources"]["github"],
        )
        self.assertEqual(json.loads(self.scan())["events"], ["github READFAIL cleared"])

        def herdr_failed(*args):
            if args[0] != "gh":
                return "{invalid"
            return self.fake(*args)

        self.assertIn("READFAIL herdr:", self.scan(herdr_failed))

    def test_merged_closed_and_departed_prs_pruned_without_extra_calls(self):
        self.pr["mergeable"] = "CONFLICTING"
        self.scan()
        for state in ("MERGED", "CLOSED"):
            self.pr["state"] = state
            self.scan()
            self.assertEqual(self.saved()["before"]["sources"]["github"], [])

        def gone(*args):
            if args[0] == "gh":
                return json.dumps(
                    dict(
                        data=dict(
                            search=dict(nodes=[], pageInfo=dict(hasNextPage=False))
                        )
                    )
                )
            return self.fake(*args)

        self.scan(gone)
        self.assertEqual(self.saved()["before"]["sources"]["github"], [])

    def test_one_paged_search_uses_config_and_no_per_pr_calls(self):
        pages = [
            dict(nodes=[self.pr], pageInfo=dict(hasNextPage=True, endCursor="next")),
            dict(nodes=[], pageInfo=dict(hasNextPage=False)),
        ]
        calls = []

        def command(*args):
            calls.append(args)
            return json.dumps(dict(data=dict(search=pages.pop(0))))

        self.assertEqual(audit.fetch_prs(command), [self.pr])
        self.assertEqual(len(calls), 2)
        self.assertIn(
            "q=is:pr is:open author:author repo:owner/product repo:owner/other updated:>=2026-10-01",
            calls[0],
        )
        self.assertIn("cursor=next", calls[1])
        calls.clear()
        self.assertEqual(
            audit.fetch_prs(lambda *args: calls.append(args) or self.fake(*args)),
            [self.pr],
        )
        self.assertEqual(len(calls), 1)

    def test_graphql_errors_and_bad_cursor_are_readfail(self):
        for response in (
            dict(errors=[dict(message="denied")]),
            dict(
                data=dict(
                    search=dict(
                        nodes=[], pageInfo=dict(hasNextPage=True, endCursor=None)
                    )
                )
            ),
        ):
            with self.assertRaises(ValueError):
                audit.fetch_prs(lambda *args: json.dumps(response))

    def test_missing_config_and_invalid_limits(self):
        with patch.dict(os.environ, {"ORCH_PR_AUTHOR": ""}):
            with self.assertRaises(ValueError):
                audit.fetch_prs(self.fake)
        for value in ("0", "101"):
            with patch.dict(os.environ, {"ORCH_AUDIT_PAGE_PRS": value}):
                with self.assertRaises(ValueError):
                    audit.fetch_prs(self.fake)

    def test_stale_slots_directories_live_and_denied_pids(self):
        slots = self.base / "slots"
        slots.mkdir()
        (slots / "dir").mkdir()
        (slots / "dead").write_text("999999")
        (slots / "empty").write_text("")
        (slots / "live").write_text(str(os.getpid()))
        sources, errors = audit.slot_sources(slots)
        self.assertEqual(
            sorted(line for lines in sources.values() for line in lines),
            ["SLOTDIR dir", "STALESLOT dead pid=999999"],
        )
        self.assertIn("slot:empty", errors)
        with patch("watcher_platform.os.kill", side_effect=PermissionError):
            self.assertEqual(audit.slot_sources(slots)[0]["slot:live"], [])

    def test_slot_readfail_named(self):
        with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            raw = audit.collect(self.fake, self.base / "slots")
        self.assertEqual(raw["unreadable"], {"slots": "READFAIL slots: denied"})

    def repo_counts(self, packs, worktrees):
        common = self.base / "common"
        packdir = common / "objects/pack"
        packdir.mkdir(parents=True, exist_ok=True)
        for path in packdir.iterdir():
            path.unlink()
        for n in range(packs):
            (packdir / f"{n}.pack").touch()
        wt = common / "worktrees"
        wt.mkdir(exist_ok=True)
        for path in wt.iterdir():
            path.rmdir()
        for n in range(worktrees):
            (wt / str(n)).mkdir()
        return lambda *args: str(common)

    def test_packs_boundary_and_steps(self):
        self.assertEqual(audit.repository_lines(self.repo_counts(50, 0), "/repo"), [])
        self.assertEqual(
            audit.repository_lines(self.repo_counts(51, 0), "/repo"),
            ["PACKS repo has over 50 packs; automatic cleanup is not keeping up"],
        )
        self.assertEqual(
            audit.repository_lines(self.repo_counts(100, 0), "/repo"),
            ["PACKS repo has over 50 packs; automatic cleanup is not keeping up"],
        )
        self.assertEqual(
            audit.repository_lines(self.repo_counts(101, 0), "/repo"),
            ["PACKS repo has over 100 packs; automatic cleanup is not keeping up"],
        )
        with patch.dict(os.environ, {"ORCH_PACKS_MAX": "10"}):
            self.assertIn(
                "over 100 packs",
                audit.repository_lines(self.repo_counts(101, 0), "/repo")[0],
            )

    def test_worktrees_boundary_and_steps(self):
        self.assertEqual(audit.repository_lines(self.repo_counts(0, 40), "/repo"), [])
        self.assertEqual(
            audit.repository_lines(self.repo_counts(0, 41), "/repo"),
            ["WORKTREES repo has over 40 worktrees; merged ones are not being removed"],
        )
        self.assertEqual(
            audit.repository_lines(self.repo_counts(0, 60), "/repo"),
            ["WORKTREES repo has over 40 worktrees; merged ones are not being removed"],
        )
        self.assertEqual(
            audit.repository_lines(self.repo_counts(0, 61), "/repo"),
            ["WORKTREES repo has over 60 worktrees; merged ones are not being removed"],
        )

    def test_worktrees_threshold_below_bucket_names_threshold(self):
        with patch.dict(os.environ, {"ORCH_WORKTREES_MAX": "10"}):
            self.assertEqual(
                audit.repository_lines(self.repo_counts(0, 11), "/repo"),
                [
                    "WORKTREES repo has over 10 worktrees; merged ones are not being removed"
                ],
            )
        with patch.dict(os.environ, {"ORCH_WORKTREES_MAX": "25"}):
            self.assertIn(
                "over 25 worktrees",
                audit.repository_lines(self.repo_counts(0, 30), "/repo")[0],
            )

    def test_missing_worktrees_directory_counts_zero(self):
        command = self.repo_counts(0, 0)
        (self.base / "common/worktrees").rmdir()
        self.assertEqual(audit.repository_lines(command, "/repo"), [])

    def test_unreadable_pack_directory_is_named_readfail(self):
        command = self.repo_counts(60, 0)
        real_scandir = os.scandir

        def scandir(path):
            if str(path).endswith("objects/pack"):
                raise PermissionError("pack denied")
            return real_scandir(path)

        with (
            open_work("owner/product owner/other", "/repo"),
            patch("audit.os.scandir", side_effect=scandir),
        ):
            raw = audit.collect(command, self.base / "slots")
        self.assertEqual(
            raw["unreadable"]["git:/repo"], "READFAIL git:/repo: pack denied"
        )
        self.assertNotIn("git:/repo", raw["sources"])

    def test_unreadable_worktrees_directory_is_named_readfail(self):
        command = self.repo_counts(0, 5)
        real_scandir = os.scandir

        def scandir(path):
            if str(path).endswith("/worktrees"):
                raise PermissionError("worktrees denied")
            return real_scandir(path)

        with patch("audit.os.scandir", side_effect=scandir):
            with self.assertRaises(PermissionError):
                audit.repository_lines(command, "/repo")

    def test_watched_repos_and_git_readfail(self):
        with open_work("owner/product owner/other", "/first /second"):

            def command(*args):
                if args[0] == "git":
                    raise subprocess.CalledProcessError(
                        128, args, stderr="not a git repo"
                    )
                return self.fake(*args)

            raw = audit.collect(command, self.base / "slots")
        self.assertEqual(
            raw["unreadable"],
            {
                "git:/first": "READFAIL git:/first: not a git repo",
                "git:/second": "READFAIL git:/second: not a git repo",
            },
        )

    def test_event_history_bounded_and_sequence_monotonic(self):
        self.scan()
        for n in range(110):
            self.pr["title"] = str(n)
            self.pr["mergeable"] = "CONFLICTING"
            self.scan()
        events = self.saved()["events"]
        self.assertEqual(len(events), 100)
        self.assertEqual(events[-1]["seq"], 111)
        self.assertEqual(events[0]["seq"], 12)

    def test_audit_heartbeat_interval_and_delivery(self):
        self.scan()
        cursor, now = {}, time.time()
        self.assertIn(
            "AUDIT BASELINE", reader.read_once(self.base, cursor, now, 900, ["audit"])
        )
        self.assertIsNone(
            reader.read_once(self.base, cursor, now + 1801, 900, ["audit"])
        )
        self.assertIsNone(
            reader.read_once(self.base, cursor, now + 3601, 900, ["audit"])
        )
        self.assertIsNone(
            reader.read_once(self.base, cursor, now + 4499, 900, ["audit"])
        )
        self.assertIn(
            "HEARTBEAT STALE",
            reader.read_once(self.base, cursor, now + 4501, 900, ["audit"]),
        )
        self.assertIn(
            "HEARTBEAT RECOVERED",
            reader.read_once(self.base, cursor, now, 900, ["audit"]),
        )

    def test_oversized_slot_pid_is_isolated_with_heartbeat(self):
        slots = self.base / "slots"
        slots.mkdir()
        (slots / "huge").write_text("9999999999999999999999999999999999999999999")
        self.scan()
        state = self.saved()
        self.assertEqual(state["fails"], 0)
        self.assertIn("slot:huge", state["before"]["unreadable"])
        self.assertTrue(
            state["before"]["unreadable"]["slot:huge"].startswith("READFAIL slot:huge:")
        )
        self.assertIn("github", state["before"]["sources"])
        self.assertTrue((self.base / "audit.heartbeat").exists())

    def test_approved_null_commit_is_old_head(self):
        self.approve()
        self.pr["latestOpinionatedReviews"]["nodes"][0]["commit"] = None
        self.assertEqual(
            self.lines(),
            [f"APPROVED-OLDHEAD {LABEL} [approval is not on the current head]"],
        )

    def test_partial_source_recovery_does_not_repeat_unchanged_error(self):
        failed = {"gh", "herdr"}

        def command(*args):
            source = "gh" if args[0] == "gh" else "herdr"
            if source in failed:
                raise ValueError("offline")
            if source == "herdr":
                return json.dumps(dict(result=dict(workspaces=[])))
            return self.fake(*args)

        events = json.loads(self.scan(command))["events"]
        self.assertIn("READFAIL github: offline", events)
        self.assertIn("READFAIL herdr: offline", events)
        failed.remove("herdr")
        self.assertEqual(
            json.loads(self.scan(command))["events"],
            [
                "herdr READFAIL cleared",
                "AUDIT BASELINE herdr [0 active lines; see audit.json before.sources]",
            ],
        )
        self.assertEqual(
            self.saved()["before"]["unreadable"], {"github": "READFAIL github: offline"}
        )
        failed.clear()
        self.assertEqual(
            json.loads(self.scan(command))["events"],
            [
                "github READFAIL cleared",
                "AUDIT BASELINE github [0 active lines; see audit.json before.sources]",
                "AUDIT BASELINE legacy [0 active lines; see audit.json before.legacy]",
            ],
        )

    def test_empty_command_response_is_isolated_by_source(self):
        from types import SimpleNamespace

        for source in ("github", "herdr"):
            with self.subTest(source=source):
                self.base = Path(self.tmp.name) / source

                def command(*args):
                    current = "github" if args[0] == "gh" else "herdr"
                    if current == source:
                        with patch.object(
                            w.runtime,
                            "run",
                            return_value=SimpleNamespace(returncode=0, stdout="\n"),
                        ):
                            return w.command(*args)
                    return self.fake(*args)

                self.scan(command)
                state = self.saved()
                self.assertEqual(state["fails"], 0)
                self.assertTrue(
                    state["before"]["unreadable"][source].startswith(
                        f"READFAIL {source}:"
                    )
                )
                other = "herdr" if source == "github" else "github"
                self.assertIn(other, state["before"]["sources"])
                self.assertTrue((self.base / "audit.heartbeat").exists())

    def test_malformed_successful_responses_are_isolated_by_source(self):
        for source in ("github", "herdr"):
            with self.subTest(source=source):
                self.base = Path(self.tmp.name) / source

                def command(*args):
                    current = "github" if args[0] == "gh" else "herdr"
                    if current == source:
                        return (
                            "[]"
                            if source == "github"
                            else '{"result":{"workspaces":[null]}}'
                        )
                    return self.fake(*args)

                self.scan(command)
                state = self.saved()
                self.assertEqual(state["fails"], 0)
                self.assertTrue(
                    state["before"]["unreadable"][source].startswith(
                        f"READFAIL {source}:"
                    )
                )
                other = "herdr" if source == "github" else "github"
                self.assertIn(other, state["before"]["sources"])
                self.assertTrue((self.base / "audit.heartbeat").exists())
