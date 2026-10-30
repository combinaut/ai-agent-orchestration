#!/usr/bin/env python3
"""Lists or watches PRs that request your review, and plans Codex auto-reviews.

  prs.py list          [scope] [--actionable]     markdown tables + snapshot for watch
  prs.py watch         [scope] [--interval S]     one line per change, forever
  prs.py dispatch-queue [scope]                   JSON: Ready PRs needing (re)review
  prs.py mark-dispatched --url U --sha S [--state pending|blocked|done] [--reason R] [--agent N] [--pane P]
  prs.py claim-dispatched --url U --sha S [--agent N]   marks S pending; exit 3 when S is already pending, blocked, or done
  prs.py release-dispatched --url U --sha S       drops S while it is pending or blocked
  prs.py remote --cwd C --repo R                  the remote of clone C (origin or upstream) that is the GitHub repo R
  prs.py review-ids --login L                     stdin: gh api --paginate --slurp .../reviews; ids of L's submitted reviews
  prs.py last-review --login L --sha S --seen IDS stdin: the same; L's newest submitted review of S not in IDS

Scope: --repo owner/name | --org ORG | --all. Default: cwd's GitHub repo, else watchers.pr_scope, else --all.
"""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
import github_poll  # noqa: E402
import orch_settings  # noqa: E402

BOT_TITLES = ("Automatic Update of README", "Changes by create-pull-request")
BOT_LOGINS = {"copilot-pull-request-reviewer", "copilot", "github-actions"}
STATE_DIR = Path.home() / ".cache" / "review-prs"
DISPATCH_FILE = STATE_DIR / "dispatched.json"
GH_TIMEOUT_S = 45
DEPARTURE_CHECKS_MAX = 10
DEPARTURE_MISSES_MAX = 5
PROJECTS = Path.home() / "Projects"
OPEN_WORK = Path.home() / ".claude" / "coordinator" / "open-work.json"

QUERY = """
query($q: String!, $me: String, $endCursor: String) {
  search(query: $q, type: ISSUE, first: 50, after: $endCursor) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      url number title isDraft updatedAt headRefOid
      author { __typename login }
      repository { nameWithOwner }
      mergeable mergeStateStatus reviewDecision
      reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved } }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      reviewRequests(first: 20) { nodes { requestedReviewer {
        __typename ... on User { login } ... on Team { slug } ... on Bot { login } } } }
      myReviews: reviews(last: 1, author: $me, states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]) {
        nodes { commit { oid } } }
    } }
  }
}"""

DEPARTURE_QUERY = """
query($url: URI!, $me: String) {
  resource(url: $url) {
    ... on PullRequest {
      state headRefOid
      reviewRequests(first: 20) { nodes { requestedReviewer {
        __typename ... on User { login } ... on Team { slug } ... on Bot { login } } } }
      myReviews: reviews(last: 1, author: $me, states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]) {
        nodes { commit { oid } } }
    }
  }
}"""


def setting(name):
    """An ORCH_* global setting: the environment, else ~/.claude/orchestration.toml (or $ORCH_CONFIG)."""
    if name in os.environ:
        return os.environ[name]
    config = os.environ.get("ORCH_CONFIG") or str(
        Path.home() / ".claude" / "orchestration.toml"
    )
    try:
        return orch_settings.load_global(config).get(name, "")
    except orch_settings.ConfigError as error:
        sys.exit(str(error))


def resolved():
    config = os.environ.get("ORCH_CONFIG") or str(
        Path.home() / ".claude" / "orchestration.toml"
    )
    try:
        return orch_settings.resolved_global(config, os.environ)
    except orch_settings.ConfigError as error:
        sys.exit(str(error))


def codex_enabled():
    return "codex" in resolved()["ORCH_PROVIDERS"].split(",")


CODEX_OFF = "codex is off in providers.enabled (ORCH_PROVIDERS); Codex reviews are not dispatched"


