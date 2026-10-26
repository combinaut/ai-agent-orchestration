#!/usr/bin/env python3
"""Chrome native messaging host for PR tabs: one request, at most one gh call or one bounded Herdr lookup, one reply, then exit."""

import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

MAX_REQUEST_BYTES = 64 * 1024
MAX_REPLY_BYTES = 1024 * 1024
GH_TIMEOUT_SECONDS = 30
MAX_STATES = 100
MAX_SEARCH = 100
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
PR_FIELDS = "state title headRefName headRepository { nameWithOwner }"
SEARCH = (
    "query($q: String!, $n: Int!, $a: String) { search(query: $q, type: ISSUE, first: $n, after: $a) { issueCount"
    " pageInfo { endCursor hasNextPage } nodes { ... on PullRequest { url title headRefName headRepository { nameWithOwner } } } } }"
)


class RequestError(Exception):
    pass


def read_request(stream):
    header = stream.read(4)
    if len(header) != 4:
        raise RequestError("no request")
    (size,) = struct.unpack("=I", header)
    if size > MAX_REQUEST_BYTES:
        raise RequestError(
            f"request too large ({size} bytes, limit {MAX_REQUEST_BYTES})"
        )
    body = stream.read(size)
    if len(body) != size:
        raise RequestError("truncated request")
    try:
        request = json.loads(body)
    except ValueError:
        raise RequestError("request is not JSON") from None
    if not isinstance(request, dict):
        raise RequestError("request is not an object")
    return request


def write_reply(stream, reply):
    body = json.dumps(reply, separators=(",", ":")).encode()
    if len(body) > MAX_REPLY_BYTES:
        body = json.dumps({"ok": False, "error": "reply too large"}).encode()
    stream.write(struct.pack("=I", len(body)) + body)
    stream.flush()


def gh_graphql(query, variables):
    args = ["gh", "api", "graphql", "-f", "query=" + query]
    for key, value in variables:
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    try:
        done = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RequestError(f"gh timed out after {GH_TIMEOUT_SECONDS}s") from None
    except FileNotFoundError:
        raise RequestError("gh not found on PATH") from None
    try:
        payload = json.loads(done.stdout)
    except ValueError:
        payload = None
    failure = (
        "; ".join(
            str(error.get("message"))
            for error in (payload or {}).get("errors") or []
            if isinstance(error, dict)
        )
        if isinstance(payload, dict)
        else ""
    )
    exit_text = (
        f"gh exited {done.returncode}" if done.returncode else "GitHub returned no data"
    )
    failure = (failure or done.stderr.strip() or exit_text)[:300]
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise RequestError(failure)
    return payload["data"], failure


def is_count(value, limit):
    return (
        isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= limit
    )


def states(request):
    prs = request.get("prs")
    if not isinstance(prs, list) or not 1 <= len(prs) <= MAX_STATES:
        raise RequestError(f"prs must list 1 to {MAX_STATES} pull requests")
    declarations, fields, variables, keys = [], [], [], []
    for i, pr in enumerate(prs):
        owner, repo, number = (
            pr.get(k) if isinstance(pr, dict) else None
            for k in ("owner", "repo", "number")
        )
        if not (
            isinstance(owner, str)
            and NAME.match(owner)
            and isinstance(repo, str)
            and NAME.match(repo)
        ):
            raise RequestError(f"invalid pull request at index {i}")
        if not is_count(number, 10**9):
            raise RequestError(f"invalid pull request number at index {i}")
        declarations.append(f"$o{i}: String!, $r{i}: String!, $n{i}: Int!")
        fields.append(
            f"p{i}: repository(owner: $o{i}, name: $r{i}) {{ pullRequest(number: $n{i}) {{ {PR_FIELDS} }} }}"
        )
        variables += [(f"o{i}", owner), (f"r{i}", repo), (f"n{i}", number)]
        keys.append(f"{owner}/{repo}#{number}")
    data, failure = gh_graphql(
        f"query({', '.join(declarations)}) {{ {' '.join(fields)} }}", variables
    )
    result = {}
    for i, key in enumerate(keys):
        node = (data.get(f"p{i}") or {}).get("pullRequest")
        result[key] = node if isinstance(node, dict) else None
    if not any(result.values()):
        raise RequestError(failure)
    return {"ok": True, "prs": result, "owners": ownership()}


