"""Host and process adapters selected once for the running platform."""
import os
import re
import subprocess
import stat
import sys
import time
from collections import Counter
from pathlib import Path


class MissingMetric(Exception):
    """A metric response lacks a required field."""


def herdr_bin():
    """HERDR_BIN_PATH (set by the services), else the configured ORCH_HERDR_BIN, else herdr on PATH."""
    return os.environ.get("HERDR_BIN_PATH") or os.environ.get("ORCH_HERDR_BIN") or "herdr"


def optional(read):
    try:
        return read()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, MissingMetric):
        return None


def match_number(pattern, text):
    match = re.search(pattern, text)
    if not match:
        raise MissingMetric("number field absent")
    return float(match[1])


def fields(text, count):
    values = text.split()
    if len(values) < count:
        raise MissingMetric("fields absent")
    return values


def memory_field(values, name):
    if name not in values:
        raise MissingMetric(name)
    return values[name]


def mac_free(command):
    return match_number(r"free percentage:\s*([\d.]+)%", command("memory_pressure"))


def mac_load(command, index):
    return float(fields(command("sysctl", "-n", "vm.loadavg").strip("{} \n"), index + 1)[index])


class MacHost:
    def collect(self, command):
        return {
            "free": optional(lambda: mac_free(command)),
            "swap": optional(lambda: match_number(r"used = ([\d.]+)M", command("sysctl", "-n", "vm.swapusage"))),
            "procs": optional(lambda: len(command("ps", "-A", "-o", "pid=").splitlines())),
            "files": optional(lambda: int(fields(command("sysctl", "-n", "kern.num_files"), 1)[0])),
            "load": optional(lambda: mac_load(command, 0)),
        }


class LinuxHost:
    def __init__(self, proc=Path("/proc")):
        self.proc = Path(proc)

    def memory(self):
        return {line.split(":", 1)[0]: int(fields(line, 2)[1])
                for line in (self.proc / "meminfo").read_text().splitlines()}

    def free(self):
        values = self.memory()
        return 100 * memory_field(values, "MemAvailable") / memory_field(values, "MemTotal")

    def swap(self):
        values = self.memory()
        return (memory_field(values, "SwapTotal") - memory_field(values, "SwapFree")) / 1024

    def files(self):
        allocated, unused, _ = map(int, fields((self.proc / "sys/fs/file-nr").read_text(), 3))
        return allocated - unused

    def collect(self, command):
        return {
            "free": optional(self.free),
            "swap": optional(self.swap),
            "procs": optional(lambda: sum(p.name.isdigit() and p.is_dir() for p in self.proc.iterdir())),
            "files": optional(self.files),
            "load": optional(lambda: float(fields((self.proc / "loadavg").read_text(), 1)[0])),
        }


HOST = MacHost() if sys.platform == "darwin" else LinuxHost()


def host_metrics(command):
    return HOST.collect(command)


def elapsed_seconds(elapsed):
    days, sep, clock = elapsed.partition("-")
    age = int(days) * 86400 if sep else 0
    seconds = 0
    for value in (clock if sep else elapsed).split(":"):
        seconds = seconds * 60 + int(value)
    return age + seconds


class MacProcesses:
    def collect(self, command):
        rows = []
        text = command("ps", "-A", "-o", "pid=,ppid=,rss=,etime=,lstart=,comm=")
        commands = {}
        for line in command("ps", "-A", "-o", "pid=,command=").splitlines():
            pid, _, cmd = line.strip().partition(" ")
            commands[pid] = cmd.strip()
        for line in text.splitlines():
            parts = line.split(None, 9)
            if len(parts) != 10:
                raise ValueError("unparseable process")
            pid, ppid, rss, elapsed = parts[:4]
            rows.append(dict(pid=pid, ppid=ppid, rss=int(rss), age=elapsed_seconds(elapsed),
                             start=" ".join(parts[4:9]), comm=parts[9],
                             command=commands.get(pid, parts[9])))
        return rows


