#!/usr/bin/env python3
"""Show one user question and atomically record its answer."""

import argparse
import os
import sys
import tempfile
import termios
import time
import tty
from pathlib import Path


def answer_path(question):
    return Path(question).with_suffix(".answer")


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text.strip() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def show(question, status=""):
    os.system("clear")
    print(
        Path(question)
        .read_text()
        .rstrip()
        .replace("Answer in the Coordinator pane.", "")
    )
    print("\nPress 1-9 to answer, or t to type an answer.")
    if status:
        print("\n" + status)


def read_key():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        return os.read(fd, 1).decode("latin1")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def follow(question):
    answer = answer_path(question)
    show(question)
    escape = ""
    while True:
        key = read_key()
        if not key:
            return
        if key == "\x1b":
            escape = "start"
            continue
        if escape:
            if escape == "start":
                escape = "sequence" if key in "[O" else ""
            elif "@" <= key <= "~":
                escape = ""
            continue
        if key in "123456789":
            atomic_write(answer, key)
            show(question, f"Sent: {key}. The Coordinator will pick it up.")
        elif key == "t":
            show(question)
            text = input("Your answer: ")
            if text.strip():
                atomic_write(answer, text)
                show(
                    question, f"Sent: {text.strip()}. The Coordinator will pick it up."
                )
            else:
                show(question)
        time.sleep(0.05)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("question", type=Path)
    args = parser.parse_args(argv)
    if not args.question.is_file():
        parser.error(f"question is not a regular file: {args.question}")
    follow(args.question)


if __name__ == "__main__":
    main()
