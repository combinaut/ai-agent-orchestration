import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase, open_work
import watchers as w

PR = json.loads((w.ROOT / "fixtures" / "audit-pr.json").read_text())


def pr(number, decision="APPROVED", branch="feature"):
    row = json.loads(json.dumps(PR))
    row.update(number=number, headRefName=branch, reviewDecision=decision)
    row["latestOpinionatedReviews"] = {"nodes": [{"state": "APPROVED", "commit": {"oid": row["headRefOid"]}}]}
    return row


WORKSPACES = {"result": {"workspaces": [
    {"workspace_id": "wW", "tokens": {"owner": "wArch"},
     "worktree": {"checkout_path": "/wt/product/feature", "repo_root": "/repo/product"}},
    {"workspace_id": "wArch", "tokens": {}, "worktree": {"checkout_path": "/repo/product", "repo_root": "/repo/product"}}]}}
PANES = []
REMOTES = {"/repo/product": "git@github.com:owner/product.git\n", "/repo/other": "git@github.com:owner/other.git\n"}
WORKTREES = "worktree /repo/product\nbranch refs/heads/main\n\nworktree /wt/product/feature\nbranch refs/heads/feature\n"


class PullsTest(IsolatedTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"ORCH_PR_AUTHOR": "author"})
        env.start()
        self.addCleanup(env.stop)
        work = open_work(PR["repository"]["nameWithOwner"])
        work.start()
        self.addCleanup(work.stop)
        self.calls = []

    def command(self, prs, departed=None):
        def run(*argv):
            self.calls.append(argv)
            if argv[:3] == ("gh", "api", "graphql"):
                return json.dumps({"data": {"search": {"nodes": prs, "pageInfo": {"hasNextPage": False}}}})
            if argv[1:3] == ("workspace", "list"):
                return json.dumps(WORKSPACES)
            if argv[1:3] == ("pane", "list"):
                return json.dumps({"result": {"panes": PANES}})
            if argv[0] == "git" and argv[3:] == ("rev-parse", "--show-toplevel"):
                return "/wt/product/pane\n"
            if argv[0] == "git" and argv[-1] == "--git-common-dir":
                return "/repo/product/.git\n"
            if argv[0] == "git" and argv[3:5] == ("remote", "get-url"):
                return REMOTES[argv[2]]
            if argv[0] == "git":
                return WORKTREES
            if argv[:2] == ("gh", "api"):
                return json.dumps((departed or {})[argv[2]])
            raise AssertionError(argv)
        return run

    def test_owner_follows_branch_worktree_and_workspace_tag(self):
        owners = w.pull_owners(self.command([]), [pr(7), pr(8, branch="nobody")])
        self.assertEqual(owners, {PR["repository"]["nameWithOwner"] + "#7": "wArch"})

    def test_baseline_then_state_changes_with_owner(self):
        repo = PR["repository"]["nameWithOwner"]
        raw = w.collect_pulls(self.command([pr(7, decision="REVIEW_REQUIRED")]), None)
        state, events = w.reduce_pulls(None, raw)
        self.assertTrue(events[-1].startswith("PULLS BASELINE"))
        raw = w.collect_pulls(self.command([pr(7)]), state)
        state, events = w.reduce_pulls(state, raw)
        self.assertIn(f"APPROVED {repo}#7 owner=wArch {PR['title']}", events)
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([pr(7)]), state))
        self.assertEqual(events, [])
        departed = {f"repos/{repo}/pulls/7": {"state": "closed", "merged": True}}
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([], departed), state))
        self.assertIn(f"APPROVED {repo}#7 cleared", events)
        self.assertEqual(events[-1], f"MERGED {repo}#7 owner=wArch {PR['title']}")
        self.assertEqual(state["prs"], {})

    def test_first_poll_saves_active_lines_without_start_events_then_reports_a_new_line(self):
        repo = PR["repository"]["nameWithOwner"]
        state, events = w.reduce_pulls(None, w.collect_pulls(self.command([pr(7)]), None))
        self.assertEqual(events, ["PULLS BASELINE [1 active lines]"])

        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([pr(7), pr(8)]), state))
        self.assertEqual(events, [f"APPROVED {repo}#8 owner=wArch {PR['title']}"])

    def test_first_poll_keeps_read_failures_in_the_silent_baseline(self):
        raw = w.collect_pulls(self.command([pr(7)]), None)
        raw["unreadable"] = ["owners"]
        state, events = w.reduce_pulls(None, raw)
        self.assertEqual(events, ["PULLS BASELINE [1 active lines]"])
        _, events = w.reduce_pulls(state, raw)
        self.assertEqual(events, ["PULLS READFAIL owners"])

    def test_departed_reads_are_capped_per_poll(self):
        before = {"prs": {f"o/r#{n}": dict(title="t", owner=None, lines={}) for n in range(w.PULLS_DEPARTED_MAX + 5)},
                  "unreadable": []}
        departed = {f"repos/o/r/pulls/{n}": {"state": "open", "merged": False} for n in range(w.PULLS_DEPARTED_MAX + 5)}
        w.collect_pulls(self.command([], departed), before)
        self.assertEqual(sum(argv[:2] == ("gh", "api") and argv[2].startswith("repos/") for argv in self.calls),
                         w.PULLS_DEPARTED_MAX)

    def test_owner_lookup_failure_is_a_readfail_and_events_still_flow(self):
        def broken(*argv):
            if argv[1:3] == ("workspace", "list"):
                raise OSError("no herdr")
            return self.command([pr(7)])(*argv)
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([]), None))
        state, events = w.reduce_pulls(state, w.collect_pulls(broken, state))
        self.assertEqual(events[0], "PULLS READFAIL owners")
        self.assertIn(f"APPROVED {PR['repository']['nameWithOwner']}#7 owner=? {PR['title']}", events)

    def test_same_named_repos_in_other_orgs_do_not_share_owners(self):
        other = pr(9)
        other["repository"] = {"nameWithOwner": "elsewhere/product", "defaultBranchRef": {"name": "main"}}
        owners = w.pull_owners(self.command([]), [pr(7), other])
        self.assertEqual(owners, {"owner/product#7": "wArch"})

    def test_owner_lookup_outage_keeps_known_owners(self):
        repo = PR["repository"]["nameWithOwner"]
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([pr(7, decision="REVIEW_REQUIRED")]), None))
        state, _ = w.reduce_pulls(state, w.collect_pulls(self.command([pr(7, decision="REVIEW_REQUIRED")]), state))

        def outage(prs, departed=None):
            def run(*argv):
                if argv[1:3] == ("workspace", "list"):
                    raise OSError("no herdr")
                return self.command(prs, departed)(*argv)
            return run
        state, _ = w.reduce_pulls(state, w.collect_pulls(outage([pr(7, decision="REVIEW_REQUIRED")]), state))
        self.assertEqual(state["prs"][f"{repo}#7"]["owner"], "wArch")
        merged = {f"repos/{repo}/pulls/7": {"state": "closed", "merged": True}}
        _, events = w.reduce_pulls(state, w.collect_pulls(outage([], merged), state))
        self.assertEqual(events[-1], f"MERGED {repo}#7 owner=wArch {PR['title']}")

    def test_departed_reads_stop_at_the_poll_deadline(self):
        before = {"prs": {f"o/r#{n}": dict(title="t", owner=None, lines={}) for n in range(5)}, "unreadable": []}
        departed = {f"repos/o/r/pulls/{n}": {"state": "closed", "merged": True} for n in range(5)}
        ticks = iter([0, 0, 30, w.PULLS_DEPARTED_SECONDS, 999, 999])
        raw = w.collect_pulls(self.command([], departed), before, clock=lambda: next(ticks))
        self.assertEqual(len(raw["departed"]), 2)

    def test_a_failed_departed_read_does_not_block_the_others(self):
        before = {"prs": {"o/r#1": dict(title="a", owner=None, lines={"RED": "RED o/r#1 a"}),
                          "o/r#2": dict(title="b", owner=None, lines={})}, "unreadable": []}
        departed = {"repos/o/r/pulls/2": {"state": "closed", "merged": True}}
        state, events = w.reduce_pulls(before, w.collect_pulls(self.command([], departed), before))
        self.assertIn("PULLS READFAIL pr:o/r#1", events)
        self.assertIn("MERGED o/r#2 owner=? b", events)
        self.assertEqual(state["prs"]["o/r#1"]["misses"], 1)

    def test_failing_departures_go_to_the_back_of_the_queue(self):
        before = {"prs": {f"o/r#{n}": dict(title="t", owner=None, lines={}, misses=5) for n in range(w.PULLS_DEPARTED_MAX)},
                  "unreadable": []}
        before["prs"]["o/r#99"] = dict(title="new", owner=None, lines={})
        departed = {"repos/o/r/pulls/99": {"state": "closed", "merged": True}}
        _, events = w.reduce_pulls(before, w.collect_pulls(self.command([], departed), before))
        self.assertIn("MERGED o/r#99 owner=? new", events)

    def test_departed_reads_stop_at_their_reserved_window(self):
        before = {"prs": {"o/r#1": dict(title="t", owner=None, lines={})}, "unreadable": []}
        ticks = iter([0, w.PULLS_DEPARTED_SECONDS + 1])
        raw = w.collect_pulls(self.command([]), before, clock=lambda: next(ticks))
        self.assertEqual(raw["departed"], {})
        self.assertFalse(any(argv[2].startswith("repos/") for argv in self.calls if argv[:2] == ("gh", "api")))

    def test_an_unattempted_failed_departure_stays_unreadable(self):
        before = {"prs": {"o/r#1": dict(title="a", owner=None, lines={}, misses=2),
                          "o/r#2": dict(title="b", owner=None, lines={})}, "unreadable": ["pr:o/r#1"]}
        ticks = iter([0, w.PULLS_DEPARTED_SECONDS])
        raw = w.collect_pulls(self.command([]), before, clock=lambda: next(ticks))
        self.assertIn("pr:o/r#1", raw["unreadable"])
        _, events = w.reduce_pulls(before, raw)
        self.assertNotIn("PULLS READFAIL cleared", events)

    def test_waiting_departures_keep_their_miss_count_so_the_queue_rotates(self):
        before = {"prs": {f"o/r#{n}": dict(title="t", owner=None, lines={}, misses=1) for n in range(w.PULLS_DEPARTED_MAX + 3)},
                  "unreadable": []}
        state, _ = w.reduce_pulls(before, w.collect_pulls(self.command([], {}), before))
        misses = sorted(pr["misses"] for pr in state["prs"].values())
        self.assertEqual(misses, [1] * 3 + [2] * w.PULLS_DEPARTED_MAX)
        tried = [argv[2] for argv in self.calls if argv[:2] == ("gh", "api") and argv[2].startswith("repos/")]
        self.calls.clear()
        w.collect_pulls(self.command([], {}), state)
        later = [argv[2] for argv in self.calls if argv[:2] == ("gh", "api") and argv[2].startswith("repos/")]
        self.assertTrue(set(later) - set(tried))

    def test_a_departure_still_open_keeps_last_known_state(self):
        before = {"prs": {"o/r#3": dict(title="t", owner=None, lines={"RED": "RED o/r#3 t"})}, "unreadable": []}
        departed = {"repos/o/r/pulls/3": {"state": "open", "merged": False}}
        state, events = w.reduce_pulls(before, w.collect_pulls(self.command([], departed), before))
        self.assertEqual(events, [])
        self.assertEqual(state["prs"]["o/r#3"]["lines"], before["prs"]["o/r#3"]["lines"])

    def test_pulls_history_keeps_the_latest_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for n in range(w.HISTORY_BATCHES + 5):
                lines = {"RED": f"RED o/r#1 T [{n}]"} if n % 2 else {}
                w.cycle(base, "pulls", lambda before: dict(prs={"o/r#1": dict(title="T", owner=None, lines=lines)},
                                                         departed={}, unreadable=[]))
            events = json.loads((base / "pulls.json").read_text())["events"]
        self.assertEqual(len(events), w.HISTORY_BATCHES)
        self.assertEqual(events[-1]["seq"] - events[0]["seq"], w.HISTORY_BATCHES - 1)

    def test_slow_owner_lookup_cannot_starve_departed_reads(self):
        repo = PR["repository"]["nameWithOwner"]
        before = {"prs": {f"{repo}#7": dict(title="Fix", owner="wArch", lines={"APPROVED": f"APPROVED {repo}#7 Fix"})},
                  "unreadable": []}
        merged = {f"repos/{repo}/pulls/7": {"state": "closed", "merged": True}}
        elapsed = [0]
        run = self.command([pr(8)], merged)

        def slow(*argv):
            if argv[0] == "git" or argv[1:3] == ("workspace", "list"):
                elapsed[0] += 61
            return run(*argv)
        for _ in range(3):
            state, events = w.reduce_pulls(before, w.collect_pulls(slow, before, clock=lambda: elapsed[0]))
            self.assertEqual(events[-1], f"MERGED {repo}#7 owner=wArch Fix")

    def test_a_workspace_found_through_its_pane_directory_has_its_owner(self):
        pane_workspace = {"workspace_id": "wP", "tokens": {"owner": "wArch2"}}
        pane = {"workspace_id": "wP", "pane_id": "wP:p1", "cwd": "/wt/product/pane/sub"}
        listing = WORKTREES + "\nworktree /wt/product/pane\nbranch refs/heads/pane-branch\n"
        with patch.dict(WORKSPACES["result"], {"workspaces": WORKSPACES["result"]["workspaces"] + [pane_workspace]}), \
                patch(__name__ + ".PANES", [pane]), patch(__name__ + ".WORKTREES", listing):
            owners = w.pull_owners(self.command([]), [pr(7), pr(9, branch="pane-branch")])
        self.assertEqual(owners, {"owner/product#7": "wArch", "owner/product#9": "wArch2"})

    def test_an_ownerless_workspace_on_the_same_checkout_keeps_the_owner(self):
        reviewer = {"workspace_id": "wR", "tokens": {}, "worktree": {"checkout_path": "/wt/product/feature", "repo_root": "/repo/product"}}
        with patch.dict(WORKSPACES["result"], {"workspaces": WORKSPACES["result"]["workspaces"] + [reviewer]}):
            owners = w.pull_owners(self.command([]), [pr(7)])
        self.assertEqual(owners, {"owner/product#7": "wArch"})

    def test_an_unowned_worktree_of_a_second_clone_keeps_the_owner(self):
        second = {"workspace_id": "wS", "tokens": {}, "worktree": {"checkout_path": "/wt/second/feature", "repo_root": "/repo/second"}}
        run = self.command([])

        def command(*argv):
            if argv[0] == "git" and argv[2] == "/repo/second" and argv[3:5] == ("remote", "get-url"):
                return REMOTES["/repo/product"]
            if argv[0] == "git" and argv[2] == "/repo/second" and argv[3] == "worktree":
                return "worktree /repo/second\nbranch refs/heads/main\n\nworktree /wt/second/feature\nbranch refs/heads/feature\n"
            return run(*argv)
        with patch.dict(WORKSPACES["result"], {"workspaces": WORKSPACES["result"]["workspaces"] + [second]}):
            owners = w.pull_owners(command, [pr(7)])
        self.assertEqual(owners, {"owner/product#7": "wArch"})

    def test_a_root_without_origin_or_folder_is_no_owner_failure(self):
        extra = [{"workspace_id": "wL", "tokens": {"owner": "wX"}, "worktree": {"checkout_path": "/repo/local", "repo_root": "/repo/local"}},
                 {"workspace_id": "wG", "tokens": {"owner": "wX"}, "worktree": {"checkout_path": "/repo/gone", "repo_root": "/repo/gone"}}]
        run = self.command([])

        def command(*argv):
            if argv[0] == "git" and argv[2] == "/repo/local":
                raise subprocess.CalledProcessError(2, argv, stderr="error: No such remote 'origin'")
            if argv[0] == "git" and argv[2] == "/repo/gone":
                raise subprocess.CalledProcessError(128, argv, stderr="fatal: cannot change to '/repo/gone': No such file or directory")
            return run(*argv)
        failed = []
        with patch.dict(WORKSPACES["result"], {"workspaces": WORKSPACES["result"]["workspaces"] + extra}):
            owners = w.pull_owners(command, [pr(7)], failed)
        self.assertEqual((owners, failed), ({"owner/product#7": "wArch"}, []))

    def test_owner_lookup_lists_worktrees_only_for_watched_repos(self):
        unrelated = {"workspace_id": "wU", "tokens": {"owner": "wX"},
                     "worktree": {"checkout_path": "/repo/other", "repo_root": "/repo/other"}}
        with patch.dict(WORKSPACES["result"], {"workspaces": WORKSPACES["result"]["workspaces"] + [unrelated]}):
            w.pull_owners(self.command([]), [pr(7)])
        self.assertFalse([argv for argv in self.calls if argv[0] == "git" and argv[2] == "/repo/other" and argv[3] == "worktree"])

    def test_one_failing_root_leaves_the_other_owners(self):
        broken = {"workspace_id": "wB", "tokens": {"owner": "wY"},
                  "worktree": {"checkout_path": "/x/product", "repo_root": "/x/product"}}
        run = self.command([])

        def failing(*argv):
            if argv[0] == "git" and argv[2] == "/x/product":
                raise OSError("slow disk")
            return run(*argv)
        failed = []
        with patch.dict(WORKSPACES["result"], {"workspaces": WORKSPACES["result"]["workspaces"] + [broken]}):
            owners = w.pull_owners(failing, [pr(7)], failed)
        self.assertEqual(owners, {f"{PR['repository']['nameWithOwner']}#7": "wArch"})
        self.assertEqual(failed, ["*/product"])

    def test_no_matching_checkout_keeps_old_owners_in_other_repositories_too(self):
        other = pr(9, branch="gone")
        other["repository"] = {"nameWithOwner": "owner/tool", "defaultBranchRef": {"name": "main"}}
        before = {"prs": {"owner/product#7": dict(title="a", owner="wOld", lines={}),
                          "owner/tool#9": dict(title="b", owner="wOld", lines={})}, "unreadable": []}
        tool = {"workspace_id": "wT", "tokens": {}, "worktree": {"checkout_path": "/repo/tool", "repo_root": "/repo/tool"}}
        run = self.command([pr(7, branch="gone"), other])

        def failing(*argv):
            if argv[0] == "git" and argv[2] == "/repo/product":
                raise OSError("slow disk")
            if argv[0] == "git" and argv[2] == "/repo/tool":
                return "git@github.com:owner/tool.git\n" if argv[3] == "remote" else "worktree /repo/tool\nbranch refs/heads/main\n"
            return run(*argv)
        with patch.dict(WORKSPACES["result"], {"workspaces": WORKSPACES["result"]["workspaces"] + [tool]}):
            raw = w.collect_pulls(failing, before)
        self.assertEqual(raw["prs"]["owner/product#7"]["owner"], "wOld")
        self.assertEqual(raw["prs"]["owner/tool#9"]["owner"], "wOld")

    def test_no_matching_checkout_keeps_old_owners_across_organizations(self):
        other = pr(9, branch="gone")
        other["repository"] = {"nameWithOwner": "elsewhere/product", "defaultBranchRef": {"name": "main"}}
        before = {"prs": {"owner/product#7": dict(title="a", owner="wOld", lines={}),
                          "elsewhere/product#9": dict(title="b", owner="wOld", lines={})}, "unreadable": []}
        second = {"workspace_id": "wE", "tokens": {}, "worktree": {"checkout_path": "/e/product", "repo_root": "/e/product"}}
        run = self.command([pr(7, branch="gone"), other])

        def failing(*argv):
            if argv[0] == "git" and argv[2] == "/repo/product" and argv[3] == "worktree":
                raise OSError("slow disk")
            if argv[0] == "git" and argv[2] == "/e/product":
                return "git@github.com:elsewhere/product.git\n" if argv[3] == "remote" else "worktree /e/product\nbranch refs/heads/main\n"
            return run(*argv)
        with patch.dict(WORKSPACES["result"], {"workspaces": WORKSPACES["result"]["workspaces"] + [second]}):
            raw = w.collect_pulls(failing, before)
        self.assertEqual(raw["prs"]["owner/product#7"]["owner"], "wOld")
        self.assertEqual(raw["prs"]["elsewhere/product#9"]["owner"], "wOld")

    def test_a_clone_in_a_differently_named_directory_still_maps_its_owners(self):
        rows = {"result": {"workspaces": [
            {"workspace_id": "wW", "tokens": {"owner": "wArch"}, "worktree": {"checkout_path": "/wt/feature", "repo_root": "/repo/product-custom"}}]}}
        run = self.command([])

        def renamed(*argv):
            if argv[1:3] == ("workspace", "list"):
                self.calls.append(argv)
                return json.dumps(rows)
            if argv[0] == "git" and argv[2] == "/repo/product-custom":
                self.calls.append(argv)
                return "git@github.com:owner/product.git\n" if argv[3] == "remote" else "worktree /wt/feature\nbranch refs/heads/feature\n"
            return run(*argv)
        self.assertEqual(w.pull_owners(renamed, [pr(7)]), {"owner/product#7": "wArch"})

    def test_logged_search_gap_keeps_seven_events_and_owners_across_restart(self):
        rows = []
        for number in (46, 357, 358, 32111, 32198, 32904):
            row = pr(number, decision="REVIEW_REQUIRED")
            if number == 46:
                row["reviewThreads"]["nodes"] = [{"id": "thread-46", "isResolved": False}]
            else:
                row["reviews"]["nodes"] = [{"id": f"review-{number}", "author": {"login": "copilot-pull-request-reviewer"},
                                            "body": "Needs a closer look\nSame review"}]
            if number == 32111:
                row["reviewDecision"] = "APPROVED"
                row["latestOpinionatedReviews"]["nodes"] = [{"state": "APPROVED", "commit": {"oid": "old"}}]
            rows.append(row)
        repo = PR["repository"]["nameWithOwner"]
        departed = {f"repos/{repo}/pulls/{row['number']}": {"state": "open", "merged": False} for row in rows}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            def poll(rows, departed=None):
                result = w.cycle(base, "pulls", lambda before: w.collect_pulls(self.command(rows, departed), before))
                return json.loads(result)["events"] if result else []
            self.assertEqual(poll(rows), ["PULLS BASELINE [7 active lines]"])
            self.assertFalse(any("cleared" in event for event in poll([], departed)))
            self.assertFalse(any("owner=" in event for event in poll(rows)))
            state = json.loads((base / "pulls.json").read_text())["before"]
            self.assertEqual(state["prs"][f"{repo}#32111"]["owner"], "wArch")

    def test_recycled_architect_keeps_owner_when_branch_workspace_is_missing(self):
        row = pr(32111)
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        # The architect workspace survives a pane recycle, but the matching worker checkout is absent.
        work = {"workspaces": [{"id": "wArch", "checkout": "/repo/product", "repo_root": "/repo/product", "owner": None}],
                "panes": [{"pane": "wArch:p25", "workspace": "wArch"}]}
        with patch.object(w.discovery, "open_work", return_value=work):
            state, _ = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(state["prs"]["owner/product#32111"]["owner"], "wArch")

    def test_new_review_id_and_head_and_thread_identity_are_real_changes(self):
        row = pr(7)
        row["latestOpinionatedReviews"]["nodes"][0]["commit"]["oid"] = "old"
        row["reviews"]["nodes"] = [{"id": "review-1", "author": {"login": "copilot-pull-request-reviewer"},
                                     "body": "Needs a closer look\nCheck it"}]
        row["reviewThreads"]["nodes"] = [{"id": "thread-1", "isResolved": False}]
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        # Restart with unchanged IDs or edited display text is silent.
        state = json.loads(json.dumps(state))
        row["reviews"]["nodes"][0]["body"] += " edited"
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(events, [])
        row["reviews"]["nodes"][0]["id"] = "review-2"
        row["headRefOid"] = "head-2"
        row["reviewThreads"]["nodes"][0]["id"] = "thread-2"
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        for kind in ("COPILOT", "APPROVED-OLDHEAD", "THREADS"):
            self.assertTrue(any(event.startswith(kind + " owner/product#7 owner=") for event in events), (kind, events))
        # A short review window temporarily shows no bot, then the same review again.
        row["reviews"]["nodes"] = []
        state, _ = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        row["reviews"]["nodes"] = [{"id": "review-2", "author": {"login": "copilot-pull-request-reviewer"},
                                     "body": "Needs a closer look\nCheck it"}]
        _, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertFalse(any(event.startswith("COPILOT ") and "owner=" in event for event in events))

    def test_partial_pr_read_keeps_evidence_and_readable_sibling_changes(self):
        row = pr(7)
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row, pr(8, decision="REVIEW_REQUIRED")]), None))
        row["reviews"] = None
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row, pr(8)]), state))
        self.assertIn("PULLS READFAIL pr:owner/product#7", events)
        self.assertEqual(state["prs"]["owner/product#7"]["lines"], {"APPROVED": f"APPROVED owner/product#7 {PR['title']}"})
        self.assertTrue(any(event.startswith("APPROVED owner/product#8 owner=") for event in events))
        self.assertFalse(any("#7 cleared" in event for event in events))

    def test_truncated_connections_do_not_clear_known_conditions(self):
        row = pr(7, decision="REVIEW_REQUIRED")
        row["reviews"]["nodes"] = [{"id": "review-1", "author": {"login": "copilot-pull-request-reviewer"},
                                     "body": "Needs a closer look\nCheck it"}]
        row["reviewThreads"]["nodes"] = [{"id": "thread-1", "isResolved": False}]
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        for name, page in (("reviews", "hasPreviousPage"), ("reviewThreads", "hasNextPage")):
            row[name] = {"nodes": [], "pageInfo": {page: True}}
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(set(state["prs"]["owner/product#7"]["lines"]), {"COPILOT", "THREADS"})
        self.assertFalse(any("cleared" in event for event in events))
        self.assertEqual(events, ["PULLS READFAIL pr:owner/product#7"])

    def test_positive_owner_reassignment_still_reroutes(self):
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([pr(7)]), None))
        rows = json.loads(json.dumps(WORKSPACES))
        rows["result"]["workspaces"][0]["tokens"]["owner"] = "wOther"
        with patch(__name__ + ".WORKSPACES", rows):
            state, events = w.reduce_pulls(state, w.collect_pulls(self.command([pr(7)]), state))
        self.assertEqual(state["prs"]["owner/product#7"]["owner"], "wOther")
        self.assertTrue(any("owner=wOther" in event for event in events))

    def test_legacy_active_copilot_state_seeds_identity_without_realert(self):
        row = pr(7)
        row["reviews"]["nodes"] = [{"id": "review-1", "author": {"login": "copilot-pull-request-reviewer"},
                                     "body": "Needs a closer look\nCheck it"}]
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        for saved in state["prs"].values():
            saved.pop("identities")
            saved.pop("seen_reviews")
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(events, [])
        self.assertEqual(state["prs"]["owner/product#7"]["seen_reviews"], ["review-1"])

    def test_new_id_reaches_feed_and_partial_reads_do_not_refire_it(self):
        row = pr(7)
        row["reviews"]["nodes"] = [{"id": "review-1", "author": {"login": "copilot-pull-request-reviewer"},
                                     "body": "Needs a closer look\nCheck it"}]
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        policy = {}
        old_line = w.pull_line(state["prs"]["owner/product#7"]["lines"]["COPILOT"], "owner/product#7", "wArch")
        w.alert_policy.apply(policy, [{"mode": "pulls", "events": [old_line]}], 1)
        row["reviews"]["nodes"][0]["id"] = "review-2"
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        feed = w.alert_policy.apply(policy, [{"mode": "pulls", "events": events}], 2)
        self.assertEqual(sum("COPILOT " in line and "(start)" in line for line in feed), 1)
        row["reviews"] = None
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        feed = w.alert_policy.apply(policy, [{"mode": "pulls", "events": events}], 3)
        self.assertFalse(any("COPILOT" in line for line in feed))

    def test_open_departures_rotate_so_later_closed_prs_are_read(self):
        before = {"prs": {f"o/r#{n:02}": dict(title="t", owner=None, lines={})
                          for n in range(w.PULLS_DEPARTED_MAX + 1)}, "unreadable": []}
        departed = {f"repos/o/r/pulls/{n:02}": {"state": "open", "merged": False}
                    for n in range(w.PULLS_DEPARTED_MAX)}
        departed[f"repos/o/r/pulls/{w.PULLS_DEPARTED_MAX:02}"] = {"state": "closed", "merged": True}
        state, _ = w.reduce_pulls(before, w.collect_pulls(self.command([], departed), before))
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([], departed), state))
        self.assertTrue(any(event.startswith(f"MERGED o/r#{w.PULLS_DEPARTED_MAX:02}") for event in events))
        self.assertFalse(state["unreadable"])

    def test_whole_search_failure_keeps_durable_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            w.cycle(base, "pulls", lambda before: w.collect_pulls(self.command([pr(7)]), before))
            saved = json.loads((base / "pulls.json").read_text())["before"]
            def broken(*argv):
                if argv[:3] == ("gh", "api", "graphql"):
                    return json.dumps({"data": {"search": {"nodes": [], "pageInfo": {"hasNextPage": False}}},
                                       "errors": [{"message": "partial search"}]})
                return self.command([])(*argv)
            for _ in range(3):
                w.cycle(base, "pulls", lambda before: w.collect_pulls(broken, before))
            self.assertEqual(json.loads((base / "pulls.json").read_text())["before"], saved)

    def test_partial_thread_identity_keeps_last_known_threads(self):
        row = pr(7)
        row["reviewThreads"]["nodes"] = [{"id": "thread-1", "isResolved": False}]
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        del row["reviewThreads"]["nodes"][0]["id"]
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(events, ["PULLS READFAIL pr:owner/product#7"])
        self.assertIn("THREADS", state["prs"]["owner/product#7"]["lines"])

    def test_settings_failure_preserves_stacked_approval_evidence(self):
        row = pr(7)
        row["baseRefName"] = "stack-base"
        with patch.object(w.audit, "repo_settings", return_value={"ORCH_MERGE_GATE_CHECKS": "full-ci"}):
            state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        old = state["prs"]["owner/product#7"]["lines"]
        with patch.object(w.audit, "repo_settings", side_effect=OSError("unreadable")):
            state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(events, ["PULLS READFAIL settings:owner/product#7"])
        self.assertEqual(state["prs"]["owner/product#7"]["lines"], old)

    def test_truncated_checks_do_not_clear_saved_red_event(self):
        row = pr(7)
        contexts = row["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]
        contexts["nodes"][0]["conclusion"] = "FAILURE"
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        contexts["nodes"] = []
        contexts["pageInfo"]["hasNextPage"] = True
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertIn("RED", state["prs"]["owner/product#7"]["lines"])
        self.assertFalse(any("cleared" in event for event in events))
        self.assertIn("PULLS READFAIL pr:owner/product#7", events)

    def test_title_only_edits_do_not_restart_conditions(self):
        for decision, base, red in (("APPROVED", "main", False), ("APPROVED", "stack", False),
                                    ("CHANGES_REQUESTED", "main", False), ("REVIEW_REQUIRED", "main", True)):
            with self.subTest(decision=decision, base=base, red=red):
                row = pr(7, decision=decision)
                row["baseRefName"] = base
                if red:
                    row["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"][0]["conclusion"] = "FAILURE"
                with patch.object(w.audit, "repo_settings", return_value={}):
                    state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
                    row["title"] = "A new display title"
                    _, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
                self.assertEqual(events, [])

    def test_truncated_threads_reconcile_new_resolved_and_unseen_evidence(self):
        row = pr(7, decision="REVIEW_REQUIRED")
        row["reviewThreads"]["nodes"] = [{"id": "T1", "isResolved": False}]
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        row["reviewThreads"]["pageInfo"]["hasNextPage"] = True
        row["headRefOid"] = "head-2"
        row["reviewThreads"]["nodes"] = [{"id": "T2", "isResolved": False}]
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertTrue(any(event.startswith("THREADS owner/product#7 owner=") for event in events))
        self.assertEqual(state["prs"]["owner/product#7"]["identities"]["THREADS"], ["head-2", ["T1", "T2"]])
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(events, [])
        row["reviewThreads"]["nodes"] = [{"id": "T1", "isResolved": True}]
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(state["prs"]["owner/product#7"]["identities"]["THREADS"], ["head-2", ["T2"]])
        self.assertTrue(any(event.startswith("THREADS owner/product#7 owner=") for event in events))
        row["reviewThreads"] = {"nodes": [], "pageInfo": {"hasNextPage": False}}
        _, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertIn("THREADS owner/product#7 cleared", events)

    def test_truncated_checks_reconcile_visible_failures_and_current_head(self):
        row = pr(7, decision="REVIEW_REQUIRED")
        contexts = row["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]
        contexts["nodes"][0]["conclusion"] = "FAILURE"
        state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
        row["headRefOid"] = "head-2"
        contexts["nodes"] = [{"name": "lint", "conclusion": "FAILURE", "startedAt": "2"}]
        contexts["pageInfo"]["hasNextPage"] = True
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertTrue(any(event.startswith("RED owner/product#7 owner=") for event in events))
        self.assertEqual(state["prs"]["owner/product#7"]["identities"]["RED"][0], "head-2")
        self.assertIn("lint", state["prs"]["owner/product#7"]["lines"]["RED"])
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertEqual(events, [])
        contexts["nodes"] = [{"name": "lint", "conclusion": "SUCCESS", "startedAt": "3"}]
        state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertIn("unit", state["prs"]["owner/product#7"]["lines"]["RED"])
        self.assertNotIn("lint", state["prs"]["owner/product#7"]["lines"]["RED"])
        contexts["nodes"] = [{"name": "unit", "conclusion": "SUCCESS", "startedAt": "4"}]
        _, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
        self.assertIn("RED owner/product#7 cleared", events)

    def test_truncated_checks_do_not_retain_invalidated_stacked_approval(self):
        for change in ("decision", "base", "head"):
            with self.subTest(change=change), patch.object(w.audit, "repo_settings", return_value={}):
                row = pr(7)
                row["baseRefName"] = "stack"
                state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
                row["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["pageInfo"]["hasNextPage"] = True
                if change == "decision":
                    row["reviewDecision"] = "CHANGES_REQUESTED"
                elif change == "base":
                    row["baseRefName"] = "main"
                else:
                    row["headRefOid"] = "head-2"
                state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
                self.assertNotIn("APPROVED-STACKED", state["prs"]["owner/product#7"]["lines"])
                self.assertIn("APPROVED-STACKED owner/product#7 cleared", events)

    def test_partial_stacked_checks_do_not_reuse_evidence_from_another_head_or_base(self):
        for change in ("head", "base"):
            with self.subTest(change=change), patch.object(w.audit, "repo_settings", return_value={"ORCH_MERGE_GATE_CHECKS": "unit"}):
                row = pr(7)
                row["baseRefName"] = "stack-a"
                state, _ = w.reduce_pulls(None, w.collect_pulls(self.command([row]), None))
                contexts = row["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]
                contexts["nodes"] = []
                contexts["pageInfo"]["hasNextPage"] = True
                if change == "head":
                    row["headRefOid"] = "head-2"
                    row["latestOpinionatedReviews"]["nodes"][0]["commit"]["oid"] = "head-2"
                else:
                    row["baseRefName"] = "stack-b"
                state, events = w.reduce_pulls(state, w.collect_pulls(self.command([row]), state))
                line = state["prs"]["owner/product#7"]["lines"]["APPROVED-STACKED"]
                self.assertIn("base " + row["baseRefName"], line)
                self.assertIn("full CI unknown", line)
                self.assertTrue(any(event.startswith("APPROVED-STACKED owner/product#7 owner=") for event in events))

    def test_one_search_call_per_poll(self):
        w.collect_pulls(self.command([pr(n) for n in range(20)]), None)
        self.assertEqual(sum(argv[:3] == ("gh", "api", "graphql") for argv in self.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
