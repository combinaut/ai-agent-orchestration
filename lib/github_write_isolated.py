#!/usr/bin/env python3
"""Run github_write with only this trusted library directory on sys.path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import github_write  # noqa: E402

sys.exit(github_write.main())
