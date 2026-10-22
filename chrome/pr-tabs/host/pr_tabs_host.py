#!/usr/bin/env python3
"""Chrome native messaging host for PR tabs: one request, at most one gh call, one reply, then exit."""

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
            '. "$1"; orch_load_config; printf "%s\\0" "$ORCH_PR_TABS_ORG" "$ORCH_PR_TABS_DEFAULT_REPO" "$ORCH_PR_TABS_OPEN_ALL"',
            "orch-config",
            str(library),
        ],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    return result.stdout.split("\0")[:3]


def settings(request):
    try:
        org, repo, query = project_settings()
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
    return {
        "ok": True,
        "org": org,
        "defaultRepo": repo,
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


def ownership():
    """The optional env adapter owns runtime discovery; cache across one-shot hosts."""
    root = Path(__file__).resolve().parents[3]
    try:
        spec = importlib.util.spec_from_file_location(
            "orch_settings", root / "lib/orch_settings.py"
        )
        config = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = config
        spec.loader.exec_module(config)
        values = config.load_global(
            os.environ.get(
                "ORCH_CONFIG", str(Path.home() / ".claude/orchestration.toml")
            )
        )
        values.update(
            {key: value for key, value in os.environ.items() if key.startswith("ORCH_")}
        )
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


HANDLERS = {"states": states, "search": search, "settings": settings}
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
