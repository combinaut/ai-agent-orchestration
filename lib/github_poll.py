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
from email.utils import parsedate_to_datetime
from pathlib import Path

SPACING = 3
WAIT = 15
MAX_DELAY = 3600


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


def holder_message(directory):
    try:
        holder = json.loads((directory / "holder.json").read_text())
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
    except FileNotFoundError:
        return False
    except (ValueError, TypeError, AttributeError, OverflowError):
        return False
    except OSError:
        return False


def run(argv, invoke, timeout=45):
    """invoke(env, pass_fds) returns a captured text CompletedProcess; no retries."""
    started = time.monotonic()
    directory = state_directory()
    path = directory / "github.json"
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = os.open(directory / "github.lock", os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as error:
        raise OSError(f"GitHub poll state {directory}: {error}") from error
    with os.fdopen(lock, "r+") as stream:
        end = started + min(WAIT, max(0, timeout - 0.25))
        while True:
            if active_cooldown(path):
                raise Cooldown(
                    f"GitHub poll cooldown; state {path}; read not attempted"
                )
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise Busy(
                        f"GitHub poll busy; {holder_message(directory)}; read not attempted"
                    ) from None
                time.sleep(min(0.05, end - time.monotonic()))
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
            callers = state.setdefault("callers", {})
            label = Path(sys.argv[0]).name
            if label == "watchers.py" and len(sys.argv) > 1:
                label += ":" + sys.argv[1]
            counter = callers.setdefault(label, {"started": 0, "limited": 0})
            counter["started"] += 1
            previous = float(state.get("last_monotonic", -SPACING))
            if not math.isfinite(previous):
                raise ValueError("invalid start time")
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
        if time.monotonic() + spacing >= started + timeout:
            raise Busy(
                f"GitHub poll busy; {holder_message(directory)}; admission deadline exhausted"
            )
        time.sleep(spacing)
        state.pop("next", None)
        state.pop("pending", None)
        state["last_monotonic"] = time.monotonic()
        state["last_started"] = counter["last_started"] = time.time()
        try:
            write_state(path, state)
        except OSError as error:
            raise OSError(f"GitHub poll state {path}: {error}") from error
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0.1:
            raise Busy(
                f"GitHub poll busy; {holder_message(directory)}; admission deadline exhausted"
            )
        actual_start = time.monotonic()
        result = invoke(
            dict(
                os.environ,
                GH_DEBUG="api",
                GITHUB_POLL_SECONDS=str(remaining),
                GITHUB_POLL_DEADLINE=str(started + timeout),
                GITHUB_POLL_HOLDER=str(holder),
                GITHUB_POLL_STATE=str(path),
            ),
            (lock,),
        )
        recorded = json.loads(holder.read_text())
        state["last_monotonic"] = recorded.get("monotonic", actual_start)
        state["last_started"] = recorded.get("since", time.time())
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
                delay = min(MAX_DELAY, 60 * 2 ** (failures - 1))
            state["until"] = time.time() + min(delay, MAX_DELAY) + random.uniform(1, 5)
            state["failures"] = failures
            counter["limited"] += 1
            if result.returncode == 0:
                result.returncode = 1
        elif result.returncode == 0:
            state["until"], state["failures"] = 0, 0
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
        str(Path(__file__).resolve()),
        "--supervise",
        env.get("GITHUB_POLL_DEADLINE", str(time.monotonic() + 45)),
        *argv,
    ]


def supervise(deadline, argv):
    process = subprocess.Popen(argv)
    holder = os.environ.get("GITHUB_POLL_HOLDER")

    def expired(_signal, _frame):
        raise subprocess.TimeoutExpired(argv, 0)

    def stopped(_signal, _frame):
        raise SystemExit(1)

    previous_alarm = signal.signal(signal.SIGALRM, expired)
    previous_term = signal.signal(signal.SIGTERM, stopped)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, deadline - time.monotonic() - 0.1))
    try:
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
            state.update(last_monotonic=monotonic, last_started=since)
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
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        signal.signal(signal.SIGALRM, previous_alarm)
        signal.signal(signal.SIGTERM, previous_term)


def main():
    try:
        argv = ["gh", *sys.argv[1:]]
        result = run(
            argv,
            lambda env, pass_fds: subprocess.run(
                supervised(argv, env),
                start_new_session=True,
                env=env,
                pass_fds=pass_fds,
                capture_output=True,
                text=True,
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
        sys.exit(supervise(float(sys.argv[2]), sys.argv[3:]))
    sys.exit(main())
