"""Compatibility with #47's owner snapshot and adjacent worker settings."""

import importlib.util
import json
import os
import tomllib
from unittest.mock import patch

from test_support import IsolatedTestCase
import watchers as w


class WaitingCompatibilityTests(IsolatedTestCase):
    def test_shared_workspace_envelope_does_not_gate_waiting_on_an_open_pr(self):
        row = dict(
            pane_id="p1",
            workspace_id="w1",
            name="worker",
            agent_status="done",
            agent_session={"value": "s1"},
            tokens={},
        )
        listing = {
            "result": {
                "workspaces": [dict(workspace_id="w1", tokens={"owner": "architect"})]
            }
        }
        with (
            patch.dict(os.environ, HERDR_ENV="1"),
            patch.object(w.time, "time", return_value=0),
            patch.object(
                w,
                "command",
                side_effect=[
                    json.dumps({"result": {"agents": [row]}}),
                    json.dumps(listing),
                ],
            ),
        ):
            raw = w.collect("workers", None)
        self.assertEqual(raw["workspaces"], listing)
        # #47 checks this field to suppress closure of a worker whose PR remains open.
        raw["pr_open"] = {"p1": True}
        state, events = w.reduce_workers(None, raw)
        self.assertEqual(events, [])
        state, events = w.reduce_workers(state, dict(raw, observed_at=601))
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].startswith("WORKER WAITING "))
        self.assertEqual(set(state["waiting"]), {"s1"})
        self.assertNotIn("idle", state)
        # #47 uses {} for a successful pass with no idle worker to look up.
        state, events = w.reduce_workers(
            state, dict(raw, observed_at=602, workspaces={})
        )
        self.assertEqual(state["waiting"], {})
        self.assertTrue(any("cleared" in event for event in events))

    def test_malformed_shared_owner_snapshot_preserves_existing_waiting_state(self):
        row = dict(
            pane_id="p1",
            workspace_id="w1",
            name="worker",
            agent_status="done",
            agent_session={"value": "s1"},
            tokens={},
        )
        raw = dict(
            result={"agents": [row]},
            observed_at=0,
            workspaces={
                "result": {
                    "workspaces": [
                        dict(workspace_id="w1", tokens={"owner": "architect"})
                    ]
                }
            },
        )
        state, _ = w.reduce_workers(None, raw)
        state, _ = w.reduce_workers(state, dict(raw, observed_at=601))
        for listing in (
            {"result": {"workspaces": [None]}},
            {"result": {"workspaces": None}},
            {"result": {}},
            {"result": {"workspaces": [dict(workspace_id="w1", tokens=[])]}},
        ):
            with self.subTest(listing=listing):
                after, events = w.reduce_workers(
                    state, dict(raw, observed_at=700, workspaces=listing)
                )
                self.assertEqual(after["waiting"], state["waiting"])
                self.assertFalse(
                    any(line.startswith("WORKER WAITING ") for line in events)
                )

    def test_waiting_settings_and_idle_close_setting_share_one_workers_table(self):
        path = w.ROOT.parents[1] / "lib/orch_settings.py"
        spec = importlib.util.spec_from_file_location("compat_settings", path)
        settings = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(settings)
        combined = list(settings.SETTINGS)
        # #47 adds workers.idle_s immediately before pr_tabs.org in the schema.
        index = next(
            i for i, setting in enumerate(combined) if setting.key == "pr_tabs.org"
        )
        combined.insert(
            index,
            settings._g(
                "workers.idle_s",
                "ORCH_WORKER_IDLE_S",
                "int",
                7200,
                "Idle close threshold",
            ),
        )
        with patch.object(settings, "SETTINGS", combined):
            example = settings.example("global")
        parsed = tomllib.loads(example)
        self.assertEqual(example.count("[workers]"), 1)
        self.assertEqual(
            parsed["workers"], dict(wait_min=600, wait_repeat_min=1800, idle_s=7200)
        )