class LinuxProcesses:
    def __init__(self, proc=Path("/proc"), ticks=None):
        self.proc = Path(proc)
        self.ticks = ticks or os.sysconf("SC_CLK_TCK")

    def read_process(self, path, uptime):
        stat = (path / "stat").read_text()
        name, _, tail = stat[stat.index("(") + 1:].rpartition(")")
        values = fields(tail, 20)
        start = int(values[19])
        status = (path / "status").read_text()
        rss = int(match_number(r"(?m)^VmRSS:\s*(\d+)\s+kB", status)) if "VmRSS:" in status else 0
        argv = (path / "cmdline").read_bytes().decode(errors="replace").split("\0")
        cmd = " ".join(argv).strip()
        return dict(pid=path.name, ppid=values[1], rss=rss,
                    age=max(0, int(uptime - start / self.ticks)), start=str(start),
                    comm=argv[0] or name, command=cmd or name)

    def collect(self, command):
        uptime = float(fields((self.proc / "uptime").read_text(), 1)[0])
        rows = []
        self.skipped_processes = 0
        for path in self.proc.iterdir():
            if not path.name.isdigit() or not path.is_dir():
                continue
            try:
                rows.append(self.read_process(path, uptime))
            except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, MissingMetric):
                # A process may exit, be restricted, or expose incomplete fields.
                self.skipped_processes += 1
        return rows


PROCESSES = MacProcesses() if sys.platform == "darwin" else LinuxProcesses()


def process_rows(command):
    return PROCESSES.collect(command)


READ_REASON_CHARS = 200


def read_failure_reason(error):
    if isinstance(error, subprocess.CalledProcessError):
        lines = str(error.stderr or "").strip().splitlines()
        reason = f"exit {error.returncode}" + (f": {lines[0]}" if lines else "")
    else:
        reason = f"{type(error).__name__}: {error}"
    return reason[:READ_REASON_CHARS]


def balance_read(read, label=None):
    """A labelled read that fails writes one stderr line saying which command, how long, and why."""
    started = time.monotonic()
    try:
        return read()
    except (OSError, subprocess.SubprocessError, MissingMetric, ValueError, IndexError, ZeroDivisionError) as error:
        if label:
            print(f"balance read failed: {label} after {time.monotonic() - started:.1f}s: "
                  f"{read_failure_reason(error)}", file=sys.stderr)
        return None


def git_root_marker(path):
    try:
        (path / ".git").stat()
    except FileNotFoundError:
        return False
    return True


def worktree_root(cwd):
    path = Path(cwd)
    parts = path.parts
    if "worktrees" not in parts:
        return None
    index = parts.index("worktrees")
    boundary = Path(*parts[:index + 1])
    for candidate in (path, *path.parents):
        if candidate == boundary:
            break
        if git_root_marker(candidate):
            return candidate
    return boundary / parts[index + 1] if index + 1 < len(parts) else None


def worktree_totals(rows, errors=None):
    errors = errors if errors is not None else []
    totals = {}
    for row in rows:
        try:
            root = worktree_root(row["cwd"])
        except OSError:
            errors.append("pid:" + row["pid"] if row.get("pid") else "cwd:" + row["cwd"])
            continue
        if root:
            total = totals.setdefault(str(root), dict(name=root.name, cpu=0, mb=0))
            total["cpu"] += row["cpu"]
            total["mb"] += row["rss"] / 1024
    names = Counter(total["name"] for total in totals.values())
    for root, total in totals.items():
        if names[total["name"]] > 1:
            total["name"] = root
    return sorted(totals.values(), key=lambda row: (-row["cpu"], row["name"]))


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def heavy_slots_path():
    return Path.home() / ".claude/heavy-slots"


def read_slots(directory=None):
    directory = directory if directory is not None else heavy_slots_path()
    try:
        paths = sorted(directory.iterdir())
    except FileNotFoundError:
        return {}, {}
    except OSError as error:
        return None, {"slots": error}
    rows, errors = {}, {}
    for path in paths:
        if path.name.startswith("."):
            continue
        key = "slot:" + path.name
        try:
            info = path.stat()
            if stat.S_ISDIR(info.st_mode):
                rows[key] = dict(name=path.name, directory=True, alive=False)
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            pid = int(path.read_text().strip())
            if pid <= 0:
                raise ValueError("slot PID must be positive")
            rows[key] = dict(name=path.name, directory=False, pid=pid, alive=process_alive(pid))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, OverflowError) as error:
            errors[key] = error
    return rows, errors


def live_slots(directory=None):
    rows, errors = read_slots(directory)
    return (sum(row["alive"] for row in rows.values()) if rows is not None else None,
            sorted(errors) if rows is not None else [])