def worker_model():
    if not codex_enabled():
        sys.exit(CODEX_OFF)
    model = orch_settings.tier_model(resolved(), "ORCH_CODEX_WORKER_MODEL")
    if not model:
        sys.exit(
            "set models.codex_worker or models.codex_smart (ORCH_CODEX_WORKER_MODEL, ORCH_CODEX_SMART_MODEL) in ~/.claude/orchestration.toml"
        )
    return model


def gh(*args):
    try:
        result = github_poll.run(
            ["gh", *args],
            lambda env, pass_fds: subprocess.run(
                ["gh", *args],
                capture_output=True,
                text=True,
                timeout=GH_TIMEOUT_S,
                env=env,
                pass_fds=pass_fds,
            ),
        )
    except OSError as error:
        raise RuntimeError(str(error)) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh timed out after {GH_TIMEOUT_S}s") from None
    if result.returncode != 0:
        raise RuntimeError((result.stderr.strip().splitlines() or ["gh failed"])[-1])
    return result.stdout


def viewer_login():
    return gh(
        "api",
        "graphql",
        "-f",
        "query={ viewer { login } }",
        "--jq",
        ".data.viewer.login",
    ).strip()


def resolve_scope(args):
    if args.all:
        return "", "all repos"
    if args.org and not args.repo:
        return f"org:{args.org}", f"org {args.org}"
    repo = args.repo
    if repo and "/" not in repo:
        if not args.org:
            sys.exit("--repo without an owner needs --org, or pass owner/name")
        repo = f"{args.org}/{repo}"
    if not repo:
        try:
            repo = gh(
                "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"
            ).strip()
        except RuntimeError:
            scope = setting("ORCH_WATCHERS_PR_SCOPE")
            return scope, scope or "all repos"
    return f"repo:{repo}", repo


def search(q, me):
    raw = gh(
        "api",
        "graphql",
        "--paginate",
        "--slurp",
        "-H",
        "Accept: application/vnd.github.merge-info-preview+json",
        "-f",
        f"query={QUERY}",
        "-f",
        f"q={q}",
        "-f",
        f"me={me}",
    )
    return [n for page in json.loads(raw) for n in page["data"]["search"]["nodes"] if n]


def my_review_sha(pr):
    nodes = (pr.get("myReviews") or {}).get("nodes") or []
    return ((nodes[-1].get("commit") or {}).get("oid")) if nodes else None


def dispatched_records(pr, dispatched):
    """The valid per-head records for a PR from locally dispatched state."""
    entry = dispatched.get(pr["url"]) if isinstance(dispatched, dict) else None
    return heads(entry)


def dispatched_current_head_done(pr, dispatched):
    """Whether a completed local dispatch already covers this PR head."""
    head = pr.get("headRefOid") or ""
    return (
        bool(head)
        and dispatched_records(pr, dispatched).get(head, {}).get("state") == "done"
    )


def dispatched_done_on_other_head(pr, dispatched):
    """Whether a completed local dispatch reviewed a SHA other than this head."""
    head = pr.get("headRefOid") or ""
    records = dispatched_records(pr, dispatched)
    if not head or dispatched_current_head_done(pr, dispatched):
        return False
    return any(
        sha != head and record.get("state") == "done" for sha, record in records.items()
    )


def fetch(scope):
    login = viewer_login()
    me = login.lower()
    requested = search(
        f"is:pr is:open review-requested:@me archived:false {scope}".strip(), login
    )
    # PRs I reviewed whose head moved since, with no new review request.
    reviewed = search(
        f"is:pr is:open reviewed-by:@me -review-requested:@me archived:false {scope}".strip(),
        login,
    )
    dispatched = load_dispatched()
    moved = [
        n
        for n in reviewed
        if not dispatched_current_head_done(n, dispatched)
        and (
            (my_review_sha(n) and my_review_sha(n) != n.get("headRefOid"))
            or dispatched_done_on_other_head(n, dispatched)
        )
    ]
    seen = set()
    nodes = []
    for n in requested + moved:
        if n["url"] not in seen:
            seen.add(n["url"])
            nodes.append(n)
    nodes = [n for n in nodes if not n["title"].startswith(BOT_TITLES)]
    nodes = [
        n
        for n in nodes
        if not (n.get("author") and (n["author"].get("login") or "").lower() == me)
    ]
    return sorted(
        nodes, key=lambda p: (p["repository"]["nameWithOwner"], -p["number"])
    ), login


