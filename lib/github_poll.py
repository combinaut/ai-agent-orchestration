"""Machine-shared admission for participating GitHub CLI pollers."""

import fcntl
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path

SPACING = 3
WAIT = 15
MAX_DELAY = 3600
DEADLINE_TICK = 0.25


class Busy(OSError):
    """Admission remained occupied for the caller's bounded wait."""


class Cooldown(OSError):
    """Shared rate-limit cooldown; no admission wait or network attempt."""


class NoRepository(OSError):
    """The CLI confirmed that the current checkout has no GitHub repository."""


def state_directory():
    return Path(
        os.environ.get("GITHUB_POLL_STATE_DIR")
        or Path.home() / ".cache/orch-github-poll"
    )


def write_state(path, state):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def diagnostics(stderr):
    lines = stderr.splitlines()
    if not any(line.startswith(("* ", "> ", "< ")) for line in lines):
        return {}, 0, stderr.strip()
    headers, status, messages = {}, 0, []
    tail = next(
        (
            i + 1
            for i in range(len(lines) - 1, -1, -1)
            if lines[i].startswith("* Request took ")
        ),
        None,
    )
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if match := re.match(r"< HTTP/[\d.]+ (\d+)", line):
            headers, status = {}, int(match[1])
            while index < len(lines):
                match = re.match(r"< ([\w-]+):\s*(.*)", lines[index])
                if not match:
                    break
                headers[match[1].lower()] = match[2].strip()
                index += 1
            if index < len(lines) and not lines[index]:
                while index < len(lines) and not lines[index]:
                    index += 1
                body = "\n".join(lines[index:])
                try:
                    if "json" not in headers.get("content-type", "").lower():
                        break
                    if re.match(
                        r"\* body is too long(?: \(\d+ bytes\) to print, skipping \(longer than \d+ bytes\)|, skipping \(contains more than \d+ bytes\))$",
                        lines[index] if index < len(lines) else "",
                    ):
                        index += 1
                        continue
                    _, consumed = json.JSONDecoder().raw_decode(body)
                except ValueError:
                    break
                # Complete JSON and logger omissions delimit subsequent responses.
                index += body[:consumed].count("\n") + 1
        elif line.startswith(("gh:", "GraphQL:", "error:", "HTTP ")):
            messages.append(line)
    return (
        headers,
        status,
        "\n".join(lines[tail:] if tail is not None else messages).strip(),
    )


def retry_delay(headers, now):
    value = headers.get("retry-after")
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = parsedate_to_datetime(value).timestamp() - now
            except (ValueError, TypeError, OverflowError):
                delay = -1
        if math.isfinite(delay) and delay >= 0:
            return min(delay, MAX_DELAY)
    if headers.get("x-ratelimit-remaining") != "0":
        return None
    try:
        delay = float(headers["x-ratelimit-reset"]) - now
        if math.isfinite(delay) and delay >= 0:
            return min(delay, MAX_DELAY)
    except (KeyError, ValueError, OverflowError):
        pass
    return None


def holder_message(directory, name="holder.json"):
    try:
        holder = json.loads((directory / name).read_text())
        age = max(0, time.time() - holder["since"])
        return f"holder pid={holder['pid']} age={age:.1f}s" + (
            " stuck" if age > 60 else ""
        )
    except (OSError, ValueError, TypeError, KeyError):
        return "holder pid=unknown age=unknown"


def active_cooldown(path):
    try:
        until = float(json.loads(path.read_text()).get("until", 0))
        return time.time() < until <= time.time() + MAX_DELAY + 5
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return False


class Deadline(OSError):
    """The individual CLI call exhausted its budget."""


class _Expired(BaseException):
    """Deadline delivery bypasses recoverable read-error handlers."""


def _late_tick(_signum, _frame):
    """macOS can deliver one SIGALRM after the interval timer is disarmed."""


def _after_timer(handler):
    return _late_tick if handler == signal.SIG_DFL else handler


