import copy
import fcntl
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase, open_work

import alert_policy
import audit
import watchers as w
from test_audit import LABEL, PR
from test_watchers import reader
from watcher_platform import live_slots


class IndependentReviewTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.pr = copy.deepcopy(PR)
        env = patch.dict(
            os.environ, {"ORCH_PR_AUTHOR": "author", "ORCH_AUDIT_PAGE_NODES": "100"}
        )
        env.start()
        self.addCleanup(env.stop)
        work = open_work("owner/product")
        work.start()
        self.addCleanup(work.stop)
        settings = patch.object(
            audit,
            "repo_settings",
            lambda command, repo, ref, cache: audit.orch_settings.merged(
                [], os.environ
            ),
        )
        settings.start()
        self.addCleanup(settings.stop)

    def fake(self, *args):
        if args[0] == "gh":
            return json.dumps(
                {
                    "data": {
                        "search": {
                            "nodes": [self.pr],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            )
        return '{"result":{"workspaces":[]}}'

    def raw(self, sources, unreadable=None):
        return dict(sources=sources, unreadable=unreadable or {})

    def reduce(self, before, sources, unreadable=None):
        return w.reduce("audit", before, self.raw(sources, unreadable))

    def test_m1_real_subprocess_stderr_for_every_command_source(self):
        detail = "HTTP 401: Bad credentials " + "x" * 250
        with open_work("owner/product", "/repo"):
            for source in ("github", "herdr", "git:/repo"):
                with self.subTest(source=source):

                    def command(*args):
                        current = (
                            "github"
                            if args[0] == "gh"
                            else "git:/repo"
                            if args[0] == "git"
                            else "herdr"
                        )
                        if current == source:
                            return w.command(
                                sys.executable,
                                "-c",
                                "import sys; sys.stderr.write(sys.argv[1]); sys.exit(7)",
                                detail,
                            )
                        if current == "git:/repo":
                            return str(self.base)
                        return self.fake(*args)

                    result = audit.collect(command, self.base / "slots")
                    self.assertEqual(
                        result["unreadable"][source],
                        f"READFAIL {source}: {detail[:200]}",
                    )

    def test_m2_recovered_first_snapshot_is_only_a_source_summary(self):
        before, _ = self.reduce(
            None, {"herdr": []}, {"github": "READFAIL github: offline"}
        )
        lines = [f"CONFLICT {n}" for n in range(50)]
        after, events = self.reduce(before, {"github": lines, "herdr": []})
        self.assertNotIn(lines[0], events)
        self.assertEqual(
            events,
            [
                "github READFAIL cleared",
                "AUDIT BASELINE github [50 active lines; see audit.json before.sources]",
            ],
        )
        self.assertEqual(after["sources"]["github"], lines)
        self.assertEqual(self.reduce(after, {"github": lines, "herdr": []})[1], [])

    def test_m2_new_source_baseline_then_only_changed_lines(self):
        before, _ = self.reduce(None, {"github": []})
        before, events = self.reduce(before, {"github": [], "git:/new": ["PACKS new"]})
        self.assertEqual(
            events,
            ["AUDIT BASELINE git:/new [1 active lines; see audit.json before.sources]"],
        )
        self.assertEqual(
            self.reduce(before, {"github": [], "git:/new": ["PACKS changed"]})[1],
            ["PACKS changed", "CLEARED PACKS new"],
        )

    def test_a_title_ending_in_cleared_is_a_live_condition(self):
        before, _ = self.reduce(None, {"github": []})
        before, events = self.reduce(before, {"github": ["APPROVED o/r#7 Fix cleared"]})
        self.assertEqual(events, ["APPROVED o/r#7 Fix cleared"])
        self.assertEqual(
            self.reduce(before, {"github": ["APPROVED o/r#7 Fix cleared"]})[1], []
        )
        state = {}
        self.assertEqual(
            alert_policy.apply(state, [{"mode": "audit", "events": events}], 0),
            ["audit: APPROVED o/r#7 Fix cleared (start)"],
        )

    def test_resolved_conditions_clear_and_a_count_change_does_not(self):
        line = "THREADS o/r#1 Fix [4 unresolved]"
        before, _ = self.reduce(
            None, {"github": [line, "CONFLICT o/r#2 B [conflicts with main]"]}
        )
        before, events = self.reduce(
            before, {"github": ["THREADS o/r#1 Fix [5 unresolved]"]}
        )
        self.assertEqual(
            events,
            [
                "THREADS o/r#1 Fix [5 unresolved]",
                "CLEARED CONFLICT o/r#2 B [conflicts with main]",
            ],
        )
        state = {}
        alert_policy.apply(
            state,
            [{"mode": "audit", "events": ["CONFLICT o/r#2 B [conflicts with main]"]}],
            0,
        )
        lines = alert_policy.apply(state, [{"mode": "audit", "events": events}], 10)
        self.assertEqual(
            lines,
            [
                "audit: THREADS o/r#1 Fix [5 unresolved] (start)",
                "audit: CONFLICT o/r#2 B [conflicts with main] (clear)",
            ],
        )

    def test_m3_status_context_failure(self):
        self.pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
            "nodes"
        ] = [{"context": "external", "state": "FAILURE"}]
        self.assertEqual(audit.pr_lines(self.pr), [f"RED {LABEL} [external]"])

    def test_m3_nonapproved_opinionated_review_on_head(self):
        self.pr["reviewDecision"] = "APPROVED"
        self.pr["latestOpinionatedReviews"]["nodes"] = [
            {"state": "CHANGES_REQUESTED", "commit": {"oid": "head"}}
        ]
        self.assertEqual(
            audit.pr_lines(self.pr),
            [f"APPROVED-OLDHEAD {LABEL} [approval is not on the current head]"],
        )

    def test_m3_noreviewer_approved_exclusion_zero_requests(self):
        self.pr["reviewDecision"] = "APPROVED"
        self.pr["reviewRequests"]["totalCount"] = 0
        self.pr["latestOpinionatedReviews"]["nodes"] = [
            {"state": "APPROVED", "commit": {"oid": "head"}}
        ]
        self.assertEqual(audit.pr_lines(self.pr), [f"APPROVED {LABEL}"])

    def test_m3_latest_wins_tie(self):
        self.pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
            "nodes"
        ] = [
            dict(name="unit", conclusion="SUCCESS", startedAt="same"),
            dict(name="unit", conclusion="FAILURE", startedAt="same"),
        ]
        self.assertEqual(audit.pr_lines(self.pr), [f"RED {LABEL} [unit]"])

    def test_l1_shared_slot_rules_and_per_file_isolation(self):
        slots = self.base / "slots"
        slots.mkdir()
        (slots / ".DS_Store").write_text("garbage")
        (slots / "broken").symlink_to(slots / "absent")
        (slots / "live").write_text(str(os.getpid()))
        (slots / "dead").write_text("999999")
        (slots / "bad").write_text("garbage")
        (slots / "huge").write_text("9" * 100)
        (slots / "dir").mkdir()
        count, errors = live_slots(slots)
        self.assertEqual(count, 1)
        self.assertEqual(sorted(errors), ["slot:bad", "slot:huge"])
        raw = audit.collect(self.fake, slots)
        lines = [
            line for source_lines in raw["sources"].values() for line in source_lines
        ]
        self.assertIn("STALESLOT dead pid=999999", lines)
        self.assertIn("SLOTDIR dir", lines)
        self.assertEqual(sorted(raw["unreadable"]), ["slot:bad", "slot:huge"])
        self.assertFalse(any(".DS_Store" in line or "broken" in line for line in lines))

    def test_l1_denied_file_preserves_only_its_previous_alert(self):
        slots = self.base / "slots"
        slots.mkdir()
        (slots / "dead").write_text("999999")
        (slots / "healthy").write_text(str(os.getpid()))
        before, _ = w.reduce("audit", None, audit.collect(self.fake, slots))
        read = Path.read_text

        def denied(path, *args, **kwargs):
            if path.name == "dead":
                raise PermissionError("denied")
            return read(path, *args, **kwargs)

        with patch.object(Path, "read_text", denied):
            raw = audit.collect(self.fake, slots)
        after, events = w.reduce("audit", before, raw)
        self.assertEqual(after["sources"]["slot:dead"], ["STALESLOT dead pid=999999"])
        self.assertEqual(after["sources"]["slot:healthy"], [])
        self.assertEqual(events, ["READFAIL slot:dead: denied"])

    def test_l2_busy_lock_reports_running_without_writing_state(self):
        with (self.base / "hog.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            collector = unittest.mock.Mock()
            line = w.cycle(self.base, "hog", collector)
            self.assertEqual(
                json.loads(line), {"mode": "hog", "events": ["hog running"]}
            )
            collector.assert_not_called()
            self.assertFalse((self.base / "hog.json").exists())

    def test_l3_heartbeat_grace_uses_mode_timing(self):
        w.cycle(self.base, "audit", lambda before: self.raw({"github": []}))
        now, cursor = time.time(), {}
        reader.read_once(self.base, cursor, now, 900, ["audit"])
        with patch.dict(
            w.WATCH_TIMING, {"audit": {"interval": 3600, "heartbeat_grace": 4500}}
        ):
            self.assertIsNone(
                reader.read_once(self.base, cursor, now + 3601, 900, ["audit"])
            )
            self.assertIn(
                "HEARTBEAT STALE",
                reader.read_once(self.base, cursor, now + 4501, 900, ["audit"]),
            )

    def test_l4_individual_recovery_and_unchanged_error_dedupe(self):
        before, _ = self.reduce(None, {"github": [], "herdr": []})
        errors = {
            "github": "READFAIL github: offline",
            "herdr": "READFAIL herdr: offline",
        }
        before, _ = self.reduce(before, {}, errors)
        before, events = self.reduce(
            before, {"herdr": []}, {"github": errors["github"]}
        )
        self.assertEqual(events, ["herdr READFAIL cleared"])
        self.assertEqual(
            self.reduce(before, {"github": [], "herdr": []})[1],
            ["github READFAIL cleared"],
        )

    def test_l6_page_size_is_canonical_and_fixed_for_the_pass(self):
        self.pr["reviewThreads"]["pageInfo"]["hasNextPage"] = True

        def command(*args):
            self.assertIn("ni=100", args)
            os.environ["ORCH_AUDIT_PAGE_NODES"] = "80"
            return self.fake(*args)

        with patch.dict(os.environ, {"ORCH_AUDIT_PAGE_NODES": "0100"}):
            self.assertEqual(
                audit.github_lines(command),
                [f"TRUNCATED {LABEL} [more than 100 review threads]"],
            )

    def test_l1_directory_failure_keeps_per_file_uncertainty(self):
        before, _ = self.reduce(
            None, {"slots": [], "slot:dead": ["STALESLOT dead pid=999999"]}
        )
        before, _ = self.reduce(
            before, {"slots": []}, {"slot:dead": "READFAIL slot:dead: denied"}
        )
        after, events = self.reduce(before, {}, {"slots": "READFAIL slots: denied"})
        self.assertEqual(events, ["READFAIL slots: denied"])
        self.assertEqual(after["unreadable"]["slot:dead"], "READFAIL slot:dead: denied")
        self.assertEqual(after["sources"]["slot:dead"], ["STALESLOT dead pid=999999"])

    def test_l3_audit_loop_defaults_to_its_configured_interval(self):
        fixture = self.base / "fixture.json"
        fixture.write_text(json.dumps(self.raw({"github": []})))
        with (
            patch.object(
                sys,
                "argv",
                [
                    "watchers.py",
                    "audit",
                    "--state-dir",
                    str(self.base),
                    "--fixture",
                    str(fixture),
                    "--loop",
                ],
            ),
            patch.object(
                w.runtime, "sleep", side_effect=RuntimeError("stop loop")
            ) as sleep,
            patch("builtins.print"),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop loop"):
                w.main()
        sleep.assert_called_once_with(1800)

    def test_l1_unreadable_slot_directory_is_one_missing_balance_metric(self):
        with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            self.assertEqual(live_slots(self.base / "slots"), (None, []))
            self.assertEqual(
                audit.collect(self.fake, self.base / "slots")["unreadable"],
                {"slots": "READFAIL slots: denied"},
            )
