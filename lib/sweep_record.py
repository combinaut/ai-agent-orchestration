#!/usr/bin/env python3
"""Checks the record /finalize-sweep writes at the end of its validate step."""

import argparse
import hashlib
import json
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}")
COUNTS = ("slices", "sweep_agents", "validators", "findings")
MODES = ("agents", "serial")
FIELDS = ("head", "base", "clean", "mode", *COUNTS, "validator_ids", "verdicts")
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
KEEP = ("CONFIRMED_NEW", "CONFIRMED_REGRESSED", "DISPUTED_DROP")
SLICE_LINES = 400
SCRIPT_FIELDS = (
    "version",
    "pr",
    "runtime",
    "model",
    "kind",
    "tree",
    "plan",
    "pr_text",
    "sweep_agent_ids",
    "items",
    "dropped",
    "open_fix",
    "deviation",
)
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
EMPTY_HASH = hashlib.sha256(b"").hexdigest()
CLEAN_TREE = {
    "status": EMPTY_HASH,
    "diff": EMPTY_HASH,
    "cached": EMPTY_HASH,
    "untracked": {},
    "untracked_hash": hashlib.sha256(b"{}").hexdigest(),
}


def plan_problem(slices, paths=None):
    """Why a slice plan breaks the slicing rules, else None; with paths, every one must appear exactly once."""
    if not isinstance(slices, list) or not slices:
        return "the plan has no slices"
    seen = []
    for chunk in slices:
        files = chunk.get("files") if isinstance(chunk, dict) else None
        if (
            not isinstance(files, list)
            or not files
            or not all(
                isinstance(f, dict) and isinstance(f.get("path"), str) for f in files
            )
        ):
            return "a slice has no file list"
        lines = [f.get("lines") for f in files]
        if any(
            n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0)
            for n in lines
        ):
            return f"slice {chunk.get('name')}: a line count is not a non-negative integer or null"
        binary = [n is None for n in lines]
        if any(binary) and (not all(binary) or chunk.get("name") != "binary"):
            return (
                f"slice {chunk.get('name')}: binary files go only in the binary slice"
            )
        if chunk.get("name") == "binary" and not all(binary):
            return "the binary slice holds a text file"
        size = sum(n or 0 for n in lines)
        total = chunk.get("lines")
        if isinstance(total, bool) or not isinstance(total, int) or total != size:
            return f"slice {chunk.get('name')}: lines is {chunk.get('lines')}, its files add up to {size}"
        if size > SLICE_LINES and len(files) > 1:
            return f"slice {chunk.get('name')}: {size} lines in {len(files)} files; only a single file may pass {SLICE_LINES}"
        seen += [f["path"] for f in files]
    twice = sorted({p for p in seen if seen.count(p) > 1})
    if twice:
        return f"{twice[0]} is in more than one slice"
    if paths is not None:
        missing = sorted(set(paths) - set(seen))
        if missing:
            return f"{missing[0]} is in no slice"
        extra = sorted(set(seen) - set(paths))
        if extra:
            return f"{extra[0]} is not a changed file"
    return None


def finding_problem(finding):
    """Why one kept or dropped finding has a missing or mistyped field, else None."""
    if not isinstance(finding, dict):
        return "a finding is not an object"
    name = finding.get("id")
    for field in ("id", "file", "category", "summary"):
        if not isinstance(finding.get(field), str) or not finding[field]:
            return f"finding {name}: {field} must be a non-empty string"
    line = finding.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 0:
        return f"finding {name}: line must be a non-negative integer"
    if finding.get("class") not in ("fix", "nit"):
        return f"finding {name}: class must be fix or nit"
    if finding.get("verdict") not in VERDICTS:
        return f"finding {name}: verdict {finding.get('verdict')!r} is not a validator verdict"
    return None


