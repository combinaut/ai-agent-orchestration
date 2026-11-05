import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import watchers as w

PR = "https://github.com/acme/app/pull/7"
ISSUE = "https://github.com/acme/app/issues/8"


class TodoStaleTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.status = self.root / "status"
        self.status.mkdir()
        self.env = patch.dict(
            "os.environ",
            {
                "ORCH_USER_TODO_STATUS_DIR": str(self.status),
                "ORCH_USER_TODO_OUTPUT": str(self.root / "todo.md"),
                "ORCH_USER_TODO_STATE": str(self.root / "state.json"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.calls = []
        self.before = None

    def write(self, *items):
        text = "## Waits on the user\n" + "".join(f"- {i}\n" for i in items)
        (self.status / "Architect__Tooling.md").write_text(text)

    def age_links(self):
        path = self.root / "state.json"
        state = json.loads(path.read_text())
        for entry in state["links"].values():
            entry["at"] -= w.todo_module().LINK_TTL + 1
        path.write_text(json.dumps(state))

    def cycle(self, states):
        def lookup(url, timeout=None):
            self.calls.append(url)
            result = states[url]
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(w, "github_link_state", lookup):
            raw = w.collect_todo(self.before)
        self.before, events = w.reduce_todo(self.before, raw)
        w.commit_todo_closures(raw)
        shown = sorted(item["text"] for item in raw["now"])
        return shown, [e for e in events if e.startswith("TODO AUTO-CLOSED")]

    def test_all_links_resolved_hides_the_item_with_one_feed_line(self):
        self.write(f"Merge {PR} and close {ISSUE} -> Coordinator")
        shown, events = self.cycle({PR: "merged", ISSUE: "closed"})
        self.assertEqual(shown, [])
        self.assertEqual(
            events,
            [
                f"TODO AUTO-CLOSED Architect__Tooling: Merge {PR} and close {ISSUE} -> Coordinator ({PR} merged)"
            ],
        )
        shown, events = self.cycle({PR: "merged", ISSUE: "closed"})
        self.assertEqual((shown, events), ([], []))

    def test_one_open_link_keeps_the_item(self):
        self.write(f"Merge {PR} and close {ISSUE} -> Coordinator")
        shown, events = self.cycle({PR: "merged", ISSUE: "open"})
        self.assertEqual(len(shown), 1)
        self.assertEqual(events, [])

    def test_item_without_a_link_stays_and_is_not_looked_up(self):
        self.write("Approve the design -> Coordinator")
        shown, events = self.cycle({})
        self.assertEqual(len(shown), 1)
        self.assertEqual((events, self.calls), ([], []))

    def test_failed_lookup_keeps_the_item(self):
        self.write(f"Merge {PR} -> Coordinator")
        shown, events = self.cycle({})
        self.assertEqual(len(shown), 1)
        self.assertEqual(events, [])

    def test_changed_text_after_auto_close_shows_again(self):
        self.write(f"Merge {PR} -> Coordinator")
        self.assertEqual(self.cycle({PR: "merged"})[0], [])
        self.write(f"Review {ISSUE} first -> Coordinator")
        shown, events = self.cycle({ISSUE: "open"})
        self.assertEqual(len(shown), 1)
        self.assertEqual(events, [])

    def test_resolved_state_is_cached_across_cycles(self):
        self.write(f"Merge {PR} and see {ISSUE} -> Coordinator")
        self.cycle({PR: "merged", ISSUE: "open"})
        self.age_links()
        self.cycle({PR: "merged", ISSUE: "open"})
        self.assertEqual(self.calls.count(PR), 1)
        self.assertEqual(self.calls.count(ISSUE), 2)

    def test_open_state_is_looked_up_again_after_the_ttl(self):
        self.write(f"Merge {PR} -> Coordinator")
        self.cycle({PR: "open"})
        self.cycle({PR: "open"})
        self.assertEqual(self.calls, [PR])
        self.age_links()
        self.cycle({PR: "open"})
        self.assertEqual(self.calls, [PR, PR])

    def test_lookups_per_cycle_are_bounded(self):
        urls = [f"https://github.com/acme/app/pull/{n}" for n in range(1, 9)]
        self.write(*(f"Merge {url} -> Coordinator" for url in urls))
        self.cycle({url: "open" for url in urls})
        self.assertEqual(len(self.calls), w.todo_module().LINK_LOOKUPS_MAX)
        self.cycle({url: "open" for url in urls})
        self.assertEqual(len(set(self.calls)), 8)

    def test_lookups_stop_when_the_cycle_budget_is_spent(self):
        urls = [f"https://github.com/acme/app/pull/{n}" for n in range(1, 4)]
        self.write(*(f"Merge {url} -> Coordinator" for url in urls))
        todo = w.todo_module()
        ticks = iter([0, 0, 6, todo.LINK_BUDGET_SECONDS, todo.LINK_BUDGET_SECONDS + 1])
        timeouts = []

        def lookup(url, timeout):
            self.calls.append(url)
            timeouts.append(timeout)
            return "merged"

        links = todo.resolve_links(
            self.status,
            self.root / "state.json",
            lookup,
            clock=lambda: next(ticks),
        )
        self.assertEqual(self.calls, urls[:1])
        self.assertEqual(timeouts, [todo.LINK_BUDGET_SECONDS - 6])
        self.assertEqual(list(links), urls[:1])
        raw = todo.merge(
            self.status, self.root / "todo.md", self.root / "state.json", links=links
        )
        todo.commit_auto_closed(
            raw["auto_closed"], self.root / "state.json", self.root / "todo.md"
        )
        shown = (self.root / "todo.md").read_text()
        self.assertNotIn(urls[0], shown)
        self.assertIn(urls[1], shown)

    def test_expired_early_links_do_not_starve_never_checked_ones(self):
        urls = [f"https://github.com/acme/app/pull/{n}" for n in range(1, 9)]
        self.write(*(f"Merge {url} -> Coordinator" for url in urls))
        self.cycle({url: "open" for url in urls})
        self.age_links()
        self.calls.clear()
        self.cycle({url: "open" for url in urls})
        self.assertEqual(self.calls[:3], urls[5:])

    def test_cached_resolution_survives_a_merge_without_links(self):
        self.write(f"Merge {PR} -> Coordinator")
        self.assertEqual(self.cycle({PR: "merged"})[0], [])
        self.write(f"Merge {PR} again -> Coordinator")
        todo = w.todo_module()
        todo.merge(self.status, self.root / "todo.md", self.root / "state.json")
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual(state["links"][PR]["state"], "merged")

    def test_cached_resolution_applies_to_a_changed_item_after_the_original_was_dismissed(
        self,
    ):
        self.write(f"Merge {PR} -> Coordinator")
        self.cycle({PR: "merged"})
        self.cycle({})
        self.write(f"Merge {PR} again -> Coordinator")
        shown, events = self.cycle({})
        self.assertEqual(shown, [])
        self.assertEqual(len(events), 1)

    def test_auto_closed_events_survive_a_watcher_state_reset(self):
        self.write(f"Merge {PR} -> Coordinator")
        base = self.root / "watch"
        base.mkdir()
        (base / "todo.json").write_text("{not json")
        with patch.object(w, "github_link_state", lambda url, timeout=None: "merged"):
            out = json.loads(w.cycle(base, "todo", w.collect_todo))
        self.assertTrue(any(e.startswith("TODO AUTO-CLOSED ") for e in out["events"]))

    def run_cycle(self, base):
        with patch.object(w, "github_link_state", lambda url, timeout=None: "merged"):
            line = w.cycle(base, "todo", w.collect_todo)
        return json.loads(line)["events"] if line else []

    def shown_in_output(self):
        return (self.root / "todo.md").read_text()

    def test_item_stays_and_its_event_is_sent_after_a_collect_failure(self):
        self.write(f"Merge {PR} -> Coordinator")
        base = self.root / "watch"
        self.run_cycle(base)
        (self.root / "state.json").unlink()
        (self.root / "todo.md").unlink()
        with patch.object(w.todo_answers, "collect", side_effect=OSError("timeout")):
            first = self.run_cycle(base)
        self.assertFalse(any("AUTO-CLOSED" in e for e in first))
        self.assertIn(PR, self.shown_in_output())
        second = self.run_cycle(base)
        self.assertEqual(sum("AUTO-CLOSED" in e for e in second), 1)
        self.assertNotIn(PR, self.shown_in_output())

    def test_item_stays_and_its_event_is_sent_after_a_publish_failure(self):
        self.write(f"Merge {PR} -> Coordinator")
        base = self.root / "watch"
        with patch.object(w, "publish_events", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_cycle(base)
        self.assertIn(PR, self.shown_in_output())
        second = self.run_cycle(base)
        self.assertEqual(sum("AUTO-CLOSED" in e for e in second), 1)
        self.assertNotIn(PR, self.shown_in_output())

    def test_every_url_form_must_resolve_before_the_item_closes(self):
        forms = (
            "http://github.com/acme/app/issues/8",
            "https://www.github.com/acme/app/issues/8",
            "www.github.com/acme/app/issues/8",
            "github.com/acme/app/issues/8",
            "HTTPS://GitHub.com/acme/app/issues/8",
        )
        for form in forms:
            with self.subTest(form=form):
                self.write(f"Merge {PR} then close {form} -> Coordinator")
                self.before = None
                (self.root / "state.json").unlink(missing_ok=True)
                shown, events = self.cycle({PR: "merged", ISSUE: "open"})
                self.assertEqual(len(shown), 1)
                self.assertEqual(events, [])

    def test_url_forms_are_looked_up_as_one_https_url(self):
        self.write("Merge HTTP://www.GitHub.com/Acme/App/pull/7 -> Coordinator")
        self.cycle({PR: "merged"})
        self.assertEqual(self.calls, [PR])

    def test_an_unresolvable_reference_keeps_the_item(self):
        for tail in (
            "and fix #123",
            "and PR 124",
            "and issue 125",
            "and Pull Request #126",
        ):
            with self.subTest(tail=tail):
                self.write(f"Merge {PR} {tail} -> Coordinator")
                self.before = None
                (self.root / "state.json").unlink(missing_ok=True)
                shown, events = self.cycle({PR: "merged"})
                self.assertEqual(len(shown), 1)
                self.assertEqual(events, [])

    def test_urls_on_other_hosts_are_not_links(self):
        for other in (
            "https://example.com/github.com/acme/app/pull/7",
            "https://evil.github.com/acme/app/pull/7",
            "https://notgithub.com/acme/app/pull/7",
        ):
            with self.subTest(other=other):
                self.write(f"Merge {other} -> Coordinator")
                shown, events = self.cycle({PR: "merged"})
                self.assertEqual((len(shown), events, self.calls), (1, [], []))

    def test_a_reference_covered_by_a_url_does_not_keep_the_item(self):
        self.write(f"Merge PR 7 and #7 ({PR}) -> Coordinator")
        shown, events = self.cycle({PR: "merged"})
        self.assertEqual((shown, len(events)), ([], 1))

    def test_github_link_state_passes_the_timeout_to_gh(self):
        seen = []

        def fake(*argv, timeout=None):
            seen.append(timeout)
            return json.dumps({"state": "open"})

        with patch.object(w, "command", fake):
            w.github_link_state(PR, 3.5)
        self.assertEqual(seen, [3.5])

    def test_github_link_state_reads_pull_and_issue_rows(self):
        rows = {
            "repos/acme/app/pulls/7": {"state": "closed", "merged": True},
            "repos/acme/app/pulls/9": {"state": "closed", "merged": False},
            "repos/acme/app/pulls/10": {"state": "open", "merged": False},
            "repos/acme/app/issues/8": {"state": "closed"},
        }
        with patch.object(w, "command", lambda *argv, **_: json.dumps(rows[argv[2]])):
            self.assertEqual(w.github_link_state(PR), "merged")
            self.assertEqual(
                w.github_link_state("https://github.com/acme/app/pull/9"), "closed"
            )
            self.assertEqual(
                w.github_link_state("https://github.com/acme/app/pull/10"), "open"
            )
            self.assertEqual(w.github_link_state(ISSUE), "closed")


if __name__ == "__main__":
    unittest.main()
