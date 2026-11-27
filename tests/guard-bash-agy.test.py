#!/usr/bin/env python3
"""Tests for the agy PreToolUse adapter around guard-bash."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["ORCH_ENV"] = "none"

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "hooks/guard-bash-agy.py"
GUARD = ROOT / "hooks/guard-bash.py"
PUSH_MAIN = "git push origin main"


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def run(payload, home, adapter=ADAPTER):
    env = dict(os.environ, HOME=str(home))
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(adapter)],
        input=text,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def agy(command, cwd=None, tool="run_command", args=None, **extra):
    payload = {
        "toolCall": {
            "id": "t1",
            "name": tool,
            "args": args or {"CommandLine": command},
        },
        "stepIdx": 1,
        "hookEventName": "PreToolUse",
        **extra,
    }
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return payload


def guard_reason(command, cwd, home):
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(
            {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}
        ),
        capture_output=True,
        text=True,
        env=dict(os.environ, HOME=str(home)),
    )
    assert result.returncode == 2, result
    return result.stderr.strip()


def log_lines(home):
    log = home / ".claude/hooks/guard-bash.log"
    return log.read_text().splitlines() if log.exists() else []


def passed(result):
    return result.returncode == 0 and result.stdout == "" and result.stderr == ""


def denied(result, reason):
    return result.returncode == 0 and json.loads(result.stdout) == {
        "decision": "deny",
        "reason": reason,
    }


def fake_adapter(root, guard_body):
    hooks = root / "fake-hooks"
    hooks.mkdir()
    (hooks / "guard-bash-agy.py").write_text(ADAPTER.read_text())
    (hooks / "guard-bash.py").write_text(guard_body)
    return hooks / "guard-bash-agy.py"


def setup(root):
    home = root / "home"
    (home / ".claude/hooks").mkdir(parents=True)
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feature")
    git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    git(repo, "worktree", "add", "-q", str(root / "linked"), "-b", "other")
    single = root / "single"
    single.mkdir()
    git(single, "init", "-q")
    return home, repo, single


def test_deny(root, home, repo, single):
    result = run(agy(PUSH_MAIN, repo), home)
    assert denied(result, guard_reason(PUSH_MAIN, repo, home)), result


def test_cwd(root, home, repo, single):
    assert "decision" in json.loads(run(agy("git stash", repo), home).stdout)
    assert passed(run(agy("git stash", single, workspacePaths=[str(repo)]), home))


def test_workspace_path(root, home, repo, single):
    result = run(agy("git stash", workspacePaths=[str(repo)]), home)
    assert "decision" in json.loads(result.stdout), result


def test_send_input(root, home, repo, single):
    args = {"Action": "send_input", "Input": PUSH_MAIN, "TaskId": "c/task-1"}
    result = run(agy(None, repo, tool="manage_task", args=args), home)
    assert json.loads(result.stdout)["decision"] == "deny", result


def test_other_tool(root, home, repo, single):
    assert passed(run(agy(PUSH_MAIN, repo, tool="view_file"), home))
    status = {"Action": "status", "Input": PUSH_MAIN, "TaskId": "c/task-1"}
    assert passed(run(agy(None, repo, tool="manage_task", args=status), home))
    assert log_lines(home) == []


def test_bad_input(root, home, repo, single):
    for bad in ("not json", "{}", json.dumps(agy(PUSH_MAIN))):
        before = len(log_lines(home))
        assert passed(run(bad, home)), bad
        lines = log_lines(home)
        assert len(lines) == before + 1, (bad, lines)
        assert re.match(r"\d{4}-\d\d-\d\dT\S+ guard-bash-agy fail-open: ", lines[-1])


def test_guard_crash(root, home, repo, single):
    crash = fake_adapter(
        root, "import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n"
    )
    assert passed(run(agy(PUSH_MAIN, repo), home, adapter=crash))
    lines = log_lines(home)
    assert len(lines) == 1 and "exit 1: boom" in lines[0], lines


def main():
    failed = []
    for name, test in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            try:
                test(root, *setup(root))
                print(f"ok {name}")
            except Exception as exc:
                failed.append(name)
                print(f"FAIL {name}: {type(exc).__name__}: {exc}"[:300])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
