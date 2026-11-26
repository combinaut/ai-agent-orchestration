#!/usr/bin/env python3
"""Conservative CI dependency map; unmapped paths select the complete suite."""

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEAVY = {"pr-tabs", "hog", "balance", "workers", "audit", "units", "tripwire"}
SWEEP = {"bats:sweep-record", "bats:sweep-run"}
DOC_READERS = {
    "bats:install",
    "bats:review-prs",
    "bats:todo-pane",
    "bats:sweep-run",
    "watchers",
}
SOURCES = {
    "lib/sweep_record.py": SWEEP,
    "lib/sweep_run.py": {"bats:sweep-run"},
    "orch-sweep": SWEEP,
    "orch-sweep-record": SWEEP,
    "docs/sweep-script.md": {"bats:sweep-run"},
    "hooks/guard-bash.py": {"guard", "bats:install"},
}
WATCHER_STEPS = {
    "coord-hog-watch.sh": "hog",
    "coord-balance-watch.sh": "balance",
    "coord-worker-watch.sh": "workers",
    "coord-audit-watch.sh": "audit",
    "test_hog.py": "hog",
    "test_balance.py": "balance",
    "test_balance_followup.py": "balance",
    "test_workers.py": "workers",
    "test_audit.py": "audit",
    "test_audit_review.py": "audit",
    "test_audit_legacy.py": "audit",
}


def platform_sensitive(path):
    if path.startswith("chrome/pr-tabs/"):
        return path.startswith("chrome/pr-tabs/host/")
    if path in {"orch-sweep", "orch-sweep-record"}:
        return True
    return (
        path.endswith((".sh", ".bash"))
        or path.endswith(".bats")
        and path not in {"tests/sweep-record.bats", "tests/sweep-run.bats"}
        or path.startswith("hooks/")
        or "/service/macos/" in path
        or path.startswith((".github/", "scripts/ci/", "tests/platform/"))
        or path
        in {
            "tests/test_helper.bash",
            "tests/setup_suite.bash",
            "coordinator/watchers/watcher_platform.py",
            "lib/github_poll.py",
            "skills/review-prs/scripts/prs.py",
            "coordinator/watchers/test_prs_watch.py",
        }
    )


def select(paths, everything=False):
    suites = set()
    full = everything
    macos = everything
    for path in paths:
        macos |= platform_sensitive(path)
        if path in SOURCES:
            suites |= SOURCES[path]
        elif path.startswith("chrome/pr-tabs/"):
            suites.add("pr-tabs")
            if path.startswith("chrome/pr-tabs/host/"):
                suites |= {"bats:chrome-host", "pr-tabs-host"}
            if path == "chrome/pr-tabs/extension/manifest.json":
                suites.add("bats:chrome-host")
        elif path.startswith("tests/") and path.endswith(".bats"):
            name = Path(path).stem
            if (ROOT / "tests" / f"{name}.bats").exists():
                suites.add(f"bats:{name}")
            else:
                full = True
        elif path == "tests/guard-bash.test.py":
            suites.add("guard")
        elif path.startswith("coordinator/watchers/test_") and path.endswith(".py"):
            suites.add("watchers")
            if Path(path).name in WATCHER_STEPS:
                suites.add(WATCHER_STEPS[Path(path).name])
        elif Path(path).name in WATCHER_STEPS and path.startswith(
            "coordinator/watchers/"
        ):
            suites |= {
                "watchers",
                WATCHER_STEPS[Path(path).name],
                "units",
                "bats:install",
            }
        elif "/service/linux/" in path:
            suites |= {"units", "bats:install", "watchers"}
        elif path.startswith("coordinator/watchers/fixtures/"):
            suites |= {"watchers", "hog", "balance", "workers", "audit", "bats:install"}
        elif (
            path.endswith(".md")
            or path == "LICENSE"
            or path.startswith(("docs/", "skills/"))
            and path.endswith(
                (".txt", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".pdf")
            )
        ):
            suites |= DOC_READERS
        else:
            full = True
            # Unclassified executable/config paths may affect either platform.
            if not path.endswith(".py"):
                macos = True
    if full:
        suites = HEAVY | {"all"}
    return {"full": full, "macos": macos, "suites": sorted(suites)}


def changed_files(base, head):
    result = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, head], cwd=ROOT
    )
    return result.decode().rstrip("\0").split("\0") if result else []


def event_plan(event, name):
    try:
        if name == "pull_request":
            pr = event["pull_request"]
            base = subprocess.check_output(
                ["git", "merge-base", pr["base"]["sha"], pr["head"]["sha"]],
                cwd=ROOT,
                text=True,
            ).strip()
            paths = changed_files(base, pr["head"]["sha"])
        elif name == "push":
            paths = changed_files(event["before"], event["after"])
        else:
            paths = []
        return select(paths, everything=name not in {"pull_request", "push"})
    except (KeyError, subprocess.CalledProcessError):
        return select([], everything=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=Path, help="JSON array of changed paths")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    args = parser.parse_args()
    if args.files:
        plan = select(json.loads(args.files.read_text()))
    else:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        plan = event_plan(event, args.event)
    print(json.dumps(plan, sort_keys=True))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.write(f"macos={str(plan['macos']).lower()}\n")
            stream.write(f"suites={' '.join(plan['suites'])}\n")


if __name__ == "__main__":
    main()
