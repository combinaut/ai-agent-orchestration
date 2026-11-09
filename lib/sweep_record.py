#!/usr/bin/env python3
"""Checks the record /finalize-sweep writes at the end of its validate step."""

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}")
TOKEN = re.compile(r"[A-Za-z0-9_-]+")
CODEX_PATH = re.compile(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
VALIDATOR_MARKER = "finalize-sweep validator"
FIELDS = ("head", "base", "findings", "validator_ids")
COUNTS = ("slices", "sweep_agents", "validators", "findings")
MODES = ("agents", "serial")
RICH_FIELDS = ("head", "base", "clean", "mode", *COUNTS, "validator_ids", "verdicts")
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
MAX_SLICES = 25
KINDS = ("full", "text")
DEVIATIONS = ("slices-over-25",)
MARKER = "<!-- orch-sweep v1 -->"
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
GH_TIMEOUT = 45
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
    if not isinstance(record["pr"], str) or not PR_URL.fullmatch(record["pr"]):
        return "pr must be a GitHub pull request URL"
    if record["mode"] != "agents":
        return "an orch-sweep record runs in agents mode"
    if record["runtime"] not in ("codex", "claude"):
        return "runtime must be codex or claude"
    if not isinstance(record["model"], str) or not record["model"]:
        return "model must name the agents' model"
    if record["kind"] not in KINDS:
        return "kind must be full or text"
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
    if record["kind"] == "text":
        if record["plan"] != [] or record["slices"] != 0:
            return "a text record sweeps no slices: plan [] and slices 0"
    else:
        why = plan_problem(record["plan"])
        if why:
            return f"plan: {why}"
        if len(record["plan"]) != record["slices"]:
            return (
                f"plan has {len(record['plan'])} slices, slices is {record['slices']}"
            )
    deviation = record["deviation"]
    if deviation is not None and not (
        isinstance(deviation, dict)
        and deviation.get("what") in DEVIATIONS
        and isinstance(deviation.get("reason"), str)
        and deviation["reason"].strip()
        and isinstance(deviation.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", deviation["sha256"])
    ):
        return f"deviation must be null or name one of {', '.join(DEVIATIONS)} with a reason and the sha256 of the user's allow file"
    if (
        record["slices"] > MAX_SLICES
        and (deviation or {}).get("what") != "slices-over-25"
    ):
        return f"{record['slices']} slices pass the limit of {MAX_SLICES} without the user's slices-over-25 allow"
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
    missing = [field for field in RICH_FIELDS if field not in record]
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
    text = record.get("tool") == "orch-sweep" and record.get("kind") == "text"
    if record["slices"] < 1 and not text:
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


def git(*args, retry_errors=False):
    """Stdout of a git command in the current repo, else None."""
    try:
        done = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as error:
        if retry_errors:
            raise GateError(f"git {' '.join(args)}: {error}") from error
        return None
    if retry_errors and done.returncode and (args[0] == "log" or done.returncode != 1):
        raise GateError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout if done.returncode == 0 else None


def since(
    path, swept, base, head, retry_errors=False, *, manual=False, expected_base=None
):
    """(why, commits): why the record's commits do not fit head, else the log of the commits after the swept one."""
    for sha in (swept, base, head):
        if (
            git(
                "rev-parse",
                "--verify",
                "--quiet",
                f"{sha}^{{commit}}",
                retry_errors=retry_errors,
            )
            is None
        ):
            return f"{path}: git cannot resolve {sha}", ""
    if manual:
        if swept == base:
            return f"{path}: head equals base; the sweep covered an empty diff", ""
        if expected_base is not None and expected_base != base:
            return f"{path}: base {base}, expected {expected_base}", ""
    if (
        git("merge-base", "--is-ancestor", base, swept, retry_errors=retry_errors)
        is None
    ):
        target = swept if manual else f"the swept {swept}"
        return f"{path}: base {base} is not an ancestor of {target}", ""
    if (
        git("merge-base", "--is-ancestor", swept, head, retry_errors=retry_errors)
        is None
    ):
        return (
            f"{path}: swept {swept}, which is not an ancestor of the PR head {head}",
            "",
        )
    commits = git("log", "--oneline", f"{swept}..{head}", retry_errors=retry_errors)
    if commits is None:
        return f"{path}: git log {swept}..{head} failed", ""
    return None, commits


def text_sha(pr):
    """Hash of a pull request's title and body."""
    return hashlib.sha256(
        json.dumps([pr.get("title") or "", pr.get("body") or ""]).encode()
    ).hexdigest()


def cache_path(repo, number, head, kind="full"):
    """Where orch-sweep keeps its copy of a record; an allow file has kind allow."""
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    name = f"{head}.json" if kind == "full" else f"{head}.{kind}.json"
    return root / "review-prs" / "sweeps" / repo / str(number) / name


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def comment_record(body):
    """The orch-sweep record in a sweep comment's json block, else None."""
    if MARKER not in body:
        return None
    block = re.search(r"```json\n(.*?)\n```", body, re.S)
    try:
        record = json.loads(block.group(1)) if block else None
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("tool") != "orch-sweep":
        return None
    return record


class GateError(Exception):
    """A gate that cannot read GitHub, Git or its settings."""


def gh_json(*args):
    try:
        done = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        raise GateError(f"gh {' '.join(args)} timed out after {GH_TIMEOUT}s") from None
    except OSError as error:
        raise GateError(f"cannot run gh: {error}") from error
    if done.returncode:
        tail = done.stderr.strip().splitlines()[-1:] or [""]
        raise GateError(f"gh {' '.join(args)} failed: {tail[0]}")
    try:
        return json.loads(done.stdout)
    except ValueError as error:
        raise GateError(f"gh {' '.join(args)}: not JSON: {error}") from error


def record_problem(record, head):
    """Why a published record fails schema validation, else None."""
    with tempfile.TemporaryDirectory(prefix="orch-gate-") as scratch:
        path = Path(scratch, "record.json")
        path.write_text(json.dumps(record))
        why, _ = problem(str(path), head)
    return (
        f"the {record.get('kind')} sweep record of {head}: {why.split(': ', 1)[1]}"
        if why
        else None
    )


def finalize_enabled():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import orch_settings

    try:
        return bool(orch_settings.load_repo(".")["ORCH_REVIEW_FINALIZE_COMMANDS"])
    except (orch_settings.ConfigError, OSError) as error:
        raise GateError(f"cannot read finalize settings: {error}") from error


def pr_merge_base(base, head):
    if not isinstance(base, str) or not SHA.fullmatch(base):
        raise GateError("the PR response has no valid base.sha")
    if (
        git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", retry_errors=True)
        is None
    ):
        raise GateError(f"git cannot resolve live base {base}; fetch the base branch")
    found = git("merge-base", base, head, retry_errors=True)
    if not found:
        raise GateError(
            f"git cannot find the merge base of live base {base} and {head}"
        )
    return found.strip()


def review_disputes(body):
    return {
        (int(comment), finding.strip())
        for comment, finding, _ in re.findall(
            r"^Sweep dispute ([0-9]+)/([^:\n]+):[ \t]+(\S[^\n]*)$", body or "", re.M
        )
    }


def gate(url, head, review_body=None):
    """Why the PR at url may not be approved at head, else None; raises GateError when GitHub cannot be read."""
    match = PR_URL.fullmatch(url)
    if not match:
        return f"--pr {url!r} is not a GitHub pull request URL"
    if not SHA.fullmatch(head):
        return f"--head {head!r} is not a full 40-character SHA"
    repo, number = match.group(1), int(match.group(2))
    pr = gh_json("api", f"repos/{repo}/pulls/{number}")
    live = (pr.get("head") or {}).get("sha")
    if live != head:
        return f"the PR head is {live}, not {head}"
    if not finalize_enabled():
        return None
    base_ref = (pr.get("base") or {}).get("ref")
    merge_base = pr_merge_base((pr.get("base") or {}).get("sha"), head)
    me = gh_json("api", "user").get("login")
    trusted = {me, (pr.get("user") or {}).get("login")} - {None}
    if not me:
        raise GateError("cannot read the reviewer login")
    pages = gh_json(
        "api", "--paginate", "--slurp", f"repos/{repo}/issues/{number}/comments"
    )
    order = []
    for comment in (c for page in pages for c in page):
        if (comment.get("user") or {}).get("login") not in trusted:
            continue
        record = comment_record(comment.get("body") or "")
        if record and record.get("head") == head and record.get("kind") in KINDS:
            order.append((record, comment.get("id")))
    latest = {record["kind"]: (record, cid) for record, cid in order}
    if "full" not in latest:
        return f"no full orch-sweep comment by {me} for the exact head {head}"
    full, full_id = latest["full"]
    newest, newest_id = order[-1]
    for record in [full] + ([newest] if newest["kind"] == "text" else []):
        why = record_problem(record, head)
        if why:
            return why
        why, _ = since(
            f"the {record['kind']} sweep record",
            head,
            record["base"],
            head,
            retry_errors=True,
        )
        if why:
            return why
        if record["base"] != merge_base:
            return f"the PR base changed since the {record['kind']} sweep: it swept from {record['base']}, and {base_ref} gives {merge_base}; run orch-sweep run"
    if newest.get("pr_text_sha256") != text_sha(pr):
        return f"the PR title or body changed since the {newest['kind']} sweep of {head}; run orch-sweep run --kind text"
    has_fixes = full["open_fix"] or newest["open_fix"]
    if review_body is None and has_fixes:
        reviews = gh_json(
            "api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/reviews"
        )
        own = [
            r
            for page in reviews
            for r in page
            if (r.get("user") or {}).get("login") == me
            and r.get("commit_id") == head
            and r.get("submitted_at")
        ]
        review_body = (
            max(own, key=lambda r: r["submitted_at"]).get("body") if own else ""
        )
    disputes = review_disputes(review_body)
    for record, cid in [(full, full_id)] + (
        [(newest, newest_id)] if newest["kind"] == "text" else []
    ):
        count = sum(
            1
            for item in record["items"]
            if item["class"] == "fix"
            and not (
                record is full
                and newest["kind"] == "text"
                and item["category"] == "pr-text"
            )
            and (cid, item["id"]) not in disputes
        )
        if count:
            suffix = (
                " outside the PR text"
                if record is full and newest["kind"] == "text"
                else ""
            )
            return f"the {record['kind']} sweep of {head} has {count} open fix findings{suffix}"
    return None


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


def record_since(path, record, head, base):
    """(why, commits): why the record does not fit the PR head, else the log after the swept commit."""
    return since(
        path, record["head"], record["base"], head, manual=True, expected_base=base
    )


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


def transcript_problem_for(found, family, swept, committed, agent_id, record_path):
    """(why, mtime): one candidate validator transcript's result and freshness."""
    try:
        mtime = os.path.getmtime(found)
        if mtime < int(committed):
            return (
                f"{record_path}: validator {agent_id} was written before commit {swept}",
                mtime,
            )
        brief = claude_brief(found) if family == "Claude" else None
    except OSError as error:
        return (
            f"{record_path}: validator {agent_id} transcript cannot read: {error}",
            float("-inf"),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        brief = ""
    if family == "Codex" and not agent_id.startswith(
        f"/root/finalize_sweep_validator_{swept[:12]}"
    ):
        return (
            f"{record_path}: Codex validator {agent_id} must start with "
            f"/root/finalize_sweep_validator_{swept[:12]}",
            mtime,
        )
    if family == "Claude" and (
        not isinstance(brief, str)
        or VALIDATOR_MARKER not in brief
        or swept not in brief
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
    check.add_argument("--exact", action="store_true")
    check.add_argument("--session")
    check.add_argument("--no-transcripts", action="store_true")
    approve = commands.add_parser("gate")
    approve.add_argument("--pr", required=True)
    approve.add_argument("--head", required=True)
    args = parser.parse_args(argv[1:])
    if args.command == "gate":
        try:
            found = gate(args.pr, args.head)
        except GateError as error:
            print(
                f"ERROR: cannot check the gate: {one_line(str(error))}", file=sys.stderr
            )
            return 2
        if found:
            print(f"ERROR: {one_line(found)}", file=sys.stderr)
            return 1
        print(f"OK: the sweep of {args.head} allows APPROVE")
        return 0
    for name, sha in (("--head", args.head), ("--base", args.base)):
        if sha is not None and not SHA.fullmatch(sha):
            parser.error(f"{name} {sha!r} is not a full 40-character SHA")
    found, record = load(args.record)
    if not found and record.get("tool") == "orch-sweep":
        found, record = problem(args.record, args.head, args.base)
    if not found and args.exact and record["head"] != args.head:
        found = f"{args.record}: swept {record['head']}, not the exact head {args.head}"
    commits = ""
    if not found:
        found, commits = record_since(args.record, record, args.head, args.base)
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
