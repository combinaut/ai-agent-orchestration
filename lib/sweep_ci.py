#!/usr/bin/env python3
"""Publish sweep-record using only code and configuration from the default branch."""

import json
import os
import subprocess
import sys
from pathlib import Path

import sweep_record as gate


def write_status(repo, head, state, description):
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("github_write.py")),
            "--timeout",
            "60",
            "api",
            "--method",
            "POST",
            f"repos/{repo}/statuses/{head}",
            "-f",
            "context=sweep-record",
            "-f",
            f"state={state}",
            "-f",
            f"description={description[:140]}",
        ],
        check=True,
    )


def evaluate(repo, number, allowed):
    pr = gate.gh_json("api", f"repos/{repo}/pulls/{number}")
    if pr["state"] != "open":
        return
    head = pr["head"]["sha"]
    write_status(repo, head, "pending", "Checking the current head's sweep record")
    try:
        # Fetch objects as data. Never check out, import or run the PR tree.
        subprocess.run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "fetch",
                "--no-tags",
                "origin",
                f"+refs/pull/{number}/head:refs/sweep/head",
                f"+refs/heads/{pr['base']['ref']}:refs/sweep/base",
            ],
            check=True,
            timeout=120,
        )
        merge_base = gate.git("merge-base", head, "refs/sweep/base")
        if not merge_base:
            raise gate.GateError("cannot compute the current merge base")
        pages = gate.gh_json(
            "api", "--paginate", "--slurp", f"repos/{repo}/issues/{number}/comments"
        )
        why = gate.comments_problem(
            pr,
            [c for page in pages for c in page],
            repo,
            number,
            head,
            merge_base.strip(),
            {pr["user"]["login"], *allowed},
            local=False,
        )
        live = gate.gh_json("api", f"repos/{repo}/pulls/{number}")
        if any(live[k] != pr[k] for k in ("head", "base", "title", "body")):
            why = "PR changed during validation; waiting for the next refresh"
        write_status(
            repo,
            head,
            "failure" if why else "success",
            why or "Valid orch-sweep record for this exact head",
        )
    except (gate.GateError, OSError, subprocess.SubprocessError) as error:
        write_status(repo, head, "error", f"Cannot validate sweep record: {error}")
        raise


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    repo = os.environ["GITHUB_REPOSITORY"]
    if len(sys.argv) == 2 and sys.argv[1] != "--discover":
        numbers = [int(sys.argv[1])]
    elif "issue" in event:
        numbers = [event["issue"]["number"]] if "pull_request" in event["issue"] else []
    else:
        run = event["workflow_run"]
        # GitHub can omit pull_requests for fork runs; resolve association by SHA.
        prs = gate.gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/commits/{run['head_sha']}/pulls",
        )
        numbers = sorted(
            {
                pr["number"]
                for page in prs
                for pr in page
                if pr["head"]["sha"] == run["head_sha"]
            }
        )
    if len(sys.argv) == 2 and sys.argv[1] == "--discover":
        print(json.dumps(numbers))
        return
    allowed = json.loads(Path(".github/sweep-record.json").read_text())[
        "allowed_authors"
    ]
    if not isinstance(allowed, list) or not all(
        isinstance(a, str) and a for a in allowed
    ):
        raise gate.GateError("allowed_authors must be a list of logins")
    for number in numbers:
        evaluate(repo, number, allowed)


if __name__ == "__main__":
    main()
