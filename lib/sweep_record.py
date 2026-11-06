#!/usr/bin/env python3
"""Checks the record /finalize-sweep writes: head, base, findings and the validator ids."""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}")
TOKEN = re.compile(r"[A-Za-z0-9_-]+")
CODEX_PATH = re.compile(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
VALIDATOR_MARKER = "finalize-sweep validator"
FIELDS = ("head", "base", "findings", "validator_ids")


def git(*args):
    """Stdout of a git command in the current repo, else None."""
    try:
        done = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def one_line(text):
    """The text with control characters escaped, so a diagnostic stays one line."""
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: repr(m.group())[1:-1], text)


def load(path):
    """(why, record): the record at path, or why it cannot be used."""
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return f"{path}: no sweep record", None
    except OSError as error:
        return f"{path}: cannot read: {error}", None
    except ValueError as error:
        return f"{path}: not valid JSON: {error}", None
    if not isinstance(record, dict):
        return f"{path}: the record must be a JSON object", None
    for field in FIELDS:
        if field not in record:
            return f"{path}: missing field {field}", None
    for name in ("head", "base"):
        sha = record[name]
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            return f"{path}: {name} {sha!r} is not a full 40-character SHA", None
    findings, ids = record["findings"], record["validator_ids"]
    if isinstance(findings, bool) or not isinstance(findings, int) or findings < 0:
        return f"{path}: findings must be a non-negative integer", None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return f"{path}: validator_ids must be a list of strings", None
    if findings > 0 and not ids:
        return f"{path}: {findings} findings and no validator id", None
    return None, record


def since(path, record, head, base):
    """(why, commits): why the record does not fit the PR head, else the log after the swept commit."""
    swept = record["head"]
    for sha in (swept, record["base"], head):
        if git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}") is None:
            return f"{path}: git cannot resolve {sha}", ""
    if swept == record["base"]:
        return f"{path}: head equals base; the sweep covered an empty diff", ""
    if base is not None and base != record["base"]:
        return f"{path}: base {record['base']}, expected {base}", ""
    if git("merge-base", "--is-ancestor", record["base"], swept) is None:
        return f"{path}: base {record['base']} is not an ancestor of {swept}", ""
    if git("merge-base", "--is-ancestor", swept, head) is None:
        return (
            f"{path}: swept {swept}, which is not an ancestor of the PR head {head}",
            "",
        )
    commits = git("log", "--oneline", f"{swept}..{head}")
    if commits is None:
        return f"{path}: git log {swept}..{head} failed", ""
    return None, commits


def claude_transcript(session, agent_id):
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    pattern = os.path.join(
        glob.escape(root),
        "projects",
        "*",
        session,
        "subagents",
        f"agent-{agent_id}.jsonl",
    )
    return next(iter(glob.glob(pattern)), None)


def codex_transcripts(session, agent_id):
    if not CODEX_PATH.fullmatch(agent_id):
        return []
    root = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    pattern = os.path.join(glob.escape(root), "sessions", "**", "rollout-*.jsonl")
    matches = []
    for path in glob.glob(pattern, recursive=True):
        try:
            with open(path, encoding="utf-8") as handle:
                meta = json.loads(handle.readline()).get("payload", {})
            spawn = meta["source"]["subagent"]["thread_spawn"]
            matches_rollout = (
                spawn.get("parent_thread_id") == session
                and spawn.get("agent_path") == agent_id
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if matches_rollout:
            matches.append(path)
    return matches


def claude_brief(path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            message = item.get("message", {})
            if item.get("type") == "user" and message.get("role") == "user":
                content = message.get("content", "")
                if isinstance(content, list):
                    return "".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                return content
    return ""


def codex_brief(path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            payload = item.get("payload", {})
            if (
                item.get("type") == "event_msg"
                and payload.get("type") == "user_message"
            ):
                return payload.get("message", "")
    return ""


def transcript_problem_for(found, family, swept, committed, agent_id, record_path):
    """(why, mtime): one candidate validator transcript's result and freshness."""
    try:
        mtime = os.path.getmtime(found)
        if mtime < int(committed):
            return (
                f"{record_path}: validator {agent_id} was written before commit {swept}",
                mtime,
            )
        with open(found, encoding="utf-8", errors="replace") as handle:
            handle.read()
        brief = claude_brief(found) if family == "Claude" else codex_brief(found)
    except OSError as error:
        return (
            f"{record_path}: validator {agent_id} transcript cannot read: {error}",
            float("-inf"),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        brief = ""
    if not isinstance(brief, str) or (
        VALIDATOR_MARKER not in brief or swept not in brief
    ):
        return (
            f"{record_path}: validator {agent_id} brief must include {VALIDATOR_MARKER} "
            f"and name {swept}",
            mtime,
        )
    return None, mtime


def transcript_problem(path, swept, session, agent_id):
    """Why agent_id is no validator transcript of session for the swept head, else None."""
    if not TOKEN.fullmatch(agent_id) and not CODEX_PATH.fullmatch(agent_id):
        return f"{path}: validator id {agent_id!r} is not a plain id"
    candidates = []
    if TOKEN.fullmatch(agent_id):
        found = claude_transcript(session, agent_id)
        if found:
            candidates.append(("Claude", found))
    candidates.extend(
        ("Codex", found) for found in codex_transcripts(session, agent_id)
    )
    if not candidates:
        return (
            f"{path}: validator {agent_id} is no supported Claude or Codex subagent "
            f"transcript of session {session}; Pi and Cursor validators are unsupported"
        )
    committed = git("show", "-s", "--format=%ct", swept)
    if committed is None:
        return f"{path}: git cannot read the commit time of {swept}"
    newest_problem, newest_mtime = None, float("-inf")
    for family, found in candidates:
        problem, mtime = transcript_problem_for(
            found, family, swept, committed, agent_id, path
        )
        if problem is None:
            return None
        if mtime >= newest_mtime:
            newest_problem, newest_mtime = problem, mtime
    return newest_problem


class Parser(argparse.ArgumentParser):
    """Reports usage errors as one escaped ERROR line and exit 1; subparsers share the class."""

    def error(self, message):
        print(f"ERROR: {one_line(message)}", file=sys.stderr)
        sys.exit(1)


def main(argv):
    parser = Parser(prog="sweep_record.py")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--record", required=True)
    check.add_argument("--head", required=True)
    check.add_argument("--base")
    check.add_argument("--session")
    check.add_argument("--no-transcripts", action="store_true")
    args = parser.parse_args(argv[1:])
    for name, sha in (("--head", args.head), ("--base", args.base)):
        if sha is not None and not SHA.fullmatch(sha):
            parser.error(f"{name} {sha!r} is not a full 40-character SHA")
    found, record = load(args.record)
    commits = ""
    if not found:
        found, commits = since(args.record, record, args.head, args.base)
    attestation = args.no_transcripts or not record or record["findings"] == 0
    if not found and not attestation:
        if not args.session or not TOKEN.fullmatch(args.session):
            found = f"{args.record}: --session <worker session id> is required"
        for agent_id in record["validator_ids"]:
            found = found or transcript_problem(
                args.record, record["head"], args.session, agent_id
            )
    if found:
        print(f"ERROR: {one_line(found)}", file=sys.stderr)
        return 1
    note = "; attestation only" if args.no_transcripts and record["findings"] else ""
    if commits:
        print(
            one_line(f"OK: {args.record} swept {record['head']}{note}; commits since:")
        )
        print(commits, end="")
    else:
        print(one_line(f"OK: {args.record} covers {args.head}{note}"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
