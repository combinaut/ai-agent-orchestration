#!/usr/bin/env python3
"""Lists or watches PRs that request your review, and plans Codex auto-reviews.

  prs.py list          [scope] [--actionable]     markdown tables + snapshot for watch
  prs.py watch         [scope] [--interval S]     one line per change, forever
  prs.py dispatch-queue [scope]                   JSON: Ready PRs needing (re)review
  prs.py mark-dispatched --url U --sha S [--state pending|done] [--agent N] [--pane P]
  prs.py claim-dispatched --url U --sha S [--agent N]   marks S pending; exit 3 when S is already claimed
  prs.py release-dispatched --url U --sha S       drops a pending mark of S, back to the mark before it

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
import orch_settings  # noqa: E402

BOT_TITLES = ("Automatic Update of README", "Changes by create-pull-request")
BOT_LOGINS = {"copilot-pull-request-reviewer", "copilot", "github-actions"}
STATE_DIR = Path.home() / ".cache" / "review-prs"
DISPATCH_FILE = STATE_DIR / "dispatched.json"
GH_TIMEOUT_S = 45
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
      reviewThreads(first: 100) { nodes { isResolved } }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      reviewRequests(first: 20) { nodes { requestedReviewer {
        __typename ... on User { login } ... on Team { slug } ... on Bot { login } } } }
      myReviews: reviews(last: 1, author: $me, states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]) {
        nodes { commit { oid } } }
    } }
  }
}"""


def setting(name):
    """An ORCH_* global setting: the environment, else ~/.claude/orchestration.toml (or $ORCH_CONFIG)."""
    if name in os.environ:
        return os.environ[name]
    config = os.environ.get("ORCH_CONFIG") or str(Path.home() / ".claude" / "orchestration.toml")
    try:
        return orch_settings.load_global(config).get(name, "")
    except orch_settings.ConfigError as error:
        sys.exit(str(error))


def worker_model():
    model = setting("ORCH_CODEX_WORKER_MODEL")
    if not model or model.startswith("<"):
        sys.exit("set models.codex_worker (ORCH_CODEX_WORKER_MODEL) in ~/.claude/orchestration.toml")
    return model


def gh(*args):
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh timed out after {GH_TIMEOUT_S}s") from None
    if result.returncode != 0:
        raise RuntimeError((result.stderr.strip().splitlines() or ["gh failed"])[-1])
    return result.stdout


def viewer_login():
    return gh("api", "graphql", "-f", "query={ viewer { login } }",
              "--jq", ".data.viewer.login").strip()


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
            repo = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
        except RuntimeError:
            scope = setting("ORCH_WATCHERS_PR_SCOPE")
            return scope, scope or "all repos"
    return f"repo:{repo}", repo


def search(q, me):
    raw = gh("api", "graphql", "--paginate", "--slurp",
             "-H", "Accept: application/vnd.github.merge-info-preview+json",
             "-f", f"query={QUERY}", "-f", f"q={q}", "-f", f"me={me}")
    return [n for page in json.loads(raw) for n in page["data"]["search"]["nodes"] if n]


def my_review_sha(pr):
    nodes = (pr.get("myReviews") or {}).get("nodes") or []
    return ((nodes[-1].get("commit") or {}).get("oid")) if nodes else None


def fetch(scope):
    login = viewer_login()
    me = login.lower()
    requested = search(f"is:pr is:open review-requested:@me archived:false {scope}".strip(), login)
    # PRs I reviewed whose head moved since, with no new review request.
    reviewed = search(f"is:pr is:open reviewed-by:@me -review-requested:@me archived:false {scope}".strip(), login)
    moved = [n for n in reviewed
             if my_review_sha(n) and my_review_sha(n) != n.get("headRefOid")]
    seen = set()
    nodes = []
    for n in requested + moved:
        if n["url"] not in seen:
            seen.add(n["url"])
            nodes.append(n)
    nodes = [n for n in nodes if not n["title"].startswith(BOT_TITLES)]
    nodes = [n for n in nodes
             if not (n.get("author") and (n["author"].get("login") or "").lower() == me)]
    return sorted(nodes, key=lambda p: (p["repository"]["nameWithOwner"], -p["number"]))


def is_bot(typename, login):
    return typename == "Bot" or (login or "").lower() in BOT_LOGINS


def ci_state(pr):
    ci = (pr["commits"]["nodes"] or [{}])[0].get("commit", {}).get("statusCheckRollup") or {}
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
    open_threads = sum(1 for t in pr["reviewThreads"]["nodes"] if not t["isResolved"])
    if open_threads:
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
    return {p["url"]: {
        "title": p["title"],
        "draft": p["isDraft"],
        "author": author_name(p),
        "ci": ci_state(p),
        "conflict": None if p["mergeable"] == "UNKNOWN" else p["mergeable"] == "CONFLICTING",
        "sha": p.get("headRefOid") or "",
    } for p in nodes}