def script_problem(record):
    """Why a record written by orch-sweep fails the checks of its own fields, else None."""
    missing = [field for field in SCRIPT_FIELDS if field not in record]
    if missing:
        return f"missing field {missing[0]}"
    if record["version"] != 1 or isinstance(record["version"], bool):
        return "version must be 1"
    if record["deviation"] is not None:
        return "deviation must be null"
    if not isinstance(record["pr"], str) or not PR_URL.fullmatch(record["pr"]):
        return "pr must be a GitHub pull request URL"
    if record["mode"] != "agents":
        return "an orch-sweep record runs in agents mode"
    if record["runtime"] not in ("codex", "claude"):
        return "runtime must be codex or claude"
    if not isinstance(record["model"], str) or not record["model"]:
        return "model must name the agents' model"
    if record["kind"] != "full":
        return "kind must be full"
    if record["pr_text"] != 1 or isinstance(record["pr_text"], bool):
        return "pr_text must be 1; every orch-sweep run checks the PR title and body"
    expected = 2 * record["slices"] + record["pr_text"]
    if record["sweep_agents"] != expected:
        return f"{record['sweep_agents']} sweep_agents; {record['slices']} slices and pr_text {record['pr_text']} start {expected}"
    ids = record["sweep_agent_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        return "sweep_agent_ids must be a list of non-empty strings"
    if len(set(ids)) != len(ids) or len(ids) != record["sweep_agents"]:
        return f"{len(set(ids))} distinct sweep_agent_ids for {record['sweep_agents']} sweep agents"
    shared = sorted(set(ids) & set(record["validator_ids"]))
    if shared:
        return f"validator id {shared[0]} is also a sweep agent id"
    tree = record["tree"]
    if (
        not isinstance(tree, dict)
        or tree.get("head") != record["head"]
        or any(tree.get(k) != v for k, v in CLEAN_TREE.items())
    ):
        return "tree must hold the five tree values of a clean tree at head"
    why = plan_problem(record["plan"])
    if why:
        return f"plan: {why}"
    if len(record["plan"]) != record["slices"]:
        return f"plan has {len(record['plan'])} slices, slices is {record['slices']}"
    items, dropped = record["items"], record["dropped"]
    if not isinstance(items, list) or not isinstance(dropped, list):
        return "items and dropped must be lists of findings"
    seen = set()
    for finding in items + dropped:
        why = finding_problem(finding)
        if why:
            return why
        if finding["id"] in seen:
            return f"finding id {finding['id']} appears twice"
        seen.add(finding["id"])
    if any(i["verdict"] not in KEEP for i in items):
        return "an item has a verdict that drops it"
    kept = sum(record["verdicts"].get(v, 0) for v in KEEP)
    if len(items) != kept:
        return f"{len(items)} items for {kept} kept verdicts"
    if any(i["verdict"] in KEEP for i in dropped):
        return "a dropped finding has a verdict that keeps it"
    if len(items) + len(dropped) != record["findings"]:
        return f"{len(items)} items and {len(dropped)} dropped for {record['findings']} findings"
    for verdict in VERDICTS:
        held = sum(1 for i in items + dropped if i["verdict"] == verdict)
        said = record["verdicts"].get(verdict, 0)
        if held != said:
            return f"the findings hold {verdict} {held}, verdicts says {said}"
    fixes = sum(1 for i in items if i["class"] == "fix")
    open_fix = record["open_fix"]
    if isinstance(open_fix, bool) or not isinstance(open_fix, int) or open_fix < 0:
        return "open_fix must be a non-negative integer"
    if open_fix != fixes:
        return f"open_fix is {record['open_fix']}, the items hold {fixes} fix findings"
    return None


def problem(path, head, base=None):
    """(why, record): why the record fails the checks that need no git, else (None, record)."""
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
    missing = [field for field in FIELDS if field not in record]
    if missing:
        return f"{path}: missing field {missing[0]}", None
    for name, sha in (("head", record["head"]), ("base", record["base"])):
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            return f"{path}: {name} {sha!r} is not a full 40-character SHA", None
    for name, sha in (("--head", head), ("--base", base)):
        if sha is not None and not SHA.fullmatch(sha):
            return f"{name} {sha!r} is not a full 40-character SHA", None
    if record["clean"] is not True:
        return (
            f"{path}: clean must be true; the sweep needs a committed head and a clean tree",
            None,
        )
    for field in COUNTS:
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"{path}: {field} must be a non-negative integer", None
    if record["head"] == record["base"]:
        return f"{path}: head equals base; the sweep covered an empty diff", None
    if record["mode"] not in MODES:
        return f"{path}: mode must be agents or serial", None
    serial = record["mode"] == "serial"
    if record["slices"] < 1:
        return f"{path}: slices must be at least 1", None
    if not serial and record["sweep_agents"] < 1:
        return f"{path}: sweep_agents must be at least 1", None
    if not serial and record["sweep_agents"] < 2 * record["slices"]:
        return (
            f"{path}: {record['sweep_agents']} sweep_agents for {record['slices']} slices; each slice starts two",
            None,
        )
    if serial and (
        record["sweep_agents"] or record["validators"] or record["validator_ids"]
    ):
        return (
            f"{path}: a serial record has no sweep agents, validators or validator ids",
            None,
        )
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
    if not serial and record["findings"] > 0 and record["validators"] < 1:
        return f"{path}: {record['findings']} findings and no validator started", None
    if sum(verdicts.values()) != record["findings"]:
        return (
            f"{path}: verdicts add up to {sum(verdicts.values())}, findings is {record['findings']}",
            None,
        )
    if record.get("tool") == "orch-sweep":
        why = script_problem(record)
        if why:
            return f"{path}: {why}", None
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
    """(why, commits): why the record's commits do not fit head, else the log of the commits after the swept one."""
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
    commits = git("log", "--oneline", f"{swept}..{head}")
    if commits is None:
        return f"{path}: git log {swept}..{head} failed", ""
    return None, commits


def one_line(text):
    """The text with control characters escaped, so a diagnostic stays one line."""
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: repr(m.group())[1:-1], text)


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
    check.add_argument("--exact", action="store_true")
    args = parser.parse_args(argv[1:])
    found, record = problem(args.record, args.head, args.base)
    commits = ""
    if not found and args.exact and record["head"] != args.head:
        found = f"{args.record}: swept {record['head']}, not the exact head {args.head}"
    if not found:
        swept = record["head"]
        found, commits = since(args.record, swept, record["base"], args.head)
    if found:
        print(f"ERROR: {one_line(found)}", file=sys.stderr)
        return 1
    note = "; serial: no independent validator" if record["mode"] == "serial" else ""
    if commits:
        print(one_line(f"OK: {args.record} swept {swept}{note}; commits since:"))
        print(commits, end="")
    else:
        print(one_line(f"OK: {args.record} covers {args.head}{note}"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
