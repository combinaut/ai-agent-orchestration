#!/usr/bin/env python3
"""Checks the record /finalize-sweep writes at the end of its validate step."""

import argparse
import json
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}")
COUNTS = ("slices", "sweep_agents", "validators", "findings")
POSITIVE = ("slices", "sweep_agents")
FIELDS = ("head", "base", "clean", *COUNTS, "validator_ids", "verdicts")
VERDICTS = (
    "CONFIRMED_NEW",
    "CONFIRMED_REGRESSED",
    "DISPUTED_DROP",
    "RESPECT_DROP",
    "ALREADY_FIXED",
    "ALLOWED_PLANNED",
    "PREEXISTING",
    "FALSE",
    "NOT_WORTH_IT",
)


def problem(path, head, base=None):
    """(why, record): why the record at path does not prove a sweep of head, else None."""
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return f"{path}: no sweep record", None
    except (OSError, ValueError) as error:
        return f"{path}: not valid JSON: {error}", None
    if not isinstance(record, dict):
        return f"{path}: the record must be a JSON object", None
    missing = [field for field in FIELDS if field not in record]
    if missing:
        return f"{path}: missing field {missing[0]}", None
    for sha in (record["head"], record["base"], head, *([base] if base else [])):
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            return f"{path}: {sha!r} is not a full 40-character SHA", None
    if record["clean"] is not True:
        return (
            f"{path}: clean must be true; the sweep needs a committed head and a clean tree",
            None,
        )
    for field in COUNTS:
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"{path}: {field} must be a non-negative integer", None
    for field in POSITIVE:
        if record[field] < 1:
            return f"{path}: {field} must be at least 1", None
    if base and record["base"] != base:
        return f"{path}: base is {record['base']}, expected {base}", None
    verdicts = record["verdicts"]
    if not isinstance(verdicts, dict) or any(
        isinstance(n, bool) or not isinstance(n, int) or n < 0
        for n in verdicts.values()
    ):
        return f"{path}: verdicts must map each verdict to a count", None
    unknown = [key for key in verdicts if key not in VERDICTS]
    if unknown:
        return f"{path}: unknown verdict {unknown[0]}", None
    ids = record["validator_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        return f"{path}: validator_ids must be a list of non-empty strings", None
    if len(set(ids)) != len(ids):
        return f"{path}: validator_ids repeat an id", None
    if len(ids) != record["validators"]:
        return (
            f"{path}: {len(ids)} validator_ids for {record['validators']} validators",
            None,
        )
    if record["findings"] > 0 and record["validators"] < 1:
        return f"{path}: {record['findings']} findings and no validator started", None
    if sum(verdicts.values()) != record["findings"]:
        return (
            f"{path}: verdicts add up to {sum(verdicts.values())}, findings is {record['findings']}",
            None,
        )
    return None, record


def git(*args):
    """Stdout of a git command in the current repo, else None."""
    try:
        done = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def since(path, swept, base, head):
    """Why the swept commit does not lead to head, else the commits after it."""
    for sha in (swept, base, head):
        if git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}") is None:
            return f"{path}: git cannot resolve {sha}", ""
    if git("merge-base", "--is-ancestor", base, swept) is None:
        return f"{path}: base {base} is not an ancestor of the swept {swept}", ""
    if git("merge-base", "--is-ancestor", swept, head) is None:
        return (
            f"{path}: swept {swept}, which is not an ancestor of the PR head {head}",
            "",
        )
    return None, git("log", "--oneline", f"{swept}..{head}") or ""


def one_line(text):
    """The text with control characters escaped, so a diagnostic stays one line."""
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: repr(m.group())[1:-1], text)


def main(argv):
    parser = argparse.ArgumentParser(prog="sweep_record.py")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--record", required=True)
    check.add_argument("--head", required=True)
    check.add_argument("--base")
    args = parser.parse_args(argv[1:])
    found, record = problem(args.record, args.head, args.base)
    commits = ""
    if not found:
        swept = record["head"]
        found, commits = since(args.record, swept, record["base"], args.head)
    if found:
        print(f"ERROR: {one_line(found)}", file=sys.stderr)
        return 1
    if commits:
        print(f"OK: {args.record} swept {swept}; commits since:")
        print(commits, end="")
    else:
        print(f"OK: {args.record} covers {args.head}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
