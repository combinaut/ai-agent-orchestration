import argparse
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import todo_answers as a
import watchers as w

TEXT = "Deploy release [1: now] [2: later] -> Architect::Tooling"
OWNER = "Architect::Tooling"


class TodoAnswerTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = patch.dict(
            os.environ,
            HOME=str(self.base),
            ORCH_USER_TODO_STATUS_DIR=str(self.base / "status"),
            ORCH_USER_QUESTIONS_DIR=str(self.base / "questions"),
        )
        env.start()
        self.addCleanup(env.stop)
        (self.base / "status").mkdir()
        (self.base / "status" / f"{OWNER}.md").write_text(
            "## Waits on the user\n- " + TEXT + "\n"
        )
        route = patch.object(
            a.discovery, "architect_owners", return_value={OWNER: "workspace"}
        )
        route.start()
        self.addCleanup(route.stop)

    def test_choice_syntax_and_plain_fallback(self):
        self.assertEqual(a.choices(TEXT), {"1": "now", "2": "later"})
        self.assertEqual(a.choices("Merge it -> Architect::Tooling"), {})
        self.assertEqual(a.choices("Invalid [1: yes] [1: no]"), {})
        self.assertEqual(a.choices("Long [1: " + "x" * 513 + "]"), {})
        self.assertEqual(
            a.choices(" ".join(f"[{number}: choice]" for number in range(1, 11))), {}
        )

    def test_answer_length_bound(self):
        for text in ["x" * 513, "   "]:
            with self.assertRaises(ValueError):
                a.submit(OWNER, TEXT, text)
        a.submit(OWNER, TEXT, "x" * 512)

    def test_one_pending_answer_per_item(self):
        a.submit(OWNER, TEXT, "now")
        with self.assertRaisesRegex(ValueError, "already queued"):
            a.submit(OWNER, TEXT, "later")
        self.assertEqual(len(list(a.directory().glob("*.json"))), 1)

    def test_pending_answer_capacity_and_expiry(self):
        with patch.object(a, "PENDING_MAX", 1):
            record = a.submit(OWNER, TEXT, "now")
            path = self.base / "status" / f"{OWNER}.md"
            other = TEXT.replace("Deploy release", "Deploy another release")
            path.write_text(path.read_text() + "- " + other + "\n")
            with self.assertRaisesRegex(ValueError, "capacity"):
                a.submit(OWNER, other, "later")
            with patch.object(
                a.time, "time", return_value=record["at"] + a.KEEP_SECONDS + 1
            ):
                a.submit(OWNER, other, "later")
        self.assertEqual(len(list(a.directory().glob("*.json"))), 1)

    def test_stale_and_plain_items_cannot_be_answered(self):
        for text in ["No choices", TEXT + " changed"]:
            with self.assertRaises(ValueError):
                a.submit(OWNER, text, "now")
        with self.assertRaisesRegex(ValueError, "invalid architect"):
            a.submit("../other", TEXT, "now")

    def test_answer_editor_preserves_selection_until_armed(self):
        editor = a.AnswerInput(a.choices(TEXT))
        self.assertEqual(editor.feed("2"), ("answer", "later"))
        editor = a.AnswerInput(a.choices(TEXT))
        editor.feed("t")
        for char in "custom reply":
            editor.feed(char)
        self.assertEqual(editor.feed("\n"), ("answer", "custom reply"))
        self.assertEqual(editor.feed("\x1b"), ("cancel", None))

    def test_answer_editor_buffer_bound(self):
        editor = a.AnswerInput(a.choices(TEXT))
        editor.feed("t")
        for _ in range(a.ANSWER_MAX + 1):
            editor.feed("x")
        self.assertEqual(len(editor.entry), a.ANSWER_MAX)
        self.assertIn("512", editor.error)
        _, view = self.view_app()
        preview, _ = view.answer_lines(editor, 20)
        self.assertLessEqual(len(preview), 4)
        self.assertTrue(all(view.cells(line) <= 20 for line in preview))

    def test_existing_producer_routes_and_dedupes_answer(self):
        a.submit(OWNER, TEXT, "now")
        records, failed = a.collect(w.command)
        self.assertEqual(failed, [])
        raw = dict(now=[], unreadable=[], read=[], scanned=True, todo_answers=records)
        after, lines = w.reduce_todo(None, raw)
        self.assertEqual(len(lines), 1)
        self.assertIn("TODO ANSWER owner=workspace", lines[0])
        self.assertIn(" = now", lines[0])
        self.assertEqual(w.reduce_todo(after, raw)[1], [])
        spec = importlib.util.spec_from_file_location(
            "todo_answer_inbox", Path(__file__).with_name("notify-inbox.py")
        )
        inbox = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inbox)
        args = argparse.Namespace(bindings_dir=self.base, binding=None)
        routed = inbox.native_messages(args, ["stamp todo: " + lines[0]], False, [])
        self.assertEqual(routed[0][0], str(self.base / "architect-workspace.json"))
        import alert_policy

        state = dict(
            before={},
            events=[
                dict(seq=1, at=100, line=json.dumps(dict(mode="todo", events=lines)))
            ],
        )
        (self.base / "todo.json").write_text(json.dumps(state))
        cursor, late = {}, []
        history = inbox.history(self.base, cursor, ["todo"], 101, late)
        policy, _ = alert_policy.rebuild(history, 101)
        self.assertEqual(len(alert_policy.apply(policy, late, 101)), 1)

    def test_unavailable_owner_keeps_pending_answer(self):
        a.submit(OWNER, TEXT, "now")
        with patch.object(
            a.discovery,
            "architect_owners",
            side_effect=a.discovery.Unavailable("offline"),
        ):
            records, failed = a.collect(w.command)
        self.assertEqual(records, [])
        self.assertTrue(failed)
        self.assertEqual(len(list(a.directory().glob("*.json"))), 1)

    def test_answer_submission_dismisses_like_successful_jump(self):
        module = w.todo_module()
        output = self.base / "todo.md"
        state = self.base / "todo.state.json"
        module.merge(self.base / "status", output, state)
        item = module.rendered_items(output, state)[0]
        module.answer_item(item, "later", state, output)
        self.assertEqual(module.rendered_items(output, state), [])
        module.merge(self.base / "status", output, state)
        self.assertEqual(module.rendered_items(output, state), [])

    def view_app(self, popup=False):
        import io

        module = w.todo_module()
        output = self.base / "todo.md"
        state = self.base / "todo.state.json"
        module.merge(self.base / "status", output, state)
        spec = importlib.util.spec_from_file_location(
            "answer_ui", w.ROOT.parent / "user-todo-view.py"
        )
        view = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(view)
        view.TODO = output
        env = patch.dict(os.environ, ORCH_USER_TODO_STATE=str(state))
        env.start()
        self.addCleanup(env.stop)
        hidden = view.Hidden(self.base / "hidden.json", self.base / "feed.log")
        app = view.App(
            hidden,
            False,
            io.StringIO(),
            lambda: os.terminal_size((80, 24)),
            report=lambda *_: None,
            popup=popup,
        )
        app.tick()
        return app, view

    def test_buttons_view_answers_choice_without_changing_number_selection(self):
        app, _ = self.view_app()
        app.feed("1")
        self.assertEqual(a.records(), [])
        app.feed("a2")
        self.assertEqual(a.records()[0]["answer"], "later")
        self.assertEqual(app.rows, [])

    def test_buttons_view_free_text_and_escape_preserve_popup_keys(self):
        app, _ = self.view_app(popup=True)
        app.feed("a\x1b")
        app.lone_escape()
        self.assertFalse(app.closing)
        self.assertEqual(a.records(), [])
        app.feed("atq deploy after lunch\n")
        self.assertFalse(app.closing)
        self.assertEqual(a.records()[0]["answer"], "q deploy after lunch")

    def test_answer_button_mouse_hit_opens_choice_mode(self):
        app, _ = self.view_app()
        row, first, _, _, _ = next(hit for hit in app.hits if hit[3] == "answer")
        app.feed(f"\x1b[<0;{first};{row}M")
        self.assertIsNotNone(app.picker)
        app.feed("1")
        self.assertEqual(a.records()[0]["answer"], "now")

    def test_legacy_list_answers_choice_and_free_text(self):
        import contextlib
        import io

        module = w.todo_module()
        output = self.base / "todo.md"
        state = self.base / "todo.state.json"
        module.merge(self.base / "status", output, state)
        args = argparse.Namespace(output=output, state=state, pane=None)
        keys = iter([b"1atdeploy after lunch\n", b""])
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(module.os, "isatty", return_value=False),
        ):
            module.follow(
                args,
                wait=lambda *_: ([0], [], []),
                read=lambda *_: next(keys),
                run_jump=lambda *_: self.fail("answer must not jump"),
                report=lambda *_: None,
            )
        self.assertEqual(a.records()[0]["answer"], "deploy after lunch")
        self.assertEqual(module.rendered_items(output, state), [])

    def test_native_owner_lookup_rejects_ambiguous_names(self):
        with patch.dict(os.environ, ORCH_ENV="herdr"):
            adapter = a.discovery.adapter()
        data = dict(
            result=dict(
                agents=[
                    dict(name=OWNER, workspace_id="one", tokens=dict(role="architect")),
                    dict(name=OWNER, workspace_id="two", tokens=dict(role="architect")),
                ]
            )
        )
        self.assertEqual(adapter.architect_owners(lambda *_: json.dumps(data)), {})

    def test_removed_then_readded_item_returns_after_an_answer(self):
        module = w.todo_module()
        output = self.base / "todo.md"
        state = self.base / "todo.state.json"
        module.merge(self.base / "status", output, state, now=100)
        item = module.rendered_items(output, state)[0]
        module.answer_item(item, "now", state, output)
        source = self.base / "status" / f"{OWNER}.md"
        original = source.read_text()
        source.write_text("## Waits on the user\n")
        module.merge(self.base / "status", output, state, now=101)
        source.write_text(original)
        module.merge(self.base / "status", output, state, now=102)
        self.assertEqual(len(module.rendered_items(output, state)), 1)

    def test_collect_expiry_prunes_pending_answer_records(self):
        record = a.submit(OWNER, TEXT, "now")
        with patch.object(
            a.time, "time", return_value=record["at"] + a.KEEP_SECONDS + 1
        ):
            self.assertEqual(a.collect(w.command), ([], []))
        self.assertEqual(a.records(), [])

    def test_submission_lock_wait_is_bounded(self):
        import fcntl
        import time

        directory = a.directory()
        questions = self.base / "questions"
        questions.mkdir()
        with (
            (questions / ".answers.lock").open("a") as lock,
            patch.object(a, "LOCK_SECONDS", 0.02),
        ):
            fcntl.flock(lock, fcntl.LOCK_EX)
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                a.submit(OWNER, TEXT, "now")
            self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(list(directory.glob("*.json")), [])

    def test_queued_answer_recovers_dismissal_after_ui_exit(self):
        module = w.todo_module()
        output = self.base / "todo.md"
        state = self.base / "todo.state.json"
        module.merge(self.base / "status", output, state, now=100)
        generation = json.loads(state.read_text())["first_seen"][a.item_id(OWNER, TEXT)]
        a.submit(OWNER, TEXT, "now", generation)
        module.merge(self.base / "status", output, state, now=101)
        self.assertEqual(module.rendered_items(output, state), [])