def search(request):
    query, limit = request.get("query"), request.get("limit")
    if not isinstance(query, str) or not 1 <= len(query) <= 256:
        raise RequestError("query must be 1 to 256 characters")
    if not is_count(limit, MAX_SEARCH):
        raise RequestError(f"limit must be 1 to {MAX_SEARCH}")
    after = request.get("after")
    if after is not None and not (isinstance(after, str) and 1 <= len(after) <= 256):
        raise RequestError("after must be 1 to 256 characters")
    variables = [("q", query), ("n", limit)] + ([("a", after)] if after else [])
    data, failure = gh_graphql(SEARCH, variables)
    found = data.get("search")
    if not isinstance(found, dict):
        raise RequestError(failure)
    prs = [
        node
        for node in found.get("nodes") or []
        if isinstance(node, dict) and node.get("url")
    ]
    page = found.get("pageInfo") or {}
    return {
        "ok": True,
        "owners": ownership(),
        "total": found.get("issueCount") or len(prs),
        "prs": prs,
        "endCursor": page.get("endCursor"),
        "hasNextPage": bool(page.get("hasNextPage")),
    }


def project_settings():
    """Use the watcher loader, including exported-value precedence."""
    library = Path(__file__).resolve().parents[3] / "coordinator/watchers/coord-lib.sh"
    result = subprocess.run(
        [
            "/bin/bash",
            "-e",
            "-c",
            '. "$1"; orch_load_config; printf "%s\\0" "$ORCH_PR_TABS_ORG" "$ORCH_PR_TABS_DEFAULT_REPO" "$ORCH_PR_TABS_REPOS" "$ORCH_PR_TABS_OPEN_ALL"',
            "orch-config",
            str(library),
        ],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    return result.stdout.split("\0")[:4]


def settings(request):
    try:
        org, repo, extra, query = project_settings()
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        config = os.environ.get(
            "ORCH_CONFIG", str(Path.home() / ".claude/orchestration.toml")
        )
        raise RequestError(
            f"Cannot load PR-tabs settings from {config}: {type(error).__name__}"
        ) from None
    if org and not re.fullmatch(r"[A-Za-z0-9-]{1,39}", org):
        raise RequestError(
            "ORCH_PR_TABS_ORG must be a GitHub owner name (1-39 letters, digits, or hyphens)"
        )
    if repo and not NAME.fullmatch(repo):
        raise RequestError(
            "ORCH_PR_TABS_DEFAULT_REPO must be a GitHub repository name (1-100 letters, digits, underscores, dots, or hyphens)"
        )
    names = [name.strip() for name in extra.split(",") if name.strip()]
    if any(not NAME.fullmatch(name) for name in names):
        raise RequestError(
            "ORCH_PR_TABS_REPOS must be comma-separated GitHub repository names (1-100 letters, digits, underscores, dots, or hyphens each)"
        )
    repos = []
    for name in ([repo] if repo else []) + names:
        if name.lower() not in (seen.lower() for seen in repos):
            repos.append(name)
    return {
        "ok": True,
        "org": org,
        "defaultRepo": repo,
        "repos": repos,
        "openAll": query,
    }


OWNER_CACHE_SECONDS = 30


def is_owner_map(value):
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    )


@contextmanager
def discovery_settings(values):
    # Main's adapter reads these process settings. Restore them for in-process callers.
    updates = {key: values.get(key, "") for key in ("ORCH_ENV", "ORCH_HERDR_BIN")}
    if values.get("ORCH_HERDR_SOCKET"):
        updates["HERDR_SOCKET_PATH"] = values["ORCH_HERDR_SOCKET"]
    before = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update(updates)
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def discover_owners(root, values):
    sys.path.insert(0, str(root / "coordinator/watchers"))
    discovery = importlib.import_module("discovery")
    deadline = time.monotonic() + 5

    def command(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, 5)
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=remaining,
            check=True,
            env=dict(os.environ, LC_ALL="C"),
        ).stdout.strip()

    with discovery_settings(values):
        return discovery.branch_owners(command)


