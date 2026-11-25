import io
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

import worker_signals as signals


class SubagentWakeTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.store = self.base / ".codex"
        self.rollouts = self.store / "sessions"
        self.rollouts.mkdir(parents=True)
        home = patch.object(Path, "home", return_value=self.base)
        home.start()
        self.addCleanup(home.stop)
        self.db = self.store / "state_5.sqlite"
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT,
                    updated_at INTEGER, archived INTEGER DEFAULT 0);
                CREATE TABLE thread_spawn_edges (parent_thread_id TEXT,
                    child_thread_id TEXT PRIMARY KEY, status TEXT);
            """)
        self.lifecycle("parent", "task_complete", 100)
        self.lifecycle("child", "task_complete", 101, parent="parent")
        self.work = dict(
            workspaces=[dict(id="worker", owner="owner")],
            panes=[dict(pane="pane", workspace="worker", agent="codex", status="idle")],
        )
        discovery = patch.object(signals.discovery, "open_work", return_value=self.work)
        discovery.start()
        self.addCleanup(discovery.stop)
        queue = patch.object(signals, "queue_wakeup")
        self.queue = queue.start()
        self.addCleanup(queue.stop)

    def lifecycle(self, identifier, kind, when, parent=None):
        path = self.rollouts / f"rollout-{identifier}.jsonl"
        source = (
            {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
            if parent
            else "cli"
        )
        rows = [
            dict(type="session_meta", payload=dict(id=identifier, source=source)),
            dict(
                type="event_msg",
                timestamp=datetime.fromtimestamp(when, timezone.utc).isoformat(),
                payload=dict(type=kind, turn_id=f"turn-{when}", completed_at=when),
            ),
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT OR REPLACE INTO threads VALUES (?, ?, ?, 0)",
                (identifier, str(path), when),
            )
            if parent:
                db.execute(
                    "INSERT OR REPLACE INTO thread_spawn_edges VALUES (?, ?, 'open')",
                    (parent, identifier),
                )

    def command(self, *args, **kwargs):
        self.assertEqual(args[1:3], ("agent", "get"))
        return json.dumps(
            dict(
                result=dict(
                    agent=dict(
                        pane_id="pane",
                        workspace_id="worker",
                        agent_status=self.work["panes"][0]["status"],
                        agent_session=dict(agent="codex", value="parent"),
                    )
                )
            )
        )

    def poll(self):
        return signals.collect_live(self.command, 200)

    def test_codex_store_shape_wakes_idle_parent_once(self):
        self.poll()
        self.poll()
        self.queue.assert_called_once()
        session, message = self.queue.call_args.args
        self.assertEqual(session, "parent")
        self.assertIn("child", message)
        self.assertIn("subagent", message.lower())

    def test_active_parent_turn_is_not_woken_even_with_stale_idle_pane_status(self):
        self.lifecycle("parent", "task_started", 100)
        self.poll()
        self.queue.assert_not_called()

    def test_child_completion_within_same_second_is_observed(self):
        self.lifecycle("parent", "task_complete", 100.1)
        self.lifecycle("child", "task_complete", 100.2, parent="parent")
        self.poll()
        self.queue.assert_called_once()

    def test_running_child_is_not_completion(self):
        self.lifecycle("child", "task_started", 101, parent="parent")
        self.poll()
        self.queue.assert_not_called()

    def test_working_parent_does_not_wake(self):
        self.work["panes"][0]["status"] = "working"
        self.poll()
        self.queue.assert_not_called()

    def test_completed_child_from_before_parent_idle_is_not_replayed(self):
        self.lifecycle("parent", "task_complete", 102)
        self.poll()
        self.queue.assert_not_called()

    def test_unknown_schema_fails_safe_and_logs_once(self):
        with sqlite3.connect(self.db) as db:
            db.execute("DROP TABLE thread_spawn_edges")
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.poll()
            self.poll()
        self.queue.assert_not_called()
        self.assertEqual(stderr.getvalue().count("subagent wake unavailable"), 1)

    def test_unknown_rollout_fails_safe_and_logs_once(self):
        self.lifecycle("child", "task_unknown", 101, parent="parent")
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.poll()
            self.poll()
        self.queue.assert_not_called()
        self.assertEqual(stderr.getvalue().count("subagent wake unavailable"), 1)

    def test_foreign_child_rollout_is_rejected(self):
        self.lifecycle("child", "task_complete", 101, parent="foreign")
        with patch("sys.stderr", new_callable=io.StringIO):
            self.poll()
        self.queue.assert_not_called()

    def test_native_session_change_before_send_does_not_wake(self):
        replies = [self.command("herdr", "agent", "get") for _ in range(2)]
        changed = json.loads(replies[1])
        changed["result"]["agent"]["agent_session"]["value"] = "replacement"
        replies[1] = json.dumps(changed)
        with patch.object(self, "command", side_effect=replies):
            self.poll()
        self.queue.assert_not_called()

    def test_failed_native_send_is_retried_without_losing_completion(self):
        self.queue.side_effect = [OSError("offline"), None]
        with patch("sys.stderr", new_callable=io.StringIO):
            self.poll()
            self.poll()
            self.poll()
        self.assertEqual(self.queue.call_count, 2)

    def test_receipts_and_warnings_are_pruned_with_closed_session(self):
        self.poll()
        self.lifecycle("child", "task_unknown", 102, parent="parent")
        with patch("sys.stderr", new_callable=io.StringIO):
            self.poll()
        state = signals.directory() / "subagent-wakes"
        self.assertTrue(list(state.glob("*.json")))
        self.assertTrue(list(state.glob("*.warning")))
        self.work["panes"] = []
        self.poll()
        self.assertEqual(list(state.iterdir()), [])
