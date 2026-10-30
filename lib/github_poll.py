"""Machine-shared admission for participating GitHub CLI pollers."""

import fcntl
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

SPACING = 3


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
            return delay
    try:
        delay = float(headers["x-ratelimit-reset"]) - now
        if math.isfinite(delay) and delay >= 0:
            return delay
    except (KeyError, ValueError, OverflowError):
        pass
    return None


def run(argv, invoke):
    """invoke(env, pass_fds) returns a captured, text-mode CompletedProcess; no retries."""
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(directory / "github.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock, "r+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OSError("GitHub poll busy; read not attempted") from None
        path = directory / "github.json"
        try:
            state = json.loads(path.read_text()) if path.exists() else {}
            state.setdefault("until", 0)
            until = float(state.get("until", 0))
            failures = int(state.get("failures", 0))
            if not math.isfinite(until) or failures < 0:
                raise ValueError("invalid cooldown")
            callers = state.setdefault("callers", {})
            label = Path(sys.argv[0]).name
            if label == "watchers.py" and len(sys.argv) > 1:
                label += ":" + sys.argv[1]
            counter = callers.setdefault(label, {"started": 0, "limited": 0})
            counter["started"] += 1
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
            raise OSError("GitHub poll state unreadable; read not attempted") from None
        if until > time.time():
            raise OSError(f"GitHub poll cooldown until {until:.0f}; read not attempted")
        # The lock and elapsed wait bound starts independently of wall-clock changes.
        time.sleep(SPACING)
        state.pop("next", None)
        state.pop("pending", None)
        counter["last_started"] = time.time()
        write_state(path, state)
        result = invoke(dict(os.environ, GH_DEBUG="api"), (lock,))
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
                delay = min(3600, 60 * 2 ** (failures - 1))
            state["until"] = time.time() + delay + random.uniform(1, 5)
            state["failures"] = failures
            counter["limited"] += 1
            if result.returncode == 0:
                result.returncode = 1
        elif result.returncode == 0:
            state["until"], state["failures"] = 0, 0
        write_state(path, state)
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


def main():
    try:
        argv = ["gh", *sys.argv[1:]]
        result = run(
            argv,
            lambda env, pass_fds: subprocess.run(
                argv,
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
    sys.exit(main())
