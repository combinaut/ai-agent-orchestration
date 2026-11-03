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
            # github_poll changes a confirmed limit to a nonzero result and
            # publishes its shared cooldown.  All other failures are final.
            if github_poll.active_cooldown(directory / "github.json"):
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
    stdin = sys.stdin.read() if not sys.stdin.isatty() else None
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
