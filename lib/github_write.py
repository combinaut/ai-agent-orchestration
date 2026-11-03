#!/usr/bin/env python3
"""Serialized, retry-safe GitHub CLI writes and Actions dispatches.

Only an explicit secondary-limit/abuse response is retried.  Any transport
failure is intentionally terminal: the server may already have applied a
write, so replaying it could duplicate comments, reviews, or dispatches.
"""

import argparse
import fcntl
import math
import os
import subprocess
import sys
import time

import github_poll

MAX_ATTEMPTS = 4
DEFAULT_TIMEOUT = 300


def positive_float(value, name):
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{name} must be a number") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(f"{name} must be non-negative")
    return parsed


def configuration():
    base = positive_float(
        os.environ.get("GITHUB_WRITE_BACKOFF_BASE", "60"), "backoff base"
    )
    jitter = positive_float(os.environ.get("GITHUB_WRITE_JITTER_MAX", "5"), "jitter")
    return base, jitter


def cooldown_remaining(path):
    try:
        import json

        until = float(json.loads(path.read_text()).get("until", 0))
        if not math.isfinite(until) or until > time.time() + github_poll.MAX_DELAY + 5:
            return 0
        return max(0, until - time.time())
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return 0


def sleep_until_ready(path, deadline):
    delay = cooldown_remaining(path)
    if not delay:
        return
    if time.monotonic() + delay >= deadline:
        raise TimeoutError(
            f"GitHub write cooldown needs {delay:.1f}s; caller timeout would expire"
        )
    time.sleep(delay)


def replay_safe(argv):
    args = argv[1:] if argv[:1] == ["gh"] else argv
    if args[:1] == ["api"]:
        return not any(
            arg == "--paginate" or arg.startswith("--paginate=") for arg in args
        )
    return args[:2] == ["workflow", "run"]


def reads_stdin(argv):
    for index, arg in enumerate(argv):
        next_arg = argv[index + 1] if index + 1 < len(argv) else ""
        if arg in ("--input", "--body-file") and next_arg == "-":
            return True
        if arg in ("--input=-", "--body-file=-"):
            return True
        if arg in ("-F", "--field", "-f", "--raw-field") and (
            next_arg == "@-" or next_arg.endswith("=@-")
        ):
            return True
        if arg.startswith(("-F", "-f")) and (
            arg[2:] == "@-" or arg[2:].endswith("=@-")
        ):
            return True
        if arg.startswith(("--field=", "--raw-field=")) and arg.endswith("=@-"):
            return True
    return argv[:2] == ["workflow", "run"] and "--json" in argv


def limit_not_replayed(result, path):
    wait = cooldown_remaining(path)
    detail = result.stderr.strip()
    result.stderr = "\n".join(
        part
        for part in (
            detail,
            "GitHub secondary/rate limit confirmed; command not replayed because it may have side effects beyond one request."
            + (f" Shared cooldown: {wait:.1f}s." if wait else ""),
        )
        if part
    )
    return result


def run(argv, *, timeout=DEFAULT_TIMEOUT, stdin=None):
    """Run one idempotency-unsafe CLI command, retrying only proven limits."""
    if timeout <= 0:
        raise TimeoutError("GitHub write timeout must be positive")
    base, jitter = configuration()
    directory = github_poll.state_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = directory / "github-write.lock"
    started = time.monotonic()
    deadline = started + timeout
    with open(lock_path, "a+") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("GitHub write timeout waiting for another retry")
                time.sleep(min(0.05, deadline - time.monotonic()))
        for attempt in range(1, MAX_ATTEMPTS + 1):
            sleep_until_ready(directory / "github.json", deadline)
            remaining = deadline - time.monotonic()
            if remaining < 2:
                raise TimeoutError("GitHub write timeout exhausted before an attempt")
            try:
                result = github_poll.run(
                    argv,
                    lambda env, pass_fds: github_poll.capture(
                        github_poll.supervised(argv, env),
                        env=env,
                        pass_fds=pass_fds,
                        timeout=remaining,
                        input=stdin,
                    ),
                    timeout=remaining,
                    retry_base=base,
                    retry_jitter=(0, jitter),
                )
            except github_poll.Cooldown:
                # A reader may have published a cooldown after our preflight.
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError(
                        "GitHub write retry attempts exhausted"
                    ) from None
                continue
            if result.returncode == 0:
                return result
            # Only this invocation can prove a limit response. A reader may
            # publish a shared cooldown after our write completes, but that
            # cannot make an uncertain write safe to replay.
            if getattr(result, "github_limited", False):
                if not replay_safe(argv) or (reads_stdin(argv[1:]) and stdin is None):
                    return limit_not_replayed(result, directory / "github.json")
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError("GitHub write retry attempts exhausted")
                continue
            return result
    raise AssertionError("unreachable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=lambda value: positive_float(value, "timeout"),
        default=DEFAULT_TIMEOUT,
    )
    parser.add_argument("gh", nargs=argparse.REMAINDER, help="arguments passed to gh")
    args = parser.parse_args()
    if not args.gh:
        parser.error(
            "pass gh arguments, for example: api -X POST repos/owner/repo/issues"
        )
    stdin = (
        sys.stdin.read() if reads_stdin(args.gh) and not sys.stdin.isatty() else None
    )
    try:
        result = run(["gh", *args.gh], timeout=args.timeout, stdin=stdin)
    except (OSError, TimeoutError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1
    sys.stdout.write(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
