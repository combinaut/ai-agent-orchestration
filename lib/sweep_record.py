#!/usr/bin/env python3
"""Checks the record /finalize-sweep writes at the end of its validate step."""

import argparse
import json
import sys

COUNTS = ("slices", "sweep_agents", "validators", "findings")
FIELDS = ("head", "base", *COUNTS, "validator_ids", "verdicts")


def problem(path, head):
    """Why the record at path does not prove a sweep of head, else None."""
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return f"{path}: no sweep record"
    except (OSError, ValueError) as error:
        return f"{path}: not valid JSON: {error}"
    if not isinstance(record, dict):
        return f"{path}: the record must be a JSON object"
    missing = [field for field in FIELDS if field not in record]
    if missing:
        return f"{path}: missing field {missing[0]}"
    if record["head"] != head:
        return f"{path}: swept {record['head']}, the PR head is {head}"
    for field in COUNTS:
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"{path}: {field} must be a non-negative integer"
    verdicts = record["verdicts"]
    if not isinstance(verdicts, dict) or any(
        isinstance(n, bool) or not isinstance(n, int) or n < 0
        for n in verdicts.values()
    ):
        return f"{path}: verdicts must map each verdict to a count"
    ids = record["validator_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        return f"{path}: validator_ids must be a list of non-empty strings"
    if len(ids) != record["validators"]:
        return f"{path}: {len(ids)} validator_ids for {record['validators']} validators"
    if record["findings"] > 0 and record["validators"] < 1:
        return f"{path}: {record['findings']} findings and no validator started"
    if sum(verdicts.values()) != record["findings"]:
        return f"{path}: verdicts add up to {sum(verdicts.values())}, findings is {record['findings']}"
    return None


def main(argv):
    parser = argparse.ArgumentParser(prog="sweep_record.py")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--record", required=True)
    check.add_argument("--head", required=True)
    args = parser.parse_args(argv[1:])
    found = problem(args.record, args.head)
    if found:
        print(f"ERROR: {found}", file=sys.stderr)
        return 1
    print(f"OK: {args.record} covers {args.head}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