class MacBalance:
    def metrics(self, command):
        return dict(free=balance_read(lambda: mac_free(command)),
                    load5=balance_read(lambda: mac_load(command, 1)))

    def processes(self, command):
        text = balance_read(lambda: command("ps", "-U", str(os.getuid()), "-o", "pid=,%cpu=,rss=,stat=,uid="), "ps")
        directories = balance_read(lambda: command("lsof", "-n", "-d", "cwd", "-Fpn"), "lsof")
        if text is None or directories is None:
            return [], ["processes"]
        cwd = {}
        pid = None
        for line in directories.splitlines():
            if line.startswith("p"):
                pid = line[1:]
            elif line.startswith("n") and pid:
                cwd[pid] = line[1:]
        rows, errors = [], []
        for line in text.splitlines():
            values = line.split()
            pid = values[0] if values else "unknown"
            try:
                if int(values[4]) != os.getuid():
                    continue
                if values[3].startswith("Z"):
                    continue
                if pid not in cwd:
                    if pid.isdigit() and not process_alive(int(pid)):
                        continue
                    errors.append("pid:" + pid)
                    continue
                rows.append(dict(pid=pid, cwd=cwd[pid], cpu=float(values[1]), rss=int(values[2])))
            except (ValueError, IndexError):
                errors.append("pid:" + pid)
        return rows, errors


def process_cpu(ticks, old_ticks, elapsed, frequency, age):
    if old_ticks is None or elapsed <= 0 or ticks < old_ticks:
        return 100 * ticks / frequency / max(age, 1 / frequency)
    return 100 * (ticks - old_ticks) / frequency / elapsed


class LinuxBalance:
    def __init__(self, proc=Path("/proc"), ticks=None):
        self.host = LinuxHost(proc)
        self.proc = Path(proc)
        self.ticks = ticks or os.sysconf("SC_CLK_TCK")

    def metrics(self, command):
        return dict(free=balance_read(self.host.free),
                    load5=balance_read(lambda: float(fields((self.proc / "loadavg").read_text(), 2)[1])))

    def process(self, path):
        stat = (path / "stat").read_text()
        values = fields(stat[stat.rindex(")") + 1:], 22)
        # Zombies and kernel threads cannot contribute worktree load.
        if values[0] == "Z" or int(values[6]) & 0x00200000:
            return None
        return dict(pid=path.name, cwd=str((path / "cwd").readlink()),
                    identity=path.name + ":" + values[19], start=int(values[19]), ticks=int(values[11]) + int(values[12]),
                    rss=int(values[21]) * os.sysconf("SC_PAGE_SIZE") / 1024)

    def owned_processes(self):
        paths = balance_read(lambda: list(self.proc.iterdir()))
        if paths is None:
            return None, ["processes"]
        rows, errors = [], []
        for path in paths:
            if not path.name.isdigit() or not path.is_dir():
                continue
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                row = self.process(path)
                if row is not None:
                    rows.append(row)
            except (OSError, ValueError, IndexError, MissingMetric):
                if path.exists():
                    errors.append("pid:" + path.name)
        return rows, errors

    def sample(self, previous):
        rows, errors = self.owned_processes()
        uptime = balance_read(lambda: float(fields((self.proc / "uptime").read_text(), 1)[0]))
        if uptime is None or rows is None:
            return dict(rows=[], errors=errors + ([] if rows is None else ["processes"]), cpu_sample=None)
        previous = previous or {}
        elapsed = uptime - previous.get("uptime", uptime)
        counters = {}
        for row in rows:
            counters[row["identity"]] = row["ticks"]
            row["cpu"] = process_cpu(row["ticks"], previous.get("ticks", {}).get(row["identity"]),
                                     elapsed, self.ticks, uptime - row["start"] / self.ticks)
        return dict(rows=rows, errors=errors, cpu_sample=dict(uptime=uptime, ticks=counters))


BALANCE = MacBalance() if sys.platform == "darwin" else LinuxBalance()


def balance_metrics(command, limits, slots=None, adapter=None, before=None):
    adapter = adapter or BALANCE
    raw = adapter.metrics(command)
    raw["slots"], slot_errors = live_slots(slots)
    if isinstance(adapter, LinuxBalance):
        sample = adapter.sample((before or {}).get("cpu_sample"))
        rows, errors = sample["rows"], sample["errors"]
        raw["cpu_sample"] = sample["cpu_sample"]
    else:
        rows, errors = adapter.processes(command)
        raw["cpu_sample"] = None
    raw["top"] = worktree_totals(rows, errors)
    raw["heavy"] = sum(row["cpu"] >= limits["cpu"] for row in raw["top"])
    raw["unreadable"] = sorted(errors + slot_errors)
    if errors and raw["heavy"] <= limits["heavy"]:
        raw["heavy"] = None
    if slot_errors and raw["slots"] is not None and raw["slots"] <= limits["slots"]:
        raw["slots"] = None
    return raw
