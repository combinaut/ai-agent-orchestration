import importlib.util
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase
import feed_actions
import watchers as w

spec = importlib.util.spec_from_file_location("user_todo_view", w.ROOT.parent / "user-todo-view.py")
view = importlib.util.module_from_spec(spec)
spec.loader.exec_module(view)

LIST = "## Architect::Finder.md\n- [1h] Merge it -> https://example.com/pr/1\n- [now] Same text\n\n## Architect::Tooling.md\n- [3h] Same text\n"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class ViewCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.items = view.parse(LIST)

    def hidden(self):
        return view.Hidden(self.root / "state.json", self.root / "feed.log", self.clock)

    def plain(self, lines):
        return [ANSI.sub("", line) for line in lines]

    def click(self, hits, line, column):
        return view.input_events(f"\x1b[<0;{column};{line}M", hits)[0]


class StateTest(ViewCase):
    def test_items_drop_the_age_tag_and_keep_the_owner(self):
        self.assertEqual(self.items[0], ("Architect::Finder", "Merge it -> https://example.com/pr/1"))

    def test_state_is_keyed_by_owner_and_text(self):
        hidden = self.hidden()
        hidden.ignore("Architect::Finder", "Same text")
        shown = [(owner, text) for owner, text, *_ in hidden.visible(self.items)]
        self.assertNotIn(("Architect::Finder", "Same text"), shown)
        self.assertIn(("Architect::Tooling", "Same text"), shown)

    def test_ignore_survives_a_restart_and_clears_when_the_text_changes(self):
        self.hidden().ignore("Architect::Finder", "Same text")
        restarted = self.hidden()
        self.assertEqual(len(restarted.visible(self.items)), 2)
        changed = view.parse("## Architect::Finder.md\n- [now] Same text, now urgent\n")
        self.assertEqual(len(restarted.visible(changed)), 1)

    def test_cancel_puts_a_reminder_item_back_in_the_open_list_at_once(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 7200)
        rows = hidden.visible(self.items, True)
        index = next(i for i, row in enumerate(rows) if row[3])
        self.assertEqual(view.key_action("remind", rows, index), "cancel")
        self.assertEqual(view.act(hidden, rows, self.items, "cancel", index), "reminder cancelled")
        restarted = self.hidden()
        self.assertEqual(len(restarted.visible(self.items)), 3)
        self.assertEqual(restarted.hidden_count(self.items), 0)
        self.assertEqual([row[2] for row in restarted.visible(self.items)], [False, False, False])

    def test_the_r_key_opens_the_picker_on_an_item_without_a_reminder(self):
        rows = [("A", "x", False, ""), ("B", "y", False, "back at 10:00")]
        self.assertEqual((view.key_action("remind", rows, 0), view.key_action("remind", rows, 1)), ("remind", "cancel"))
        self.assertEqual(view.key_action("open", rows, 1), "open")

    def test_ignore_replaces_a_reminder_in_the_stored_state(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        hidden.ignore(*self.items[0])
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual((len(state["ignored"]), state["later"]), (1, {}))

    def test_ignored_items_never_show_under_the_reminder_toggle(self):
        hidden = self.hidden()
        hidden.ignore(*self.items[0])
        self.assertNotIn(self.items[0], [row[:2] for row in hidden.visible(self.items, True)])

    def test_reminder_hides_until_the_picked_time_then_returns(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 7200)
        self.clock.now += 7199
        self.assertEqual(len(hidden.visible(self.items)), 2)
        self.clock.now += 1
        self.assertEqual(len(hidden.visible(self.items)), 3)

    def test_reminder_mark_shows_once(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        self.clock.now += 60
        first = next(row for row in hidden.visible(self.items) if row[:2] == self.items[0])
        again = next(row for row in hidden.visible(self.items) if row[:2] == self.items[0])
        restarted = next(row for row in self.hidden().visible(self.items) if row[:2] == self.items[0])
        self.assertEqual((first[2], again[2], restarted[2]), (True, True, False))

    def test_counts_cover_open_and_reminder_items_but_never_ignored_ones(self):
        hidden = self.hidden()
        hidden.ignore(*self.items[0])
        hidden.remind(*self.items[1], self.clock.now + 60)
        self.assertEqual((hidden.open_count(self.items), hidden.hidden_count(self.items)), (2, 1))
        self.assertEqual([row[3] for row in hidden.visible(self.items)], [""])
        self.assertEqual(len(hidden.visible(self.items, True)), 2)
        self.clock.now += 60
        self.assertEqual(hidden.hidden_count(self.items), 0)

    def test_state_file_differs_from_the_generators(self):
        self.assertEqual(view.HIDDEN.name, "user-todo-state.json")
        self.assertNotEqual(view.HIDDEN.name, "user-todo.state.json")
        self.assertNotEqual(view.HIDDEN.with_suffix(".lock").name, "user-todo.state.json.lock")

    def test_state_write_is_atomic_through_a_same_directory_temp_file(self):
        hidden = self.hidden()
        hidden.ignore(*self.items[0])
        before = (self.root / "state.json").read_text()
        moves = []
        real = os.replace
        with patch.object(view.os, "replace", lambda src, dst: (moves.append((src, dst)), real(src, dst))[1]):
            hidden.remind(*self.items[1], self.clock.now + 60)
        (src, dst), = moves
        self.assertEqual(Path(src).parent, Path(dst).parent)
        self.assertNotEqual(src, dst)
        with patch.object(view.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                hidden.remind(*self.items[0], self.clock.now + 60)
        self.assertEqual(json.loads((self.root / "state.json").read_text())["ignored"], json.loads(before)["ignored"])
        self.assertEqual(sorted(path.name for path in self.root.iterdir() if path.name.startswith("state.json.")), [])


class FeedTest(ViewCase):
    def test_ignore_writes_one_feed_line_the_action_filter_matches(self):
        self.hidden().ignore("Architect::Finder", "Merge it -> https://example.com/pr/1")
        lines = (self.root / "feed.log").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^\S+ todo: TODO IGNORED Architect::Finder: Merge it -> https://example.com/pr/1 \(start\)$")
        self.assertTrue(feed_actions.is_action(lines[0], feed_actions.action_patterns()))

    def test_only_ignore_lines_match_the_new_pattern(self):
        patterns = feed_actions.action_patterns()
        quiet = ["2026-10-21T10:00:00+0200 todo: TODO IGNORED (start)",
                 "2026-10-21T10:00:00+0200 todo: TODO IGNORED Architect::Finder: x (clear)",
                 "2026-10-21T10:00:00+0200 todo: TODO ignored Architect::Finder: x (start)",
                 "2026-10-21T10:00:00+0200 pulls: TODO IGNORED Architect::Finder: x (start)",
                 "2026-10-21T10:00:00+0200 todo: TODO IGNORED Two Words: x (start)",
                 "2026-10-21T10:00:00+0200 todo: TODO 1 fresh: x (start)"]
        self.assertEqual([line for line in quiet if feed_actions.is_action(line, patterns)], [])

    def test_feed_text_is_one_line(self):
        line = view.feed_line("Architect::Finder", "a\nb\tc\x1b[31m", self.clock.now)
        self.assertEqual(line.count("\n"), 1)
        self.assertTrue(line.endswith("(start)\n"))

    def test_an_unwritable_feed_keeps_the_ignore_and_says_so(self):
        hidden = view.Hidden(self.root / "state.json", self.root / "missing" / "feed.log", self.clock)
        rows = [(*self.items[0], False, "")]
        self.assertIn("feed not written", view.act(hidden, rows, self.items, "ignore", 0))
        self.assertEqual(len(self.hidden().visible(self.items)), 2)


class ClickTest(ViewCase):
    def layout(self, width, selected=0, picker=None):
        hidden = self.hidden()
        rows = hidden.visible(self.items)
        return view.layout(rows, width, 0, selected, False, picker, len(rows))

    def test_each_button_is_hit_across_its_whole_label_and_nowhere_else(self):
        for width in (80, 40, 24):
            lines, hits = self.layout(width)
            shown = self.plain(lines)
            for line, first, last, action, index in hits:
                for column in range(first, last + 1):
                    self.assertEqual(self.click(hits, line, column), [(action, index)])
                self.assertEqual(self.click(hits, line, last + 1), [])
                label = shown[line - 1][first - 1:last].strip().split("] ")[-1]
                self.assertTrue(label in ("Open", "Ignore", "Reminder") or label.startswith(("show", "hide")), label)

    def test_buttons_still_match_after_the_message_wraps(self):
        self.items = view.parse("## A.md\n- " + "word " * 30 + "end\n")
        lines, hits = self.layout(30)
        by_action = {action: line for line, _f, _l, action, _i in hits}
        self.assertEqual(len(set(by_action[a] for a in ("open", "ignore", "remind"))) >= 1, True)
        self.assertIn("Open", self.plain(lines)[by_action["open"] - 1])

    def test_three_actions_and_the_toggle_are_reachable(self):
        _lines, hits = self.layout(80)
        self.assertEqual({action for _l, _f, _t, action, _i in hits if _i == 0}, {"open", "ignore", "remind"})
        self.assertIn("show", [action for _l, _f, _t, action, _i in hits])

    def test_only_a_left_press_counts(self):
        _lines, hits = self.layout(80)
        line, first, _last, _action, _index = hits[0]
        for sequence in (f"\x1b[<0;{first};{line}m", f"\x1b[<2;{first};{line}M", f"\x1b[<64;{first};{line}M"):
            self.assertEqual(view.input_events(sequence, hits)[0], [])

    def test_a_split_escape_sequence_waits_for_its_rest(self):
        _lines, hits = self.layout(80)
        line, first, *_ = hits[0]
        sequence = f"\x1b[<0;{first};{line}M"
        events, pending, _ = view.input_events(sequence[:5], hits)
        self.assertEqual((events, pending), ([], sequence[:5]))
        self.assertEqual(view.input_events(pending + sequence[5:], hits)[0], [("open", 0)])

    def test_keys_map_to_the_selected_item(self):
        _lines, hits = self.layout(80)
        self.assertEqual(view.input_events("oir", hits)[0], [("open", None), ("ignore", None), ("remind", None)])
        self.assertEqual(view.input_events("\x1b[B\x1b[A", hits)[0], [("move", 1), ("move", -1)])
        self.assertEqual(view.input_events("2", hits)[0], [("select", 1)])

    def test_a_reminder_item_offers_cancel_reminder_in_the_third_button(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        rows = hidden.visible(self.items, True)
        index = next(i for i, row in enumerate(rows) if row[3])
        lines, hits = view.layout(rows, 80, 1, index, True, None, 3)
        third = next(h for h in hits if h[4] == index and h[3] == "cancel")
        self.assertIn("[r] Cancel reminder", self.plain(lines)[third[0] - 1][third[1] - 1:third[2]])
        self.assertEqual(self.click(hits, third[0], third[1]), [("cancel", index)])
        self.assertNotIn("remind", {h[3] for h in hits if h[4] == index})
        other = next(h for h in hits if h[4] != index and h[3] == "remind")
        self.assertIn("Reminder", self.plain(lines)[other[0] - 1])

    def test_key_hints_show_on_the_selected_item_only(self):
        shown = self.plain(self.layout(80, 0)[0])
        self.assertEqual(sum("[o] Open" in line for line in shown), 1)

    def test_picker_buttons_are_clickable_and_digits_pick_a_time(self):
        lines, hits = self.layout(80, 0, {"index": 0, "mode": "pick", "entry": ""})
        picks = {action for _l, _f, _t, action, _i in hits if action.startswith("pick")}
        self.assertEqual(picks, {"pick1", "pick2", "pick3", "pick4", "pick5"})
        self.assertEqual(view.input_events("3", hits, "", "pick")[0], [("pick3", None)])
        self.assertEqual(view.input_events("t", hits, "", "pick")[0], [("type", None)])


class PickerTest(ViewCase):
    def test_quick_picks_and_typed_values(self):
        now = self.clock.now
        self.assertEqual([view.remind_until(p, now) - now for p in ("pick1", "pick2", "pick3", "pick4")],
                         [900, 3600, 7200, 14400])
        self.assertEqual((view.remind_until("30m", now) - now, view.remind_until("3H", now) - now), (1800, 10800))

    def test_tomorrow_is_nine_local_time(self):
        when = time.localtime(view.remind_until("pick5", self.clock.now))
        self.assertEqual((when.tm_hour, when.tm_min), (9, 0))
        self.assertGreater(view.remind_until("pick5", self.clock.now), self.clock.now)

    def test_anything_but_minutes_or_hours_is_rejected(self):
        for text in ("", "0m", "2d", "x", "1.5h", "-5m", "30", "m30", "99999m"):
            self.assertIsNone(view.remind_until(text, self.clock.now), text)


class AutoHideTest(ViewCase):
    def test_reminders_stay_open_only_on_a_reminder_row(self):
        rows = [("A", "x", False, "back at 10:00"), ("B", "y", False, "")]
        self.assertTrue(view.reminders_stay(True, rows, 0))
        self.assertFalse(view.reminders_stay(True, rows, 1))
        self.assertFalse(view.reminders_stay(False, rows, 0))

    def test_reminders_close_after_thirty_seconds_without_input(self):
        self.assertTrue(view.reminders_idle(True, 100, 130))
        self.assertFalse(view.reminders_idle(True, 100, 131))

    def test_counts_line_and_toggle_labels_add_up(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        lines = self.plain(view.layout(hidden.visible(self.items), 80, hidden.hidden_count(self.items), 0, False, None,
                                       hidden.open_count(self.items))[0])
        self.assertEqual(lines[0], "3 open · 1 on reminder")
        self.assertTrue(any("show reminders (1)" in line for line in lines))
        shown = self.plain(view.layout(hidden.visible(self.items, True), 80, 1, 0, True, None, 3)[0])
        self.assertTrue(any("hide reminders (1)" in line for line in shown))

    def test_the_view_starts_with_reminders_hidden(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        self.assertEqual(len(hidden.visible(self.items)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
