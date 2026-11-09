#!/usr/bin/env python3
"""Stand-in for codex exec and claude -p in tests/sweep-run.bats; behaviour comes from FAKE_* variables."""

import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

if os.environ.get("FAKE_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
tmp = Path(os.environ["TMP"])
kind = Path(sys.argv[0]).name
prompt = sys.stdin.read()
role = re.search(r"^ROLE: (\S+)", prompt, re.M).group(1)
with (tmp / "agent.calls").open("a") as log:
    log.write(
        json.dumps(
            {
                "kind": kind,
                "role": role,
                "argv": sys.argv[1:],
                "executable": sys.argv[0],
                "injected": {
                    key: os.environ.get(key)
                    for key in ("PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS")
                },
            }
        )
        + "\n"
    )
prompts = tmp / "prompts"
prompts.mkdir(exist_ok=True)
(prompts / f"{role}.{os.getpid()}").write_text(prompt)
diff = re.search(r" is in (.+?\.diff)[;.]", prompt)
if diff:
    (prompts / f"{role}.{os.getpid()}.diff").write_bytes(
        Path(diff.group(1)).read_bytes()
    )

live = tmp / "live"
live.mkdir(exist_ok=True)
(live / str(os.getpid())).touch()
with (tmp / "live.max").open("a") as log:
    log.write(f"{len(list(live.iterdir()))}\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
(live / str(os.getpid())).unlink()

if os.environ.get("FAKE_CHILD") == role:
    child = subprocess.Popen(
        ["bash", "-c", "trap '' TERM; sleep 1000"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with (tmp / "child.pids").open("a") as log:
        log.write(f"{child.pid}\n")
if os.environ.get("FAKE_PIPE_CHILD") == role:
    child = subprocess.Popen(
        ["bash", "-c", "trap '' TERM; sleep 1000"], stdin=subprocess.DEVNULL
    )
    with (tmp / "child.pids").open("a") as log:
        log.write(f"{child.pid}\n")
if os.environ.get("FAKE_HANG") == role:
    time.sleep(1000)
if os.environ.get("FAKE_WRITE") == role:
    target = Path(os.environ["FAKE_WRITE_PATH"])
    target.write_text(target.read_text() + "x\n" if target.exists() else "x\n")
if os.environ.get("FAKE_FAIL") == role:
    sys.exit(1)

if role == "validator":
    ids = re.findall(r'"id": "([FP]\d+)"', prompt.split("--- Findings ---")[-1])
    drop = os.environ.get("FAKE_DROP_ID")
    answer = {
        "verdicts": [
            {
                "id": i,
                "verdict": os.environ.get("FAKE_VERDICT", "CONFIRMED_NEW"),
                "class": os.environ.get("FAKE_CLASS", "fix"),
                "reason": "checked",
            }
            for i in ids
            if i != drop
        ]
    }
else:
    count = int(
        os.environ.get(
            "FAKE_PR_FINDINGS" if role == "pr-text" else "FAKE_FINDINGS", "0"
        )
    )
    files = re.findall(r"^- (\S+)", prompt, re.M) or ["PR body"]
    answer = {
        "findings": [
            {
                "file": files[0],
                "line": i + 1,
                "category": role,
                "summary": f"{role} {os.getpid()} {i}"
                + "x" * int(os.environ.get("FAKE_PAD", "0")),
                "evidence": f"{files[0]}:{i + 1}",
            }
            for i in range(count)
        ]
    }

    if os.environ.get("FAKE_BAD_FINDING") == role:
        answer["findings"][0]["file"] = ""

session = f"{kind}-{uuid.uuid4().hex[:12]}"
if role == "validator" and os.environ.get("FAKE_PERSIST"):
    if kind == "codex":
        home = Path(os.environ["CODEX_HOME"])
        path = home / "sessions" / "2026" / "11" / f"rollout-{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "type": "session_meta",
                "payload": {
                    "id": session,
                    "session_id": "unrelated-metadata-session",
                    "source": "cli",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": json.dumps(answer)}],
                },
            },
        ]
    else:
        home = Path(os.environ["CLAUDE_CONFIG_DIR"])
        path = home / "projects" / "-repo" / f"{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "type": "user",
                "sessionId": session,
                "message": {"role": "user", "content": prompt},
            },
            {
                "type": "assistant",
                "sessionId": session,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": json.dumps(answer)}],
                },
            },
        ]
    path.write_text("\n".join(map(json.dumps, rows)) + "\n")
if kind == "codex":
    out = sys.argv[sys.argv.index("-o") + 1]
    Path(out).write_text(json.dumps(answer))
    print(json.dumps({"type": "thread.started", "thread_id": session}))
    print(json.dumps({"type": "turn.completed"}))
else:
    print(
        json.dumps(
            {
                "type": "result",
                "is_error": False,
                "session_id": session,
                "result": json.dumps(answer),
                "structured_output": answer,
            }
        )
    )
