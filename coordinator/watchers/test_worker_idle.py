"""WORKER IDLE: a worker idle or done for more than ORCH_WORKER_IDLE_S with no open PR, sent to its owning architect."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_support import IsolatedTestCase

import watchers as w

DEFAULT_TIMEOUT = w.runtime.COMMAND_TIMEOUT
PANE = "wT:p1"
LINE = f"WORKER IDLE {PANE} owner=wArch"
CLEARED = LINE + " cleared"
AFTER = 7201
STOPPED_OPERATIONS = (
    "rebase-merge",
    "rebase-apply",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
)


def pr_json(number=3, owner="acme"):
    return json.dumps([{"number": number, "headRepositoryOwner": {"login": owner}}])


def idle_raw(
    now,
    status="idle",
    owner="wArch",
    prs=None,
    workspaces=True,
    role=None,
    name="fixture-worker",
    session="s-w",
):
    """One worker in workspace wT read at `now`; `prs` is the open-PR result of the pass (pane -> True, False, None)."""
    agent = {
        "agent": "claude",
        "agent_session": {"value": session} if session else {},
        "agent_status": status,
        "cwd": "/wt/task",
        "name": name,
        "pane_id": PANE,
        "terminal_title_stripped": name,
        "tokens": {"role": role} if role else {},
        "workspace_id": "wT",
    }
    raw = {"result": {"agents": [agent]}, "observed_at": now, "closed": []}
    raw["workspaces"] = (
        {
            "result": {
                "workspaces": [
                    {"workspace_id": "wT", "tokens": {"owner": owner} if owner else {}}
                ]
            }
        }
        if workspaces
        else None
    )
    if prs is not None:
        raw["pr_open"] = prs
    return raw


def no_waiting_alerts(case):
    """WORKER WAITING has its own tests; the idle tests read only WORKER IDLE lines."""
    env = patch.dict(os.environ, {"ORCH_WAIT_S": "1e12", "ORCH_WAIT_REPEAT_S": "1e12"})
    env.start()
    case.addCleanup(env.stop)


class WorkerIdleTests(IsolatedTestCase):
    def setUp(self):
        no_waiting_alerts(self)
        self.tmp = tempfile.TemporaryDirectory(dir=w.ROOT)
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def events(self, raw):
        line = w.cycle(self.base, "workers", lambda before: raw)
        return json.loads(line)["events"] if line else []

    def step(self, now, **kwargs):
        prs = kwargs.pop("pr", "none")
        return self.events(
            idle_raw(now, prs=None if prs == "none" else {PANE: prs}, **kwargs)
        )

    def test_a_worker_idle_for_more_than_the_threshold_with_no_open_pr_alerts(self):
        self.assertEqual(self.step(0), [])
        self.assertEqual(self.step(7200, pr=False), [])
        self.assertEqual(self.step(AFTER, pr=False), [LINE])

    def test_the_threshold_is_a_setting(self):
        with patch.dict(os.environ, {"ORCH_WORKER_IDLE_S": "600"}):
            self.assertEqual(self.step(0), [])
            self.assertEqual(self.step(600, pr=False), [])
            self.assertEqual(self.step(601, pr=False), [LINE])

    def test_one_line_per_idle_period_and_a_new_one_after_the_worker_worked(self):
        self.step(0)
        self.assertEqual(self.step(AFTER, pr=False), [LINE])
        self.assertEqual(self.step(AFTER + 60, pr=False), [])
        self.assertEqual(self.step(30000, pr=False), [])
        self.assertEqual(self.step(30060, status="working"), [CLEARED])
        self.assertEqual(self.step(30120), [])
        self.assertEqual(self.step(30120 + 7200, pr=False), [])
        self.assertEqual(self.step(30120 + AFTER, pr=False), [LINE])

    def test_a_new_session_in_the_pane_starts_a_new_idle_period(self):
        self.step(0, session="a")
        self.assertEqual(self.step(AFTER, session="b", pr=False), [])
        self.assertEqual(self.step(AFTER + 7200, session="b", pr=False), [])
        self.assertEqual(self.step(AFTER + AFTER, session="b", pr=False), [LINE])

    def test_a_new_session_is_not_suppressed_by_the_old_one_having_fired(self):
        self.step(0, session="a")
        self.assertEqual(self.step(AFTER, session="a", pr=False), [LINE])
        self.assertEqual(self.step(AFTER + 60, session="b"), [CLEARED])
        self.assertEqual(self.step(AFTER + 60 + AFTER, session="b", pr=False), [LINE])

    def test_missing_session_metadata_keeps_the_idle_period(self):
        self.step(0, session="a")
        self.step(3600, session=None)
        self.assertEqual(self.step(AFTER, session="a", pr=False), [LINE])

    def reduce(self, previous, **kwargs):
        raw = idle_raw(AFTER, prs={PANE: False}, **kwargs)
        present = {row["pane_id"]: row for row in w.agents(raw)}
        return w.reduce_idle(previous, raw, present)

    def test_a_row_without_a_session_id_keeps_the_period_of_the_saved_session(self):
        saved = {PANE: {"owner": "wArch", "since": 0, "session": "a"}}
        found, events = self.reduce(saved, session=None)
        self.assertEqual(events, [LINE])
        self.assertEqual((found[PANE]["since"], found[PANE]["session"]), (0, "a"))

    def test_a_saved_period_without_a_session_id_keeps_its_clock_when_a_session_is_first_seen(
        self,
    ):
        found, events = self.reduce({PANE: {"owner": "wArch", "since": 0}}, session="a")
        self.assertEqual(events, [LINE])
        self.assertEqual((found[PANE]["since"], found[PANE]["session"]), (0, "a"))

    def test_idle_and_done_are_one_idle_period(self):
        self.step(0)
        self.step(3600, status="done")
        self.assertEqual(self.step(AFTER, status="idle", pr=False), [LINE])

    def test_an_open_pr_gives_no_line(self):
        self.step(0)
        self.assertEqual(self.step(AFTER, pr=True), [])
        self.assertEqual(self.step(AFTER + 60, pr=False), [])
        self.assertEqual(self.step(AFTER + 7199, pr=False), [])

    def test_a_pr_that_is_gone_later_in_the_period_gives_the_line_once(self):
        self.step(0)
        self.assertEqual(self.step(AFTER, pr=True), [])
        self.assertEqual(self.step(AFTER + 7200, pr=True), [])
        self.assertEqual(self.step(AFTER + 14400, pr=False), [LINE])
        self.assertEqual(self.step(AFTER + 14460, pr=False), [])
        self.assertEqual(self.step(AFTER + 40000, pr=False), [])

    def test_an_unknown_recheck_of_an_open_pr_waits_another_period(self):
        self.step(0)
        self.step(AFTER, pr=True)
        self.assertEqual(self.step(AFTER + 7200, pr=None), [])
        self.assertEqual(self.step(AFTER + 7260, pr=False), [])
        self.assertEqual(self.step(AFTER + 14400, pr=False), [LINE])

    def test_an_unknown_pr_check_gives_no_line(self):
        self.step(0)
        self.assertEqual(self.step(AFTER, pr=None), [])
        self.assertEqual(self.step(AFTER + 60), [])

    def test_architects_and_the_coordinator_never_alert(self):
        for kind in (
            {"role": "architect"},
            {"role": "coordinator"},
            {"name": "Architect::demo"},
        ):
            with self.subTest(kind=kind):
                self.base = Path(self.tmp.name) / str(sorted(kind.items()))
                self.step(0, **kind)
                self.assertEqual(self.step(AFTER, pr=False, **kind), [])

    def test_a_workspace_without_an_owner_tag_never_alerts(self):
        self.step(0, owner=None)
        self.assertEqual(self.step(AFTER, owner=None, pr=False), [])

    def test_an_unreadable_workspace_list_keeps_the_clock_and_says_nothing(self):
        self.step(0)
        self.assertEqual(self.step(AFTER, workspaces=False, pr=False), [])
        self.assertEqual(self.step(AFTER + 60, pr=False), [LINE])

    def test_a_new_owner_restarts_the_period_and_clears_the_old_line(self):
        self.step(0)
        self.assertEqual(self.step(AFTER, pr=False), [LINE])
        self.assertEqual(self.step(AFTER + 60, owner="wOther"), [CLEARED])
        self.assertEqual(self.step(AFTER + 120, owner="wOther", pr=False), [])

    def test_a_worker_that_leaves_clears_its_line(self):
        self.step(0)
        self.step(AFTER, pr=False)
        raw = idle_raw(AFTER + 60)
        raw["result"]["agents"] = []
        self.assertEqual(self.events(raw), [CLEARED])


class WorkerIdleCollectTests(IsolatedTestCase):
    """The open-PR check runs through `collect`, once per idle period, when the worker crosses the line."""

    def setUp(self):
        no_waiting_alerts(self)
        self.tmp = tempfile.TemporaryDirectory(dir=w.ROOT)
        self.base = Path(self.tmp.name)
        self.calls = []
        self.timeouts = []
        self.status = "idle"
        self.owner = "wArch"
        self.count = 1
        self.blocked = False
        self.pr = "[]"
        self.prs = {}
        self.gh_error = None
        self.gh_seconds = 0
        self.clock = [0.0]
        self.herdr_seconds = 0
        self.branch_error = None
        self.remotes = {"origin": "git@github.com:acme/app.git", "upstream": None}

    def tearDown(self):
        self.tmp.cleanup()

    def agents(self):
        rows = []
        for number in range(1, self.count + 1):
            row = dict(idle_raw(0, status=self.status)["result"]["agents"][0])
            row.update(pane_id=f"wT:p{number}", agent_session={"value": f"s-{number}"})
            rows.append(row)
        if self.blocked:
            rows.append(
                dict(
                    rows[0],
                    pane_id="wB:p1",
                    workspace_id="wB",
                    agent_status="blocked",
                    agent_session={"value": "s-b"},
                    name="blocked-worker",
                )
            )
        return rows

    def fake(self, *argv):
        self.calls.append(argv)
        if argv[0] in ("git", "gh"):
            self.timeouts.append(w.runtime.COMMAND_TIMEOUT)
        if argv[0] not in ("git", "gh"):
            self.clock[0] += self.herdr_seconds
        if argv[0] == "git" and "rev-parse" in argv:
            names = [argv[at + 1] for at, arg in enumerate(argv) if arg == "--git-path"]
            return "".join(f"{self.base / 'gitdir' / name}\n" for name in names)
        if argv[1:3] == ("agent", "list"):
            return json.dumps({"result": {"agents": self.agents()}})
        if argv[1:3] == ("workspace", "list"):
            rows = [{"workspace_id": "wT", "tokens": {"owner": self.owner}}]
            rows.append({"workspace_id": "wB", "tokens": {"owner": self.owner}})
            return json.dumps({"result": {"workspaces": rows}})
        if argv[0] == "git" and "symbolic-ref" in argv:
            if self.branch_error:
                raise self.branch_error
            return "feat/task\n"
        if argv[0] == "git" and "get-url" in argv:
            url = self.remotes.get(argv[-1])
            if url is None:
                raise subprocess.CalledProcessError(
                    2, argv, stderr=f"error: No such remote '{argv[-1]}'"
                )
            return url + "\n"
        if argv[0] == "gh":
            self.clock[0] += self.gh_seconds
            if self.gh_error:
                raise self.gh_error
            return self.prs.get(argv[argv.index("--repo") + 1], self.pr)
        raise AssertionError(argv)

    def run_at(self, now, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        with (
            patch.dict(os.environ, {"HERDR_ENV": "1"}),
            patch.object(w, "command", self.fake),
            patch.object(
                w,
                "time",
                SimpleNamespace(time=lambda: now, monotonic=time.monotonic),
            ),
            patch.object(w, "_monotonic", lambda: self.clock[0]),
        ):
            line = w.cycle(
                self.base,
                "workers",
                lambda before: w.collect("workers", before, base=self.base),
            )
        return json.loads(line)["events"] if line else []

    def gh_calls(self):
        return [call for call in self.calls if call[0] == "gh"]

    def test_the_check_runs_once_when_the_worker_crosses_the_line_and_names_branch_and_repo(
        self,
    ):
        self.run_at(0)
        self.run_at(3600)
        self.run_at(7200)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(self.run_at(AFTER), [LINE])
        self.assertEqual(
            self.gh_calls(),
            [
                (
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    "acme/app",
                    "--head",
                    "feat/task",
                    "--state",
                    "open",
                    "--json",
                    "number,headRepositoryOwner",
                )
            ],
        )
        self.run_at(AFTER + 60)
        self.run_at(AFTER + 7200)
        self.assertEqual(len(self.gh_calls()), 1)

    def test_an_open_pr_is_rechecked_each_period_and_fires_when_gone(self):
        self.run_at(0)
        self.assertEqual(self.run_at(AFTER, pr=pr_json()), [])
        self.run_at(AFTER + 60)
        self.run_at(AFTER + 7199)
        self.assertEqual(len(self.gh_calls()), 1)
        self.assertEqual(self.run_at(AFTER + 7200), [])
        self.assertEqual(len(self.gh_calls()), 2)
        self.assertEqual(self.run_at(AFTER + 14400, pr="[]"), [LINE])
        self.assertEqual(len(self.gh_calls()), 3)
        self.run_at(AFTER + 30000)
        self.assertEqual(len(self.gh_calls()), 3)

    def test_the_open_pr_check_starts_again_in_a_new_idle_period(self):
        self.run_at(0)
        self.run_at(AFTER, pr=pr_json())
        self.run_at(AFTER + 20060, status="working")
        self.run_at(AFTER + 20120, status="idle")
        self.assertEqual(self.run_at(AFTER + 20120 + AFTER, pr="[]"), [LINE])
        self.assertEqual(len(self.gh_calls()), 2)

    def test_a_failed_check_is_unknown_and_retries_after_another_period(self):
        self.run_at(0)
        failure = subprocess.CalledProcessError(1, ["gh"], stderr="rate limited")
        self.assertEqual(self.run_at(AFTER, gh_error=failure), [])
        self.assertEqual(self.run_at(AFTER + 60), [])
        self.assertEqual(len(self.gh_calls()), 1)
        self.assertEqual(self.run_at(AFTER + 7199, gh_error=None), [])
        self.assertEqual(len(self.gh_calls()), 1)
        self.assertEqual(self.run_at(AFTER + 7200), [LINE])
        self.assertEqual(len(self.gh_calls()), 2)

    def test_a_failed_git_call_is_unknown(self):
        self.run_at(0)
        broken = subprocess.CalledProcessError(3, ["git"], stderr="fatal: unreadable")
        self.assertEqual(self.run_at(AFTER, branch_error=broken), [])
        self.assertEqual(self.gh_calls(), [])

    def test_an_inaccessible_checkout_is_unknown_not_missing(self):
        self.run_at(0)
        denied = subprocess.CalledProcessError(
            128, ["git"], stderr="fatal: cannot change to '/wt': Permission denied"
        )
        self.assertEqual(self.run_at(AFTER, branch_error=denied), [])
        self.assertEqual(self.gh_calls(), [])

    def test_a_checkout_that_cannot_have_a_pr_counts_as_no_pr(self):
        cases = {
            "detached HEAD": {
                "branch_error": subprocess.CalledProcessError(1, ["git"])
            },
            "not a git repository": {
                "branch_error": subprocess.CalledProcessError(
                    128, ["git"], stderr="fatal: not a git repository (or any parent)"
                )
            },
            "directory gone": {
                "branch_error": subprocess.CalledProcessError(
                    128,
                    ["git"],
                    stderr="fatal: cannot change to '/wt': No such file or directory",
                )
            },
            "no GitHub remote": {
                "remotes": {"origin": "https://example.com/a/b.git", "upstream": None}
            },
            "no remote": {"remotes": {"origin": None, "upstream": None}},
        }
        for name, case in cases.items():
            with self.subTest(name):
                self.base = Path(self.tmp.name) / name.replace(" ", "-")
                self.calls = []
                self.branch_error = None
                self.remotes = {
                    "origin": "git@github.com:acme/app.git",
                    "upstream": None,
                }
                self.run_at(0)
                self.assertEqual(self.run_at(AFTER, **case), [LINE])
                self.assertEqual(self.gh_calls(), [])

    def test_a_pr_on_the_upstream_of_a_fork_is_an_open_pr(self):
        remotes = {
            "origin": "git@github.com:me/app.git",
            "upstream": "git@github.com:acme/app.git",
        }
        prs = {"me/app": "[]", "acme/app": pr_json(7, "me")}
        self.run_at(0)
        self.assertEqual(self.run_at(AFTER, remotes=remotes, prs=prs), [])
        self.assertEqual(
            sorted(call[call.index("--repo") + 1] for call in self.gh_calls()),
            ["acme/app", "me/app"],
        )

    def test_one_failed_remote_with_no_pr_on_the_other_is_unknown(self):
        remotes = {
            "origin": "git@github.com:me/app.git",
            "upstream": "git@github.com:acme/app.git",
        }
        self.run_at(0)
        original = self.fake

        def flaky(*argv):
            if argv[0] == "gh" and "acme/app" in argv:
                raise subprocess.CalledProcessError(1, argv, stderr="boom")
            return original(*argv)

        with patch.object(self, "fake", flaky):
            self.assertEqual(self.run_at(AFTER, remotes=remotes), [])

    def test_a_remote_named_twice_is_asked_once(self):
        remotes = {
            "origin": "git@github.com:Acme/App.git",
            "upstream": "https://github.com/acme/app",
        }
        self.run_at(0)
        self.assertEqual(self.run_at(AFTER, remotes=remotes), [LINE])
        self.assertEqual(len(self.gh_calls()), 1)

    def test_a_checkout_stopped_in_an_operation_is_unknown_not_a_detached_head(self):
        heads = {
            "detached": subprocess.CalledProcessError(1, ["git"]),
            "attached": None,
        }
        for head, error in heads.items():
            for name in STOPPED_OPERATIONS:
                with self.subTest(f"{name}, {head} HEAD"):
                    self.base = Path(self.tmp.name) / head / name
                    self.calls = []
                    self.branch_error = error
                    marker = self.base / "gitdir" / name
                    marker.parent.mkdir(parents=True)
                    marker.touch()
                    self.run_at(0)
                    self.assertEqual(self.run_at(AFTER), [])
                    self.assertEqual(self.gh_calls(), [])
                    marker.unlink()
                    self.assertEqual(self.run_at(AFTER + 7200), [LINE])

    def test_a_pr_of_another_fork_with_the_same_branch_name_is_not_this_workers_pr(
        self,
    ):
        self.run_at(0)
        self.assertEqual(self.run_at(AFTER, pr=pr_json(5, "other")), [LINE])

    def test_the_head_owner_is_compared_without_case(self):
        self.run_at(0)
        self.assertEqual(self.run_at(AFTER, pr=pr_json(5, "ACME")), [])

    def test_a_pr_without_a_readable_head_owner_is_unknown(self):
        self.run_at(0)
        self.assertEqual(
            self.run_at(AFTER, pr='[{"number": 5, "headRepositoryOwner": null}]'), []
        )

    def test_the_check_constants_fit_in_the_workers_soft_budget(self):
        budget = w.WATCH_TIMING["workers"]["soft_budget"]
        self.assertLess(
            w.IDLE_CHECK_SECONDS + w.CALLS_PER_CHECK * (w.GH_TIMEOUT + 1), budget
        )

    def test_the_longest_check_makes_calls_per_check_calls(self):
        remotes = {
            "origin": "git@github.com:me/app.git",
            "upstream": "git@github.com:acme/app.git",
        }
        self.run_at(0)
        self.run_at(AFTER, remotes=remotes)
        self.assertEqual(
            len([call for call in self.calls if call[0] in ("git", "gh")]),
            w.CALLS_PER_CHECK,
        )

    def test_herdr_time_counts_against_the_check_budget(self):
        self.run_at(0, count=2)
        self.blocked = True
        events = self.run_at(AFTER, herdr_seconds=w.IDLE_CHECK_SECONDS)
        self.assertIn("WORKER wB:p1 blocked", events)
        self.assertEqual(self.gh_calls(), [])
        self.run_at(AFTER + 60, herdr_seconds=0)
        self.assertEqual(len(self.gh_calls()), 2)

    def test_at_most_three_checks_run_in_one_pass(self):
        self.run_at(0, count=5)
        first = self.run_at(AFTER)
        self.assertEqual(len(first), 3)
        self.assertEqual(len(self.gh_calls()), 3)
        second = self.run_at(AFTER + 60)
        self.assertEqual(len(second), 2)
        self.assertEqual(len(self.gh_calls()), 5)
        self.assertEqual(
            sorted(first + second),
            sorted(f"WORKER IDLE wT:p{n} owner=wArch" for n in range(1, 6)),
        )

    def test_slow_checks_stop_before_the_pass_budget_and_a_blocked_worker_is_still_reported(
        self,
    ):
        self.run_at(0, count=3, blocked=False)
        self.blocked = True
        with patch.object(w, "IDLE_CHECK_SECONDS", 1):
            events = self.run_at(AFTER, gh_seconds=1)
        self.assertIn("WORKER wB:p1 blocked", events)
        self.assertEqual(len(self.gh_calls()), 1)
        with patch.object(w, "IDLE_CHECK_SECONDS", 1):
            self.run_at(AFTER + 60)
        self.assertEqual(len(self.gh_calls()), 2)

    def test_the_pass_deadline_is_not_taken_for_an_unknown_check(self):
        self.run_at(0, count=3)
        self.assertEqual(
            self.run_at(AFTER, gh_error=TimeoutError("pass deadline exceeded")), []
        )
        self.assertEqual(len(self.gh_calls()), 1)
        self.assertEqual(
            json.loads((self.base / "workers.json").read_text())["fails"], 1
        )

    def test_git_and_gh_calls_of_a_check_are_short_and_the_default_comes_back(self):
        self.run_at(0)
        self.run_at(AFTER)
        self.assertEqual(set(self.timeouts), {w.GH_TIMEOUT})
        self.assertEqual(w.runtime.COMMAND_TIMEOUT, DEFAULT_TIMEOUT)

    def test_a_pass_where_the_status_cannot_be_read_keeps_the_idle_period(self):
        self.run_at(0)
        self.assertEqual(self.run_at(AFTER), [LINE])
        self.assertEqual(
            self.run_at(AFTER + 60, status="unknown"),
            ["WORKER READFAIL wT:p1(unknown)"],
        )
        self.assertEqual(
            self.run_at(AFTER + 120, status="idle"), ["WORKER READFAIL cleared"]
        )
        self.assertEqual(self.run_at(AFTER + 7321), [])
        self.assertEqual(len(self.gh_calls()), 1)

    def test_a_missing_status_is_a_status_that_cannot_be_read(self):
        self.run_at(0)
        self.assertEqual(self.run_at(AFTER), [LINE])
        self.run_at(AFTER + 60, status=None)
        self.run_at(AFTER + 120, status="idle")
        self.assertEqual(self.run_at(AFTER + 7321), [])
        self.assertEqual(len(self.gh_calls()), 1)

    def test_the_workspace_list_is_read_once_per_pass(self):
        self.run_at(0, status="working")
        self.run_at(60, status="working")
        self.assertEqual(
            len([call for call in self.calls if call[1:3] == ("workspace", "list")]), 2
        )


class WorkerIdleWithWaitingTests(IsolatedTestCase):
    """WORKER WAITING (finished workers, 10 min) and WORKER IDLE (2 h, no open PR) are separate alerts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=w.ROOT)
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def step(self, now, **kwargs):
        prs = kwargs.pop("pr", "none")
        raw = idle_raw(now, prs=None if prs == "none" else {PANE: prs}, **kwargs)
        line = w.cycle(self.base, "workers", lambda before: raw)
        return json.loads(line)["events"] if line else []

    def test_each_alert_comes_without_the_other_and_both_come_when_both_are_due(self):
        self.step(0)
        first = self.step(601)
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].startswith("WORKER WAITING "))
        later = self.step(AFTER, pr=False)
        self.assertIn(LINE, later)
        self.assertEqual([e for e in later if e.startswith("WORKER IDLE")], [LINE])


if __name__ == "__main__":
    unittest.main()