@contextmanager
def call_deadline(seconds, *, expired_error=_Expired):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    restored_handler = (
        previous_handler if previous_timer[0] else _after_timer(previous_handler)
    )
    started = time.time()
    monotonic_started = time.monotonic()
    previous_remaining = previous_timer[0]
    if endpoints := getattr(previous_handler, "_poll_deadline", None):
        # A poller's periodic alarm is a clock-check tick, not its deadline.
        previous_remaining = max(
            0.001, min(endpoints[0] - started, endpoints[1] - monotonic_started)
        )
    inherited = 0 < previous_remaining <= seconds
    inherited_fired = False
    duration = previous_remaining if inherited else seconds
    expires = started + duration
    monotonic_expires = monotonic_started + duration

    def expired(signum, frame):
        nonlocal inherited_fired
        if time.time() < expires and time.monotonic() < monotonic_expires:
            return
        expired._fired = True
        signal.setitimer(signal.ITIMER_REAL, 0)
        if inherited and callable(previous_handler):
            inherited_fired = True
            try:
                previous_handler(signum, frame)
            except TimeoutError as error:
                raise _Expired from error
            inherited_fired = False
        raise expired_error

    expired._poll_deadline = (expires, monotonic_expires)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(
        signal.ITIMER_REAL,
        max(0.001, min(DEADLINE_TICK, expires - started)),
        DEADLINE_TICK,
    )

    def restore():
        if inherited_fired:
            if signal.getsignal(signal.SIGALRM) is expired:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
        else:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, restored_handler)
            if previous_timer[0] and not getattr(previous_handler, "_fired", False):
                signal.setitimer(
                    signal.ITIMER_REAL,
                    max(
                        0.001,
                        previous_timer[0]
                        - max(
                            time.time() - started, time.monotonic() - monotonic_started
                        ),
                    ),
                    previous_timer[1],
                )

    try:
        yield
        if time.time() >= expires or time.monotonic() >= monotonic_expires:
            expired(signal.SIGALRM, None)
    finally:
        try:
            restore()
        except BaseException:
            restore()
            raise


def run(argv, invoke, timeout=45, retry_base=60, retry_jitter=(1, 5)):
    """Main-thread captured text invocation with one deadline and no retries."""
    started = time.time()
    monotonic_deadline = time.monotonic() + timeout
    try:
        with call_deadline(timeout):
            return _run(
                argv,
                invoke,
                timeout,
                started,
                retry_base,
                retry_jitter,
                monotonic_deadline=monotonic_deadline,
            )
    except _Expired as error:
        if isinstance(error.__cause__, TimeoutError):
            raise error.__cause__
        raise Deadline(
            f"GitHub poll deadline exceeded; state {state_directory() / 'github.json'}"
        ) from None


