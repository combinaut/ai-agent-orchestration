"""The action filter over feed lines and the text an agent may be shown; feed-actions.regex holds the pattern."""

import re
from pathlib import Path

ACTIONS_PATH = Path(__file__).resolve().parent / "feed-actions.regex"
PR_TEXT = re.compile(r"(pulls: \S+ \S+#\d+) owner=\S+.* (\([a-z]+\))$")


def action_patterns(path=ACTIONS_PATH):
    return [re.compile(line) for line in path.read_text().splitlines() if line]


def is_action(line, patterns):
    return any(pattern.search(line) for pattern in patterns)


def native(line):
    """A pulls line keeps only its kind, PR and reason, never PR text."""
    return PR_TEXT.sub(r"\1 \2; read the PR on GitHub", line)