def is_bot(typename, login):
    return typename == "Bot" or (login or "").lower() in BOT_LOGINS


def ci_state(pr):
    ci = (pr["commits"]["nodes"] or [{}])[0].get("commit", {}).get(
        "statusCheckRollup"
    ) or {}
    return ci.get("state")


def age(pr):
    raw = pr.get("updatedAt")
    if not raw:
        return ""
    then = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - then).days
    if days <= 0:
        return "today"
    if days == 1:
        return "1d"
    if days < 14:
        return f"{days}d"
    if days < 60:
        return f"{days // 7}w"
    return f"{days // 30}mo"


def author_name(pr):
    a = pr.get("author") or {}
    login = a.get("login") or "unknown"
    if is_bot(a.get("__typename"), login):
        return f"{login} (bot)"
    return login


def status(pr):
    parts = [author_name(pr), age(pr)]
    ci = ci_state(pr)
    if ci in ("FAILURE", "ERROR"):
        parts.append("CI failing")
    elif ci in ("PENDING", "EXPECTED"):
        parts.append("CI running")
    threads = pr["reviewThreads"]
    open_threads = sum(1 for t in threads["nodes"] if not t["isResolved"])
    if (threads.get("pageInfo") or {}).get("hasNextPage"):
        parts.append(f"{open_threads}+ threads (first 100 checked)")
    elif open_threads:
        parts.append(f"{open_threads} thread{'s' if open_threads > 1 else ''}")
    if pr["mergeable"] == "CONFLICTING":
        parts.append("conflicts")
    elif pr["mergeable"] == "UNKNOWN":
        parts.append("merge state unknown")
    return " · ".join(p for p in parts if p)


def bucket(pr):
    if pr["isDraft"]:
        return "draft"
    if pr["mergeable"] == "CONFLICTING":
        return "conflict"
    if pr["mergeable"] == "UNKNOWN":
        return "unknown"
    ci = ci_state(pr)
    if ci in ("FAILURE", "ERROR"):
        return "ci_fail"
    if ci in ("PENDING", "EXPECTED"):
        return "ci_run"
    return "ready"


def snapshot(nodes):
    return {
        p["url"]: {
            "title": p["title"],
            "draft": p["isDraft"],
            "author": author_name(p),
            "ci": ci_state(p),
            "conflict": None
            if p["mergeable"] == "UNKNOWN"
            else p["mergeable"] == "CONFLICTING",
            "sha": p.get("headRefOid") or "",
        }
        for p in nodes
    }


def state_file(scope):
    return STATE_DIR / (
        hashlib.sha1(f"review:{scope}".encode()).hexdigest()[:12] + ".json"
    )


def load_state(scope):
    path = state_file(scope)
    return json.loads(path.read_text()) if path.exists() else None


def save_state(scope, snap):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state_file(scope)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(snap, indent=1))
    os.replace(tmp, path)


