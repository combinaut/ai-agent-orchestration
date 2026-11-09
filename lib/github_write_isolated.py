#!/usr/bin/env python3
"""Run github_write with only this trusted library directory on sys.path."""

import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import github_write  # noqa: E402


def cancel(number, _frame):
    """Let capture reap its separate supervisor group before this writer exits."""
    raise SystemExit(128 + number)


for number in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(number, cancel)

sys.exit(github_write.main())
