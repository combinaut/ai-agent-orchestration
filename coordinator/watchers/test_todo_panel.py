import contextlib
import importlib.util
import io
import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import todo_answers as a
import watchers as w

spec = importlib.util.spec_from_file_location(
    "todo_panel", w.ROOT.parent / "todo-panel.py"
)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)

OWNER = "Architect::Tooling"
CHOICE_TEXT = "Deploy release [1: now] [2: later] -> Architect::Tooling"
PLAIN_TEXT = "Merge it -> Architect::Tooling"


class TodoPanelTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.output = self.base / "todo.md"
        self.state = self.base / "todo.state.json"
        env = patch.dict(
            os.environ,
            HOME=str(self.base),
            ORCH_USER_TODO_STATUS_DIR=str(self.base / "status"),
            ORCH_USER_TODO_OUTPUT=str(self.output),
            ORCH_USER_TODO_STATE=str(self.state),
            ORCH_USER_QUESTIONS_DIR=str(self.base / "questions"),
        )
        env.start()
        self.addCleanup(env.stop)
        (self.base / "status").mkdir()
        self.source = self.base / "status" / f"{OWNER}.md"
        self.write_items(CHOICE_TEXT, PLAIN_TEXT)
        self.module = w.todo_module()
        self.merge(100)
        self.dir = self.base / ".claude/coordinator/todo-panel"

    def write_items(self, *texts):
        self.source.write_text(
            "## Waits on the user\n" + "".join(f"- {text}\n" for text in texts)
        )

    def merge(self, now):
        self.module.merge(self.base / "status", self.output, self.state, now=now)

    def run_panel(self, *argv):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = panel.main(list(argv))
        return code, stream.getvalue()

    def items(self):
        self.run_panel("render")
        return json.loads((self.dir / "items.json").read_text())["items"]

    def key_of(self, fragment):
        return next(item["key"] for item in self.items() if fragment in item["text"])

    def answers_file(self, *entries):
        path = self.base / "answers.json"
        path.write_text(json.dumps({"answers": list(entries)}))
        return str(path)

    def test_render_lists_the_open_items_keeps_keys_stable_and_drops_hidden_items(self):
        items = {item["text"].split(" ->")[0]: item for item in self.items()}
        self.assertEqual(items["Deploy release"]["choices"], ["now", "later"])
        self.assertEqual(items["Merge it"]["choices"], [])
        self.assertEqual(items["Deploy release"]["owner"], OWNER)
        first = self.key_of("Deploy release")
        self.assertEqual(first, self.key_of("Deploy release"))
        view = panel.view()
        hidden = view.Hidden(view.HIDDEN, view.FEED)
        with hidden.lock():
            hidden.dismissed.add(view.key(OWNER, PLAIN_TEXT))
            hidden.save()
        self.assertEqual(
            [item["text"] for item in self.items()],
            [panel.display_text(a, CHOICE_TEXT)],
        )

    def test_items_json_holds_no_owner_path_item_id_or_generation(self):
        self.items()
        raw = (self.dir / "items.json").read_text()
        page = json.loads(raw)
        self.assertEqual(
            {"key", "owner", "text", "choices", "error"}, set(page["items"][0])
        )
        self.assertNotIn("generation", raw)
        self.assertNotIn(a.item_id(OWNER, CHOICE_TEXT), raw)
        self.assertNotIn("\\u0000", raw)
        self.assertNotIn(str(self.base), raw)
        self.assertNotIn(f"{OWNER}.md", raw)

    def test_choice_answer_is_queued_and_the_item_leaves_the_next_render(self):
        key = self.key_of("Deploy release")
        code, out = self.run_panel(
            "apply", self.answers_file({"id": "a1", "key": key, "answer": "later"})
        )
        self.assertEqual(code, 0)
        self.assertIn("queued a1", out)
        records = a.records()
        self.assertEqual([record["answer"] for record in records], ["later"])
        self.assertEqual(records[0]["generation"], 100)
        self.assertNotIn(key, [item["key"] for item in self.items()])

    def test_stale_generation_is_rejected(self):
        key = self.key_of("Deploy release")
        self.write_items(PLAIN_TEXT)
        self.merge(101)
        self.write_items(CHOICE_TEXT, PLAIN_TEXT)
        self.merge(102)
        _, out = self.run_panel(
            "apply", self.answers_file({"id": "a1", "key": key, "answer": "now"})
        )
        self.assertIn("failed a1", out)
        self.assertIn("stale answer", out)
        self.assertEqual(a.records(), [])

    def test_unknown_key_is_rejected(self):
        self.items()
        _, out = self.run_panel(
            "apply", self.answers_file({"id": "a1", "key": "nope", "answer": "now"})
        )
        self.assertIn("failed a1: unknown item", out)
        self.assertEqual(a.records(), [])

    def test_blank_non_string_or_over_512_character_answers_are_rejected(self):
        key = self.key_of("Merge it")
        for answer in ("x" * 513, "   ", 7):
            _, out = self.run_panel(
                "apply",
                self.answers_file(
                    {"id": "long" + str(answer)[:3], "key": key, "answer": answer}
                ),
            )
            self.assertIn("answer must contain 1-512 characters", out)
        self.assertEqual(a.records(), [])

    def test_a_non_string_key_is_an_unknown_item_and_earlier_entries_stay_recorded(
        self,
    ):
        key = self.key_of("Deploy release")
        entries = [
            {"id": "b1", "key": key, "answer": "later"},
            {"id": "b2", "key": ["not", "a", "string"], "answer": "now"},
        ]
        _, out = self.run_panel("apply", self.answers_file(*entries))
        self.assertIn("queued b1", out)
        self.assertIn("failed b2: unknown item", out)
        applied = json.loads((self.dir / "applied.json").read_text())
        self.assertEqual(applied["b1"], "queued")
        self.assertEqual(applied["b2"], "failed")

    def test_entries_without_a_valid_id_are_skipped_and_the_batch_goes_on(self):
        key = self.key_of("Deploy release")
        entries = [
            "junk",
            {"id": 5, "key": key, "answer": "now"},
            {"id": "", "key": key, "answer": "now"},
            {"id": "x" * 65, "key": key, "answer": "now"},
            {"id": "ok", "key": key, "answer": "later"},
        ]
        _, out = self.run_panel("apply", self.answers_file(*entries))
        self.assertEqual(out.count("failed <no id>"), 4)
        self.assertIn("queued ok", out)
        self.assertEqual(len(a.records()), 1)

    def test_a_non_list_answers_value_is_an_error(self):
        path = self.base / "answers.json"
        path.write_text(json.dumps({"answers": {"id": "a1"}}))
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = self.run_panel("apply", str(path))
        self.assertEqual(code, 2)

    def test_applied_ids_beyond_the_keep_limit_are_trimmed_oldest_first(self):
        key = self.key_of("Deploy release")
        entries = [{"id": f"t{n}", "key": key, "answer": " "} for n in range(3)]
        with patch.object(panel, "APPLIED_KEEP", 2):
            self.run_panel("apply", self.answers_file(*entries))
        applied = json.loads((self.dir / "applied.json").read_text())
        self.assertEqual(list(applied), ["t1", "t2"])

    def test_render_keeps_errors_only_for_items_still_shown(self):
        key = self.key_of("Merge it")
        (self.dir / "errors.json").write_text(
            json.dumps({key: "kept", "gone-key": "dropped"})
        )
        self.run_panel("render")
        errors = json.loads((self.dir / "errors.json").read_text())
        self.assertEqual(errors, {key: "kept"})

    def test_a_queued_answer_clears_the_error_kept_for_its_item(self):
        key = self.key_of("Deploy release")
        (self.dir / "errors.json").write_text(json.dumps({key: "old reason"}))
        self.run_panel(
            "apply", self.answers_file({"id": "c1", "key": key, "answer": "now"})
        )
        errors = json.loads((self.dir / "errors.json").read_text())
        self.assertNotIn(key, errors)

    def test_render_and_apply_run_under_the_exclusive_lock(self):
        with patch.object(panel.fcntl, "flock") as flock:
            self.run_panel("render")
            self.run_panel("apply", self.answers_file())
        self.assertEqual(
            [call.args[1] for call in flock.call_args_list],
            [panel.fcntl.LOCK_EX, panel.fcntl.LOCK_EX],
        )

    def test_manifest_values_of_the_wrong_type_are_unknown_items(self):
        key = self.key_of("Deploy release")
        for bad in ({"owner": 5}, {"generation": "100"}):
            value = {"owner": OWNER, "text": CHOICE_TEXT, "generation": 100, **bad}
            (self.dir / "manifest.json").write_text(json.dumps({key: value}))
            _, out = self.run_panel(
                "apply",
                self.answers_file(
                    {"id": f"w{len(bad)}{list(bad)}", "key": key, "answer": "now"}
                ),
            )
            self.assertIn("unknown item", out)
        self.assertEqual(a.records(), [])

    def test_the_js_and_python_answer_limits_agree(self):
        page = (Path(panel.__file__).parent / "todo-panel.js").read_text()
        limit = int(re.search(r"ANSWER_MAX = (\d+)", page).group(1))
        self.assertEqual(limit, a.ANSWER_MAX)

    def test_a_manifest_generation_of_none_is_a_known_item(self):
        key = self.key_of("Deploy release")
        value = {"owner": OWNER, "text": CHOICE_TEXT, "generation": None}
        (self.dir / "manifest.json").write_text(json.dumps({key: value}))
        _, out = self.run_panel(
            "apply", self.answers_file({"id": "n1", "key": key, "answer": "now"})
        )
        self.assertNotIn("unknown item", out)
        self.assertIn("failed n1: stale answer", out)

    def test_the_same_item_listed_twice_is_rendered_once(self):
        self.write_items(CHOICE_TEXT, CHOICE_TEXT)
        self.merge(101)
        texts = [item["text"] for item in self.items()]
        self.assertEqual(texts.count(panel.display_text(a, CHOICE_TEXT)), 1)

    def test_an_item_that_returns_gets_a_new_key(self):
        first = self.key_of("Deploy release")
        self.write_items(PLAIN_TEXT)
        self.merge(101)
        self.write_items(CHOICE_TEXT, PLAIN_TEXT)
        self.merge(102)
        self.assertNotEqual(first, self.key_of("Deploy release"))

    def test_an_os_or_timeout_error_fails_that_entry_and_the_batch_goes_on(self):
        key = self.key_of("Deploy release")
        loaded = panel.view()
        generator = loaded.generator()

        def refuse(*args, **kwargs):
            raise OSError("disk full")

        generator.answer_from_view = refuse
        loaded.generator = lambda: generator
        entries = [{"id": "o1", "key": key, "answer": "now"}]
        with patch.object(panel, "view", lambda: loaded):
            code, out = self.run_panel("apply", self.answers_file(*entries))
        self.assertEqual(code, 0)
        self.assertIn("failed o1: disk full", out)

    def test_a_crash_mid_batch_keeps_earlier_entries_and_a_rerun_skips_them(self):
        key = self.key_of("Deploy release")
        path = self.answers_file(
            {"id": "b1", "key": key, "answer": "later"},
            {"id": "b2", "key": key, "answer": "now"},
        )
        loaded = panel.view()
        generator = loaded.generator()
        real = generator.answer_from_view
        calls = []

        def crash_on_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real(*args, **kwargs)

        generator.answer_from_view = crash_on_second
        loaded.generator = lambda: generator
        with patch.object(panel, "view", lambda: loaded):
            with self.assertRaises(RuntimeError):
                self.run_panel("apply", path)
        applied = json.loads((self.dir / "applied.json").read_text())
        self.assertEqual(applied, {"b1": "queued"})
        self.assertEqual(len(a.records()), 1)
        _, out = self.run_panel("apply", path)
        self.assertIn("skipped b1", out)
        self.assertNotIn("failed b1", out)
        self.assertEqual(len(a.records()), 1)

    def test_a_manifest_value_that_is_not_a_dict_is_an_unknown_item(self):
        key = self.key_of("Deploy release")
        (self.dir / "manifest.json").write_text(json.dumps({key: "corrupt"}))
        code, out = self.run_panel(
            "apply", self.answers_file({"id": "m1", "key": key, "answer": "now"})
        )
        self.assertEqual(code, 0)
        self.assertIn("failed m1: unknown item", out)
        self.assertEqual(a.records(), [])

    def test_a_manifest_dict_without_the_render_keys_is_an_unknown_item(self):
        key = self.key_of("Deploy release")
        (self.dir / "manifest.json").write_text(
            json.dumps({key: {"owner": OWNER, "generation": 100}})
        )
        code, out = self.run_panel(
            "apply", self.answers_file({"id": "m2", "key": key, "answer": "now"})
        )
        self.assertEqual(code, 0)
        self.assertIn("failed m2: unknown item", out)
        self.assertEqual(a.records(), [])

    def test_choice_answer_must_be_one_of_the_choices(self):
        key = self.key_of("Deploy release")
        _, out = self.run_panel(
            "apply", self.answers_file({"id": "a1", "key": key, "answer": "never"})
        )
        self.assertIn("not one of the choices", out)
        self.assertEqual(a.records(), [])

    def test_free_text_fails_before_187_and_the_reason_stays_on_the_item(self):
        key = self.key_of("Merge it")
        _, out = self.run_panel(
            "apply", self.answers_file({"id": "a1", "key": key, "answer": "yes"})
        )
        self.assertIn("failed a1: this item has no valid choices", out)
        self.assertEqual(a.records(), [])
        item = next(item for item in self.items() if item["key"] == key)
        self.assertEqual(item["error"], "this item has no valid choices")

    def test_a_file_without_answers_is_an_error(self):
        path = self.base / "answers.json"
        path.write_text("not json")
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = self.run_panel("apply", str(path))
        self.assertEqual(code, 2)

    def test_render_stages_the_page_files_for_publishing(self):
        stage = self.base / "stage"
        self.run_panel("render", "--stage", str(stage))
        self.assertEqual(
            sorted(path.name for path in stage.iterdir()),
            ["items.json", "todo-panel.html", "todo-panel.js"],
        )
        self.assertEqual(
            (stage / "items.json").read_text(), (self.dir / "items.json").read_text()
        )
