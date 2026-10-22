"""Render Radar commands with the absolute mise Node executable."""

import json
import pathlib
import re
import sys

manifest = pathlib.Path(sys.argv[1]).read_text()
rendered, substitutions = re.subn(
    r'^([ \t]*command[ \t]*=[ \t]*\[\s*)"node"',
    lambda match: match[1] + json.dumps(sys.argv[2], ensure_ascii=False),
    manifest,
    flags=re.MULTILINE,
)
if substitutions == 0:
    sys.exit("Radar manifest has no Node commands to pin")
if re.search(
    r"^[ \t]*(?:command|[\"']command[\"'])\s*=\s*\[\s*[\"']node[\"']",
    rendered,
    flags=re.MULTILINE,
):
    sys.exit("Radar manifest still has an unpinned Node command")
print(rendered, end="")
