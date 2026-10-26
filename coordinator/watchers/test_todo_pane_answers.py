"""Interactive answers use fake Herdr only."""

import ast
import importlib.util
import json
from unittest.mock import patch

from test_support import IsolatedTestCase

import test_todo_answers as existing
import todo_answers as a
import watchers as w
from todo_test_support import EMPTY_SCREEN, TYPED_SCREEN, FakeHerdr

OWNER, TEXT = existing.OWNER, existing.TEXT

EMPTY = EMPTY_SCREEN


class PaneAnswerTests(IsolatedTestCase):
    setUp = existing.TodoAnswerTests.setUp
    view_app = existing.TodoAnswerTests.view_app

    def fake(self, app, view, *, status="idle", screen=EMPTY, rows=None, fail=False):
        self.herdr = FakeHerdr(
            self, OWNER, status=status, screen=screen, rows=rows, fail=fail, view=view
        )

    def prompts(self):
        return [call for call in self.herdr.calls if call[1:3] == ["agent", "prompt"]]

    def test_choice_sends_exact_argv_and_records_delivery(self):
        app, view = self.view_app()
        self.fake(app, view, status="done")
        app.feed("a2")
        self.assertEqual(
            [p[1:] for p in self.prompts()],
            [["agent", "prompt", "w1:p2", f"Mario (to-do panel): {TEXT} -> later"]],
        )
        self.assertEqual(app.status, "sent to Architect::Tooling")
        self.assertEqual(app.rows, [])
        record = a.records()[0]
        self.assertTrue(record["delivered_at"])
        self.assertTrue(record["pane_delivered"])

    def test_mouse_choice_sends(self):
        app, view = self.view_app()
        self.fake(app, view)
        app.feed("a")
        row, col, *_ = next(hit for hit in app.hits if hit[3] == "answer-1")
        app.feed(f"\x1b[<0;{col};{row}M")
        self.assertEqual(len(self.prompts()), 1)

    def test_typed_answer_waits_for_enter(self):
        app, view = self.view_app()
        self.fake(app, view)
        app.feed("at$(touch /tmp/never); `echo x`")
        self.assertEqual(self.prompts(), [])
        app.feed("\r")
        self.assertEqual(
            self.prompts()[0][-1],
            f"Mario (to-do panel): {TEXT} -> $(touch /tmp/never); `echo x`",
        )

    def test_sanitizes_and_caps_both_parts(self):
        app, view = self.view_app()
        self.fake(app, view)
        dirty = "\x00\x1b\x07\n hi\t " + "x" * 700
        item = "\x00\x1b\n Item\t " + "y" * 700 + " [1: yes]"
        module = w.todo_module()
        with (
            patch.object(view, "generator", return_value=module),
            patch.object(module, "answer_context") as transaction,
            patch.object(a, "submit_locked", return_value={"key": "fake"}),
            patch.object(module, "dismiss_locked"),
            patch.object(a, "write"),
        ):
            transaction.return_value.__enter__.return_value = ({}, 1, [])
            view.answer_item(OWNER, item, dirty, 1)
        expected = (
            "Mario (to-do panel): "
            + ("Item " + "y" * 700 + " [1: yes]")[:254]
            + " … "
            + ("Item " + "y" * 700 + " [1: yes]")[-255:]
            + " -> "
            + ("hi " + "x" * 700)[:512]
        )
        self.assertEqual(self.prompts()[0][-1], expected)

    def test_nonempty_composer_focuses_and_keeps_item(self):
        app, view = self.view_app()
        self.fake(app, view, screen=TYPED_SCREEN)
        app.feed("a1")
        self.assertEqual(self.prompts(), [])
        self.assertIn(["agent", "focus", "w1:p2"], [c[1:] for c in self.herdr.calls])
        self.assertEqual(app.status, "type it in the pane: now")
        self.assertEqual(a.records(), [])
        self.assertEqual(len(app.rows), 1)

    def test_unknown_screen_focuses_and_keeps_item(self):
        app, view = self.view_app()
        for screen in (
            "❯\nunrecognized dialog",
            "a screen without a composer",
        ):
            self.fake(app, view, screen=screen)
            app.feed("a1")
            self.assertEqual(app.status, "type it in the pane: now")
            self.assertEqual(self.prompts(), [])
            self.assertIn(
                ["agent", "focus", "w1:p2"], [c[1:] for c in self.herdr.calls]
            )
            self.assertEqual(len(app.rows), 1)

    def test_blocked_and_unknown_status_focus_without_send(self):
        app, view = self.view_app()
        self.fake(app, view)
        for status in ("blocked", "unknown", "waiting", None):
            self.herdr.agent["agent_status"] = status
            app.feed("a1")
            self.assertEqual(app.status, "type it in the pane: now")
            self.assertEqual(self.prompts(), [])
            self.assertEqual(len(app.rows), 1)
        self.assertEqual(
            len([c for c in self.herdr.calls if c[1:3] == ["agent", "focus"]]), 4
        )

    def test_working_focuses_and_keeps_item_without_sending(self):
        app, view = self.view_app()
        self.fake(app, view, status="working", screen=EMPTY)
        app.feed("a1")
        self.assertEqual(self.prompts(), [])
        self.assertEqual(app.status, "type it in the pane: now")
        self.assertEqual(len(app.rows), 1)
        self.assertEqual(a.records(), [])
        self.assertIn(
            ["agent", "focus", "w1:p2"], [call[1:] for call in self.herdr.calls]
        )
        self.assertFalse(
            any(call[1:3] == ["pane", "read"] for call in self.herdr.calls)
        )

    def test_worker_coordinator_and_ambiguous_targets_never_receive_input(self):
        app, view = self.view_app()
        self.fake(app, view)
        for role in ("worker", "coordinator"):
            self.herdr.agent["tokens"]["role"] = role
            self.herdr.agent["name"] = OWNER
            app.feed("a1")
            self.assertEqual(app.status, "type it in the pane: now")
            self.assertEqual(self.prompts(), [])
            self.assertEqual(a.records(), [])
        self.herdr.agent["tokens"]["role"] = "architect"
        self.herdr.rows.append(dict(self.herdr.agent, name="other", pane_id="w1:p3"))
        app.feed("a1")
        self.assertEqual(app.status, "type it in the pane: now")
        self.assertEqual(self.prompts(), [])
        self.assertFalse(any(c[1:3] == ["agent", "focus"] for c in self.herdr.calls))
        self.herdr.rows.clear()
        app.feed("a1")
        self.assertEqual(app.status, "type it in the pane: now")
        self.assertEqual(self.prompts(), [])
        self.assertEqual(a.records(), [])

    def test_failed_send_falls_back_to_native_relay(self):
        app, view = self.view_app()
        self.fake(app, view, fail=True)
        app.feed("a1")
        self.assertEqual(app.status, "relayed; the architect may ask again")
        self.assertEqual(len(self.prompts()), 1)
        self.assertFalse(a.records()[0].get("delivered_at"))
        self.assertEqual(len(a.collect(w.command)[0]), 1)
        self.assertEqual(app.rows, [])

    def test_delivered_feed_survives_without_native_double_delivery(self):
        app, view = self.view_app()
        self.fake(app, view)
        app.feed("a1")
        (self.base / "status" / f"{OWNER}.md").write_text("## Waits on the user\n")
        records, _ = a.collect(w.command)
        raw = dict(now=[], unreadable=[], read=[], scanned=True, todo_answers=records)
        after, lines = w.reduce_todo(None, raw)
        self.assertEqual(len(lines), 1)
        self.assertIn("TODO ANSWER owner=w1", lines[0])
        self.assertEqual(w.reduce_todo(after, raw)[1], [])
        spec = importlib.util.spec_from_file_location(
            "pane_inbox", w.ROOT / "notify-inbox.py"
        )
        inbox = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inbox)
        import argparse

        args = argparse.Namespace(bindings_dir=self.base, binding=None)
        stamped = ["stamp todo: " + lines[0]]
        self.assertEqual(inbox.native_messages(args, stamped, False, []), [])
        with patch.object(inbox, "send_to_socket") as send:
            self.assertTrue(
                inbox.send_line(self.base / "architect-w1.json", stamped[0])
            )
            send.assert_not_called()
        self.assertEqual(a.filter_canceled(stamped[0]), "")
        for path in [
            w.ROOT / "watchers.py",
            w.ROOT / "todo_answers.py",
            w.ROOT / "notify-inbox.py",
            w.ROOT.parent / "user-todo.py",
        ]:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                parts = (
                    node.elts
                    if isinstance(node, (ast.List, ast.Tuple))
                    else node.args
                    if isinstance(node, ast.Call)
                    else []
                )
                if parts:
                    words = [
                        part.value for part in parts if isinstance(part, ast.Constant)
                    ]
                    self.assertNotEqual(words[:2], ["agent", "prompt"], str(path))

    def test_lock_wait_revalidates_before_reserving_or_sending(self):
        import contextlib

        app, view = self.view_app()
        self.fake(app, view)
        queue_lock = a.queue_lock
        clock = [0.0]

        @contextlib.contextmanager
        def draft_while_waiting():
            with queue_lock():
                self.herdr.screen = TYPED_SCREEN
                clock[0] = 1.0
                yield

        with (
            patch.object(a, "queue_lock", draft_while_waiting),
            patch.object(view, "mono", lambda: clock[0]),
        ):
            app.feed("a1")
        self.assertEqual(self.prompts(), [])
        self.assertEqual(a.records(), [])
        self.assertEqual(len(app.rows), 1)
        self.assertEqual(app.status, "type it in the pane: now")

    def test_identity_mismatch_never_focuses_stale_pane(self):
        app, view = self.view_app()
        self.fake(app, view)
        self.herdr.rows[0] = dict(self.herdr.agent)
        self.herdr.agent["name"] = "replacement-worker"
        self.herdr.agent["tokens"] = dict(role="worker")
        app.feed("a1")
        self.assertEqual(self.prompts(), [])
        self.assertFalse(any(c[1:3] == ["agent", "focus"] for c in self.herdr.calls))
        self.assertEqual(a.records(), [])
        self.assertEqual(len(app.rows), 1)

    def test_prompt_timeout_keeps_item_without_native_relay(self):
        app, view = self.view_app()
        self.fake(app, view)
        self.herdr.timeout_prompt = True
        app.feed("a1")
        self.assertEqual(app.status, "delivery unknown; check the pane: now")
        self.assertEqual(len(app.rows), 1)
        self.assertTrue(a.records()[0]["pane_delivery_unknown_at"])
        self.assertFalse(a.records()[0].get("delivered_at"))
        self.assertEqual(a.collect(w.command), ([], []))
        self.assertEqual(
            a.filter_canceled(a.event_line(dict(a.records()[0], owner="w1"))), ""
        )
        module = w.todo_module()
        module.merge(self.base / "status", view.TODO, module.answer_state_path())
        app.refresh()
        self.assertEqual(len(app.rows), 1)
        self.herdr.timeout_prompt = False
        app.feed("a1")
        self.assertEqual(app.status, "delivery unknown; check the pane: now")
        self.assertEqual(len(self.prompts()), 1)

    def test_interrupted_dispatch_never_becomes_native_relay(self):
        app, view = self.view_app()
        self.fake(app, view)
        self.herdr.interrupt_prompt = True
        with self.assertRaises(SystemExit):
            app.feed("a1")
        self.assertEqual(a.collect(w.command), ([], []))
        self.assertTrue(a.records()[0]["pane_delivery_unknown_at"])
        self.assertFalse(a.records()[0].get("delivered_at"))
        app.refresh()
        self.assertEqual(len(app.rows), 1)

    def test_unknown_retry_reports_original_answer(self):
        app, view = self.view_app()
        self.fake(app, view)
        self.herdr.timeout_prompt = True
        app.feed("a1")
        self.herdr.timeout_prompt = False
        app.feed("a2")
        self.assertEqual(app.status, "delivery unknown; check the pane: now")
        self.assertEqual(len(self.prompts()), 1)
        self.assertEqual(a.records()[0]["answer"], "now")

    def test_signaled_prompt_process_keeps_unknown_delivery(self):
        app, view = self.view_app()
        self.fake(app, view, fail=-15)
        app.feed("a1")
        self.assertEqual(app.status, "delivery unknown; check the pane: now")
        self.assertEqual(a.collect(w.command), ([], []))
        self.assertTrue(a.records()[0]["pane_delivery_unknown_at"])
        self.assertEqual(len(app.rows), 1)

    def test_captured_real_empty_screen_and_typed_nbsp(self):
        _, view = self.view_app()
        self.assertTrue(view.empty_composer({"agent": "claude"}, EMPTY_SCREEN))
        self.assertFalse(view.empty_composer({"agent": "claude"}, TYPED_SCREEN))
        self.assertIn("❯\xa0ok", TYPED_SCREEN)
        self.assertTrue(
            view.empty_composer(
                {"agent": "claude"}, TYPED_SCREEN.replace("❯\xa0ok", "❯\xa0")
            )
        )

    def test_reads_and_focus_run_outside_both_locks(self):
        app, view = self.view_app()
        self.fake(app, view, screen=TYPED_SCREEN)
        self.herdr.check_locks = True
        app.feed("a1")
        self.assertEqual(app.status, "type it in the pane: now")
        self.assertTrue(any(c[1:3] == ["agent", "focus"] for c in self.herdr.calls))
        self.assertEqual(a.records(), [])
        self.herdr.screen = EMPTY_SCREEN
        app.feed("a1")
        self.assertEqual(len(self.prompts()), 1)

    def test_unclassified_prompt_failure_is_unknown(self):
        app, view = self.view_app()
        self.fake(app, view, fail=True)
        self.herdr.error_code = "transport_error"
        app.feed("a1")
        self.assertEqual(app.status, "delivery unknown; check the pane: now")
        self.assertEqual(a.collect(w.command), ([], []))
        self.assertEqual(len(app.rows), 1)

    def test_item_context_preserves_both_ends_within_cap(self):
        _, view = self.view_app()
        text = "Recognizable start " + "x" * 800 + " [1: now] -> Architect::Tooling"
        context = view.pane_text(text, middle=True)
        self.assertLessEqual(len(context), a.ANSWER_MAX)
        self.assertTrue(context.startswith("Recognizable start "))
        self.assertTrue(context.endswith("[1: now] -> Architect::Tooling"))
        self.assertIn(" … ", context)

    def test_identity_rule_is_shared_with_owner_discovery(self):
        _, view = self.view_app()
        adapter = a.discovery.adapter()
        self.assertIs(view.architect_labels, adapter.architect_labels)
        worker = dict(name=OWNER, workspace_id="one", tokens=dict(role="worker"))
        self.assertEqual(
            adapter.architect_owners(
                lambda *_: json.dumps(dict(result=dict(agents=[worker])))
            ),
            {},
        )

    def test_reused_pane_during_lock_wait_is_not_focused(self):
        import contextlib

        app, view = self.view_app()
        self.fake(app, view)
        queue_lock = a.queue_lock
        clock = [0.0]

        @contextlib.contextmanager
        def replace_while_waiting():
            with queue_lock():
                self.herdr.agent["name"] = "replacement-worker"
                self.herdr.agent["tokens"] = dict(role="worker")
                clock[0] = 1.0
                yield

        with (
            patch.object(a, "queue_lock", replace_while_waiting),
            patch.object(view, "mono", lambda: clock[0]),
        ):
            app.feed("a1")
        self.assertEqual(self.prompts(), [])
        self.assertFalse(any(c[1:3] == ["agent", "focus"] for c in self.herdr.calls))
        self.assertEqual(a.records(), [])
        self.assertEqual(len(app.rows), 1)

    def test_multiline_draft_cannot_end_in_a_prompt_looking_line(self):
        _, view = self.view_app()
        for tail in ("  ❯", "❯"):
            screen = EMPTY_SCREEN.replace(
                "\n❯\n", "\n❯ draft already in composer\n" + tail + "\n"
            )
            self.assertFalse(view.empty_composer({"agent": "claude"}, screen))

    def test_stale_second_view_rejects_recorded_answer_before_manual_fallback(self):
        first, first_view = self.view_app()
        second, second_view = self.view_app()
        self.fake(first, first_view)
        self.fake(second, second_view)
        second.feed("a")
        first.feed("a1")
        self.herdr.screen = TYPED_SCREEN
        second.feed("2")
        self.assertIn("already recorded", second.status)
        self.assertEqual(len(self.prompts()), 1)
        self.assertFalse(any(c[1:3] == ["agent", "focus"] for c in self.herdr.calls))
        self.assertEqual(a.records()[0]["answer"], "now")
