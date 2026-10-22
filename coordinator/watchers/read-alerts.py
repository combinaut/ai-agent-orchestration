#!/usr/bin/env python3
"""Persistent Monitor source; cursor belongs to this reader, never a live watcher."""

import argparse
import fcntl
import json
import time
from pathlib import Path

import watcher_runtime as runtime
from watchers import MODES, WATCH_TIMING, atomic

TERMINAL_KINDS = ("MERGED", "CLOSED")


def terminal_alerts(entries):
    out = []
    for entry in entries:
        alert = json.loads(entry["line"])
        ends = [
            line
            for line in alert.get("events", [])
            if line.split(" ", 1)[0] in TERMINAL_KINDS
        ]
        if ends:
            out.append(
                dict(
                    alert, events=ends, **({"at": entry["at"]} if "at" in entry else {})
                )
            )
    return out


def read_once(base, cursor, now, stale_after, modes=MODES):
    events = []
    for mode in modes:
        old = cursor.setdefault(mode, {"seq": 0, "stale": False, "since": now})
        grace = max(stale_after, WATCH_TIMING[mode]["heartbeat_grace"])
        try:
            state = json.loads((base / (mode + ".json")).read_text())
            if state.get("reset") and old.get("reset") != state["reset"]:
                old.update(seq=0, reset=state["reset"])
                if state.get("reset_line") and (
                    not state["events"] or state["events"][0]["seq"] > 1
                ):
                    events.append({"mode": mode, "events": [state["reset_line"]]})
            if state["events"] and old["seq"] < state["events"][0]["seq"] - 1:
                # Batches between the cursor and the kept history are gone; the reader resumes from the snapshot,
                # plus the kept MERGED and CLOSED lines a snapshot cannot hold.
                events.append({"mode": mode, "gap": True})
                events += terminal_alerts(state["events"])
                old["seq"] = state["events"][-1]["seq"]
            for entry in state["events"]:
                if entry["seq"] > old["seq"]:
                    alert = json.loads(entry["line"])
                    if "at" in entry:
                        alert["at"] = entry["at"]
                    events.append(alert)
                    old["seq"] = entry["seq"]
            stale = now - (base / (mode + ".heartbeat")).stat().st_mtime > grace
        except (OSError, ValueError, KeyError):
            stale = True
        # A producer the cursor first saw within its heartbeat grace may still be starting.
        stale = stale and now - old.get("since", 0) > grace
        if stale != old["stale"]:
            events.append(
                {
                    "mode": mode,
                    "events": ["HEARTBEAT STALE" if stale else "HEARTBEAT RECOVERED"],
                }
            )
            old["stale"] = stale
    return json.dumps({"alerts": events}, sort_keys=True) if events else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--cursor", required=True, type=Path)
    parser.add_argument("--stale-after", type=int, default=900)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=MODES)
    args = parser.parse_args()
    with runtime.deadline(runtime.ITERATION_TIMEOUT, "alert reader startup"):
        args.cursor.parent.mkdir(parents=True, exist_ok=True)
        lock = args.cursor.with_suffix(".lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cursor = json.loads(args.cursor.read_text()) if args.cursor.exists() else {}
    with lock:
        for _ in runtime.iterations(5, not args.once, "alert reader"):
            known = set(cursor)
            line = read_once(
                args.state_dir, cursor, time.time(), args.stale_after, args.modes
            )
            if line:
                print(line, flush=True)
            if line or set(cursor) != known:
                # At-least-once on stdout: crash before cursor commit can repeat.
                atomic(args.cursor, json.dumps(cursor, sort_keys=True))


if __name__ == "__main__":
    main()
