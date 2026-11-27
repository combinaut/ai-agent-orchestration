"""Prints `<start> <name>` for a pid and nothing when it cannot be read. exec keeps the start; a reused pid gets a new one."""

import ctypes
import sys
from pathlib import Path

PROC_PIDTBSDINFO = 3


class BsdInfo(ctypes.Structure):
    _fields_ = [
        ("head", ctypes.c_uint32 * 12),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("tail", ctypes.c_uint32 * 6),
        ("start_sec", ctypes.c_uint64),
        ("start_usec", ctypes.c_uint64),
    ]


def linux_identity(pid):
    stat = Path(f"/proc/{pid}/stat").read_text(errors="replace")
    head, _, tail = stat.rpartition(") ")
    return tail.split()[19], head.partition("(")[2]


def mac_identity(pid):
    info = BsdInfo()
    size = ctypes.sizeof(info)
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    read = libproc.proc_pidinfo(
        pid, PROC_PIDTBSDINFO, ctypes.c_uint64(0), ctypes.byref(info), size
    )
    if read != size:
        raise OSError(f"cannot read pid {pid}")
    name = (info.name or info.comm).decode(errors="replace")
    return f"{info.start_sec}.{info.start_usec:06d}", name


def main():
    pid = int(sys.argv[1])
    identity = mac_identity if sys.platform == "darwin" else linux_identity
    try:
        start, name = identity(pid)
    except OSError:
        return 1
    print(start, " ".join(name.split()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
