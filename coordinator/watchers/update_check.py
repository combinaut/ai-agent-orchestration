"""Finds and installs new commits of the orchestrator clone for the settings popup.

At most one check thread and one update process run at a time.
"""

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

BRANCH = "main"
SHOWN = 8
FETCH_S = 60
PULL_S = 120
GIT_S = 15
LOG_TAIL = 64 * 1024
CHECKING = "Checking for updates..."
CURRENT = "Orchestrator is up to date."
PULLING = "Updating: getting the new changes..."
INSTALLING = "Updating: installing..."
UPDATED = "Updated. Close and reopen this popup to use the new version."
INSTALL_MARK = "== install"


class Blocked(Exception):
    """The clone must not be updated from the popup; the message says why."""


def git(repo, *args, timeout=GIT_S):
    """The output of one git command; RuntimeError carries git's own reason."""
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if done.returncode:
        raise RuntimeError(reason(done.stderr, repo))
    return done.stdout.strip()


def reason(output, repo):
    """The one line of a command's output that says what went wrong."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    marked = [
        x for x in lines if x.startswith(("fatal:", "error:", "ERROR:", "REFUSED:"))
    ]
    line = marked[0] if marked else lines[-1] if lines else "no reason given"
    for prefix in ("fatal: ", "error: ", "ERROR: "):
        line = line.removeprefix(prefix)
    return line.replace(str(repo), "the orchestrator folder")


def require_clean_main(repo):
    if not Path(repo).is_dir():
        raise RuntimeError("the orchestrator folder is missing")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != BRANCH:
        where = "no branch" if branch == "HEAD" else f"branch {branch}"
        raise Blocked(
            f"This copy is on {where}, not {BRANCH}, so it cannot be updated here."
        )
    if git(repo, "status", "--porcelain"):
        raise Blocked("This copy has local changes, so it cannot be updated here.")


def check(repo):
    """The lines to show and whether an update can be offered."""
    try:
        require_clean_main(repo)
        git(repo, "fetch", "--quiet", timeout=FETCH_S)
        if int(git(repo, "rev-list", "--count", "@{u}..HEAD")):
            raise Blocked(
                "This copy has its own commits, so it cannot be updated here."
            )
        count = int(git(repo, "rev-list", "--count", "HEAD..@{u}"))
        if not count:
            return [CURRENT], False
        subjects = git(repo, "log", f"-{SHOWN}", "--format=%s", "HEAD..@{u}")
    except Blocked as blocked:
        return [str(blocked)], False
    except subprocess.TimeoutExpired:
        return ["Could not check for updates: git took too long"], False
    except (OSError, RuntimeError) as error:
        return [f"Could not check for updates: {error}"], False
    noun = "change" if count == 1 else "changes"
    lines = [f"Update available: {count} new {noun}.", ""]
    lines += [f"- {subject}" for subject in subjects.splitlines()]
    if count > SHOWN:
        lines.append(f"and {count - SHOWN} more")
    return lines, True


class Updater:
    """What the update tab shows; lines and offer change only through check, update and poll."""

    def __init__(self):
        self.lines, self.offer = [CHECKING], False
        self.thread, self.found = None, None
        self.process, self.log, self.repo = None, None, None
        self.ran = False

    def busy(self):
        return self.thread is not None or self.process is not None

    def check(self, repo):
        """Looks for new commits in the background; an update's result stays."""
        if self.busy() or self.ran:
            return
        self.lines, self.offer = [CHECKING], False

        def work():
            self.found = check(repo)

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def update(self, repo):
        """Starts the offered update in its own session, writing to a log file."""
        if self.busy() or not self.offer:
            return
        self.repo, self.offer, self.ran = repo, False, True
        self.lines = [PULLING]
        try:
            with tempfile.NamedTemporaryFile(
                prefix="orch-update-", suffix=".log", delete=False
            ) as log:
                self.log = Path(log.name)
                self.process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), str(repo)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
        except OSError as error:
            self.lines = [f"Update failed: {error.strerror or error}"]

    def output(self):
        with self.log.open("rb") as log:
            log.seek(max(0, log.seek(0, os.SEEK_END) - LOG_TAIL))
            return log.read().decode(errors="replace")

    def poll(self):
        """Takes over whatever the background work has produced."""
        if self.thread and not self.thread.is_alive():
            self.thread = None
            if self.found:
                self.lines, self.offer = self.found
                self.found = None
            else:
                self.lines = ["Could not check for updates: the check stopped"]
        if self.process:
            code = self.process.poll()
            text = self.output()
            head, mark, installed = text.partition(INSTALL_MARK)
            if code is None:
                self.lines = [INSTALLING if mark else PULLING]
                return
            self.process = None
            self.log.unlink(missing_ok=True)
            if code:
                why = reason(installed if mark else head, self.repo)
                self.lines = [f"Update failed: {why}"]
            else:
                self.lines = [UPDATED]


def run_update(repo):
    """Pulls and installs; run as its own process so closing the popup leaves it whole."""
    try:
        require_clean_main(repo)
        git(repo, "pull", "--ff-only", "--quiet", timeout=PULL_S)
    except (Blocked, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: {error}", flush=True)
        return 1
    print(INSTALL_MARK, flush=True)
    # No timeout: stopping install.sh partway leaves the links half made.
    return subprocess.run(["./install.sh"], cwd=repo).returncode


if __name__ == "__main__":
    sys.exit(run_update(sys.argv[1]))