def orch_values(root):
    """Global settings with ORCH_* environment overrides."""
    spec = importlib.util.spec_from_file_location(
        "orch_settings", root / "lib/orch_settings.py"
    )
    config = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = config
    spec.loader.exec_module(config)
    values = config.load_global(
        os.environ.get("ORCH_CONFIG", str(Path.home() / ".claude/orchestration.toml"))
    )
    values.update(
        {key: value for key, value in os.environ.items() if key.startswith("ORCH_")}
    )
    return values


def ownership():
    """The optional env adapter owns runtime discovery; cache across one-shot hosts."""
    root = Path(__file__).resolve().parents[3]
    try:
        values = orch_values(root)
        adapter = values.get("ORCH_ENV") or "herdr"
        if not NAME.fullmatch(adapter):
            return {}
        path = root / "env" / adapter / "open_work.py"
        if not path.is_file():
            return {}
        identity = json.dumps(
            [
                str(root),
                str(path.stat().st_mtime_ns),
                str((root / "coordinator/watchers/discovery.py").stat().st_mtime_ns),
                values,
                os.environ.get("HERDR_SOCKET_PATH"),
                os.environ.get("HERDR_BIN_PATH"),
            ],
            sort_keys=True,
        )
        key = hashlib.sha256(identity.encode()).hexdigest()[:20]
        cache = Path(log_path()).parent / "owners.json"
        try:
            saved = json.loads(cache.read_text())
            age = time.time() - saved["time"]
            if (
                saved["key"] == key
                and 0 <= age < OWNER_CACHE_SECONDS
                and (saved["owners"] is None or is_owner_map(saved["owners"]))
            ):
                return saved["owners"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            owners = discover_owners(root, values)
            if not is_owner_map(owners):
                owners = None
        except (
            Exception
        ):  # Cache failed scans without replacing Chrome's last good map.
            owners = None
        temporary = None
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=cache.parent, delete=False
            ) as out:
                temporary = Path(out.name)
                json.dump({"key": key, "time": time.time(), "owners": owners}, out)
            os.replace(out.name, cache)
            # Retire the old per-config cache files; the current format is bounded.
            for old in cache.parent.glob("owners-*.json"):
                if re.fullmatch(r"owners-[0-9a-f]{20}\.json", old.name):
                    old.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return owners
    except Exception:  # Optional runtime discovery must never break GitHub checks.
        return None


MAX_PANE_LOOKUPS = 30
HERDR_TIMEOUT_SECONDS = 5
MAX_COMMAND_BYTES = 1024 * 1024
OWNER = re.compile(r"^[A-Za-z0-9-]{1,39}$")
# git check-ref-format rules for a branch name.
BRANCH = re.compile(
    r"^(?![/.-])(?!.*(?://|\.\.|/\.|@\{|\.lock(?:/|$)))[^\x00-\x20~^:?*\[\\\x7f]+(?<![/.])\Z"
)
PANE_ID = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")
APP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
OPEN = "/usr/bin/open"
MAX_QUESTIONS = 200
AGENT_STATES = {
    "working": "working",
    "idle": "idle",
    "done": "done",
    "blocked": "waiting",
}


def head_key(item, where):
    """repo:branch (repo lowercased) of a {repo: owner/name, branch} item."""
    repo, branch = (
        item.get(key) if isinstance(item, dict) else None for key in ("repo", "branch")
    )
    owner, _, name = repo.partition("/") if isinstance(repo, str) else ("", "", "")
    if not (
        OWNER.match(owner)
        and NAME.match(name)
        and isinstance(branch, str)
        and BRANCH.match(branch)
    ):
        raise RequestError(f"invalid repo or branch{where}")
    return f"{repo.lower()}:{branch}"


def read_bounded(stream):
    stream.seek(0)
    body = stream.read(MAX_COMMAND_BYTES + 1)
    if len(body) > MAX_COMMAND_BYTES:
        raise RequestError(f"command output over {MAX_COMMAND_BYTES} bytes")
    return body.decode(errors="replace")


