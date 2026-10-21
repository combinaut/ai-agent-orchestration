import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from test_support import IsolatedTestCase
import watchers as w
import feed_actions

spec = importlib.util.spec_from_file_location("user_todo", w.ROOT.parent / "user-todo.py")
todo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(todo)


class TodoTest(IsolatedTestCase):
    def sample(self, root, text):
        status = root / "status"
        status.mkdir(exist_ok=True)
        (status / "Architect.md").write_text(text)
        return status

    def test_first_run_is_silent_then_one_new_item_alerts_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = self.sample(root, "## Waits on the user\n- Approve it -> Coordinator\n")
            with patch.dict("os.environ", {"ORCH_USER_TODO_STATUS_DIR": str(status),
                                            "ORCH_USER_TODO_OUTPUT": str(root / "todo.md"),
                                            "ORCH_USER_TODO_STATE": str(root / "ages.json")}, clear=False):
                state, events = w.reduce_todo(None, w.collect_todo(None))
                self.assertEqual(events, [])
                (status / "Architect.md").write_text("## Waits on the user\n- Approve it -> Coordinator\n- Unlock -> Architect::Tooling\n")
                state, events = w.reduce_todo(state, w.collect_todo(state))
                self.assertEqual(events, ["TODO 1 new: Architect.md: Unlock -> Architect::Tooling"])
                _, events = w.reduce_todo(state, w.collect_todo(state))
                self.assertEqual(events, [])

    def test_rewrite_same_item_and_removal_are_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = self.sample(root, "## Waits on the user\n- A   task -> Coordinator\n")
            raw = {"now": todo.status_items(status)[0], "unreadable": []}
            state, _ = w.reduce_todo(None, raw)
            (status / "Architect.md").write_text("## Waits on the user\n- A task -> Coordinator\n")
            now, unreadable = todo.status_items(status)
            state, events = w.reduce_todo(state, {"now": now, "unreadable": unreadable})
            self.assertEqual(events, [])
            (status / "Architect.md").write_text("## Other\n- ignored\n")
            now, unreadable = todo.status_items(status)
            _, events = w.reduce_todo(state, {"now": now, "unreadable": unreadable})
            self.assertEqual(events, [])

    def placeholder_items(self, line):
        with tempfile.TemporaryDirectory() as tmp:
            status = self.sample(Path(tmp), "## Waits on the user\n" + line + "\n")
            return todo.status_items(status)[0]

    def test_placeholder_none_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- none"), [])

    def test_placeholder_none_yet_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- none yet"), [])

    def test_placeholder_nothing_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- nothing"), [])

    def test_placeholder_nothing_yet_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- nothing yet"), [])

    def test_placeholder_n_a_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- n/a"), [])

    def test_placeholder_dash_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- -"), [])

    def test_placeholder_paren_none_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- (none)"), [])

    def test_placeholder_paren_none_yet_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- (none yet)"), [])

    def test_placeholder_paren_nothing_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- (nothing)"), [])

    def test_placeholder_paren_nothing_yet_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- (nothing yet)"), [])

    def test_placeholder_paren_n_a_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- (n/a)"), [])

    def test_placeholder_paren_dash_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- (-)"), [])

    def test_placeholder_upper_none_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("- None"), [])

    def test_placeholder_star_none_is_not_an_item(self):
        self.assertEqual(self.placeholder_items("* (None)"), [])

    def test_real_items_that_mention_none_stay(self):
        for line in ("- None of the PRs is green -> Coordinator", "- Nothing builds -> Architect", "- Approve none -> Coordinator"):
            with self.subTest(line=line):
                self.assertEqual([i["text"] for i in self.placeholder_items(line)], [line[2:]])

    def test_placeholder_does_not_hide_a_real_item_beside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = self.sample(Path(tmp), "## Waits on the user\n- (none)\n- Approve it -> Coordinator\n")
            self.assertEqual([i["text"] for i in todo.status_items(status)[0]], ["Approve it -> Coordinator"])

    def test_two_items_batch_and_read_failure_recovery(self):
        before = {"now": {}, "unreadable": []}
        raw = {"now": [{"id": "a\0one", "source": "a", "text": "one"}, {"id": "b\0two", "source": "b", "text": "two"}],
               "unreadable": ["bad.md"]}
        state, events = w.reduce_todo(before, raw)
        self.assertEqual(events, ["TODO READFAIL bad.md", "TODO 2 new: a: one; b: two"])
        _, events = w.reduce_todo(state, {"now": raw["now"], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED bad.md"])

    def test_initial_read_failure_is_reported_without_initial_item_alerts(self):
        state, events = w.reduce_todo(None, {"now": [], "unreadable": ["missing-status"]})
        self.assertEqual(events, ["TODO READFAIL missing-status"])
        self.assertEqual(state["unreadable"], ["missing-status"])

    def test_unreadable_source_recovery_never_realerts_its_old_item(self):
        old = {"id": "B.md\0old -> x", "source": "B.md", "text": "old -> x"}
        state, _ = w.reduce_todo(None, {"now": [old], "unreadable": []})
        state, events = w.reduce_todo(state, {"now": [old], "unreadable": ["B.md"]})
        self.assertEqual(events, ["TODO READFAIL B.md"])
        _, events = w.reduce_todo(state, {"now": [old], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED B.md"])

    def test_initial_read_failure_recovery_baselines_preexisting_items(self):
        old = {"id": "B.md\0old -> x", "source": "B.md", "text": "old -> x"}
        state, _ = w.reduce_todo(None, {"now": [], "unreadable": ["B.md"]})
        _, events = w.reduce_todo(state, {"now": [old], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED B.md"])

    def test_file_recovery_does_not_baseline_an_unrelated_new_item(self):
        recovered = {"id": "B.md\0old -> x", "source": "B.md", "text": "old -> x"}
        new = {"id": "A.md\0new -> x", "source": "A.md", "text": "new -> x"}
        state, _ = w.reduce_todo(None, {"now": [], "unreadable": ["B.md"]})
        _, events = w.reduce_todo(state, {"now": [recovered, new], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED B.md", "TODO 1 new: A.md: new -> x"])

    def test_initial_directory_failure_recovery_baselines_all_items(self):
        old = {"id": "B.md\0old -> x", "source": "B.md", "text": "old -> x"}
        state, _ = w.reduce_todo(None, {"now": [], "unreadable": ["/tmp/status"]})
        _, events = w.reduce_todo(state, {"now": [old], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED /tmp/status"])

    def test_recovered_source_alerts_items_added_during_its_outage(self):
        old = {"id": "B.md\0old -> x", "source": "B.md", "text": "old -> x"}
        new = {"id": "B.md\0new -> x", "source": "B.md", "text": "new -> x"}
        state = {"now": {old["id"]: old}, "unreadable": ["B.md"]}
        _, events = w.reduce_todo(state, {"now": [old, new], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED B.md", "TODO 1 new: B.md: new -> x"])

    def test_empty_readable_source_alerts_a_new_item_after_a_read_failure(self):
        new = {"id": "A.md\0new -> x", "source": "A.md", "text": "new -> x"}
        state, _ = w.reduce_todo(None, {"now": [], "read": ["A.md"], "unreadable": []})
        state, events = w.reduce_todo(state, {"now": [], "read": [], "unreadable": ["A.md"]})
        self.assertEqual(events, ["TODO READFAIL A.md"])
        _, events = w.reduce_todo(state, {"now": [new], "read": ["A.md"], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED A.md", "TODO 1 new: A.md: new -> x"])

    def test_initial_failure_of_an_unseen_source_still_baselines(self):
        old = {"id": "A.md\0old -> x", "source": "A.md", "text": "old -> x"}
        state, _ = w.reduce_todo(None, {"now": [], "read": [], "unreadable": ["A.md"]})
        _, events = w.reduce_todo(state, {"now": [old], "read": ["A.md"], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED A.md"])

    def test_merge_reports_empty_readable_sources_as_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = self.sample(root, "## Waits on the user\n")
            data = todo.merge(status, root / "todo.md", root / "ages.json", now=10)
            self.assertEqual(data["read"], ["Architect.md"])

    def test_empty_readable_source_alerts_a_new_item_after_a_directory_outage(self):
        new = {"id": "A.md\0new -> x", "source": "A.md", "text": "new -> x"}
        state, _ = w.reduce_todo(None, {"now": [], "read": ["A.md"], "unreadable": []})
        state, events = w.reduce_todo(state, {"now": [], "read": [], "unreadable": ["/tmp/status"]})
        self.assertEqual(events, ["TODO READFAIL /tmp/status"])
        state, events = w.reduce_todo(state, {"now": [], "read": [], "unreadable": ["/tmp/status", "/tmp/status2"]})
        _, events = w.reduce_todo(state, {"now": [new], "read": ["A.md"], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED /tmp/status", "TODO READ RECOVERED /tmp/status2", "TODO 1 new: A.md: new -> x"])

    def test_initial_directory_failure_still_baselines_after_a_repeat_outage(self):
        old = {"id": "A.md\0old -> x", "source": "A.md", "text": "old -> x"}
        state, _ = w.reduce_todo(None, {"now": [], "read": [], "unreadable": ["/tmp/status"]})
        state, _ = w.reduce_todo(state, {"now": [], "read": [], "unreadable": ["/tmp/status"]})
        _, events = w.reduce_todo(state, {"now": [old], "read": ["A.md"], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED /tmp/status"])

    def test_empty_directory_scan_alerts_a_new_item_after_a_directory_outage(self):
        new = {"id": "A.md\0new -> x", "source": "A.md", "text": "new -> x"}
        state, _ = w.reduce_todo(None, {"now": [], "read": [], "scanned": True, "unreadable": []})
        state, _ = w.reduce_todo(state, {"now": [], "read": [], "scanned": False, "unreadable": ["/tmp/status"]})
        _, events = w.reduce_todo(state, {"now": [new], "read": ["A.md"], "scanned": True, "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED /tmp/status", "TODO 1 new: A.md: new -> x"])

    def test_merge_reports_an_empty_directory_as_scanned_and_a_missing_one_as_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "status").mkdir()
            data = todo.merge(root / "status", root / "todo.md", root / "ages.json", now=10)
            self.assertTrue(data["scanned"])
            data = todo.merge(root / "missing", root / "todo.md", root / "ages.json", now=20)
            self.assertFalse(data["scanned"])

    def test_read_history_is_pruned_to_sources_that_are_still_listed(self):
        state = {"now": {}, "unreadable": [], "read": ["Gone.md", "B.md"]}
        state, _ = w.reduce_todo(state, {"now": [], "read": [], "unreadable": ["/tmp/status/B.md"]})
        self.assertEqual(state["read"], ["B.md"])

    def test_directory_outage_keeps_an_unresolved_file_read_failure(self):
        state, _ = w.reduce_todo(None, {"now": [], "read": [], "unreadable": ["/tmp/status/A.md"]})
        state, events = w.reduce_todo(state, {"now": [], "read": [], "unreadable": ["/tmp/status"]})
        self.assertEqual(events, ["TODO READFAIL /tmp/status"])
        state, events = w.reduce_todo(state, {"now": [], "read": ["A.md"], "unreadable": ["/tmp/status/A.md"]})
        self.assertEqual(events, ["TODO READ RECOVERED /tmp/status"])
        _, events = w.reduce_todo(state, {"now": [], "read": ["A.md"], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED /tmp/status/A.md"])

    def test_file_failure_clears_once_a_successful_scan_no_longer_lists_it(self):
        state, _ = w.reduce_todo(None, {"now": [], "read": [], "unreadable": ["/tmp/status/A.md"]})
        _, events = w.reduce_todo(state, {"now": [], "read": [], "unreadable": []})
        self.assertEqual(events, ["TODO READ RECOVERED /tmp/status/A.md"])

    def test_removed_then_readded_identical_item_alerts_again(self):
        item = {"id": "A.md\0again -> x", "source": "A.md", "text": "again -> x"}
        state, _ = w.reduce_todo(None, {"now": [item], "unreadable": []})
        state, events = w.reduce_todo(state, {"now": [], "unreadable": []})
        self.assertEqual(events, [])
        _, events = w.reduce_todo(state, {"now": [item], "unreadable": []})
        self.assertEqual(events, ["TODO 1 new: A.md: again -> x"])

    def test_jump_resolves_latest_session_and_urls(self):
        calls = []
        rows = {"result": {"agents": [
            {"name": "fm-arch-1014a", "terminal_title_stripped": "Architect::Agent::Tooling", "revision": 1},
            {"name": "fm-arch-1014b", "terminal_title_stripped": "Architect::Agent::Tooling", "revision": 2},
        ]}}
        def run(argv, **kwargs):
            calls.append(argv)
            return type("Result", (), {"stdout": json.dumps(rows)})()
        self.assertEqual(todo.jump({"text": "Approve -> Architect::Agent::Tooling"}, run=run), "focused fm-arch-1014b")
        self.assertEqual(calls[-1][-3:], ["agent", "focus", "fm-arch-1014b"])
        self.assertEqual(todo.jump({"text": "Read -> https://example.test"}, run=run, platform="linux"), "opened https://example.test")
        self.assertEqual(calls[-1], ["xdg-open", "https://example.test"])
        self.assertEqual(todo.jump({"text": "Missing -> Architect::Gone"}, run=run), "not found: Architect::Gone")

    def test_parser_handles_headings_bullets_fences_and_prunes_removed_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = self.sample(root, "## waits on the user  \r\n* Keep -> Coordinator\r\n  - nested -> ignored\r\n```\r\n- fenced -> ignored\r\n```\r\n### Notes\r\n- ignored -> x\r\n")
            now, _ = todo.status_items(status)
            self.assertEqual([item["text"] for item in now], ["Keep -> Coordinator"])
            todo.merge(status, root / "todo.md", root / "ages.json", now=10)
            (status / "Architect.md").write_text("## Waits on the user\n")
            todo.merge(status, root / "todo.md", root / "ages.json", now=20)
            (status / "Architect.md").write_text("## Waits on the user\n- Keep -> Coordinator\n")
            data = todo.merge(status, root / "todo.md", root / "ages.json", now=30)
            self.assertEqual(data["now"][0]["age"], 0)

    def test_missing_directory_and_unreadable_source_keep_last_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing"
            self.assertEqual(todo.status_items(missing)[1], [str(missing)])
            status = self.sample(root, "## Waits on the user\n- Keep -> Coordinator\n")
            data = todo.merge(status, root / "todo.md", root / "ages.json", now=10)
            self.assertEqual([item["text"] for item in data["now"]], ["Keep -> Coordinator"])
            original_read = Path.read_text
            def unreadable_status(path, *args, **kwargs):
                if path.name == "Architect.md":
                    raise PermissionError
                return original_read(path, *args, **kwargs)
            with patch.object(Path, "read_text", unreadable_status):
                data = todo.merge(status, root / "todo.md", root / "ages.json", now=20)
            self.assertEqual([item["text"] for item in data["now"]], ["Keep -> Coordinator"])
            self.assertEqual(len(data["unreadable"]), 1)
            with patch.object(todo, "status_items", return_value=([], [str(status)])):
                data = todo.merge(status, root / "todo.md", root / "ages.json", now=30)
            self.assertEqual([item["text"] for item in data["now"]], ["Keep -> Coordinator"])

    def test_jump_reports_open_list_and_focus_failures(self):
        def failed(argv, **kwargs):
            return type("Result", (), {"stdout": "broken", "returncode": 1})()
        self.assertEqual(todo.jump({"text": "Go -> Coordinator"}, run=failed), "agent list failed")
        self.assertEqual(todo.jump({"text": "Go without destination"}, run=failed), "no target")
        def browser_failed(argv, **kwargs):
            return type("Result", (), {"stdout": "", "returncode": 1})()
        self.assertEqual(todo.jump({"text": "Go -> https://example.test"}, run=browser_failed, platform="linux"),
                         "open failed: https://example.test")
        rows = {"result": {"agents": [{"name": "coord", "terminal_title_stripped": "Coordinator", "revision": 1}]}}
        calls = []
        def focus_failed(argv, **kwargs):
            calls.append(argv)
            return type("Result", (), {"stdout": json.dumps(rows), "returncode": 0 if argv[-1] == "list" else 1})()
        self.assertEqual(todo.jump({"text": "Go -> Coordinator"}, run=focus_failed), "focus failed: coord")

    def test_rendered_url_item_ends_with_the_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            todo.render(output, [{"source": "A.md", "text": "Merge -> https://example.test/pull/1", "age": 57600, "id": "x"}])
            self.assertEqual(output.read_text(), "\n## A.md\n- [16h] Merge -> https://example.test/pull/1\n")

    def test_rendered_items_round_trip_and_read_the_legacy_age_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            item = {"source": "A.md", "text": "Merge -> https://example.test/pull/1", "age": 120, "id": "x"}
            todo.render(output, [item])
            [read] = todo.rendered_items(output)
            self.assertEqual(read["text"], item["text"])
            self.assertEqual(read["display"], "[2m] " + item["text"])
            output.write_text("## A.md\n- Merge -> https://example.test/pull/1 [2m]\n- Plain [text]\n")
            legacy, plain = todo.rendered_items(output)
            self.assertEqual(legacy["text"], item["text"])
            self.assertEqual(legacy["display"], "[2m] " + item["text"])
            self.assertEqual(plain["text"], "Plain [text]")

    def test_follow_reads_rendered_list_once_per_key_and_exits_on_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            output.write_text("## Architect.md\n- [now] Open -> Coordinator\n")
            args = type("Args", (), {"output": output, "pane": None})()
            keys = iter([b"1", b"\n", b""])
            jumped, reports = [], []
            def wait(*_): return ([0], [], [])
            todo.follow(args, wait=wait, read=lambda *_: next(keys), run_jump=lambda item: jumped.append(item) or "focused",
                        report=lambda pane, count: reports.append(count))
            self.assertEqual([item["text"] for item in jumped], ["Open -> Coordinator"])
            self.assertEqual(reports, [1])

    def test_sgr_click_maps_wrapped_rows_to_the_right_item(self):
        items = [{"source": "A", "text": "x" * 20}, {"source": "B", "text": "short -> Coordinator"}]
        rows = todo.click_rows(items, 10)
        self.assertEqual(rows, [0, 0, 0, 1, 1, 1])
        events, rest, pending = todo.mouse_events("\x1b[<0;5;2M")
        self.assertEqual(events, [(0, 2, "M")])
        self.assertEqual(rest, "")
        self.assertEqual(pending, "")
        self.assertEqual(items[rows[events[0][1] - 1]]["source"], "A")
        events, _, _ = todo.mouse_events("\x1b[<0;5;4M")
        self.assertEqual(items[rows[events[0][1] - 1]]["source"], "B")

    def test_visible_rows_do_not_scroll_clickable_items(self):
        items = [{"source": "A", "text": "x" * 8}, {"source": "B", "text": "y" * 8}]
        visible, rows = todo.visible_items(items, width=20, height=4)
        self.assertEqual([index for index, _ in visible], [0])
        self.assertEqual(rows, [0])

    def test_wide_characters_count_two_cells_when_wrapping(self):
        item = {"source": "A", "text": "你" * 8}
        self.assertEqual(todo.item_row_count(0, item, 10), 3)
        self.assertEqual(todo.click_rows([item, {"source": "B", "text": "next"}], 10), [0, 0, 0, 1])

    def test_wide_character_never_straddles_a_row_end(self):
        self.assertEqual(todo.wrapped_rows("123456789你", 10), 2)
        self.assertEqual(todo.wrapped_rows("a\u0301" * 10, 10), 1)

    def test_every_item_is_on_some_page(self):
        items = [{"source": "A", "text": f"t{n}"} for n in range(25)]
        starts = todo.page_starts(items, 80, 24)
        self.assertGreater(len(starts), 1)
        seen = []
        for start in starts:
            visible, _ = todo.visible_items(items, 80, 24, start)
            seen += [index for index, _ in visible]
        self.assertEqual(seen, list(range(25)))

    def test_item_taller_than_the_pane_still_gets_a_page(self):
        items = [{"source": "A", "text": "x" * 500}, {"source": "B", "text": "y"}]
        visible, rows = todo.visible_items(items, 20, 6)
        self.assertEqual([index for index, _ in visible], [0])
        self.assertEqual(rows, [0, 0, 0])
        self.assertEqual(todo.page_starts(items, 20, 6), [0, 1])

    def follow_paged(self, keys, lines=6):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            output.write_text("## Architect.md\n" + "".join(f"- [now] task{n} -> Coordinator\n" for n in range(1, 7)))
            args = type("Args", (), {"output": output, "pane": None})()
            keys, jumped, screen = iter(keys), [], io.StringIO()
            with redirect_stdout(screen), patch.object(todo.os, "get_terminal_size", return_value=os.terminal_size((80, lines))), \
                    patch.object(todo.os, "isatty", return_value=True), patch.object(todo.termios, "tcgetattr", return_value=[]), \
                    patch.object(todo.termios, "tcsetattr"), patch.object(todo.tty, "setcbreak"):
                todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                            run_jump=lambda item: jumped.append(item["text"]) or "failed", report=lambda *_: None)
            return jumped, screen.getvalue()

    def test_follow_pages_forward_and_back_with_keys_and_wheel(self):
        _, screen = self.follow_paged([b"n", b"p", b"\x1b[6~", b"\x1b[5~", b"\x1b[<65;1;1M", b"\x1b[<64;1;1M", b""])
        frames = screen.split("\x1b[2J\x1b[H")[1:]
        self.assertIn("page 1/", frames[0])
        marks = ["page 1/", "page 2/", "page 1/", "page 2/", "page 1/", "page 2/", "page 1/"]
        self.assertEqual([m in f for m, f in zip(marks, frames)], [True] * 7)

    def test_follow_paging_back_on_the_first_page_stays_put(self):
        _, screen = self.follow_paged([b"p", b"n", b""])
        frames = screen.split("\x1b[2J\x1b[H")[1:]
        self.assertEqual(["page 2/" in frame for frame in frames], [False, False, True])

    def test_follow_selects_by_number_and_click_on_a_later_page(self):
        jumped, _ = self.follow_paged([b"n", b"\x1b[<0;1;1M", b"5\n", b""])
        first_on_page_two = jumped[0]
        self.assertNotEqual(first_on_page_two, "task1 -> Coordinator")
        self.assertEqual(jumped[1], "task5 -> Coordinator")

    def test_follow_clips_an_item_taller_than_the_pane(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            output.write_text("## Architect.md\n- " + "x" * 500 + " -> Coordinator [now]\n")
            args = type("Args", (), {"output": output, "pane": None})()
            screen = io.StringIO()
            with redirect_stdout(screen), patch.object(todo.os, "get_terminal_size", return_value=os.terminal_size((80, 6))), \
                    patch.object(todo.os, "isatty", return_value=True), patch.object(todo.termios, "tcgetattr", return_value=[]), \
                    patch.object(todo.termios, "tcsetattr"), patch.object(todo.tty, "setcbreak"):
                todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: b"", report=lambda *_: None)
            first = screen.getvalue().split("\x1b[2J\x1b[H")[1].split("\n")[0]
            self.assertEqual(len(first), 3 * 80)

    def test_follow_footer_stays_on_one_row_with_the_page_indicator_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            output.write_text("## A.md\n" + "".join(f"- [now] t{n} -> Coordinator\n" for n in range(1, 7)))
            args = type("Args", (), {"output": output, "pane": None})()
            screen = io.StringIO()
            with redirect_stdout(screen), patch.object(todo.os, "get_terminal_size", return_value=os.terminal_size((50, 6))), \
                    patch.object(todo.os, "isatty", return_value=True), patch.object(todo.termios, "tcgetattr", return_value=[]), \
                    patch.object(todo.termios, "tcsetattr"), patch.object(todo.tty, "setcbreak"):
                todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: b"", report=lambda *_: None)
            footer = screen.getvalue().split("\x1b[2J\x1b[H")[1].split("\x1b[?1000l")[0].rstrip("\n").split("\n")[-1]
            self.assertLess(len(footer), 50)
            self.assertTrue(footer.startswith("page 1/"))

    def test_clip_rows_counts_cells_lost_at_row_ends(self):
        line = "123456789你" * 3
        clipped = todo.clip_rows(line, 10, 3)
        self.assertEqual(todo.wrapped_rows(clipped, 10), 3)
        self.assertEqual(clipped, "123456789你123456789你1234567")
        self.assertGreater(todo.wrapped_rows(line, 10), 3)
        self.assertEqual(todo.clip_rows("short", 10, 3), "short")

    def test_follow_buffers_a_fragmented_page_key(self):
        _, screen = self.follow_paged([b"\x1b[6", b"~", b""])
        self.assertIn("page 2/", screen)
        self.assertNotIn("selected 6", screen)

    def test_follow_buffers_a_fragmented_sgr_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            output.write_text("## Architect.md\n- [now] Open -> Coordinator\n")
            args = type("Args", (), {"output": output, "pane": None})()
            keys = iter([b"\x1b[<0;5;", b"1M", b""])
            jumped = []
            todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                        run_jump=lambda item: jumped.append(item) or "focused", report=lambda *_: None)
            self.assertEqual([item["text"] for item in jumped], ["Open -> Coordinator"])

    def test_follow_click_on_first_screen_row_selects_first_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            output.write_text("## Architect.md\n- [now] Open -> Coordinator\n")
            args = type("Args", (), {"output": output, "pane": None})()
            keys = iter([b"\x1b[<0;5;1M", b""])
            jumped = []
            screen = io.StringIO()
            with redirect_stdout(screen):
                todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                            run_jump=lambda item: jumped.append(item) or "focused", report=lambda *_: None)
            self.assertEqual([item["text"] for item in jumped], ["Open -> Coordinator"])
            self.assertTrue(screen.getvalue().startswith("\x1b[2J\x1b[H1. Architect.md:"))

    def test_follow_preserves_rendered_ages(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "todo.md"
            output.write_text("## Architect.md\n- [2h] Open -> Coordinator\n")
            args = type("Args", (), {"output": output, "pane": None})()
            screen = io.StringIO()
            with redirect_stdout(screen):
                todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: b"",
                            run_jump=lambda item: "focused", report=lambda *_: None)
            self.assertIn("1. Architect.md: [2h] Open -> Coordinator", screen.getvalue())

    def test_render_has_no_count_and_empty_state_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            status = self.sample(root, "## Waits on the user\n- Act -> Coordinator\n")
            todo.merge(status, output, state, now=1)
            rendered = output.read_text()
            self.assertNotIn("YOUR TO-DO", rendered)
            self.assertIn("- [now] Act -> Coordinator", rendered)
            (status / "Architect.md").write_text("## Waits on the user\n")
            todo.merge(status, output, state, now=2)
            self.assertEqual(output.read_text(), "Nothing waits on you.\n")

    def test_dismissed_item_stays_hidden_until_changed_or_reappearing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            status = self.sample(root, "## Waits on the user\n- Act -> Coordinator\n")
            raw = todo.merge(status, output, state, now=1)
            self.assertTrue(todo.dismiss_item(raw["now"][0], state))
            raw = todo.merge(status, output, state, now=2)
            self.assertEqual(raw["now"], [])
            self.assertEqual(output.read_text(), "Nothing waits on you.\n")
            (status / "Architect.md").write_text("## Waits on the user\n- Changed -> Coordinator\n")
            raw = todo.merge(status, output, state, now=3)
            self.assertEqual([item["text"] for item in raw["now"]], ["Changed -> Coordinator"])
            (status / "Architect.md").write_text("## Waits on the user\n")
            todo.merge(status, output, state, now=4)
            (status / "Architect.md").write_text("## Waits on the user\n- Act -> Coordinator\n")
            raw = todo.merge(status, output, state, now=5)
            self.assertEqual([item["text"] for item in raw["now"]], ["Act -> Coordinator"])

    def test_dismissing_last_item_updates_the_rendered_list_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            item = {"id": "Architect.md\0Act -> Coordinator", "source": "Architect.md",
                    "text": "Act -> Coordinator"}
            output.write_text("## Architect.md\n- [now] Act -> Coordinator\n")
            state.write_text(json.dumps({"items": {item["id"]: item}}))
            self.assertTrue(todo.dismiss_item(item, state, output))
            self.assertEqual(output.read_text(), "Nothing waits on you.\n")

    def test_dismissing_item_preserves_another_rendered_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            first = {"id": "Architect.md\0First -> Coordinator", "source": "Architect.md",
                     "text": "First -> Coordinator"}
            second = {"id": "Architect.md\0Second -> Coordinator", "source": "Architect.md",
                      "text": "Second -> Coordinator"}
            output.write_text("## Architect.md\n- [now] First -> Coordinator\n- [2m] Second -> Coordinator\n")
            state.write_text(json.dumps({"items": {first["id"]: first, second["id"]: second}}))
            self.assertTrue(todo.dismiss_item(first, state, output))
            self.assertEqual(output.read_text(), "\n## Architect.md\n- [2m] Second -> Coordinator\n")

    def test_follow_dismisses_only_after_a_successful_jump(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            def reset():
                output.write_text("## Architect.md\n- [now] Act -> Coordinator\n")
                state.write_text(json.dumps({"items": {"Architect.md\u0000Act -> Coordinator":
                                             {"id": "Architect.md\u0000Act -> Coordinator", "source": "Architect.md",
                                              "text": "Act -> Coordinator"}}}))
            reset()
            args = type("Args", (), {"output": output, "state": state, "pane": None})()
            keys = iter([b"1", b"\n", b""])
            todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                        run_jump=lambda _: "focused coordinator", report=lambda *_: None)
            self.assertIn("Architect.md\u0000Act -> Coordinator", json.loads(state.read_text())["dismissed"])
            reset()
            keys = iter([b"1", b"\n", b""])
            jumps = []
            todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                        run_jump=lambda item: jumps.append(item) or "focus failed: coordinator", report=lambda *_: None)
            self.assertEqual([item["text"] for item in jumps], ["Act -> Coordinator"])
            self.assertNotIn("dismissed", json.loads(state.read_text()))

    def test_follow_click_dismisses_the_clicked_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            output.write_text("## Architect.md\n- [now] Act -> Coordinator\n")
            args = type("Args", (), {"output": output, "state": state, "pane": None})()
            keys = iter([b"\x1b[<0;5;1M", b""])
            todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                        run_jump=lambda _: "focused coordinator", report=lambda *_: None)
            self.assertIn("Architect.md\u0000Act -> Coordinator", json.loads(state.read_text())["dismissed"])

    def test_follow_combined_selections_in_one_read_use_the_renumbered_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            output.write_text("## Architect.md\n- [now] One -> Coordinator\n- [now] Two -> Coordinator\n")
            args = type("Args", (), {"output": output, "state": state, "pane": None})()
            keys, jumped = iter([b"1\n1\n", b""]), []
            todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                        run_jump=lambda item: jumped.append(item["text"]) or "focused coordinator", report=lambda *_: None)
            self.assertEqual(jumped, ["One -> Coordinator", "Two -> Coordinator"])

    def test_follow_two_clicks_in_one_read_hit_the_rows_that_were_drawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            output.write_text("## Architect.md\n- [now] One -> Coordinator\n- [now] Two -> Coordinator\n")
            args = type("Args", (), {"output": output, "state": state, "pane": None})()
            keys, jumped = iter([b"\x1b[<0;5;1M\x1b[<0;5;2M", b""]), []
            todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                        run_jump=lambda item: jumped.append(item["text"]) or "focused coordinator", report=lambda *_: None)
            self.assertEqual(jumped, ["One -> Coordinator", "Two -> Coordinator"])

    def test_follow_click_then_number_in_one_read_use_the_renumbered_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, state = root / "todo.md", root / "state.json"
            output.write_text("## Architect.md\n- [now] One -> Coordinator\n- [now] Two -> Coordinator\n")
            args = type("Args", (), {"output": output, "state": state, "pane": None})()
            keys, jumped = iter([b"\x1b[<0;5;1M1\n", b""]), []
            todo.follow(args, wait=lambda *_: ([0], [], []), read=lambda *_: next(keys),
                        run_jump=lambda item: jumped.append(item["text"]) or "focused coordinator", report=lambda *_: None)
            self.assertEqual(jumped, ["One -> Coordinator", "Two -> Coordinator"])

    def test_live_agent_finds_an_architect_by_its_agent_name_alone(self):
        rows = [{"name": "Architect::Agent::Tooling", "terminal_title_stripped": "renamed", "tokens": {"role": "architect"}, "revision": 1}]
        self.assertEqual(todo.live_agent("Architect::Agent::Tooling", rows), "Architect::Agent::Tooling")

    def test_live_agent_exact_name_shortcut_is_for_workers_only(self):
        rows = [{"name": "Architect::Agent::Tooling", "terminal_title_stripped": "old", "tokens": {"role": "architect"}, "revision": 1},
                {"name": "successor", "terminal_title_stripped": "Architect::Agent::Tooling", "tokens": {"role": "architect"}, "revision": 2}]
        self.assertEqual(todo.live_agent("Architect::Agent::Tooling", rows), "successor")
        rows = [{"name": "Coordinator", "tokens": {"role": "coordinator"}, "revision": 1},
                {"name": "new-coord", "tokens": {"role": "coordinator"}, "revision": 2}]
        self.assertEqual(todo.live_agent("Coordinator", rows), "new-coord")
        self.assertEqual(todo.live_agent("worker-1", [{"name": "worker-1", "revision": 1}, {"name": "x", "tokens": {"session": "worker-1"}, "revision": 5}]), "worker-1")

    def test_empty_config_paths_fall_back_to_coordinator_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = self.sample(root, "## Waits on the user\n- Act -> Coordinator\n")
            with patch.dict(os.environ, {"ORCH_USER_TODO_STATUS_DIR": "", "ORCH_USER_TODO_OUTPUT": "",
                                        "ORCH_USER_TODO_STATE": ""}, clear=False), \
                    patch.object(w, "todo_module") as module:
                module.return_value.merge.return_value = {"now": [], "unreadable": []}
                with patch.object(w.Path, "home", return_value=root):
                    w.collect_todo(None)
            args = module.return_value.merge.call_args.args
            self.assertEqual(args[0], root / ".claude/coordinator/status")
            self.assertEqual(args[1], root / ".claude/coordinator/user-todo.md")

    def test_status_override_does_not_relocate_output_or_state_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"ORCH_USER_TODO_STATUS_DIR": "/tmp/status", "ORCH_USER_TODO_OUTPUT": "",
                                        "ORCH_USER_TODO_STATE": ""}, clear=False), \
                    patch.object(w, "todo_module") as module, patch.object(w.Path, "home", return_value=root):
                module.return_value.merge.return_value = {"now": [], "unreadable": []}
                w.collect_todo(None)
            args = module.return_value.merge.call_args.args
            self.assertEqual(args[0], Path("/tmp/status"))
            self.assertEqual(args[1], root / ".claude/coordinator/user-todo.md")
            self.assertEqual(args[2], root / ".claude/coordinator/user-todo.state.json")

    def test_cli_loads_shared_orch_config_with_home_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "orchestration.toml"
            config.write_text('[user_todo]\noutput = "~/custom-todo.md"\n[herdr]\nbin = "/tmp/herdr"\n')
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                return type("Result", (), {"returncode": 0,
                       "stdout": f"ORCH_USER_TODO_OUTPUT={root}/custom-todo.md\0ORCH_HERDR_BIN=/tmp/herdr\0".encode()})()
            with patch.dict(os.environ, {"HOME": str(root), "ORCH_CONFIG": str(config)}, clear=True):
                todo.load_orch_config(run=run)
                self.assertEqual(os.environ["ORCH_USER_TODO_OUTPUT"], str(root / "custom-todo.md"))
                self.assertEqual(os.environ["ORCH_HERDR_BIN"], "/tmp/herdr")
            self.assertIn("coord-lib.sh", calls[0][-1])

    def test_cli_rejects_missing_selected_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, ORCH_CONFIG=str(Path(tmp) / "missing.toml"))
            result = subprocess.run([sys.executable, str(w.ROOT.parent / "user-todo.py"), "--help"],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Cannot load orchestration configuration", result.stderr)

    def test_metadata_uses_configured_herdr_binary(self):
        calls = []
        with patch.dict(os.environ, {"HERDR_BIN_PATH": "/opt/herdr"}, clear=False):
            todo.report_metadata("w1:p1", 3, run=lambda argv, **kwargs: calls.append(argv))
        self.assertEqual(calls, [["/opt/herdr", "pane", "report-metadata", "w1:p1", "--source", "orchestrate", "--token", "todo=3", "--token", "tabbar=To-do 3"]])

    def test_metadata_clears_the_tabbar_token_at_zero(self):
        calls = []
        todo.report_metadata("w1:p1", 0, run=lambda argv, **kwargs: calls.append(argv))
        self.assertEqual(calls[0][-4:], ["--token", "todo=0", "--clear-token", "tabbar"])

    def test_multi_digit_selection_and_todo_readfail_policy(self):
        self.assertEqual(todo.number_selection("12", 20), 11)
        self.assertIsNone(todo.number_selection("21", 20))
        facts = __import__("alert_policy").facts({"mode": "todo", "events": ["TODO READFAIL /tmp/has space"]})
        self.assertEqual(facts[0]["key"], "todo:readfail:/tmp/has space")
        self.assertEqual(__import__("alert_policy").facts({"mode": "todo", "events": ["TODO READ RECOVERED /tmp/has space"]})[0]["prefix"],
                         "todo:readfail:/tmp/has space")
        self.assertTrue(feed_actions.is_action("2026 todo: TODO READFAIL /tmp/status (start)", feed_actions.action_patterns()))

    def test_todo_producer_health_recovery_clears_the_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            def fail(_before):
                raise OSError("cannot write")
            for _ in range(w.READ_FAILURES):
                w.cycle(base, "todo", fail)
            failure = json.loads((base / "todo.alerts").read_text().splitlines()[-1])
            recovery = json.loads(w.cycle(base, "todo", lambda _: {"now": [], "unreadable": []}))
            state = {}
            cfg = {"ratio": 1.5, "digest": 1800, "hold": 0}
            __import__("alert_policy").apply(state, [failure, recovery], 1, cfg)
            self.assertFalse(state["keys"]["todo:health:read"]["active"])


if __name__ == "__main__":
    unittest.main()
