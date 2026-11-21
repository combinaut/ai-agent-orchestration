#!/usr/bin/env python3
"""Data side of the to-do panel: render the open items for the page, apply the answers it sends back."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

APPLIED_KEEP = 1000
ID_MAX = 64


def view():
    spec = importlib.util.spec_from_file_location(
        "user_todo_view", Path(__file__).resolve().parent / "user-todo-view.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def panel_dir():
    return Path.home() / ".claude/coordinator/todo-panel"


def read_json(path, fallback):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


def item_key(owner, text, generation):
    return hashlib.sha256(f"{owner}\0{text}\0{generation}".encode()).hexdigest()[:32]


def display_text(todo_answers, text):
    return " ".join(todo_answers.CHOICE.sub("", text).split())


def stage(directory, target):
    """Copy the page and its data where the Artifact tool may read them."""
    target.mkdir(parents=True, exist_ok=True)
    for name in ("todo-panel.html", "todo-panel.js"):
        shutil.copyfile(Path(__file__).resolve().parent / name, target / name)
    shutil.copyfile(directory / "items.json", target / "items.json")


def locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    stream = (directory / ".lock").open("a")
    fcntl.flock(stream, fcntl.LOCK_EX)
    return stream


def render(module, directory):
    todo_answers, generator = module.todo_answers, module.generator()
    hidden = module.Hidden(module.HIDDEN, module.FEED)
    rows = hidden.visible(module.load_items(False))
    manifest, items, seen = {}, [], set()
    errors = read_json(directory / "errors.json", {})
    for owner, text, _flagged, _note in rows:
        generation = generator.answer_generation(owner, text)
        key = item_key(owner, text, generation)
        if key in seen:
            continue
        seen.add(key)
        manifest[key] = {"owner": owner, "text": text, "generation": generation}
        items.append(
            {
                "key": key,
                "owner": owner,
                "text": display_text(todo_answers, text),
                "choices": list(todo_answers.choices(text).values()),
                "error": errors.get(key, ""),
            }
        )
    module.atomic_write(directory / "manifest.json", json.dumps(manifest))
    module.atomic_write(
        directory / "errors.json",
        json.dumps({key: reason for key, reason in errors.items() if key in seen}),
    )
    page = {
        "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "items": items,
    }
    module.atomic_write(directory / "items.json", json.dumps(page, indent=1))
    return len(items)


def well_formed(known):
    """True for a manifest value shaped as render writes it."""
    return (
        isinstance(known, dict)
        and isinstance(known.get("owner"), str)
        and isinstance(known.get("text"), str)
        and (known.get("generation") is None or isinstance(known["generation"], int))
    )


def reject(entry, manifest, todo_answers):
    """The reason an entry cannot be sent, or an empty string."""
    answer = entry.get("answer")
    key = entry.get("key")
    known = manifest.get(key) if isinstance(key, str) else None
    if not well_formed(known):
        return "unknown item"
    if (
        not isinstance(answer, str)
        or not answer.strip()
        or len(answer) > todo_answers.ANSWER_MAX
    ):
        return "answer must contain 1-%d characters" % todo_answers.ANSWER_MAX
    choices = todo_answers.choices(known["text"])
    if choices and answer not in choices.values():
        return "answer is not one of the choices"
    return ""


def apply(module, directory, source):
    todo_answers = module.todo_answers
    try:
        entries = json.loads(Path(source).read_text())["answers"]
        if not isinstance(entries, list):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError):
        print(f"error: {source} is not an answers file", file=sys.stderr)
        return 2
    manifest = read_json(directory / "manifest.json", {})
    errors = read_json(directory / "errors.json", {})
    applied = read_json(directory / "applied.json", {})
    generator = module.generator()

    def save():
        kept = dict(list(applied.items())[-APPLIED_KEEP:])
        module.atomic_write(directory / "applied.json", json.dumps(kept))
        module.atomic_write(directory / "errors.json", json.dumps(errors))

    for entry in entries:
        identifier = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(identifier, str) or not 0 < len(identifier) <= ID_MAX:
            print("failed <no id>: entry has no valid id")
            continue
        if identifier in applied:
            print(f"skipped {identifier}: already {applied[identifier]}")
            continue
        key = entry.get("key")
        reason = reject(entry, manifest, todo_answers)
        if not reason:
            known = manifest[key]
            try:
                generator.answer_from_view(
                    known["owner"],
                    known["text"],
                    entry["answer"],
                    module.TODO,
                    known["generation"],
                )
            except (ValueError, OSError, TimeoutError) as error:
                reason = str(error) or type(error).__name__
        if reason:
            applied[identifier] = "failed"
            if isinstance(key, str) and key in manifest:
                errors[key] = reason
            print(f"failed {identifier}: {reason}")
        else:
            applied[identifier] = "queued"
            errors.pop(key, None)
            print(f"queued {identifier}")
        save()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render", help="write items.json for the page")
    render_parser.add_argument(
        "--stage", help="also copy the page files and items.json into this directory"
    )
    apply_parser = commands.add_parser("apply", help="queue the answers in a file")
    apply_parser.add_argument("answers")
    args = parser.parse_args(argv)
    directory = panel_dir()
    module = view()
    with locked(directory):
        if args.command == "render":
            count = render(module, directory)
            if args.stage:
                stage(directory, Path(args.stage))
            print(f"{directory / 'items.json'}: {count} items")
            return 0
        return apply(module, directory, args.answers)


if __name__ == "__main__":
    sys.exit(main())
