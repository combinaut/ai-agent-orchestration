import json
import os
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

    def test_a_departure_still_open_stops_being_tracked(self):
        before = {"prs": {"o/r#3": dict(title="t", owner=None, lines={"RED": "RED o/r#3 t"})}, "unreadable": []}
        departed = {"repos/o/r/pulls/3": {"state": "open", "merged": False}}
        state, events = w.reduce_pulls(before, w.collect_pulls(self.command([], departed), before))
        self.assertEqual(events, ["RED o/r#3 cleared"])
        self.assertEqual(state["prs"], {})

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

    def test_a_failed_root_keeps_old_owners_only_for_its_own_repository(self):
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
        self.assertIsNone(raw["prs"]["owner/tool#9"]["owner"])

    def test_a_failed_root_keeps_owners_only_for_its_full_repository_name(self):
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
        self.assertIsNone(raw["prs"]["elsewhere/product#9"]["owner"])

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

    def test_one_search_call_per_poll(self):
        w.collect_pulls(self.command([pr(n) for n in range(20)]), None)
        self.assertEqual(sum(argv[:3] == ("gh", "api", "graphql") for argv in self.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
