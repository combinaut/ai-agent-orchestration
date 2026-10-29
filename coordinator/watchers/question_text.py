"""Question text for terminal display and user escalation."""

import re
from pathlib import Path

CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_controls(text):
    return CONTROLS.sub("", text)


def read_question(path):
    with Path(path).open(newline="") as stream:
        return strip_controls(stream.read())