def read_dispatched():
    """Reads valid dispatch state; only a missing file is an empty ledger."""
    try:
        data = json.loads(DISPATCH_FILE.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("dispatched state must be a JSON object")
    return data


def load_dispatched():
    """Best-effort dispatch state for discovery; invalid state means no fallback."""
    try:
        return read_dispatched()
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def update_dispatched(change):
    """Applies change(data) under an exclusive lock and replaces the file atomically."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "dispatched.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_dispatched()
        change(data)
        tmp = DISPATCH_FILE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, DISPATCH_FILE)


def open_work_clones():
    try:
        return [Path(c) for c in json.loads(OPEN_WORK.read_text()).get("clones", [])]
    except (OSError, ValueError, AttributeError):
        return []


def current_repo_root():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return (
        [Path(out.stdout.strip())] if out.returncode == 0 and out.stdout.strip() else []
    )


def resolve_cwd(name_with_owner):
    owner, name = name_with_owner.split("/", 1)
    candidates = [
        *open_work_clones(),
        PROJECTS / owner / name,
        PROJECTS / name,
        *current_repo_root(),
    ]
    for remote in ("origin", "upstream"):
        for clone in candidates:
            try:
                if (clone / ".git").exists() and orch_settings.remote_matches(
                    clone, name_with_owner, remote
                ):
                    return str(clone)
            except (OSError, subprocess.SubprocessError):
                continue
    return None


def cmd_remote(args, scope, label):
    """The remote of --cwd that is the GitHub repo --repo; exit 1 when there is none."""
    try:
        remote = orch_settings.matching_remote(args.cwd, args.repo)
    except (OSError, subprocess.SubprocessError):
        remote = None
    if not remote:
        sys.exit(f"no remote of {args.cwd} is {args.repo}")
    print(remote)


def agent_name(repo, number, sha):
    """Unique per repo, PR and head: rev<number>-<sha7>-<repo hash>."""
    return (
        f"rev{number}-{sha[:7]}-{hashlib.sha1(repo.lower().encode()).hexdigest()[:6]}"[
            :32
        ]
    )


def needs_dispatch(pr, dispatched):
    if bucket(pr) != "ready":
        return False, None
    sha = pr.get("headRefOid") or ""
    entry = dispatched.get(pr["url"]) if isinstance(dispatched, dict) else None
    known = heads(entry)
    if sha and sha in known:
        return False, None
    kind = "re-review" if known or my_review_sha(pr) else "review"
    return True, kind


def dispatch_entry(pr, kind, model):
    repo = pr["repository"]["nameWithOwner"]
    return {
        "url": pr["url"],
        "number": pr["number"],
        "title": pr["title"],
        "author": author_name(pr),
        "repo": repo,
        "sha": pr.get("headRefOid") or "",
        "kind": kind,
        "agent": agent_name(repo, pr["number"], pr.get("headRefOid") or ""),
        "cwd": resolve_cwd(repo),
        "model": model,
    }


def blocker(snap):
    """What keeps a snapshot entry from review, in bucket() order; None when it is ready."""
    if snap["draft"]:
        return "draft"
    if snap["conflict"] is True:
        return "conflicting"
    if snap["conflict"] is None:
        return "merge state unknown"
    if snap["ci"] in ("FAILURE", "ERROR"):
        return "CI failing"
    if snap["ci"] in ("PENDING", "EXPECTED"):
        return "waiting on CI"
    return None


def departure(url, login):
    """Returns a confirmed departure state, or None while the PR is still watched."""
    raw = gh(
        "api",
        "graphql",
        "-f",
        f"query={DEPARTURE_QUERY}",
        "-f",
        f"url={url}",
        "-f",
        f"me={login}",
    )
    pr = json.loads(raw)["data"]["resource"]
    if not pr:
        raise RuntimeError("PR lookup returned no pull request")
    pr["url"] = url
    state = pr.get("state")
    if state not in ("OPEN", "CLOSED", "MERGED"):
        raise RuntimeError("PR lookup returned invalid state")
    if state != "OPEN":
        return state
    for node in (pr.get("reviewRequests") or {}).get("nodes") or []:
        request = node.get("requestedReviewer") or node
        if (request.get("login") or "").lower() == login.lower():
            return None
        if request.get("slug") and not request.get("login"):
            return None
    dispatched = read_dispatched()
    if not dispatched_current_head_done(pr, dispatched) and (
        (my_review_sha(pr) and my_review_sha(pr) != pr.get("headRefOid"))
        or dispatched_done_on_other_head(pr, dispatched)
    ):
        return None
    return "cleared"


def last_known_conflict(entry):
    conflict = entry.get("conflict")
    return conflict if conflict is not None else entry.get("last_conflict")


def changes(before, after, login=None):
    lines = []
    still_open = {}
    departure_login = login
    for url, now in after.items():
        was = before.get(url)
        if was is None:
            kind = blocker(now) or "ready"
            lines.append(f"new: {url} — {now['title']} by {now['author']} ({kind})")
            continue
        parts = []
        if was["draft"] and not now["draft"]:
            parts.append("now ready for review")
        elif not was["draft"] and now["draft"]:
            parts.append("back to draft")
        if was.get("sha") and now.get("sha") and was["sha"] != now["sha"]:
            parts.append(f"new commits ({now['sha'][:7]})")
        if was["ci"] != now["ci"]:
            label = {
                "SUCCESS": "CI green",
                "FAILURE": "CI failing",
                "ERROR": "CI failing",
                "PENDING": "CI running",
                "EXPECTED": "CI running",
            }.get(now["ci"] or "", f"CI {now['ci'] or 'cleared'}")
            parts.append(label)
        was_conflict = last_known_conflict(was)
        ready_now = not now["draft"] and now["ci"] in (None, "SUCCESS")
        readiness_changed = (
            was["draft"]
            or was_conflict is not False
            or was["ci"] not in (None, "SUCCESS")
            or (was.get("sha") != now.get("sha"))
        )
        deferred_ready = False
        if now["conflict"] is None:
            kept = dict(now)
            if was_conflict is not None:
                kept["last_conflict"] = was_conflict
            if ready_now and (was.get("pending_ready") or readiness_changed):
                kept["pending_ready"] = True
            after[url] = kept
        elif was.get("pending_ready") and ready_now and now["conflict"] is False:
            deferred_ready = True
            parts.append("now ready for review")
        elif was_conflict is not True and now["conflict"] is True:
            parts.append("now conflicts")
        elif was_conflict is True and now["conflict"] is False:
            parts.append("conflicts cleared")
        elif (
            was["conflict"] is None
            and was_conflict is None
            and now["conflict"] is False
        ):
            parts.append("merge state known")
        if parts:
            became_ready = deferred_ready or (
                not now["draft"]
                and now["conflict"] is False
                and now["ci"] in (None, "SUCCESS")
                and (
                    was["draft"]
                    or was["conflict"] is not False
                    or was["ci"] not in (None, "SUCCESS")
                    or (was.get("sha") != now.get("sha"))
                )
            )
            prefix = "ready" if became_ready else "changed"
            lines.append(f"{prefix}: {url} — {now['title']}: {', '.join(parts)}")
    missing = sorted(
        ((url, was) for url, was in before.items() if url not in after),
        key=lambda entry: (-entry[1].get("misses", 0), entry[0]),
    )
    for url, was in missing[DEPARTURE_CHECKS_MAX:]:
        still_open[url] = was
    for url, was in missing[:DEPARTURE_CHECKS_MAX]:

        def keep_missed():
            misses = was.get("misses", 0) + 1
            if misses >= DEPARTURE_MISSES_MAX:
                lines.append(
                    f"cleared: {url} — {was['title']} (no longer requesting you)"
                )
            else:
                still_open[url] = {**was, "misses": misses}

        departure_login = departure_login or viewer_login()
        st = departure(url, departure_login)
        if st is None:
            keep_missed()
            continue
        if st == "cleared":
            lines.append(f"cleared: {url} — {was['title']} (no longer requesting you)")
            continue
        lines.append(f"{st.lower()}: {url} — {was['title']}")
    return lines, still_open


TITLE_MAX = 60


def table(title, prs):
    if not prs:
        return []
    lines = [
        f"**{title} ({len(prs)})**",
        "",
        "| PR | Title | Author · status |",
        "|---|---|---|",
    ]
    for p in prs:
        name = p["title"].replace("|", "/")
        if len(name) > TITLE_MAX:
            name = name[: TITLE_MAX - 1].rstrip() + "…"
        lines.append(f"| {p['url']} | {name} | {status(p)} |")
    return lines + [""]


def counts(nodes):
    actionable = sum(1 for p in nodes if bucket(p) == "ready")
    return f"{actionable} actionable, {len(nodes)} total"


def snapshot_counts(snap):
    actionable = sum(1 for entry in snap.values() if blocker(entry) is None)
    return f"{actionable} actionable, {len(snap)} total"


def cmd_list(args, scope, label):
    nodes, _login = fetch(scope)
    if any(p["mergeable"] == "UNKNOWN" for p in nodes):
        time.sleep(3)
        nodes, _login = fetch(scope)
    save_state(scope, snapshot(nodes))
    order = ("ready", "unknown", "ci_run", "ci_fail", "conflict", "draft")
    titles = {
        "ready": "🔔 Ready for your review",
        "unknown": "❔ Merge state unknown",
        "ci_run": "⏳ Waiting on CI",
        "ci_fail": "🔴 CI failing",
        "conflict": "🔴 Conflicting",
        "draft": "📝 Drafts",
    }
    groups = {b: [p for p in nodes if bucket(p) == b] for b in order}
    out = [
        f"**{len(nodes)} PRs requesting your review in {label}** — {counts(nodes)}",
        "",
    ]
    for b in order:
        if args.actionable and b != "ready":
            continue
        out += table(titles[b], groups[b])
    print("\n".join(out).rstrip())


def cmd_watch(args, scope, _label):
    fails = 0
    before = load_state(scope)
    while True:
        try:
            nodes, login = fetch(scope)
            after = snapshot(nodes)
            if before is not None:
                lines, still_open = changes(before, after, login)
                after = {**after, **still_open}
                if lines:
                    print(
                        "\n".join([*lines, f"now: {snapshot_counts(after)}"]),
                        flush=True,
                    )
            before = after
            save_state(scope, after)
            if fails >= 3:
                print("recovered: PR fetch works again", flush=True)
            fails = 0
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            fails += 1
            if fails == 3:
                print(f"PR fetch failing 3x in a row: {error}", flush=True)
        time.sleep(args.interval)


def cmd_dispatch_queue(args, scope, label):
    if not codex_enabled():
        print(
            json.dumps(
                {
                    "scope": label,
                    "model": "",
                    "queue": [],
                    "skipped": 0,
                    "note": CODEX_OFF,
                },
                indent=2,
            )
        )
        return
    model = worker_model()
    nodes, _login = fetch(scope)
    if any(p["mergeable"] == "UNKNOWN" for p in nodes):
        time.sleep(3)
        nodes, _login = fetch(scope)
    dispatched = load_dispatched()
    queue = []
    blocked = []
    for pr in nodes:
        ok, kind = needs_dispatch(pr, dispatched)
        if ok:
            queue.append(dispatch_entry(pr, kind, model))
        else:
            record = dispatched_records(pr, dispatched).get(pr.get("headRefOid") or "")
            if (record or {}).get("state") == "blocked":
                blocked.append(
                    {
                        "url": pr["url"],
                        "sha": pr.get("headRefOid") or "",
                        "reason": record.get("reason") or "unknown",
                    }
                )
    print(
        json.dumps(
            {
                "scope": label,
                "model": model,
                "queue": queue,
                "blocked": blocked,
                "skipped": sum(1 for p in nodes if bucket(p) == "ready") - len(queue),
            },
            indent=2,
        )
    )


HEADS_KEPT = 20


def heads(entry):
    """sha -> {state, agent, pane, at} for one PR; a mark from before per-head records counts as done."""
    if not isinstance(entry, dict):
        return {}
    records = entry.get("heads")
    if isinstance(records, dict):
        return {
            sha: record for sha, record in records.items() if isinstance(record, dict)
        }
    if records is not None:
        return {}
    return (
        {
            entry["sha"]: {
                "state": "done",
                **{k: entry.get(k) for k in ("agent", "pane", "at")},
            }
        }
        if entry.get("sha")
        else {}
    )


def set_head(data, url, sha, record):
    """Stores one head's record; the newest HEADS_KEPT done heads and active records stay."""
    known = dict(heads(data.get(url) or {}))
    known[sha] = record
    done = sorted(
        (s for s, h in known.items() if h.get("state") == "done"),
        key=lambda s: known[s].get("at") or "",
    )
    for old in done[:-HEADS_KEPT]:
        del known[old]
    data[url] = {"heads": known}


def head_record(args, state):
    record = {
        "state": state,
        "agent": args.agent,
        "pane": args.pane,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if args.reason:
        record["reason"] = args.reason
    return record


def cmd_claim_dispatched(args, _scope, _label):
    if not args.url or not args.sha:
        sys.exit("claim-dispatched needs --url and --sha")
    taken = []

    def claim(data):
        if args.sha in heads(data.get(args.url) or {}):
            taken.append(True)
            return
        set_head(data, args.url, args.sha, head_record(args, "pending"))

    update_dispatched(claim)
    print(f"{'TAKEN' if taken else 'CLAIMED'} {args.sha}")
    if taken:
        sys.exit(3)


def cmd_mark_dispatched(args, _scope, _label):
    if not args.url or not args.sha:
        sys.exit("mark-dispatched needs --url and --sha")
    record = head_record(args, args.state)
    update_dispatched(lambda data: set_head(data, args.url, args.sha, record))
    print(json.dumps(record))


def cmd_release_dispatched(args, _scope, _label):
    if not args.url or not args.sha:
        sys.exit("release-dispatched needs --url and --sha")

    def release(data):
        known = dict(heads(data.get(args.url) or {}))
        if (known.get(args.sha) or {}).get("state") not in ("pending", "blocked"):
            return
        del known[args.sha]
        if known:
            data[args.url] = {"heads": known}
        else:
            data.pop(args.url, None)

    update_dispatched(release)


def submitted_reviews(login):
    return [
        r
        for page in json.load(sys.stdin)
        for r in page
        if (r.get("user") or {}).get("login") == login and r.get("submitted_at")
    ]


def cmd_review_ids(args, _scope, _label):
    print(",".join(str(r["id"]) for r in submitted_reviews(args.login)))


def cmd_last_review(args, _scope, _label):
    """Prints LASTREVIEW for the newest submitted review by --login of --sha whose id is not in --seen."""
    seen = set(filter(None, (args.seen or "").split(",")))
    mine = [
        r
        for r in submitted_reviews(args.login)
        if r.get("commit_id") == args.sha and str(r.get("id")) not in seen
    ]
    if mine:
        r = max(mine, key=lambda r: r["submitted_at"])
        print(f"LASTREVIEW {r['state']} {r['submitted_at']} {r['html_url']}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "mode",
        choices=[
            "list",
            "watch",
            "dispatch-queue",
            "claim-dispatched",
            "mark-dispatched",
            "release-dispatched",
            "remote",
            "review-ids",
            "last-review",
        ],
        nargs="?",
        default="list",
    )
    parser.add_argument("--repo")
    parser.add_argument("--cwd", help="remote: the clone")
    parser.add_argument("--org")
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--actionable", action="store_true", help="list: only Ready-for-review PRs"
    )
    parser.add_argument(
        "--interval", type=int, default=120, help="watch: seconds between checks"
    )
    parser.add_argument("--login", help="last-review: reviewer login")
    parser.add_argument(
        "--seen",
        help="last-review: comma-separated review ids from before the dispatch",
    )
    parser.add_argument("--url", help="mark-dispatched: PR URL")
    parser.add_argument("--sha", help="mark-dispatched: head SHA that was dispatched")
    parser.add_argument(
        "--state",
        choices=["pending", "blocked", "done"],
        default="done",
        help="mark-dispatched: pending while the review runs, blocked when it cannot start, done once posted",
    )
    parser.add_argument(
        "--reason", help="mark-dispatched: reason for a blocked dispatch"
    )
    parser.add_argument("--agent", help="mark-dispatched: herdr agent name")
    parser.add_argument("--pane", help="mark-dispatched: herdr pane id")
    args = parser.parse_args()
    scope, label = (
        resolve_scope(args)
        if args.mode in ("list", "watch", "dispatch-queue")
        else ("", "")
    )
    {
        "list": cmd_list,
        "watch": cmd_watch,
        "dispatch-queue": cmd_dispatch_queue,
        "claim-dispatched": cmd_claim_dispatched,
        "mark-dispatched": cmd_mark_dispatched,
        "release-dispatched": cmd_release_dispatched,
        "remote": cmd_remote,
        "review-ids": cmd_review_ids,
        "last-review": cmd_last_review,
    }[args.mode](args, scope, label)


if __name__ == "__main__":
    main()
