"""Question text for terminal display and user escalation."""

import re
from pathlib import Path

OPTION = re.compile(r"^([1-9])\. (.*)$")
CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_controls(text):
    return CONTROLS.sub("", text)


def read_question(path):
    with Path(path).open(newline="") as stream:
        return strip_controls(stream.read())


def option_rows(text):
    rows = None
    for index, line in enumerate(text.splitlines()):
        if rows is not None:
            if line.strip().startswith("#"):
                break
            match = OPTION.match(line)
            if match:
                rows.append((index, match))
        elif line.strip() == "## Options":
            rows = []
    return rows
