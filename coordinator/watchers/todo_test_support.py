"""Fake Herdr with the CLI's JSON agent responses and raw pane screen output."""

import fcntl
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

EMPTY_SCREEN = (Path(__file__).parent / "fixtures/claude-empty-screen.txt").read_text()
TYPED_SCREEN = (Path(__file__).parent / "fixtures/claude-typed-screen.txt").read_text()


class FakeHerdr:
    def __init__(
        self, case, owner, *, status="idle", screen=EMPTY_SCREEN, rows=None, fail=False
    ):
        self.case, self.screen = case, screen
        self.calls, self.prompt_timeouts = [], []
        self.timeout_prompt = self.interrupt_prompt = False
        self.check_locks = False
        self.fail = int(fail)
        self.error_code = "agent_blocked" if fail else None
        self.agent = dict(
            name="tooling",
            terminal_title_stripped=owner,
            workspace_id="w1",
            pane_id="w1:p2",
            agent="claude",
            agent_status=status,
            tokens=dict(role="architect"),
            terminal_id="fake-terminal",
            tab_id="w1:t1",
            focused=False,
            revision=1,
        )
        self.rows = [self.agent] if rows is None else rows
        stub = patch.object(subprocess, "run", self.run)
        stub.start()
        case.addCleanup(stub.stop)

    def assert_unlocked(self):
        for path in (
            self.case.base / "questions/.answers.lock",
            self.case.base / "todo.state.json.lock",
        ):
            if path.exists():
                with path.open("a") as stream:
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        self.case.fail(f"Herdr read/focus holds lock: {path.name}")

    def run(self, argv, **kwargs):
        self.case.assertIsInstance(argv, list)
        self.case.assertFalse(kwargs.get("shell"))
        self.calls.append(argv)
        command = argv[1:3]
        if self.check_locks and command != ["agent", "prompt"]:
            self.assert_unlocked()
        if command == ["agent", "list"]:
            data = dict(type="agent_list", agents=self.rows)
        elif command == ["agent", "get"]:
            data = dict(type="agent_info", agent=self.agent)
        elif command == ["pane", "read"]:
            self.case.assertEqual(
                argv[3:], ["w1:p2", "--source", "visible", "--format", "text"]
            )
            return subprocess.CompletedProcess(argv, 0, self.screen, "")
        elif command == ["agent", "prompt"]:
            self.prompt_timeouts.append(kwargs.get("timeout"))
            if self.interrupt_prompt:
                raise SystemExit(143)
            if self.timeout_prompt:
                raise subprocess.TimeoutExpired(argv, 5)
            error = (
                json.dumps(
                    dict(
                        id="fake",
                        error=dict(code=self.error_code, message="fake failure"),
                    )
                )
                if self.error_code
                else "unclassified failure"
            )
            return subprocess.CompletedProcess(
                argv,
                self.fail,
                ""
                if self.fail
                else json.dumps(
                    dict(
                        id="fake", result=dict(type="agent_prompted", agent=self.agent)
                    )
                ),
                error if self.fail else "",
            )
        elif command == ["agent", "focus"]:
            data = dict(type="ok")
        else:
            self.case.fail(f"Unexpected fake Herdr call: {argv}")
        return subprocess.CompletedProcess(
            argv, 0, json.dumps(dict(id="fake", result=data)), ""
        )
