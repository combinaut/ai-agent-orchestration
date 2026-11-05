#!/usr/bin/env python3
"""Report what is typed in a Claude Code composer.

composer-state.py <pane> prints one of:
  empty           exit 0: no input; dim text (SGR 2) is a prompt suggestion
  typed: <text>   exit 1: real input, including a pending paste
  unknown         exit 2: no Claude composer on screen, or the pane is unreadable
"""

import os
import re
import subprocess
import sys

ESCAPE = re.compile(
    r"\x1b\[([0-9;:]*)m"  # SGR
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # other CSI
    r"|\x1b[@-_]"
)
BROAD_COLOR = {"38", "48", "58"}
PROMPT = "❯"
BASH_PROMPT = "!"


def dim_after(params, dim):
    """The dim state after one SGR parameter list; colour arguments are not attributes."""
    codes = [code.lstrip("0") or "0" for code in params.split(";")]
    index = 0
    while index < len(codes):
        code = codes[index]
        if code in BROAD_COLOR:
            mode = codes[index + 1] if index + 1 < len(codes) else ""
            index += {"5": 3, "2": 5}.get(mode, 2)
            continue
        if code in ("0", "22"):
            dim = False
        elif code == "2":
            dim = True
        index += 1
    return dim


def parse_lines(screen):
    """(all text, the same text with dim characters blanked) per line of an ANSI snapshot."""
    lines = []
    dim = False
    for raw in screen.split("\n"):
        plain, undimmed = [], []
        position = 0
        for match in [*ESCAPE.finditer(raw), None]:
            chunk = raw[position : match.start() if match else None]
            plain.append(chunk)
            undimmed.append(" " * len(chunk) if dim else chunk)
            if match:
                position = match.end()
                if match.group(1) is not None:
                    dim = dim_after(match.group(1), dim)
        lines.append(("".join(plain), "".join(undimmed)))
    return lines


def is_rule(plain):
    """Composer borders start at column 0; typed continuation lines are indented."""
    return plain.startswith("───")


def is_prompt(plain):
    return plain.startswith((PROMPT, BASH_PROMPT))


def typed_text(screen):
    """The composer's input with dim text removed, or None without a composer."""
    lines = parse_lines(screen)
    rules = [i for i, (plain, _) in enumerate(lines) if is_rule(plain)]
    if not rules:
        return None
    bottom = rules[-1]
    top = next((i for i in reversed(rules[:-1]) if is_prompt(lines[i + 1][0])), None)
    if top is None:
        return None
    body = [undimmed for _, undimmed in lines[top + 1 : bottom]]
    body[0] = body[0].removeprefix(PROMPT)
    return "\n".join(part.strip() for part in body).strip()


def main(argv):
    if len(argv) != 2:
        print("usage: composer-state.py <pane>", file=sys.stderr)
        return 2
    herdr = (
        os.environ.get("HERDR_BIN_PATH") or os.environ.get("ORCH_HERDR_BIN") or "herdr"
    )
    try:
        screen = subprocess.run(
            [herdr, "pane", "read", argv[1], "--source", "visible", "--ansi"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as error:
        print(f"composer-state.py: {error}", file=sys.stderr)
        print("unknown")
        return 2
    text = typed_text(screen)
    if text is None:
        print("unknown")
        return 2
    if text:
        print(f"typed: {text}")
        return 1
    print("empty")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
