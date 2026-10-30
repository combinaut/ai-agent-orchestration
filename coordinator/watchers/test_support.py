"""All Python test cases isolate service managers and Herdr from the host."""

import atexit
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOLS = {"launchctl", "systemctl", "herdr"}
if not os.environ.get("AO_TEST_SANDBOX"):
    raise RuntimeError("Run tests through tests/run.sh")
TEST_TOOLS_DIR = Path(tempfile.mkdtemp(prefix="watcher-test-tools-"))
TEST_TOOL_LOG = TEST_TOOLS_DIR / "calls"
for tool in TOOLS:
    stub = TEST_TOOLS_DIR / tool
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$TEST_TOOL_LOG"\n')
    stub.chmod(0o755)
for key in tuple(os.environ):
    if key.startswith(("HERDR_", "ORCH_ALERT_")):
        os.environ.pop(key)
os.environ.update(
    {
        "PATH": f"{TEST_TOOLS_DIR}:{os.environ.get('PATH', '')}",
        "HERDR_BIN_PATH": str(TEST_TOOLS_DIR / "herdr"),
        "ORCH_USER_QUESTIONS_DIR": str(TEST_TOOLS_DIR / "questions"),
        "TEST_TOOL_LOG": str(TEST_TOOL_LOG),
        "GITHUB_POLL_STATE_DIR": str(TEST_TOOLS_DIR / "github-poll"),
    }
)
atexit.register(shutil.rmtree, TEST_TOOLS_DIR, ignore_errors=True)

import discovery  # noqa: E402

discovery.state_path = lambda: TEST_TOOLS_DIR / "open-work.json"


def open_work(repos="", clones=""):
    """A patch that makes discovery.repos report the space-separated repos and clones."""
    from unittest.mock import patch

    found = dict(repos=repos.split(), clones=clones.split())
    return patch.object(discovery, "repos", lambda command, work=None: dict(found))


def guard_popen(spawn, base):
    def isolated_popen(args, *positional, **kwargs):
        env = kwargs.get("env")
        if env is None:
            env = os.environ
        if kwargs.get("shell"):
            raise AssertionError("Python tests must use explicit subprocess argv")
        argv = shlex.split(args) if isinstance(args, str) else args
        executable = os.fsdecode(kwargs.get("executable") or argv[0])
        if Path(executable).name in {"env", "sh", "bash", "zsh", "dash", "ksh", "fish"}:
            raise AssertionError(f"Command wrapper blocked: {executable}")
        if Path(executable).name in TOOLS:
            resolved = (
                executable
                if "/" in executable
                else shutil.which(executable, path=env.get("PATH", os.defpath))
            )
            if resolved != str(base / Path(executable).name):
                raise AssertionError(f"Host tool blocked: {executable}")
        return spawn(args, *positional, **kwargs)

    return isolated_popen


class IsolatedTestCase(unittest.TestCase):
    pass


subprocess.Popen = guard_popen(subprocess.Popen, TEST_TOOLS_DIR)