def bounded_command(deadline):
    """Runs argv without a shell before deadline, reading at most MAX_COMMAND_BYTES of each stream."""

    def command(*args):
        name = Path(args[0]).name
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RequestError(f"{name} timed out after {HERDR_TIMEOUT_SECONDS}s")
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                done = subprocess.run(
                    args,
                    stdout=out,
                    stderr=err,
                    timeout=remaining,
                    check=False,
                    env=dict(os.environ, LC_ALL="C"),
                )
            except subprocess.TimeoutExpired:
                raise RequestError(
                    f"{name} timed out after {HERDR_TIMEOUT_SECONDS}s"
                ) from None
            except FileNotFoundError:
                raise RequestError(f"{name} not found on PATH") from None
            stdout, stderr = read_bounded(out), read_bounded(err)
        if done.returncode and name != "git":
            raise RequestError(f"{name} failed: {stderr.strip()[:200]}")
        if done.returncode:
            raise subprocess.CalledProcessError(done.returncode, args, stdout, stderr)
        return stdout.strip()

    return command


def load_discovery(root):
    sys.path.insert(0, str(root / "coordinator/watchers"))
    return importlib.import_module("discovery")


def pane_rank(row, pane, order, workspaces):
    """Worker workspaces (owned by another live workspace) first, then panes with an agent, then Herdr's list order."""
    worker = row.get("owner") in workspaces and row.get("owner") != row.get("id")
    return (not worker, not pane.get("agent"), order)


def branch_panes(discovery, command, work, keys):
    """The best (rank, workspace, pane) for each repo:branch in keys that a workspace has checked out."""
    panes = {}
    for index, pane in enumerate(work["panes"]):
        panes.setdefault(pane.get("workspace"), []).append((index, pane))
    heads, chosen = {}, {}
    workspaces = {row.get("id") for row in work["workspaces"]}
    for row in work["workspaces"]:
        checkout = row.get("checkout")
        if not checkout or row.get("gone"):
            continue
        if checkout not in heads:
            try:
                heads[checkout] = discovery.checkout_head(command, checkout)
            except discovery.Unavailable:
                heads[checkout] = None
        key = heads[checkout]
        for index, pane in panes.get(row["id"], []) if key in keys else []:
            candidate = (pane_rank(row, pane, index, workspaces), row, pane)
            if key not in chosen or candidate[0] < chosen[key][0]:
                chosen[key] = candidate
    return chosen


def asking_panes(now):
    """Panes with an open orch-signal question: unanswered, not past its deadline, not being cleaned up."""
    signals = importlib.import_module("worker_signals")
    asking = set()
    for path in sorted(signals.directory().glob("signal-*.json"))[:MAX_QUESTIONS]:
        try:
            with path.open("rb") as stream:
                body = stream.read(MAX_REQUEST_BYTES + 1)
            record = json.loads(body) if len(body) <= MAX_REQUEST_BYTES else None
        except (OSError, ValueError):
            continue
        if (
            isinstance(record, dict)
            and record.get("kind") == "question"
            and not record.get("cleanup")
            and isinstance(record.get("deadline"), (int, float))
            and now < record["deadline"]
            and not path.with_suffix(".answer").exists()
        ):
            asking.add((record.get("workspace"), record.get("pane")))
    return asking


def agent_state(pane, asking):
    """working, idle, done or waiting; None without an agent or for a state Herdr cannot classify."""
    if not pane.get("agent"):
        return None
    if (pane.get("workspace"), pane.get("pane")) in asking:
        return "waiting"
    return AGENT_STATES.get(pane.get("status"))


def resolve_panes(keys):
    """The chosen pane per repo:branch and the Herdr binary, from one Herdr snapshot."""
    root = Path(__file__).resolve().parents[3]
    try:
        values = orch_values(root)
    except Exception as error:
        raise RequestError(f"Cannot load settings: {type(error).__name__}") from None
    if (values.get("ORCH_ENV") or "herdr") != "herdr":
        raise RequestError('pane jump needs install.env = "herdr"')
    discovery = load_discovery(root)
    command = bounded_command(time.monotonic() + HERDR_TIMEOUT_SECONDS)
    with discovery_settings(values):
        try:
            work = discovery.open_work(command, roots=False)
            herdr = discovery.adapter().herdr_bin()
        except discovery.Unavailable as error:
            raise RequestError(f"Herdr unavailable: {error}") from None
        chosen = branch_panes(discovery, command, work, keys)
    return chosen, herdr, values, work


