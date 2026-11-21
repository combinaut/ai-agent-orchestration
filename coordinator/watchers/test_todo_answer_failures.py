import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import alert_policy
import discovery
import todo_answers as a
import watchers as w

TEXT = "Deploy release [1: now] [2: later] -> Architect::Tooling"
OWNER = "Architect::Tooling"


def agents(*rows):
    return json.dumps(dict(result=dict(agents=list(rows))))


def architect(workspace):
    return dict(name=OWNER, workspace_id=workspace, tokens=dict(role="architect"))


class AnswerFailureTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = patch.dict(
            os.environ,
            HOME=str(self.base),
            ORCH_USER_TODO_STATUS_DIR=str(self.base / "status"),
            ORCH_USER_QUESTIONS_DIR=str(self.base / "questions"),
            ORCH_USER_TODO_OUTPUT=str(self.base / "todo.md"),
            ORCH_USER_TODO_STATE=str(self.base / "todo.state.json"),
        )
        env.start()
        self.addCleanup(env.stop)
        (self.base / "status").mkdir()
        (self.base / "status" / f"{OWNER}.md").write_text(
            "## Waits on the user\n- " + TEXT + "\n"
        )

    def failure(self, reason="down"):
        return dict(architect=OWNER, reason=reason)

    def raw(self, at, *failures):
        return dict(
            now=[],
            unreadable=[],
            read=[],
            observed_at=at,
            todo_answer_failures=failures,
        )

    def collect_with(self, owners=None, error=None, ambiguous=()):
        def fake(command, ambiguous_out=None):
            if error:
                raise discovery.Unavailable(error)
            if ambiguous_out is not None:
                ambiguous_out.update(ambiguous)
            return owners or {}

        with patch.object(a.discovery, "architect_owners", side_effect=fake):
            return a.collect(w.command)

    def test_herdr_error_is_the_reason_and_is_stored_on_the_record(self):
        a.submit(OWNER, TEXT, "now")
        ready, _, failures = self.collect_with(error="herdr agent list exited 1")
        self.assertEqual(ready, [])
        self.assertEqual(failures[0]["reason"], "herdr agent list exited 1")
        record = a.records()[0]
        self.assertEqual(record["failure"], "herdr agent list exited 1")
        self.assertIsInstance(record["failed_at"], float)
        self.assertEqual(set(failures[0]), {"architect", "reason"})

    def test_reason_is_one_line_and_at_most_200_characters(self):
        a.submit(OWNER, TEXT, "now")
        _, _, failures = self.collect_with(error="boom\n" + "x" * 500)
        self.assertNotIn("\n", failures[0]["reason"])
        self.assertEqual(len(failures[0]["reason"]), a.REASON_MAX)

    def test_reason_collapses_every_whitespace_run(self):
        a.submit(OWNER, TEXT, "now")
        _, _, failures = self.collect_with(error="boom\r\tx")
        self.assertEqual(failures[0]["reason"], "boom x")

    def test_empty_herdr_error_still_gets_a_reason(self):
        a.submit(OWNER, TEXT, "now")
        _, _, failures = self.collect_with(error=" ")
        self.assertEqual(failures[0]["reason"], "Herdr is unavailable")

    def test_name_missing_from_the_map_says_no_live_architect(self):
        a.submit(OWNER, TEXT, "now")
        _, _, failures = self.collect_with(owners={"Other": "w1"})
        self.assertEqual(failures[0]["reason"], f"no live architect named {OWNER}")

    def test_ambiguous_name_says_ambiguous(self):
        a.submit(OWNER, TEXT, "now")
        data = agents(architect("one"), architect("two"))
        ambiguous = set()
        self.assertEqual(
            discovery.architect_owners(lambda *_: data, ambiguous_out=ambiguous), {}
        )
        _, _, failures = self.collect_with(ambiguous=ambiguous)
        self.assertTrue(failures[0]["reason"].startswith("ambiguous:"))

    def test_first_failure_time_survives_later_failures_and_clears_on_ready(self):
        a.submit(OWNER, TEXT, "now")
        self.collect_with(owners={})
        first = a.records()[0]["failed_at"]
        with patch.object(a.time, "time", return_value=first + 900):
            self.collect_with(owners={})
        self.assertEqual(a.records()[0]["failed_at"], first)
        ready, _, failures = self.collect_with(owners={OWNER: "workspace"})
        self.assertEqual(len(ready), 1)
        self.assertEqual(failures, [])
        self.assertNotIn("failure", a.records()[0])
        self.assertNotIn("failed_at", a.records()[0])

    def test_answer_delivered_between_the_two_lock_phases_keeps_no_failure(self):
        record = a.submit(OWNER, TEXT, "now")

        def deliver_then_fail(command, ambiguous_out=None):
            a.mark_delivered(a.event_line(dict(record, owner="workspace")))
            return {}

        with patch.object(
            a.discovery, "architect_owners", side_effect=deliver_then_fail
        ):
            a.collect(w.command)
        stored = a.records()[0]
        self.assertTrue(stored["delivered_at"])
        self.assertNotIn("failure", stored)

    def test_second_lock_timeout_still_returns_the_ready_answers(self):
        a.submit(OWNER, TEXT, "now")
        with patch.object(a, "record_failures", side_effect=TimeoutError("busy")):
            ready, _, _ = self.collect_with(owners={OWNER: "workspace"})
            self.assertEqual(len(ready), 1)
            _, _, failures = self.collect_with(owners={})
        self.assertEqual(failures[0]["architect"], OWNER)

    def test_adapter_on_the_old_contract_still_maps_owners(self):
        a.submit(OWNER, TEXT, "now")
        adapter = type("Old", (), {"architect_owners": lambda command: {OWNER: "w1"}})
        with patch.object(a.discovery, "adapter", return_value=adapter):
            ready, _, failures = a.collect(w.command)
        self.assertEqual(([r["owner"] for r in ready], failures), (["w1"], []))

    def test_delivery_clears_failure_fields(self):
        record = a.submit(OWNER, TEXT, "now")
        self.collect_with(owners={})
        self.assertIn("failure", a.records()[0])
        a.mark_delivered(a.event_line(dict(record, owner="workspace")))
        stored = a.records()[0]
        self.assertTrue(stored["delivered_at"])
        self.assertNotIn("failure", stored)
        self.assertNotIn("failed_at", stored)

    def test_answer_failure_stays_out_of_status_read_failures(self):
        a.submit(OWNER, TEXT, "now")
        with patch.object(
            a.discovery, "architect_owners", side_effect=discovery.Unavailable("down")
        ):
            raw = w.collect_todo(None)
        self.assertEqual(raw["unreadable"], [])
        self.assertEqual(raw["todo_answer_failures"][0]["architect"], OWNER)
        before = dict(now={}, unreadable=[], read=[OWNER + ".md"], scanned=True)
        after, events = w.reduce_todo(before, raw)
        self.assertEqual(after["unreadable"], [])
        self.assertEqual(after["read"], before["read"])
        self.assertEqual(events, [f"TODO READFAIL answer:{OWNER}: down"])

    def test_failure_line_repeats_at_the_wait_repeat_cadence_not_each_cycle(self):
        line = f"TODO READFAIL answer:{OWNER}: down"
        with patch.dict(os.environ, ORCH_WAIT_REPEAT_S="1800"):
            state, events = w.reduce_todo(None, self.raw(1000, self.failure()))
            self.assertEqual(events, [line])
            state, events = w.reduce_todo(state, self.raw(1060, self.failure()))
            self.assertEqual(events, [])
            state, events = w.reduce_todo(state, self.raw(2800, self.failure()))
            self.assertEqual(events, [line + " (repeat 1)"])
            _, events = w.reduce_todo(state, self.raw(2860, self.failure()))
            self.assertEqual(events, [])

    def test_wait_repeat_setting_below_the_floor_still_waits_sixty_seconds(self):
        with patch.dict(os.environ, ORCH_WAIT_REPEAT_S="1"):
            state, _ = w.reduce_todo(None, self.raw(1000, self.failure()))
            state, events = w.reduce_todo(state, self.raw(1030, self.failure()))
            self.assertEqual(events, [])
            _, events = w.reduce_todo(state, self.raw(1060, self.failure()))
            self.assertEqual(len(events), 1)

    def test_failure_ending_emits_one_recovered_line(self):
        state, _ = w.reduce_todo(None, self.raw(1000, self.failure("down")))
        _, events = w.reduce_todo(state, self.raw(1060))
        self.assertEqual(events, [f"TODO READ RECOVERED answer:{OWNER}: down"])

    def test_reason_flipping_between_cycles_stays_quiet_until_the_next_repeat(self):
        state, _ = w.reduce_todo(None, self.raw(1000, self.failure("down")))
        for at, reason in ((1060, "gone"), (1120, "down"), (1180, "gone")):
            state, events = w.reduce_todo(state, self.raw(at, self.failure(reason)))
            self.assertEqual(events, [])
        _, events = w.reduce_todo(state, self.raw(2800, self.failure("gone")))
        self.assertEqual(events, [f"TODO READFAIL answer:{OWNER}: gone (repeat 1)"])

    def test_reason_change_at_a_repeat_gives_one_repeat_line(self):
        state, _ = w.reduce_todo(None, self.raw(1000, self.failure("down")))
        state, events = w.reduce_todo(state, self.raw(2800, self.failure("gone")))
        self.assertEqual(events, [f"TODO READFAIL answer:{OWNER}: gone (repeat 1)"])
        _, events = w.reduce_todo(state, self.raw(2860))
        self.assertEqual(events, [f"TODO READ RECOVERED answer:{OWNER}: gone"])

    def test_strict_flip_gives_one_repeat_line_per_wait_repeat_window(self):
        state, _ = w.reduce_todo(None, self.raw(1000, self.failure("down")))
        lines = []
        for cycle in range(1, 121):
            reason = "gone" if cycle % 2 else "down"
            state, events = w.reduce_todo(
                state, self.raw(1000 + cycle * 60, self.failure(reason))
            )
            lines += events
        self.assertEqual(len(lines), 4)
        self.assertTrue(all("(repeat " in line for line in lines))
        self.assertNotIn("RECOVERED", " ".join(lines))

    def test_notifier_shows_each_repeat_and_one_recovered_clears_them_all(self):
        lines = [
            f"TODO READFAIL answer:{OWNER}: down",
            f"TODO READFAIL answer:{OWNER}: down (repeat 1)",
            f"TODO READFAIL answer:{OWNER}: down (repeat 2)",
        ]
        state, shown = {}, []
        for at, line in enumerate(lines + [lines[-1]], start=1):
            shown += alert_policy.apply(
                state, [dict(mode="todo", events=[line], at=at)], at
            )
        self.assertEqual(len(shown), 3)
        cleared = alert_policy.apply(
            state,
            [
                dict(
                    mode="todo",
                    events=[f"TODO READ RECOVERED answer:{OWNER}: down"],
                    at=9,
                )
            ],
            9,
        )
        cleared += alert_policy.apply(state, [], 100000)
        self.assertEqual(len(cleared), 1)
        self.assertFalse(any(entry["active"] for entry in state["keys"].values()))
