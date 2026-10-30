#!/usr/bin/env python3
"""Lists or watches PRs that request your review, and plans Codex auto-reviews.

  prs-copy.py list          [scope] [--actionable]     markdown tables + snapshot for watch
  prs-copy.py watch         [scope] [--interval S]     one line per change, forever
  prs-copy.py dispatch-queue [scope]                   JSON: Ready PRs needing (re)review
  prs-copy.py mark-dispatched --url U --sha S [--agent N] [--pane P]

Scope: --repo owner/name | --org ORG | --all. Default: cwd's GitHub repo, else --all.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import watcher_runtime as runtime

BOT_TITLES = ("Automatic Update of README", "Changes by create-pull-request")
BOT_LOGINS = {"copilot-pull-request-reviewer", "copilot", "github-actions"}
STATE_DIR = Path.home() / ".cache" / "review-prs"
DISPATCH_FILE = STATE_DIR / "dispatched.json"
DEFAULT_MODEL = os.environ.get("ORCH_WATCHERS_WORKER_MODEL", "")
PROJECTS = Path.home() / "Projects"

QUERY = """
query($q: String!, $endCursor: String) {
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
      latestReviews(first: 20) { nodes { author { __typename login } state commit { oid } } }
    } }
  }
}"""


def gh(*args):
    result = runtime.run(["gh", *args])
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
        except runtime.github_poll.NoRepository:
            return "", "all repos"
    return f"repo:{repo}", repo


def search(q):
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
    )
    return [n for page in json.loads(raw) for n in page["data"]["search"]["nodes"] if n]


def my_review_sha(pr, me):
    for review in pr["latestReviews"]["nodes"]:
        if ((review.get("author") or {}).get("login") or "").lower() == me:
            return (review.get("commit") or {}).get("oid")
    return None


def fetch(scope):
    me = viewer_login().lower()
    requested = search(
        f"is:pr is:open review-requested:@me archived:false {scope}".strip()
    )
    # PRs I reviewed whose head moved since, with no new review request.
    reviewed = search(
        f"is:pr is:open reviewed-by:@me -review-requested:@me archived:false {scope}".strip()
    )
    moved = [
        n
        for n in reviewed
        if my_review_sha(n, me) and my_review_sha(n, me) != n.get("headRefOid")
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
    return sorted(nodes, key=lambda p: (p["repository"]["nameWithOwner"], -p["number"]))


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
    state_file(scope).write_text(json.dumps(snap, indent=1))


def load_dispatched():
    if not DISPATCH_FILE.exists():
        return {}
    return json.loads(DISPATCH_FILE.read_text())


def save_dispatched(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DISPATCH_FILE.write_text(json.dumps(data, indent=1))


def resolve_cwd(name_with_owner):
    owner, name = name_with_owner.split("/", 1)
    for candidate in (
        PROJECTS / owner / name,
        PROJECTS / name,
        Path.cwd() if Path.cwd().name == name else None,
    ):
        if candidate and (candidate / ".git").exists():
            return str(candidate)
    return None


def agent_name(number):
    return f"rev{number}"[:32]


def needs_dispatch(pr, dispatched):
    if bucket(pr) != "ready":
        return False, None
    sha = pr.get("headRefOid") or ""
    prev = dispatched.get(pr["url"]) or {}
    if prev.get("sha") == sha and sha:
        return False, None
    kind = "re-review" if prev.get("sha") else "review"
    return True, kind


def dispatch_entry(pr, kind):
    repo = pr["repository"]["nameWithOwner"]
    return {
        "url": pr["url"],
        "number": pr["number"],
        "title": pr["title"],
        "author": author_name(pr),
        "repo": repo,
        "sha": pr.get("headRefOid") or "",
        "kind": kind,
        "agent": agent_name(pr["number"]),
        "cwd": resolve_cwd(repo),
        "model": DEFAULT_MODEL,
        "primary_repo": repo == os.environ.get("ORCH_WATCHERS_PRIMARY_REPO", ""),
    }


def changes(before, after):
    lines = []
    for url, now in after.items():
        was = before.get(url)
        if was is None:
            kind = "draft" if now["draft"] else "ready"
            lines.append(f"new: {url} — {now['title']} by {now['author']} ({kind})")
            continue
        # GitHub reports UNKNOWN while it recomputes mergeability; keep the last known state.
        if now["conflict"] is None:
            now["conflict"] = bool(was["conflict"])
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
        if not was["conflict"] and now["conflict"]:
            parts.append("now conflicts")
        elif was["conflict"] and not now["conflict"]:
            parts.append("conflicts cleared")
        if parts:
            became_ready = (
                not now["draft"]
                and not now["conflict"]
                and now["ci"] in (None, "SUCCESS")
                and (
                    was["draft"]
                    or was["conflict"]
                    or was["ci"] not in (None, "SUCCESS")
                    or (was.get("sha") != now.get("sha"))
                )
            )
            prefix = "ready" if became_ready else "changed"
            lines.append(f"{prefix}: {url} — {now['title']}: {', '.join(parts)}")
    still_open = {}
    for url, was in before.items():
        if url in after:
            continue
        try:
            st = gh("pr", "view", url, "--json", "state", "--jq", ".state").strip()
        except RuntimeError as error:
            if "Could not resolve to a" not in str(error):
                raise
            misses = was.get("misses", 0) + 1
            if misses < 5:
                still_open[url] = {**was, "misses": misses}
                continue
            st = "OPEN"
        if st not in ("OPEN", "CLOSED", "MERGED"):
            raise RuntimeError("PR lookup returned invalid state")
        if st == "OPEN":
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
    actionable = sum(
        1 for p in nodes if bucket(p) == "ready" and p["mergeable"] != "UNKNOWN"
    )
    return f"{actionable} actionable, {len(nodes)} total"


def cmd_list(args, scope, label):
    nodes = fetch(scope)
    if any(p["mergeable"] == "UNKNOWN" for p in nodes):
        time.sleep(3)
        nodes = fetch(scope)
    save_state(scope, snapshot(nodes))
    order = ("ready", "ci_run", "ci_fail", "conflict", "draft")
    titles = {
        "ready": "🔔 Ready for your review",
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
    with runtime.deadline(runtime.ITERATION_TIMEOUT, "PR watch startup"):
        before = load_state(scope)
    for _ in runtime.iterations(args.interval, label="PR watch"):
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
        except (
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
        ) as error:
            fails += 1
            if fails == 3:
                print(f"PR fetch failing 3x in a row: {error}", flush=True)


def cmd_dispatch_queue(args, scope, label):
    nodes = fetch(scope)
    if any(p["mergeable"] == "UNKNOWN" for p in nodes):
        time.sleep(3)
        nodes = fetch(scope)
    dispatched = load_dispatched()
    queue = []
    for pr in nodes:
        ok, kind = needs_dispatch(pr, dispatched)
        if ok:
            queue.append(dispatch_entry(pr, kind))
    print(
        json.dumps(
            {
                "scope": label,
                "model": DEFAULT_MODEL,
                "queue": queue,
                "skipped": sum(1 for p in nodes if bucket(p) == "ready") - len(queue),
            },
            indent=2,
        )
    )


def cmd_mark_dispatched(args, _scope, _label):
    if not args.url or not args.sha:
        sys.exit("mark-dispatched needs --url and --sha")
    data = load_dispatched()
    data[args.url] = {
        "sha": args.sha,
        "agent": args.agent,
        "pane": args.pane,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    save_dispatched(data)
    print(json.dumps(data[args.url]))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "mode",
        choices=["list", "watch", "dispatch-queue", "mark-dispatched"],
        nargs="?",
        default="list",
    )
    parser.add_argument("--repo")
    parser.add_argument("--org")
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--actionable", action="store_true", help="list: only Ready-for-review PRs"
    )
    parser.add_argument(
        "--interval", type=int, default=120, help="watch: seconds between checks"
    )
    parser.add_argument("--url", help="mark-dispatched: PR URL")
    parser.add_argument("--sha", help="mark-dispatched: head SHA that was dispatched")
    parser.add_argument("--agent", help="mark-dispatched: herdr agent name")
    parser.add_argument("--pane", help="mark-dispatched: herdr pane id")
    args = parser.parse_args()
    scope, label = resolve_scope(args)
    {
        "list": cmd_list,
        "watch": cmd_watch,
        "dispatch-queue": cmd_dispatch_queue,
        "mark-dispatched": cmd_mark_dispatched,
    }[args.mode](args, scope, label)


if __name__ == "__main__":
    main()