def panes(request):
    prs = request.get("prs")
    if not isinstance(prs, list) or not 1 <= len(prs) <= MAX_PANE_LOOKUPS:
        raise RequestError(f"prs must list 1 to {MAX_PANE_LOOKUPS} branches")
    keys = [head_key(pr, f" at index {i}") for i, pr in enumerate(prs)]
    chosen, _, _, _ = resolve_panes(set(keys))
    try:
        asking = asking_panes(time.time())
    except Exception:  # Question records only refine the state.
        asking = set()
    return {
        "ok": True,
        "panes": {
            key: {"state": agent_state(chosen[key][2], asking)}
            if key in chosen
            else None
            for key in keys
        },
    }


def focus_argv(herdr, row, pane):
    """agent focus for a pane with an agent, else workspace focus."""
    target = pane.get("pane") if pane.get("agent") else row.get("id")
    if not isinstance(target, str) or not PANE_ID.match(target):
        raise RequestError("Herdr returned an invalid pane or workspace id")
    if pane.get("agent"):
        return [herdr, "agent", "focus", target]
    return [herdr, "workspace", "focus", target]


def focus_target(row, pane, work):
    """Route owned workers to their architect using the existing discovery snapshot."""
    owner = next(
        (item for item in work["workspaces"] if item.get("id") == row.get("owner")),
        None,
    )
    if owner is None or owner.get("id") == row.get("id"):
        return row, pane
    architect = next(
        (
            item
            for item in work["panes"]
            if item.get("workspace") == owner["id"]
            and item.get("role") == "architect"
            and item.get("agent")
        ),
        {},
    )
    return owner, architect


def raise_terminal(values):
    """Brings pr_tabs.terminal_app to the front on macOS; nothing when unset."""
    app = values.get("ORCH_PR_TABS_TERMINAL_APP") or ""
    if not app or sys.platform != "darwin":
        return
    if not APP_NAME.match(app):
        raise RequestError("ORCH_PR_TABS_TERMINAL_APP must be an application name")
    bounded_command(time.monotonic() + HERDR_TIMEOUT_SECONDS)(OPEN, "-a", app)


def focus_pane(request):
    key = head_key(request, "")
    chosen, herdr, values, work = resolve_panes({key})
    if key not in chosen:
        raise RequestError(f"no Herdr pane has {key} checked out")
    _, row, pane = chosen[key]
    row, pane = focus_target(row, pane, work)
    with discovery_settings(values):
        bounded_command(time.monotonic() + HERDR_TIMEOUT_SECONDS)(
            *focus_argv(herdr, row, pane)
        )
    raise_terminal(values)
    return {"ok": True, "pane": pane.get("pane")}


HANDLERS = {
    "states": states,
    "search": search,
    "settings": settings,
    "panes": panes,
    "focusPane": focus_pane,
}
LOG_BYTES = 256 * 1024


def log_path():
    return os.environ.get("PR_TABS_LOG") or os.path.expanduser(
        "~/.claude/chrome-pr-tabs/host.log"
    )


def log(request, reply):
    """Appends one line per request; a line that would pass LOG_BYTES first moves the file to <log>.1."""
    summary = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "type": request.get("type") if request else None,
    }
    if reply.get("ok") and "prs" in reply and isinstance(reply["prs"], dict):
        summary["states"] = {
            key: (node or {}).get("state") for key, node in reply["prs"].items()
        }
    elif reply.get("ok") and isinstance(reply.get("panes"), dict):
        summary["found"] = sum(1 for pane in reply["panes"].values() if pane)
    elif reply.get("ok") and "pane" in reply:
        summary["pane"] = reply["pane"]
    elif reply.get("ok"):
        summary["hits"] = len(reply.get("prs") or [])
    else:
        summary["error"] = reply.get("error")
    entry = (json.dumps(summary) + "\n").encode()
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) + len(entry) > LOG_BYTES:
            os.replace(path, path + ".1")
        with open(path, "ab") as out:
            out.write(entry)
    except OSError:
        pass


def main():
    request = None
    try:
        request = read_request(sys.stdin.buffer)
        kind = request.get("type")
        handler = HANDLERS.get(kind) if isinstance(kind, str) else None
        if handler is None:
            raise RequestError(f"unknown request type: {kind!r}")
        reply = handler(request)
    except RequestError as error:
        reply = {"ok": False, "error": str(error)}
    write_reply(sys.stdout.buffer, reply)
    log(request, reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