def _run(
    argv,
    invoke,
    timeout,
    started,
    retry_base=60,
    retry_jitter=(1, 5),
    *,
    monotonic_deadline=None,
):
    if monotonic_deadline is None:
        monotonic_deadline = time.monotonic() + timeout

    def remaining():
        return min(
            started + timeout - time.time(), monotonic_deadline - time.monotonic()
        )

    directory = state_directory()
    path = directory / "github.json"
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = os.open(directory / "github.lock", os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as error:
        raise OSError(f"GitHub poll state {directory}: {error}") from error
    with os.fdopen(lock, "r+") as stream:
        wait = min(WAIT, max(0, timeout - 0.25))
        end = started + wait
        monotonic_end = monotonic_deadline - timeout + wait
        while True:
            if active_cooldown(path):
                raise Cooldown(
                    f"GitHub poll cooldown; state {path}; read not attempted"
                )
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= end or time.monotonic() >= monotonic_end:
                    raise Busy(
                        f"GitHub poll busy; {holder_message(directory)}; read not attempted"
                    ) from None
                time.sleep(
                    max(
                        0,
                        min(0.05, end - time.time(), monotonic_end - time.monotonic()),
                    )
                )
        holder = directory / "holder.json"
        try:
            write_state(holder, {"pid": os.getpid(), "since": time.time()})
        except OSError as error:
            raise OSError(f"GitHub poll holder {holder}: {error}") from error
        try:
            state = json.loads(path.read_text()) if path.exists() else {}
            state.setdefault("until", 0)
            until = float(state.get("until", 0))
            failures = int(state.get("failures", 0))
            if not math.isfinite(until) or failures < 0:
                raise ValueError("invalid cooldown")
            if until > time.time() + MAX_DELAY + 5:
                state["until"] = 0
            elif until > time.time():
                raise Cooldown(
                    f"GitHub poll cooldown; state {path}; read not attempted"
                )
            callers = state.setdefault("callers", {})
            label = Path(sys.argv[0]).name
            if label == "watchers.py" and len(sys.argv) > 1:
                label += ":" + sys.argv[1]
            counter = callers.setdefault(label, {"started": 0, "limited": 0})
            if any(
                type(counter.get(field)) is not int or counter[field] < 0
                for field in ("started", "limited")
            ):
                raise ValueError("invalid caller counter")
            counter["started"] += 1
            previous = float(state.get("last_monotonic", -SPACING))
            if not math.isfinite(previous):
                raise ValueError("invalid start time")
            if state.get("pending_start"):
                previous = time.monotonic()
        except Cooldown:
            raise
        except (
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            OverflowError,
            OSError,
        ):
            try:
                os.replace(
                    path, path.with_name(f"github.json.corrupt.{time.time_ns()}")
                )
                write_state(path, {"until": time.time() + 60})
            except OSError as error:
                raise OSError(f"GitHub poll state {path}: {error}") from error
            raise Cooldown(
                f"GitHub poll state recovered at {path}; cooldown 60s; read not attempted"
            ) from None
        elapsed = time.monotonic() - previous
        spacing = max(0, SPACING - elapsed) if elapsed >= 0 else SPACING
        if spacing >= remaining():
            raise Busy(
                f"GitHub poll busy; {holder_message(directory)}; admission deadline exhausted"
            )
        time.sleep(spacing)
        state["pending_start"] = True
        state["last_monotonic"] = time.monotonic()
        state["last_started"] = counter["last_started"] = time.time()
        try:
            write_state(path, state)
        except OSError as error:
            raise OSError(f"GitHub poll state {path}: {error}") from error
        if remaining() < 2:
            raise Busy(
                f"GitHub poll busy; {holder_message(directory)}; admission deadline exhausted"
            )
        result = invoke(
            dict(
                os.environ,
                GH_DEBUG="api",
                GITHUB_POLL_DEADLINE=str(started + timeout),
                GITHUB_POLL_MONOTONIC_DEADLINE=str(monotonic_deadline),
                GITHUB_POLL_HOLDER=str(holder),
                GITHUB_POLL_STATE=str(path),
            ),
            (lock,),
        )
        recorded = json.loads(holder.read_text())
        if "monotonic" in recorded:
            state["last_monotonic"] = recorded["monotonic"]
            state["last_started"] = recorded["since"]
        else:
            state["last_monotonic"], state["last_started"] = (
                time.monotonic(),
                time.time(),
            )
        state["pending_start"] = False
        headers, status, message = diagnostics(result.stderr)
        limited = status == 429 or bool(
            re.search(
                r"secondary rate limit|rate.?limit(?: already)? exceeded|abuse detection",
                message,
                re.I,
            )
        )
        if limited:
            failures = min(failures + 1, 7)
            delay = retry_delay(headers, time.time())
            if delay is None:
                delay = min(MAX_DELAY, retry_base * 2 ** (failures - 1))
            state["until"] = time.time() + delay + random.uniform(*retry_jitter)
            state["failures"] = failures
            counter["limited"] += 1
            if result.returncode == 0:
                result.returncode = 1
        elif result.returncode == 0:
            state["until"], state["failures"] = 0, 0
        result.github_limited = limited
        try:
            write_state(path, state)
        except OSError as error:
            raise OSError(f"GitHub poll state {path}: {error}") from error
        result.stderr = message or (
            f"gh failed (exit {result.returncode})" if result.returncode else ""
        )
        if (
            result.returncode
            and list(argv[1:3]) == ["repo", "view"]
            and re.search(
                r"not a git repository|none of the git remotes|no git remotes found",
                result.stderr,
                re.I,
            )
        ):
            raise NoRepository(result.stderr)
        return result


def supervised(argv, env):
    return [
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        "--supervise",
        env.get("GITHUB_POLL_DEADLINE", str(time.monotonic() + 45)),
        *argv,
    ]


def supervise(deadline, argv, *, capture_group=False):
    owns_group = capture_group and os.getpgrp() == os.getpid()
    monotonic_deadline = float(
        os.environ.get(
            "GITHUB_POLL_MONOTONIC_DEADLINE",
            time.monotonic() + max(0, deadline - time.time()),
        )
    )
    process = None
    pending = set()

    def defer(signum, _frame):
        pending.add(signum)

    holder = os.environ.get("GITHUB_POLL_HOLDER")

    def expired(_signal, _frame):
        if (
            time.time() >= deadline - 0.1
            or time.monotonic() >= monotonic_deadline - 0.1
        ):
            signal.setitimer(signal.ITIMER_REAL, 0)
            raise subprocess.TimeoutExpired(argv, 0)

    def stopped(_signal, _frame):
        raise SystemExit(1)

    previous_alarm = signal.signal(signal.SIGALRM, defer)
    previous_term = signal.signal(signal.SIGTERM, defer)
    previous_int = signal.signal(signal.SIGINT, defer)
    signal.setitimer(
        signal.ITIMER_REAL,
        max(
            0.001,
            min(
                DEADLINE_TICK,
                deadline - time.time() - 0.1,
                monotonic_deadline - time.monotonic() - 0.1,
            ),
        ),
        DEADLINE_TICK,
    )
    try:
        try:
            process = subprocess.Popen(argv, start_new_session=not owns_group)
        finally:
            signal.signal(signal.SIGALRM, expired)
            signal.signal(signal.SIGTERM, stopped)
            signal.signal(signal.SIGINT, previous_int)
            for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
                if signum in pending:
                    signal.raise_signal(signum)
        if holder:
            since, monotonic = time.time(), time.monotonic()
            write_state(
                Path(holder),
                {
                    "pid": os.getpid(),
                    "gh_pid": process.pid,
                    "since": since,
                    "monotonic": monotonic,
                },
            )
            path = Path(os.environ["GITHUB_POLL_STATE"])
            state = json.loads(path.read_text())
            state.update(
                pending_start=False, last_monotonic=monotonic, last_started=since
            )
            write_state(path, state)
        return process.wait()
    except subprocess.TimeoutExpired:
        print("gh deadline exceeded", file=sys.stderr)
        return 124
    except OSError as error:
        print(f"GitHub poll supervisor {holder}: {error}", file=sys.stderr)
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if process is not None and process.returncode is None:
            try:
                os.killpg(os.getpid() if owns_group else process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        signal.signal(signal.SIGALRM, _after_timer(previous_alarm))
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def kill_capture(process):
    """Kill the private capture group, including a dead supervisor's CLI tree."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as error:
        # SIGKILL can precede the child becoming waitable on macOS.
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            raise error from None


def capture(argv, *, env, pass_fds, timeout, input=None):
    process = None
    pending = []

    def defer(signum, _frame):
        pending.append(signum)

    handlers = {
        number: signal.signal(number, defer)
        for number in (signal.SIGINT, signal.SIGALRM, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                stdin=subprocess.PIPE if input is not None else None,
                start_new_session=True,
                env=env,
                pass_fds=pass_fds,
            )
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
            for number in pending:
                signal.raise_signal(number)
        stdout, stderr = process.communicate(input=input, timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except BaseException:
        if process is not None:
            kill_capture(process)
            process.wait(timeout=1)
        raise
    finally:
        if process is not None:
            process.stdout.close()
            process.stderr.close()


def main():
    try:
        argv = ["gh", *sys.argv[1:]]
        result = run(
            argv,
            lambda env, pass_fds: capture(
                supervised(argv, env),
                env=env,
                pass_fds=pass_fds,
                timeout=45,
            ),
        )
        sys.stdout.write(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--supervise":
        value = sys.argv[2]
        if "GITHUB_POLL_MONOTONIC_DEADLINE" in os.environ:
            deadline = float(value)
        else:
            # Pre-wall-clock callers remain loaded in long-running watchers.
            monotonic_deadline = float(value)
            os.environ["GITHUB_POLL_MONOTONIC_DEADLINE"] = value
            deadline = time.time() + max(0, monotonic_deadline - time.monotonic())
        sys.exit(supervise(deadline, sys.argv[3:], capture_group=True))
    sys.exit(main())