def state_file(scope):
    return STATE_DIR / (hashlib.sha1(f"review:{scope}".encode()).hexdigest()[:12] + ".json")


def load_state(scope):
    path = state_file(scope)
    return json.loads(path.read_text()) if path.exists() else None


def save_state(scope, snap):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state_file(scope)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(snap, indent=1))
    os.replace(tmp, path)


def load_dispatched():
    if not DISPATCH_FILE.exists():
        return {}
    return json.loads(DISPATCH_FILE.read_text())


def update_dispatched(change):
    """Applies change(data) under an exclusive lock and replaces the file atomically."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "dispatched.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load_dispatched()
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
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(out.stdout.strip())] if out.returncode == 0 and out.stdout.strip() else []


def resolve_cwd(name_with_owner):
    owner, name = name_with_owner.split("/", 1)
    candidates = [*open_work_clones(), PROJECTS / owner / name, PROJECTS / name, *current_repo_root()]
    for clone in candidates:
        try:
            if (clone / ".git").exists() and orch_settings.origin_matches(clone, name_with_owner):
                return str(clone)
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def agent_name(repo, number, sha):
    """Unique per repo, PR and head: rev<number>-<sha7>-<repo hash>."""
    return f"rev{number}-{sha[:7]}-{hashlib.sha1(repo.lower().encode()).hexdigest()[:6]}"[:32]


def needs_dispatch(pr, dispatched):
    if bucket(pr) != "ready":
        return False, None
    sha = pr.get("headRefOid") or ""
    prev = dispatched.get(pr["url"]) or {}
    if claimed(prev, sha):
        return False, None
    kind = "re-review" if prev.get("sha") or my_review_sha(pr) else "review"
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


def changes(before, after):
    lines = []
    for url, now in after.items():
        was = before.get(url)
        if was is None:
            kind = blocker(now) or "ready"
            lines.append(f"new: {url} — {now['title']} by {now['author']} ({kind})")
            continue
        # conflict: True, False, or None while GitHub recomputes mergeability.
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
        if was["conflict"] is not True and now["conflict"] is True:
            parts.append("now conflicts")
        elif was["conflict"] is True and now["conflict"] is False:
            parts.append("conflicts cleared")
        elif was["conflict"] is None and now["conflict"] is False:
            parts.append("merge state known")
        if parts:
            became_ready = (
                not now["draft"] and now["conflict"] is False
                and now["ci"] in (None, "SUCCESS")
                and (was["draft"] or was["conflict"] is not False or was["ci"] not in (None, "SUCCESS")
                     or (was.get("sha") != now.get("sha")))
            )
            prefix = "ready" if became_ready else "changed"
            lines.append(f"{prefix}: {url} — {now['title']}: {', '.join(parts)}")
    for url, was in before.items():
        if url in after:
            continue
        st = gh("pr", "view", url, "--json", "state", "--jq", ".state").strip()
        if st == "OPEN":
            lines.append(f"cleared: {url} — {was['title']} (no longer requesting you)")
            continue
        lines.append(f"{st.lower()}: {url} — {was['title']}")
    return lines, {}


TITLE_MAX = 60


def table(title, prs):
    if not prs:
        return []
    lines = [f"**{title} ({len(prs)})**", "",
             "| PR | Title | Author · status |", "|---|---|---|"]
    for p in prs:
        name = p["title"].replace("|", "/")
        if len(name) > TITLE_MAX:
            name = name[:TITLE_MAX - 1].rstrip() + "…"
        lines.append(f"| {p['url']} | {name} | {status(p)} |")
    return lines + [""]


def counts(nodes):
    actionable = sum(1 for p in nodes if bucket(p) == "ready")
    return f"{actionable} actionable, {len(nodes)} total"


def cmd_list(args, scope, label):
    nodes = fetch(scope)
    if any(p["mergeable"] == "UNKNOWN" for p in nodes):
        time.sleep(3)
        nodes = fetch(scope)
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
    out = [f"**{len(nodes)} PRs requesting your review in {label}** — {counts(nodes)}", ""]
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
            nodes = fetch(scope)
            after = snapshot(nodes)
            if before is not None:
                lines, still_open = changes(before, after)
                after = {**after, **still_open}
                if lines:
                    print("\n".join([*lines, f"now: {counts(nodes)}"]), flush=True)
            before = after
            save_state(scope, after)
            if fails >= 3:
                print("recovered: PR fetch works again", flush=True)
            fails = 0
        except (RuntimeError, json.JSONDecodeError, KeyError) as error:
            fails += 1
            if fails == 3:
                print(f"PR fetch failing 3x in a row: {error}", flush=True)
        time.sleep(args.interval)


def cmd_dispatch_queue(args, scope, label):
    nodes = fetch(scope)
    if any(p["mergeable"] == "UNKNOWN" for p in nodes):
        time.sleep(3)
        nodes = fetch(scope)
    model = worker_model()
    dispatched = load_dispatched()
    queue = []
    for pr in nodes:
        ok, kind = needs_dispatch(pr, dispatched)
        if ok:
            queue.append(dispatch_entry(pr, kind, model))
    print(json.dumps({
        "scope": label,
        "model": model,
        "queue": queue,
        "skipped": sum(1 for p in nodes if bucket(p) == "ready") - len(queue),
    }, indent=2))


def claimed(entry, sha):
    earlier = entry.get("previous") or {}
    return bool(sha) and (entry.get("sha") == sha or (earlier.get("sha") == sha and earlier.get("state") == "pending"))


def cmd_claim_dispatched(args, scope, label):
    if not args.url or not args.sha:
        sys.exit("claim-dispatched needs --url and --sha")
    taken = []

    def claim(data):
        if claimed(data.get(args.url) or {}, args.sha):
            taken.append(True)
            return
        mark_entry(args, "pending")(data)

    update_dispatched(claim)
    print(f"{'TAKEN' if taken else 'CLAIMED'} {args.sha}")
    if taken:
        sys.exit(3)


def cmd_mark_dispatched(args, _scope, _label):
    if not args.url or not args.sha:
        sys.exit("mark-dispatched needs --url and --sha")
    entry = mark_entry(args, args.state)
    update_dispatched(entry)
    print(json.dumps(entry.entry))


def mark_entry(args, state):
    """A change for update_dispatched that marks args.sha as state."""
    entry = {
        "sha": args.sha,
        "state": state,
        "agent": args.agent,
        "pane": args.pane,
        "at": datetime.now(timezone.utc).isoformat(),
    }

    def mark(data):
        prev = data.get(args.url) or {}
        if state == "done" and prev.get("sha") not in (None, args.sha):
            if (prev.get("previous") or {}).get("sha") == args.sha:
                prev["previous"] = entry
            return
        earlier = prev.get("previous") if prev.get("sha") == args.sha else dict(prev, previous=None)
        if state == "pending" and earlier and earlier.get("sha"):
            entry["previous"] = {k: v for k, v in earlier.items() if k != "previous"}
        data[args.url] = entry

    mark.entry = entry
    return mark


def cmd_release_dispatched(args, _scope, _label):
    if not args.url or not args.sha:
        sys.exit("release-dispatched needs --url and --sha")

    def release(data):
        entry = data.get(args.url) or {}
        earlier = entry.get("previous") or {}
        if earlier.get("sha") == args.sha and earlier.get("state") == "pending":
            entry.pop("previous")
            return
        if entry.get("sha") != args.sha or entry.get("state") != "pending":
            return
        if entry.get("previous"):
            data[args.url] = entry["previous"]
        else:
            data.pop(args.url, None)

    update_dispatched(release)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["list", "watch", "dispatch-queue", "claim-dispatched", "mark-dispatched", "release-dispatched"],
                        nargs="?", default="list")
    parser.add_argument("--repo")
    parser.add_argument("--org")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--actionable", action="store_true",
                        help="list: only Ready-for-review PRs")
    parser.add_argument("--interval", type=int, default=120, help="watch: seconds between checks")
    parser.add_argument("--url", help="mark-dispatched: PR URL")
    parser.add_argument("--sha", help="mark-dispatched: head SHA that was dispatched")
    parser.add_argument("--state", choices=["pending", "done"], default="done",
                        help="mark-dispatched: pending while the review runs, done once it is posted")
    parser.add_argument("--agent", help="mark-dispatched: herdr agent name")
    parser.add_argument("--pane", help="mark-dispatched: herdr pane id")
    args = parser.parse_args()
    scope, label = resolve_scope(args)
    {
        "list": cmd_list,
        "watch": cmd_watch,
        "dispatch-queue": cmd_dispatch_queue,
        "claim-dispatched": cmd_claim_dispatched,
        "mark-dispatched": cmd_mark_dispatched,
        "release-dispatched": cmd_release_dispatched,
    }[args.mode](args, scope, label)


if __name__ == "__main__":
    main()
