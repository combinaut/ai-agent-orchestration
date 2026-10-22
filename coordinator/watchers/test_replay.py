"""Replays the 2026-10-13 alert stream through the producers and the feed policy."""

import json
import os
import re
import unittest
from collections import defaultdict
from unittest.mock import patch

from test_support import IsolatedTestCase

import alert_policy as policy
import watchers as w

FIXTURE = w.ROOT / "fixtures" / "replay-2026-10-13"
CFG = dict(ratio=1.5, digest=1800, hold=1800)


def hog_batches():
    """hog.log hits grouped by scan; a gap over one interval gets an empty scan, as the producer saw it."""
    scans = defaultdict(list)
    for line in (FIXTURE / "hog-hits.txt").read_text().splitlines():
        ts, pid, gb, age, comm = line.split()
        scans[int(ts)].append(
            dict(
                pid=pid,
                ppid="0",
                rss=int(float(gb) * 1048576),
                age=int(age),
                start=f"s{pid}",
                comm=comm,
                command=comm,
            )
        )
    before, previous, out = None, None, []
    for ts in sorted(scans):
        if (
            previous is not None
            and ts - previous > w.WATCH_TIMING["hog"]["interval"] + 1
        ):
            before, events = w.reduce("hog", before, [])
            out.append(
                (
                    previous + w.WATCH_TIMING["hog"]["interval"],
                    {"mode": "hog", "events": events},
                )
            )
        before, events = w.reduce("hog", before, scans[ts])
        out.append((ts, {"mode": "hog", "events": events}))
        previous = ts
    before, events = w.reduce("hog", before, [])
    out.append(
        (
            previous + w.WATCH_TIMING["hog"]["interval"],
            {"mode": "hog", "events": events},
        )
    )
    return out, sum(len(rows) for rows in scans.values())


def alert_batches(start):
    """Each mode's batches in order, one producer interval apart."""
    counts, out = defaultdict(int), []
    for line in (FIXTURE / "alerts.jsonl").read_text().splitlines():
        alert = json.loads(line)
        out.append(
            (
                start
                + counts[alert["mode"]] * w.WATCH_TIMING[alert["mode"]]["interval"],
                alert,
            )
        )
        counts[alert["mode"]] += 1
    return out


def live_batches(start):
    return [
        (start + n * 60, json.loads(line))
        for n, line in enumerate(
            (FIXTURE / "fseventsd-live.jsonl").read_text().splitlines()
        )
    ]


def replay():
    hog, hits = hog_batches()
    batches = sorted(
        hog + alert_batches(hog[0][0]) + live_batches(hog[-1][0] - 3600),
        key=lambda row: row[0],
    )
    state, trace, lines = {}, [], []
    for now, alert in batches:
        if alert["events"]:
            lines += policy.apply(state, [alert], now, CFG, trace)
    lines += policy.apply(state, [], batches[-1][0] + CFG["hold"], CFG, trace)
    events = sum(
        len(alert["events"]) for _, alert in alert_batches(0) + live_batches(0)
    )
    return dict(
        lines_in=hits + events,
        events_in=events + sum(len(a["events"]) for _, a in hog),
        lines=lines,
        trace=trace,
    )


class ReplayTest(IsolatedTestCase):
    def setUp(self):
        env = patch.dict(
            os.environ,
            {
                "ORCH_HOG_GB": "6",
                "ORCH_HOG_GIT_GB": "2",
                "ORCH_HOST_SWAP_MAX_MB": "1024",
                "ORCH_HOST_LOAD_MAX": "40",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.result = replay()

    def by_key(self):
        sequence = defaultdict(list)
        for key, reason in self.result["trace"]:
            sequence[key].append(reason)
        return sequence

    def phases(self, reasons):
        """A danger line opens an episode or marks a crossing inside one."""
        out = []
        for reason in reasons:
            if reason == "danger":
                if out and out[-1] == "start":
                    continue
                reason = "start"
            out.append(reason)
        return out

    def test_only_start_danger_clear_and_digest_lines_come_out(self):
        reasons = {line.rsplit(" (", 1)[1] for line in self.result["lines"]}
        self.assertLessEqual(
            reasons, {"start)", "danger)", "clear)", "digest)", "unparsed)"}
        )
        self.assertLess(len(self.result["lines"]), self.result["lines_in"] / 4)

    def test_every_key_alternates_start_and_clear(self):
        for key, reasons in self.by_key().items():
            phases = self.phases(reasons)
            self.assertEqual(
                phases,
                ["start", "clear"] * (len(phases) // 2) + ["start"] * (len(phases) % 2),
                key,
            )

    def test_held_conditions_start_once_and_clear_at_most_once(self):
        for key, reasons in self.by_key().items():
            if policy.held(key):
                phases = self.phases(reasons)
                self.assertEqual(phases.count("start"), 1, key)
                self.assertLessEqual(phases.count("clear"), 1, key)

    def test_fseventsd_gives_one_start_and_one_clear(self):
        self.assertEqual(self.by_key()["hog:340/fseventsd"], ["start", "clear"])

    def test_live_fseventsd_sequence_in_both_formats_starts_once(self):
        state, lines = {}, []
        for now, line in enumerate(
            (FIXTURE / "fseventsd-live.jsonl").read_text().splitlines()
        ):
            lines += policy.apply(state, [json.loads(line)], now * 60, CFG)
        self.assertEqual(lines, ["hog: HOG 340/fseventsd 6.2GB (start)"])

    def test_swap_starts_in_danger_once_and_load_crosses_into_danger_once_then_clears(
        self,
    ):
        self.assertEqual(self.by_key()["host:swap"], ["danger"])
        self.assertEqual(self.by_key()["host:load"], ["start", "danger", "clear"])

    def test_action_filter_sees_only_start_and_danger_lines(self):
        patterns = [
            re.compile(line)
            for line in (w.ROOT / "feed-actions.regex").read_text().splitlines()
        ]
        actions = [
            line
            for line in self.result["lines"]
            if any(p.search("2026-10-13T00:00:00+0200 " + line) for p in patterns)
        ]
        self.assertTrue(actions)
        self.assertTrue(all(line.endswith(("(start)", "(danger)")) for line in actions))
        self.assertFalse(
            [
                line
                for line in actions
                if line.startswith(("balance:", "hog: HOG", "audit:"))
            ]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
