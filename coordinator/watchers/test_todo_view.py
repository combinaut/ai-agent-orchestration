import fcntl
import importlib.util
import io
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import feed_actions
import watchers as w

spec = importlib.util.spec_from_file_location(
    "user_todo_view", w.ROOT.parent / "user-todo-view.py"
)
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
        self.assertEqual(
            self.items[0], ("Architect::Finder", "Merge it -> https://example.com/pr/1")
        )

    def test_state_is_keyed_by_owner_and_text(self):
        hidden = self.hidden()
        hidden.dismiss("Architect::Finder", "Same text")
        shown = [(owner, text) for owner, text, *_ in hidden.visible(self.items)]
        self.assertNotIn(("Architect::Finder", "Same text"), shown)
        self.assertIn(("Architect::Tooling", "Same text"), shown)

    def test_an_old_ignored_key_still_hides_its_items(self):
        (self.root / "state.json").write_text(
            json.dumps({"ignored": [view.key(*self.items[0])], "later": {}})
        )
        shown = [(owner, text) for owner, text, *_ in self.hidden().visible(self.items)]
        self.assertNotIn(self.items[0], shown)

    def test_saving_writes_dismissed_and_drops_the_old_key(self):
        (self.root / "state.json").write_text(
            json.dumps({"ignored": [view.key(*self.items[0])], "later": {}})
        )
        self.hidden().dismiss(*self.items[1])
        state = json.loads((self.root / "state.json").read_text())
        self.assertNotIn("ignored", state)
        self.assertEqual(
            sorted(state["dismissed"]),
            sorted([view.key(*self.items[0]), view.key(*self.items[1])]),
        )

    def test_dismiss_survives_a_restart_and_clears_when_the_text_changes(self):
        self.hidden().dismiss("Architect::Finder", "Same text")
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
        self.assertEqual(
            view.act(hidden, rows, self.items, "cancel", index), "reminder cancelled"
        )
        restarted = self.hidden()
        self.assertEqual(len(restarted.visible(self.items)), 3)
        self.assertEqual(restarted.hidden_count(self.items), 0)
        self.assertEqual(
            [row[2] for row in restarted.visible(self.items)], [False, False, False]
        )

    def test_the_r_key_opens_the_picker_on_an_item_without_a_reminder(self):
        rows = [("A", "x", False, ""), ("B", "y", False, "back at 10:00")]
        self.assertEqual(
            (view.key_action("remind", rows, 0), view.key_action("remind", rows, 1)),
            ("remind", "cancel"),
        )

    def test_dismiss_replaces_a_reminder_in_the_stored_state(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        hidden.dismiss(*self.items[0])
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual((len(state["dismissed"]), state["later"]), (1, {}))

    def test_dismissed_items_never_show_under_the_reminder_toggle(self):
        hidden = self.hidden()
        hidden.dismiss(*self.items[0])
        self.assertNotIn(
            self.items[0], [row[:2] for row in hidden.visible(self.items, True)]
        )

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
        first = next(
            row for row in hidden.visible(self.items) if row[:2] == self.items[0]
        )
        again = next(
            row for row in hidden.visible(self.items) if row[:2] == self.items[0]
        )
        restarted = next(
            row for row in self.hidden().visible(self.items) if row[:2] == self.items[0]
        )
        self.assertEqual((first[2], again[2], restarted[2]), (True, True, False))

    def test_counts_cover_open_and_reminder_items_but_never_dismissed_ones(self):
        hidden = self.hidden()
        hidden.dismiss(*self.items[0])
        hidden.remind(*self.items[1], self.clock.now + 60)
        self.assertEqual(
            (hidden.open_count(self.items), hidden.hidden_count(self.items)), (2, 1)
        )
        self.assertEqual([row[3] for row in hidden.visible(self.items)], [""])
        self.assertEqual(len(hidden.visible(self.items, True)), 2)
        self.clock.now += 60
        self.assertEqual(hidden.hidden_count(self.items), 0)

    def test_state_file_differs_from_the_generators(self):
        self.assertEqual(view.HIDDEN.name, "user-todo-state.json")
        self.assertNotEqual(view.HIDDEN.name, "user-todo.state.json")
        self.assertNotEqual(
            view.HIDDEN.with_suffix(".lock").name, "user-todo.state.json.lock"
        )

    def test_state_write_is_atomic_through_a_same_directory_temp_file(self):
        hidden = self.hidden()
        hidden.dismiss(*self.items[0])
        before = (self.root / "state.json").read_text()
        moves = []
        real = os.replace
        with patch.object(
            view.os,
            "replace",
            lambda src, dst: (moves.append((src, dst)), real(src, dst))[1],
        ):
            hidden.remind(*self.items[1], self.clock.now + 60)
        ((src, dst),) = moves
        self.assertEqual(Path(src).parent, Path(dst).parent)
        self.assertNotEqual(src, dst)
        with patch.object(view.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                hidden.remind(*self.items[0], self.clock.now + 60)
        self.assertEqual(
            json.loads((self.root / "state.json").read_text())["dismissed"],
            json.loads(before)["dismissed"],
        )
        self.assertEqual(
            sorted(
                path.name
                for path in self.root.iterdir()
                if path.name.startswith("state.json.")
            ),
            [],
        )


class FeedTest(ViewCase):
    def test_dismiss_writes_one_feed_line_the_action_filter_skips(self):
        self.hidden().dismiss(
            "Architect::Finder", "Merge it -> https://example.com/pr/1"
        )
        lines = (self.root / "feed.log").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(
            lines[0],
            r"^\S+ todo: TODO DISMISSED Architect::Finder: Merge it -> https://example.com/pr/1 \(start\)$",
        )
        self.assertFalse(
            feed_actions.is_action(lines[0], feed_actions.action_patterns())
        )

    def test_no_dismissed_line_is_an_action(self):
        patterns = feed_actions.action_patterns()
        quiet = [
            "2026-10-21T10:00:00+0200 todo: TODO DISMISSED Architect::Finder: x (start)",
            "2026-10-21T10:00:00+0200 todo: TODO DISMISSED (start)",
            "2026-10-21T10:00:00+0200 todo: TODO DISMISSED Architect::Finder: x (clear)",
            "2026-10-21T10:00:00+0200 todo: TODO dismissed Architect::Finder: x (start)",
            "2026-10-21T10:00:00+0200 pulls: TODO DISMISSED Architect::Finder: x (start)",
            "2026-10-21T10:00:00+0200 todo: TODO DISMISSED Two Words: x (start)",
            "2026-10-21T10:00:00+0200 todo: TODO 1 fresh: x (start)",
        ]
        self.assertEqual(
            [line for line in quiet if feed_actions.is_action(line, patterns)], []
        )

    def test_the_line_is_appended_under_the_shared_feed_lock(self):
        events = []

        class Lock:
            def __enter__(self):
                events.append("lock")

            def __exit__(self, *exc):
                events.append("unlock")

        real = os.write
        with (
            patch.object(view.feed_attach, "locked", lambda feed: Lock()),
            patch.object(
                view.os,
                "write",
                lambda fd, data: (events.append("write"), real(fd, data))[1],
            ),
        ):
            self.hidden().dismiss(*self.items[0])
        self.assertEqual(events, ["lock", "write", "unlock"])

    def test_feed_text_is_one_line(self):
        line = view.feed_line("Architect::Finder", "a\nb\tc\x1b[31m", self.clock.now)
        self.assertEqual(line.count("\n"), 1)
        self.assertTrue(line.endswith("(start)\n"))

    def test_an_unwritable_feed_keeps_the_dismiss_and_says_so(self):
        hidden = view.Hidden(
            self.root / "state.json", self.root / "missing" / "feed.log", self.clock
        )
        rows = [(*self.items[0], False, "")]
        self.assertIn(
            "feed not written", view.act(hidden, rows, self.items, "dismiss", 0)
        )
        self.assertEqual(len(self.hidden().visible(self.items)), 2)


class ClickTest(ViewCase):
    def layout(self, width, selected=0, picker=None):
        hidden = self.hidden()
        rows = hidden.visible(self.items)
        lines, hits = view.layout(rows, width, 1, selected, False, picker, len(rows))
        return lines, [h for h in hits if h[3] != "focus"]

    def test_each_button_is_hit_across_its_whole_label_and_nowhere_else(self):
        for width in (80, 40, 24):
            lines, hits = self.layout(width)
            shown = self.plain(lines)
            for line, first, last, action, index in hits:
                for column in range(first, last + 1):
                    self.assertEqual(self.click(hits, line, column), [(action, index)])
                self.assertEqual(self.click(hits, line, last + 1), [])
                label = shown[line - 1][first - 1 : last].strip().split("] ")[-1]
                self.assertTrue(
                    label in ("Dismiss", "Reminder")
                    or label.startswith(("show", "hide")),
                    label,
                )

    def test_buttons_still_match_after_the_message_wraps(self):
        self.items = view.parse("## A.md\n- " + "word " * 30 + "end\n")
        lines, hits = self.layout(30)
        by_action = {action: line for line, _f, _l, action, _i in hits}
        self.assertEqual(
            len(set(by_action[a] for a in ("dismiss", "remind"))) >= 1, True
        )
        self.assertIn("Dismiss", self.plain(lines)[by_action["dismiss"] - 1])

    def test_two_actions_and_the_toggle_are_reachable(self):
        _lines, hits = self.layout(80)
        self.assertEqual(
            {action for _l, _f, _t, action, _i in hits if _i == 0},
            {"dismiss", "remind"},
        )
        self.assertIn("show", [action for _l, _f, _t, action, _i in hits])

    def test_only_a_left_press_counts(self):
        _lines, hits = self.layout(80)
        line, first, _last, _action, _index = hits[0]
        for sequence in (
            f"\x1b[<0;{first};{line}m",
            f"\x1b[<2;{first};{line}M",
            f"\x1b[<64;{first};{line}M",
        ):
            self.assertEqual(view.input_events(sequence, hits)[0], [])

    def test_a_split_escape_sequence_waits_for_its_rest(self):
        _lines, hits = self.layout(80)
        line, first, *_ = hits[0]
        sequence = f"\x1b[<0;{first};{line}M"
        events, pending = view.input_events(sequence[:5], hits)
        self.assertEqual((events, pending), ([], sequence[:5]))
        self.assertEqual(
            view.input_events(pending + sequence[5:], hits)[0], [("dismiss", 0)]
        )

    def test_keys_map_to_the_selected_item(self):
        _lines, hits = self.layout(80)
        self.assertEqual(
            view.input_events("dr", hits)[0],
            [("dismiss", None), ("remind", None)],
        )
        self.assertEqual(
            view.input_events("\x1b[B\x1b[A", hits)[0], [("move", 1), ("move", -1)]
        )
        self.assertEqual(view.input_events("2", hits)[0], [("digit", "2")])

    def test_a_reminder_item_offers_cancel_reminder_in_the_third_button(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        rows = hidden.visible(self.items, True)
        index = next(i for i, row in enumerate(rows) if row[3])
        lines, hits = view.layout(rows, 80, 1, index, True, None, 3)
        third = next(h for h in hits if h[4] == index and h[3] == "cancel")
        self.assertIn(
            "[r] Cancel reminder",
            self.plain(lines)[third[0] - 1][third[1] - 1 : third[2]],
        )
        self.assertEqual(self.click(hits, third[0], third[1]), [("cancel", index)])
        self.assertNotIn("remind", {h[3] for h in hits if h[4] == index})
        other = next(h for h in hits if h[4] != index and h[3] == "remind")
        self.assertIn("Reminder", self.plain(lines)[other[0] - 1])

    def test_dismiss_key_hint_shows_on_the_selected_item_only(self):
        shown = self.plain(self.layout(80, 0)[0])
        self.assertEqual(sum("[d] Dismiss" in line for line in shown), 1)

    def test_picker_buttons_are_clickable_and_digits_pick_a_time(self):
        lines, hits = self.layout(80, 0, {"index": 0, "mode": "pick", "entry": ""})
        picks = {action for _l, _f, _t, action, _i in hits if action.startswith("pick")}
        self.assertEqual(picks, {"pick1", "pick2", "pick3", "pick4", "pick5"})
        self.assertEqual(view.input_events("3", hits, "pick")[0], [("pick3", None)])
        self.assertEqual(view.input_events("t", hits, "pick")[0], [("type", None)])


class NoOpenTest(ViewCase):
    def layout(self, rows=None, selected=0, showing=False):
        rows = rows or self.hidden().visible(self.items)
        return view.layout(rows, 80, 1, selected, showing, None, len(rows))

    def test_no_open_hit_is_drawn_by_default(self):
        _lines, hits = self.layout()
        self.assertNotIn("open", {hit[3] for hit in hits})

    def test_o_is_ignored(self):
        _lines, hits = self.layout()
        self.assertEqual(view.input_events("o", hits)[0], [])

    def test_the_footer_has_no_open_hint(self):
        self.assertNotIn("o open", " · ".join(view.footer_rows(200)))

    def test_a_reminder_row_has_dismiss_and_cancel_reminder_without_open(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        rows = hidden.visible(self.items, True)
        index = next(i for i, row in enumerate(rows) if row[3])
        _lines, hits = self.layout(rows, index, True)
        self.assertEqual(
            {hit[3] for hit in hits if hit[4] == index and hit[3] != "focus"},
            {"dismiss", "cancel"},
        )


class PickerTest(ViewCase):
    def test_quick_picks_and_typed_values(self):
        now = self.clock.now
        self.assertEqual(
            [
                view.remind_until(p, now) - now
                for p in ("pick1", "pick2", "pick3", "pick4")
            ],
            [900, 3600, 7200, 14400],
        )
        self.assertEqual(
            (view.remind_until("30m", now) - now, view.remind_until("3H", now) - now),
            (1800, 10800),
        )

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
        lines = self.plain(
            view.layout(
                hidden.visible(self.items),
                80,
                hidden.hidden_count(self.items),
                0,
                False,
                None,
                hidden.open_count(self.items),
            )[0]
        )
        self.assertEqual(lines[0], "3 open · 1 on reminder")
        self.assertTrue(any("show reminders (1)" in line for line in lines))
        shown = self.plain(
            view.layout(hidden.visible(self.items, True), 80, 1, 0, True, None, 3)[0]
        )
        self.assertTrue(any("hide reminders (1)" in line for line in shown))

    def test_the_toggle_and_the_s_hint_need_a_reminder(self):
        for count, expected in ((0, False), (1, True)):
            lines = self.plain(
                view.layout(
                    self.hidden().visible(self.items), 80, count, 0, False, None, 3
                )[0]
            )
            text = "\n".join(lines)
            self.assertEqual("show reminders" in text, expected)
            self.assertEqual("s show reminders" in text, expected)
            self.assertEqual(
                any(
                    "show" in h[3]
                    for h in view.layout(
                        self.hidden().visible(self.items), 80, count, 0, False, None, 3
                    )[1]
                ),
                expected,
            )
        self.assertNotIn(view.SHOW_HINT, " ".join(view.footer_rows(200, True, False)))

    def test_the_view_starts_with_reminders_hidden(self):
        hidden = self.hidden()
        hidden.remind(*self.items[0], self.clock.now + 60)
        self.assertEqual(len(hidden.visible(self.items)), 2)


class StateWriteTest(ViewCase):
    def seed_expired(self):
        first = self.hidden()
        first.remind(*self.items[0], self.clock.now + 60)
        self.clock.now += 60
        return first

    def stored(self):
        return json.loads((self.root / "state.json").read_text())

    def test_expiry_cleanup_keeps_what_another_viewer_wrote_meanwhile(self):
        self.seed_expired()
        stale = self.hidden()
        other = self.hidden()
        other.dismiss(*self.items[1])
        other.remind(*self.items[2], self.clock.now + 999)
        stale.visible(self.items)
        state = self.stored()
        self.assertEqual(state["dismissed"], [view.key(*self.items[1])])
        self.assertEqual(list(state["later"]), [view.key(*self.items[2])])

    def test_expiry_cleanup_keeps_a_reminder_another_viewer_extended(self):
        self.seed_expired()
        stale = self.hidden()
        self.hidden().remind(*self.items[0], self.clock.now + 999)
        stale.visible(self.items)
        self.assertIn(view.key(*self.items[0]), self.stored()["later"])

    def test_each_write_starts_from_the_file_not_from_the_loaded_copy(self):
        for name, write in (
            ("dismiss", lambda h: h.dismiss(*self.items[1])),
            ("remind", lambda h: h.remind(*self.items[1], self.clock.now + 60)),
            ("cancel", lambda h: h.cancel(*self.items[1])),
        ):
            with self.subTest(name):
                (self.root / "state.json").unlink(missing_ok=True)
                stale = self.hidden()
                self.hidden().dismiss(*self.items[2])
                write(stale)
                self.assertEqual(
                    self.stored()["dismissed"].count(view.key(*self.items[2])), 1, name
                )

    def test_writers_in_parallel_lose_nothing(self):
        names = [("Owner", f"item {n}") for n in range(12)]
        real = view.Hidden.save

        def slow_save(hidden):
            time.sleep(0.005)
            real(hidden)

        with patch.object(view.Hidden, "save", slow_save):
            threads = [
                threading.Thread(target=lambda n=n: self.hidden().dismiss(*n))
                for n in names
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(self.stored()["dismissed"]), len(names))

    def test_corrupt_state_reads_as_empty_and_the_next_write_repairs_it(self):
        for bad in ("{not json", "[]", '{"dismissed": 5, "later": "x"}', ""):
            with self.subTest(bad):
                (self.root / "state.json").write_text(bad)
                hidden = self.hidden()
                self.assertEqual(len(hidden.visible(self.items)), 3)
                hidden.dismiss(*self.items[0])
                self.assertEqual(self.stored()["dismissed"], [view.key(*self.items[0])])

    def test_actions_outside_the_rows_change_nothing(self):
        hidden = self.hidden()
        rows = [(*self.items[0], False, "")]
        for index in (-1, 1, 99, None):
            self.assertEqual(view.act(hidden, rows, self.items, "dismiss", index), "")
        self.assertFalse((self.root / "state.json").exists())


class Screen:
    """A fake terminal for App: collects output and answers the size."""

    def __init__(self, columns=80, lines=24):
        self.out = io.StringIO()
        self.columns, self.lines = columns, lines

    def size(self):
        return os.terminal_size((self.columns, self.lines))

    def last_frame(self):
        frame = self.out.getvalue().rsplit("\x1b[H\x1b[2J", 1)[-1]
        return ANSI.sub("", frame)


class AppCase(ViewCase):
    def setUp(self):
        super().setUp()
        self.screen = Screen()
        self.mono = Clock(100.0)
        self.loads = []
        self.current = list(self.items)
        patcher = patch.object(
            view,
            "load_items",
            lambda demo: (self.loads.append(1), list(self.current))[1],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def app(self, **kwargs):
        app = view.App(
            self.hidden(),
            False,
            self.screen.out,
            self.screen.size,
            self.mono,
            kwargs.get("jump", lambda text: "jumped"),
        )
        app.tick()
        return app


class HeadingClickTest(AppCase):
    AGENTS = {
        "result": {
            "agents": [
                {
                    "name": "finder-1",
                    "pane_id": "w1:p1",
                    "terminal_title_stripped": "Architect::Finder",
                    "tokens": {"role": "architect"},
                }
            ]
        }
    }

    def run_click(self, line):
        calls = []

        def fake(argv, **_kwargs):
            calls.append(argv[1:])
            out = json.dumps(self.AGENTS) if argv[1:3] == ["agent", "list"] else ""
            return subprocess.CompletedProcess(argv, 0, stdout=out)

        app = self.app(jump=view.jump)
        _lines, hits = view.layout(app.rows, 80, 0, 0, False, None, len(app.rows))
        row = next(h[0] for h in hits if h[3] == "focus" and h[4] == line)
        with patch("subprocess.run", fake):
            app.feed(f"\x1b[<0;5;{row}M")
        return calls, app.status

    def test_clicking_a_heading_focuses_that_architects_agent(self):
        calls, status = self.run_click(0)
        self.assertIn(["agent", "focus", "finder-1"], calls)
        self.assertEqual(status, "focused finder-1")

    def test_an_unknown_architect_focuses_nothing_and_says_so(self):
        calls, status = self.run_click(2)
        self.assertFalse([c for c in calls if c[:2] == ["agent", "focus"]])
        self.assertEqual(status, "not found: Architect::Tooling")


class HeadingWindowTest(AppCase):
    OWNERS = [
        "Architect::Alpha::Billing::Reports",
        "Architect::Beta",
        "Architect::Gamma::Ops",
    ]

    def setUp(self):
        super().setUp()
        text = "".join(
            f"## {owner}.md\n- " + "word " * 12 + f"end{n}\n"
            for n, owner in enumerate(self.OWNERS)
        )
        self.current = view.parse(text)
        self.jumps = []

    def open_app(self, columns=80, lines=24):
        self.screen.columns, self.screen.lines = columns, lines
        app = self.app(jump=self.jumps.append)
        return app

    def focus_rows(self, app):
        return [(h[0], h[1], h[2], h[4]) for h in app.hits if h[3] == "focus"]

    def click_every_heading_row(self, make):
        """Clicks each focus row of a fresh app; a click selects its item and moves the rows."""
        seen = set()
        for n in range(len(self.focus_rows(make()))):
            app = make()
            row, first, last, index = self.focus_rows(app)[n]
            shown = self.screen.last_frame().split("\r\n")[row - 1].lstrip("▌ ")
            self.assertIn(shown, f"{index + 1}. {app.rows[index][0]}")
            self.assertTrue(shown)
            self.jumps.clear()
            app.feed(f"\x1b[<0;{(first + last) // 2};{row}M")
            self.assertEqual(
                self.jumps, [f"{app.rows[index][1]} -> {app.rows[index][0]}"]
            )
            seen.add(index)
        return seen

    def test_every_visible_row_of_a_wrapped_heading_focuses_its_architect(self):
        app = self.open_app(columns=16)
        rows = [r for r, _f, _l, i in self.focus_rows(app) if i == 0]
        self.assertGreater(len(rows), 1)
        self.assertEqual(
            self.click_every_heading_row(lambda: self.open_app(columns=16)),
            {i for _r, _f, _l, i in self.focus_rows(app)},
        )

    def test_headings_still_hit_after_the_pane_is_resized(self):
        for columns, lines in ((80, 40), (20, 30), (50, 12), (30, 40)):

            def resized():
                app = self.open_app(columns=80, lines=40)
                self.screen.columns, self.screen.lines = columns, lines
                app.tick()
                return app

            visible = {i for _r, _f, _l, i in self.focus_rows(resized())}
            self.assertIn(0, visible)
            self.assertEqual(self.click_every_heading_row(resized), visible)

    def test_headings_hit_in_a_scrolled_view_with_a_selected_item(self):
        def scrolled():
            app = self.open_app(columns=30, lines=8)
            app.feed("3")
            return app

        app = scrolled()
        self.assertEqual(app.selected, 2)
        self.assertGreater(app.top, 0)
        visible = {i for _r, _f, _l, i in self.focus_rows(app)}
        self.assertIn(2, visible)
        self.assertNotIn(0, visible)
        self.assertEqual(self.click_every_heading_row(scrolled), visible)

    def test_hover_and_other_buttons_never_focus(self):
        app = self.open_app()
        row, first, _last, _index = self.focus_rows(app)[0]
        for report in (
            f"\x1b[<35;{first};{row}M",
            f"\x1b[<0;{first};{row}m",
            f"\x1b[<1;{first};{row}M",
            f"\x1b[<2;{first};{row}M",
            f"\x1b[<64;{first};{row}M",
            f"\x1b[<32;{first};{row}M",
        ):
            app.feed(report)
        self.assertEqual(self.jumps, [])


class PaneTokenTest(AppCase):
    def calls(self):
        log = Path(os.environ["TEST_TOOL_LOG"])
        return (
            [line for line in log.read_text().splitlines() if "report-metadata" in line]
            if log.exists()
            else []
        )

    def test_the_view_reports_the_todo_token_on_its_own_pane_at_start_and_on_change(
        self,
    ):
        Path(os.environ["TEST_TOOL_LOG"]).write_text("")
        with patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p7"}):
            app = view.App(
                self.hidden(), False, self.screen.out, self.screen.size, self.mono
            )
            app.tick()
            app.tick()
            self.assertEqual(
                self.calls(),
                [
                    "pane report-metadata w1:p7 --source orchestrate --token todo=3 --token tabbar=To-do 3"
                ],
            )
            app.feed("d")
        self.assertEqual(
            self.calls()[-1],
            "pane report-metadata w1:p7 --source orchestrate --token todo=2 --token tabbar=To-do 2",
        )
        self.assertEqual(len(self.calls()), 2)

    def test_reminders_still_count_as_open_in_the_token(self):
        Path(os.environ["TEST_TOOL_LOG"]).write_text("")
        with patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p7"}):
            app = view.App(
                self.hidden(), False, self.screen.out, self.screen.size, self.mono
            )
            app.hidden.remind(*self.items[0], self.clock.now + 600)
            app.tick()
        self.assertEqual(
            self.calls(),
            [
                "pane report-metadata w1:p7 --source orchestrate --token todo=3 --token tabbar=To-do 3"
            ],
        )

    def test_without_a_pane_nothing_is_reported(self):
        Path(os.environ["TEST_TOOL_LOG"]).write_text("")
        with patch.dict(os.environ, {"HERDR_PANE_ID": ""}):
            view.App(
                self.hidden(), False, self.screen.out, self.screen.size, self.mono
            ).tick()
        self.assertEqual(self.calls(), [])


class PickerIdentityTest(AppCase):
    def test_a_refresh_that_reorders_the_list_keeps_the_picker_on_its_item(self):
        app = self.app()
        app.feed("r")
        self.assertEqual(app.picker["key"], self.items[0])
        self.current = [self.items[1], self.items[0], self.items[2]]
        self.mono.now += 4
        app.tick()
        app.feed("1")
        later = json.loads((self.root / "state.json").read_text())["later"]
        self.assertEqual(list(later), [view.key(*self.items[0])])

    def test_a_typed_time_goes_to_the_item_the_picker_was_opened_for(self):
        app = self.app()
        app.feed("r")
        app.feed("t")
        self.current = self.current[1:] + self.current[:1]
        self.mono.now += 4
        app.tick()
        app.feed("30m\r")
        self.assertEqual(
            list(json.loads((self.root / "state.json").read_text())["later"]),
            [view.key(*self.items[0])],
        )

    def test_the_picker_closes_when_its_item_disappears(self):
        app = self.app()
        app.feed("r")
        self.current = self.current[1:]
        self.mono.now += 4
        app.tick()
        self.assertIsNone(app.picker)
        self.assertIn("gone", app.status)
        app.feed("1")
        self.assertFalse((self.root / "state.json").exists())

    def test_the_selection_follows_its_item_through_a_refresh(self):
        app = self.app()
        app.feed("2")
        self.current = [self.items[1], self.items[0], self.items[2]]
        self.mono.now += 4
        app.tick()
        self.assertEqual(app.selected, 0)
        self.current = [self.items[0]]
        self.mono.now += 4
        app.tick()
        self.assertEqual(app.selected, 0)


class KeyboardTest(AppCase):
    def test_enter_focuses_the_selected_architect(self):
        jumps = []
        app = self.app(jump=jumps.append)
        app.feed("\r")
        self.assertEqual(jumps, [f"{self.items[0][1]} -> {self.items[0][0]}"])

    def test_digits_select_only_existing_items_and_restart_after_a_miss(self):
        app = self.app()
        app.feed("9")
        self.assertEqual(app.selected, 0)
        app.feed("3")
        self.assertEqual(app.selected, 2)
        app.feed("12")
        self.assertEqual(app.selected, 1)

    def test_escape_closes_the_picker(self):
        app = self.app()
        app.feed("r\x1b")
        self.assertEqual(app.buf, "\x1b")
        app.lone_escape()
        self.assertIsNone(app.picker)
        self.assertEqual(app.buf, "")

    def popup_app(self, popup=True):
        app = view.App(
            self.hidden(),
            False,
            self.screen.out,
            self.screen.size,
            self.mono,
            popup=popup,
        )
        app.tick()
        return app

    def test_escape_closes_a_popup_view(self):
        app = self.popup_app()
        app.feed("\x1b")
        app.lone_escape()
        self.assertTrue(app.closing)

    def test_encoded_or_batched_escape_closes_popup_before_later_keys(self):
        for keys in ("\x1bi", "\x1b[27u", "\x1b[27;1u", "\x1b[27;1;27~"):
            with self.subTest(keys=keys):
                app = self.popup_app()
                app.feed(keys)
                self.assertTrue(app.closing)
                self.assertFalse((self.root / "state.json").exists())

    def test_picker_cancel_keeps_the_following_partial_escape(self):
        for tail, rest in (("\x1b", ""), ("\x1b[27;", "1u")):
            with self.subTest(tail=tail):
                app = self.popup_app()
                app.feed("r\x1b" + tail)
                self.assertIsNone(app.picker)
                self.assertFalse(app.closing)
                self.assertEqual(app.buf, tail)
                if rest:
                    app.feed(rest)
                else:
                    app.lone_escape()
                self.assertTrue(app.closing)

    def test_keys_after_q_in_the_same_read_are_dropped(self):
        app = self.popup_app()
        app.feed("qi")
        self.assertTrue(app.closing)
        self.assertFalse((self.root / "state.json").exists())

    def test_q_closes_a_popup_view(self):
        app = self.popup_app()
        app.feed("q")
        self.assertTrue(app.closing)

    def test_escape_with_the_picker_open_only_cancels_the_picker(self):
        app = self.popup_app()
        app.feed("r\x1b")
        app.lone_escape()
        self.assertIsNone(app.picker)
        self.assertFalse(app.closing)
        app.feed("\x1b")
        app.lone_escape()
        self.assertTrue(app.closing)

    def test_nothing_closes_a_view_that_is_not_a_popup(self):
        app = self.popup_app(popup=False)
        app.feed("q")
        self.assertFalse(app.closing)
        app.feed("\x1b")
        app.lone_escape()
        self.assertFalse(app.closing)

    def test_q_is_typed_into_the_reminder_entry(self):
        app = self.popup_app()
        app.feed("rtq")
        self.assertFalse(app.closing)
        self.assertEqual(app.picker["entry"], "q")

    def test_q_does_nothing_while_the_reminder_choices_show(self):
        app = self.popup_app()
        app.feed("rq")
        self.assertFalse(app.closing)
        self.assertIsNotNone(app.picker)

    def test_the_close_hint_shows_only_in_a_popup(self):
        self.assertIn("Esc/q close", self.screen_text(self.popup_app()))
        self.assertNotIn("Esc/q close", self.screen_text(self.popup_app(popup=False)))

    def test_the_footer_with_the_close_hint_wraps_at_its_separators(self):
        parts = (view.FOOTER + " · " + view.CLOSE_HINT).split(" · ")
        for width in range(1, 120):
            rows = view.footer_rows(width, True)
            self.assertEqual(" · ".join(rows).split(" · "), parts)
            for row in rows:
                self.assertTrue(row in parts or view.cells(row) <= width)
        self.assertEqual(len(view.footer_rows(200, True)), 1)

    def screen_text(self, app):
        return "\n".join(self.plain(self.screen.last_frame().split("\r\n")))

    def test_an_arrow_sequence_is_not_an_escape(self):
        app = self.app()
        app.feed("r")
        app.feed("\x1b[")
        self.assertEqual(app.buf, "\x1b[")
        app.feed("B")
        self.assertEqual((app.buf, app.selected), ("", 0))
        self.assertIsNotNone(app.picker)

    def test_d_dismisses_the_selected_item_and_feeds_todo_dismissed(self):
        app = self.app()
        app.feed("d")
        self.assertEqual(
            json.loads((self.root / "state.json").read_text())["dismissed"],
            [view.key(*self.items[0])],
        )
        self.assertRegex(
            (self.root / "feed.log").read_text(),
            r"todo: TODO DISMISSED Architect::Finder: Merge it -> https://example.com/pr/1 \(start\)\n$",
        )

    def test_i_no_longer_dismisses(self):
        app = self.app()
        app.feed("i")
        self.assertFalse((self.root / "state.json").exists())

    def test_keys_apply_in_the_order_typed(self):
        app = self.app()
        app.feed("d\x1b[B")
        self.assertEqual(
            json.loads((self.root / "state.json").read_text())["dismissed"],
            [view.key(*self.items[0])],
        )
        self.assertEqual(app.selected, 1)
        app.feed("d")
        self.assertEqual(
            json.loads((self.root / "state.json").read_text())["dismissed"],
            sorted([view.key(*self.items[0]), view.key(*self.items[2])]),
        )

    def test_an_invalid_typed_time_keeps_the_picker_open_with_a_hint(self):
        app = self.app()
        app.feed("rtx\r")
        self.assertEqual(app.status, view.TYPE_HINT)
        self.assertEqual(app.picker["mode"], "type")
        self.assertFalse((self.root / "state.json").exists())
        app.feed("\x7f30m\r")
        self.assertIsNone(app.picker)
        self.assertIn("will remind", app.status)

    def test_clicks_and_enter_in_one_read_keep_their_order(self):
        jumps = []

        def jump(text):
            jumps.append(text)
            return "jumped"

        app = self.app(jump=jump)
        lines = self.screen.last_frame().split("\r\n")
        row = next(n for n, line in enumerate(lines, 1) if "Dismiss" in line)
        column = lines[row - 1].index("Dismiss") + 2
        app.feed(f"\x1b[<0;{column};{row}M\r")
        self.assertEqual(app.status, "jumped")
        self.assertEqual(jumps, [f"{self.items[1][1]} -> {self.items[1][0]}"])
        self.assertEqual(
            json.loads((self.root / "state.json").read_text())["dismissed"],
            [view.key(*self.items[0])],
        )


class ClockTest(AppCase):
    def test_refresh_and_idle_follow_the_monotonic_clock_not_the_wall_clock(self):
        app = self.app()
        app.hidden.remind(*self.items[0], self.clock.now + 600)
        count = len(self.loads)
        wall = iter(range(10000, 0, -1))
        with patch.object(view.time, "time", lambda: next(wall)):
            self.mono.now += 4
            app.tick()
            self.assertEqual(len(self.loads), count + 1)
            app.feed("s")
            self.assertTrue(app.show)
            self.mono.now += 31
            app.tick()
        self.assertFalse(app.show)

    def test_s_does_nothing_without_reminders(self):
        app = self.app()
        app.feed("s")
        self.assertFalse(app.show)

    def test_the_reminder_list_closes_when_the_last_reminder_goes(self):
        app = self.app()
        app.hidden.remind(*self.items[0], self.clock.now + 600)
        app.refresh()
        app.feed("s")
        self.assertTrue(app.show)
        app.hidden.cancel(*self.items[0])
        app.refresh()
        self.assertFalse(app.show)
        self.assertNotIn("reminders (", self.screen.last_frame())

    def test_reminder_deadlines_use_the_wall_clock(self):
        app = self.app()
        app.feed("r2")
        later = json.loads((self.root / "state.json").read_text())["later"]
        self.assertEqual(list(later.values()), [self.clock.now + 3600])

    def test_default_clocks_are_the_monotonic_and_the_wall_clock(self):
        with (
            patch.object(view.time, "monotonic", lambda: 5.0),
            patch.object(view.time, "time", lambda: 7.0),
        ):
            app = view.App(self.hidden(), False, self.screen.out, self.screen.size)
            self.assertEqual(app.clock(), 5.0)
            self.assertEqual(
                view.Hidden(self.root / "x.json", self.root / "f.log").clock(), 7.0
            )

    def test_the_list_is_read_again_after_a_resize_and_after_three_seconds(self):
        app = self.app()
        count = len(self.loads)
        app.tick()
        self.assertEqual(len(self.loads), count)
        self.screen.lines = 10
        app.tick()
        self.assertEqual(len(self.loads), count + 1)
        self.mono.now += 3.5
        app.tick()
        self.assertEqual(len(self.loads), count + 2)

    def test_reminders_hide_again_when_the_selection_leaves_them(self):
        app = self.app()
        app.hidden.remind(*self.items[0], self.clock.now + 600)
        app.refresh()
        app.feed("s1")
        self.assertTrue(app.show)
        self.assertEqual(app.selected, 0)
        app.feed("\x1b[B")
        self.assertFalse(app.show)


class ViewportTest(AppCase):
    def many(self, count):
        self.current = [
            ("Architect::Long::Owner::Name", f"message number {n}")
            for n in range(count)
        ]
        return self.app()

    def hits_ok(self, app):
        rows = self.screen.last_frame().split("\r\n")
        for line, first, last, action, index in app.hits:
            self.assertLessEqual(line, self.screen.lines - 1)
            label = {"dismiss": "Dismiss", "remind": "Reminder"}.get(action)
            if label:
                self.assertIn(label, rows[line - 1][first - 1 : last])

    def test_nothing_taller_than_the_pane_is_drawn(self):
        app = self.many(12)
        frame = self.screen.last_frame().split("\x1b[")[0]
        self.assertLessEqual(len(frame.split("\r\n")), self.screen.lines - 1)
        self.hits_ok(app)

    def test_the_selected_item_scrolls_into_view_and_its_buttons_still_click(self):
        app = self.many(12)
        app.feed("\x1b[B" * 9)
        self.assertEqual(app.selected, 9)
        frame = self.screen.last_frame()
        self.assertIn("10. Architect", frame)
        self.hits_ok(app)
        hit = next(h for h in app.hits if h[3] == "dismiss" and h[4] == 9)
        app.feed(f"\x1b[<0;{hit[1]};{hit[0]}M")
        self.assertEqual(
            json.loads((self.root / "state.json").read_text())["dismissed"],
            [view.key(*self.current[9])],
        )

    def test_every_line_fits_the_terminal_width_at_any_width(self):
        self.current = [
            (
                "Architect::Frontend::Migration",
                "a long message with several words -> Architect::Frontend::Migration",
            )
        ] * 2
        for width in range(1, 70):
            lines, hits = view.layout(
                self.hidden().visible(self.current), width, 0, 0, False, None, 2
            )
            for line in lines:
                self.assertLessEqual(
                    view.cells(ANSI.sub("", line)), width, (width, line)
                )

    def test_every_button_stays_whole_and_clickable_at_narrow_widths(self):
        for width in (30, 40, 60):
            lines, hits = view.layout(
                self.hidden().visible(self.items), width, 0, 0, False, None, 3
            )
            plain = self.plain(lines)
            for line, first, last, action, index in hits:
                if action in ("dismiss", "remind"):
                    label = {"dismiss": "Dismiss", "remind": "Reminder"}[action]
                    self.assertIn(
                        label, plain[line - 1][first - 1 : last], (width, action)
                    )

    def test_a_long_item_with_an_open_picker_keeps_its_controls_visible_and_clickable(
        self,
    ):
        self.current = [
            (
                "Architect::Frontend::Migration",
                "very long message " * 120 + "-> Architect::Frontend::Migration",
            ),
            ("Architect::Other", "short"),
        ]
        wanted = {
            "dismiss",
            "remind",
            "pick1",
            "pick2",
            "pick3",
            "pick4",
            "pick5",
            "type",
        }
        labels = {
            "dismiss": "Dismiss",
            "remind": "Reminder",
            "pick1": "15m",
            "pick2": "1h",
            "pick3": "2h",
            "pick4": "4h",
            "pick5": "tomorrow 9:00",
            "type": "type",
        }
        for columns, lines in ((80, 24), (50, 12), (40, 9), (40, 7), (40, 6), (40, 5)):
            with self.subTest(size=(columns, lines)):
                self.screen.columns, self.screen.lines = columns, lines
                self.screen.out.seek(0)
                self.screen.out.truncate()
                app = self.app()
                app.feed("r")
                frame = self.screen.last_frame().split("\x1b[")[0].split("\r\n")
                self.assertLessEqual(len(frame), lines - 1)
                mine = {h[3]: h for h in app.hits if h[4] == 0}
                self.assertEqual(wanted - set(mine), set())
                for action in wanted:
                    row, first, last, _action, _index = mine[action]
                    self.assertIn(
                        labels[action],
                        frame[row - 1][first - 1 : last],
                        (action, frame),
                    )
                    self.assertEqual(self.click(app.hits, row, first), [(action, 0)])
                self.assertEqual(any("…" in line for line in frame), lines >= 9, frame)

    def test_a_tall_item_starts_at_the_top_of_the_window(self):
        lines = ["head", ""] + [f"a{n}" for n in range(8)] + [""] + ["x", ""]
        top, window, _ = view.viewport(lines, [], 0, 0, 5)
        self.assertEqual((top, window[0]), (2, "a0"))

    def test_the_footer_still_shows_when_everything_fits(self):
        self.screen.lines = 40
        self.app()
        self.assertIn("select", self.screen.last_frame())


def run_in_pty(env_extra, act):
    """Runs main() in a fresh PTY; act(pid, fd, read) drives it. Returns the output text."""
    path = str(w.ROOT.parent / "user-todo-view.py")
    script = f"""
import importlib.util, os, sys, termios
spec = importlib.util.spec_from_file_location("v", {path!r})
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
sys.argv = ["x", "--demo", *os.environ.get("VIEW_ARGS", "").split()]
if os.environ.get("VIEW_FAIL"):
    def boom(*a): raise OSError("boom")
    v.tty.setcbreak = boom
if os.environ.get("VIEW_PANE_GONE"):
    v.todo_popup.pane_exists = lambda *_: False
if os.environ.get("VIEW_CHECK_CLEANUP"):
    write = v.BoundedOut.write
    def checked(out, text):
        if text == v.LEAVE:
            assert v.todo_popup.read_record(v.todo_popup.pidfile()), "ownership cleared before terminal cleanup"
        write(out, text)
    v.BoundedOut.write = checked
code = 0
try:
    v.main()
except SystemExit as stop:
    code = stop.code
except KeyboardInterrupt:
    code = "interrupt"
except OSError:
    code = "oserror"
flags = termios.tcgetattr(0)[3]
print("RESULT", code, bool(flags & termios.ICANON), bool(flags & termios.ECHO), flush=True)
"""
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        os.environ.update(env_extra)
        os.execvp(sys.executable, [sys.executable, "-I", "-c", script])
    chunks = []

    def read(wait=0.5, until=None):
        end = time.time() + 8
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], wait)
            if not ready:
                if until is None:
                    return
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return
            chunks.append(data.decode(errors="ignore"))
            if until and until in "".join(chunks):
                return

    try:
        act(pid, fd, read)
        read(until="RESULT")
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        os.waitpid(pid, 0)
        os.close(fd)
    return "".join(chunks)


class PtyTest(IsolatedTestCase):
    def test_main_draws_takes_keys_and_leaves_the_terminal_as_it_found_it(self):
        def act(pid, fd, read):
            read(until="3 open")
            os.write(fd, b"rtx\r")
            read(until="minutes or hours")
            os.write(fd, b"\x7f30m\r")
            read(until="will remind")
            os.write(fd, b"\x03")

        out = run_in_pty({}, act)
        self.assertIn("minutes or hours", out)
        self.assertIn("will remind you at", out)
        self.assertIn("\x1b[?1000l", out)
        self.assertRegex(out, r"RESULT interrupt True True")

    def test_q_and_a_lone_escape_close_a_popup_and_restore_the_terminal(self):
        for name, keys in (("q", b"q"), ("escape", b"\x1b")):
            with self.subTest(name):

                def act(pid, fd, read):
                    read(until="Esc/q close")
                    os.write(fd, keys)

                out = run_in_pty({"VIEW_ARGS": "--popup"}, act)
                self.assertIn("\x1b[?1000l", out)
                self.assertRegex(out, r"RESULT 0 True True")

    def test_unknown_identity_keeps_popup_usable_and_owned_through_cleanup(self):
        with tempfile.TemporaryDirectory() as state:

            def act(pid, fd, read):
                read(until="Esc/q close")
                os.write(fd, b"\x1b")

            out = run_in_pty(
                {
                    "VIEW_ARGS": "--popup",
                    "COLUMNS": "80",
                    "HERDR_SOCKET_PATH": "/test/no-server.sock",
                    "HERDR_PLUGIN_STATE_DIR": state,
                    "VIEW_CHECK_CLEANUP": "1",
                    "ORCH_TODO_POPUP_TOKEN": "quick-close",
                },
                act,
            )
            receipts = list(Path(state).glob("todo-popup-*.json"))
            self.assertEqual(
                len(receipts), 1, "quick closure erased the startup receipt"
            )
            self.assertEqual(
                json.loads(receipts[0].read_text())["token"], "quick-close"
            )
        self.assertIn("Esc/q close", out)
        self.assertRegex(out, r"RESULT 0 True True")

    def test_a_missing_popup_pane_ends_the_loop(self):
        out = run_in_pty(
            {
                "VIEW_ARGS": "--popup",
                "HERDR_PANE_ID": "wtest:p99",
                "VIEW_PANE_GONE": "1",
            },
            lambda pid, fd, read: read(until="RESULT"),
        )
        self.assertRegex(out, r"RESULT 0 True True")

    def test_q_and_escape_leave_a_view_that_is_not_a_popup_running(self):
        def act(pid, fd, read):
            read(until="3 open")
            os.write(fd, b"q\x1b")
            read(wait=1)
            os.write(fd, b"\x03")

        out = run_in_pty({}, act)
        self.assertNotIn("Esc/q close", out)
        self.assertRegex(out, r"RESULT interrupt True True")

    def test_resizing_the_pane_redraws_within_the_new_height(self):
        def act(pid, fd, read):
            read(until="3 open")
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 8, 50, 0, 0))
            os.kill(pid, signal.SIGWINCH)
            read(until="\x1b[8;1H")
            os.write(fd, b"\x03")

        out = run_in_pty({}, act)
        frame = ANSI.sub("", out).rsplit("\x1b[H\x1b[2J", 1)[-1].split("\x1b[")[0]
        self.assertLessEqual(len(frame.split("\r\n")), 7)

    def test_sigterm_and_sighup_restore_the_terminal(self):
        for name in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(name):

                def act(pid, fd, read):
                    read(until="3 open")
                    os.kill(pid, name)

                out = run_in_pty({}, act)
                self.assertIn("\x1b[?1000l", out)
                self.assertRegex(out, r"RESULT 143 True True")

    def test_a_lost_terminal_ends_the_popup(self):
        path = str(w.ROOT.parent / "user-todo-view.py")
        script = (
            "import importlib.util, os, sys\n"
            f"spec = importlib.util.spec_from_file_location('v', {path!r})\n"
            "v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)\n"
            "v.stop = lambda *_: None\n"
            "sys.argv = ['x', '--demo', '--popup']\n"
            "v.main()\n"
        )
        pid, fd = pty.fork()
        if pid == 0:
            os.execvp(sys.executable, [sys.executable, "-I", "-c", script])
        status = None
        try:
            end = time.time() + 8
            seen = ""
            while "3 open" not in seen and time.time() < end:
                ready, _, _ = select.select([fd], [], [], 0.5)
                if ready:
                    seen += os.read(fd, 65536).decode(errors="ignore")
            os.close(fd)
            while time.time() < end and status is None:
                done, status_code = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = status_code
                else:
                    time.sleep(0.1)
        finally:
            if status is None:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        self.assertIsNotNone(status, "the popup outlived its terminal")

    def test_an_orphaned_popup_ends(self):
        path = str(w.ROOT.parent / "user-todo-view.py")
        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        parent_file, child_file = scratch / "parent", scratch / "child"
        script = (
            "import importlib.util, os, sys, time\n"
            f"spec = importlib.util.spec_from_file_location('v', {path!r})\n"
            "v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)\n"
            "sys.argv = ['x', '--demo', '--popup']\n"
            "if os.fork() == 0:\n"
            f"    open({str(parent_file)!r}, 'w').write(str(os.getpid()))\n"
            "    if os.fork() == 0:\n"
            f"        open({str(child_file)!r}, 'w').write(str(os.getpid()))\n"
            "        v.main()\n"
            "        os._exit(0)\n"
            "    time.sleep(600)\n"
            "time.sleep(600)\n"
        )
        pid, fd = pty.fork()
        if pid == 0:
            os.execvp(sys.executable, [sys.executable, "-I", "-c", script])

        def alive(file):
            try:
                os.kill(int(file.read_text()), 0)
            except (OSError, ValueError):
                return False
            return True

        def drain(seconds):
            end = time.time() + seconds
            while time.time() < end:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        os.read(fd, 65536)
                    except OSError:
                        return

        gone = started = False
        try:
            end = time.time() + 8
            while time.time() < end and not child_file.exists():
                drain(0.1)
            drain(1)
            started = alive(child_file)
            os.kill(int(parent_file.read_text()), signal.SIGKILL)
            end = time.time() + 8
            while time.time() < end and not gone:
                drain(0.2)
                gone = not alive(child_file)
        finally:
            for file in (child_file, parent_file):
                try:
                    os.kill(int(file.read_text()), signal.SIGKILL)
                except (OSError, ValueError):
                    pass
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            os.close(fd)
        self.assertTrue(started, "the popup never started")
        self.assertTrue(gone, "the popup outlived its parent")

    def test_output_that_never_drains_raises_instead_of_blocking(self):
        read_end, write_end = os.pipe()
        os.set_blocking(write_end, False)
        try:
            while True:
                os.write(write_end, b"x" * 65536)
        except BlockingIOError:
            pass
        out = view.BoundedOut(write_end, timeout=0.3)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            out.write("frame")
        self.assertLess(time.monotonic() - started, 2)
        os.close(read_end)
        os.close(write_end)

    def test_output_full_at_start_ends_the_view_without_a_traceback(self):
        read_end, write_end = os.pipe()
        os.set_blocking(write_end, False)
        try:
            while True:
                os.write(write_end, b"x" * 65536)
        except BlockingIOError:
            pass
        full = view.BoundedOut(write_end, timeout=0.2)
        self.addCleanup(os.close, read_end)
        self.addCleanup(os.close, write_end)
        with (
            patch.object(view, "BoundedOut", return_value=full),
            patch.object(view, "App", side_effect=AssertionError("drew a frame")),
            patch.object(view.termios, "tcgetattr", return_value=[]),
            patch.object(view.termios, "tcsetattr") as restore,
            patch.object(view.tty, "setcbreak"),
        ):
            view.main()
        restore.assert_called_once()

    def test_a_setup_failure_after_the_mode_change_restores_it(self):
        out = run_in_pty({"VIEW_FAIL": "1"}, lambda pid, fd, read: read(until="RESULT"))
        self.assertRegex(out, r"RESULT oserror True True")
        self.assertIn("\x1b[?1000l", out)


class ConfiguredOutputTest(ViewCase):
    """The popup and the pane both start the view without a settings loader in front of it."""

    def launch(self, config=None, **env):
        home = self.root / "home"
        link = home / ".claude/coordinator/user-todo-view.py"
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to(w.ROOT.parent / "user-todo-view.py")
        if config is not None:
            (home / ".claude/orchestration.toml").write_text(config)
        program = (
            "import importlib.util, sys; spec = importlib.util.spec_from_file_location('v', sys.argv[1]); "
            "v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v); print(v.TODO)"
        )
        clean = {k: v for k, v in os.environ.items() if not k.startswith("ORCH_")}
        result = subprocess.run(
            [sys.executable, "-c", program, str(link)],
            env={**clean, "HOME": str(home), **env},
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip()), home

    def test_the_output_set_only_in_the_settings_file_is_read(self):
        todo, _ = self.launch('[user_todo]\noutput = "/data/todo out.md"\n')
        self.assertEqual(todo, Path("/data/todo out.md"))

    def test_a_home_relative_output_in_the_settings_file_expands(self):
        todo, home = self.launch('[user_todo]\noutput = "~/lists/todo.md"\n')
        self.assertEqual(todo, home / "lists/todo.md")

    def test_the_exported_variable_wins_over_the_settings_file(self):
        todo, _ = self.launch(
            '[user_todo]\noutput = "/data/file.md"\n',
            ORCH_USER_TODO_OUTPUT="/data/env.md",
        )
        self.assertEqual(todo, Path("/data/env.md"))

    def test_no_setting_reads_the_default_list(self):
        todo, home = self.launch("")
        self.assertEqual(todo, home / ".claude/coordinator/user-todo.md")

    def test_an_unreadable_settings_file_reads_the_default_list(self):
        todo, home = self.launch("[user_todo\n")
        self.assertEqual(todo, home / ".claude/coordinator/user-todo.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
