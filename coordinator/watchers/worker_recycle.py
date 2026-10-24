"""Worker thresholds and architect decisions; no messaging or process control."""

import json
import os
import re
import sys
from pathlib import Path


def model_window(value):
    try:
        window = int(value.get("window", 0))
        if window > 0:
            return window
    except (TypeError, ValueError):
        pass
    model = value.get("model", "")
    if model.startswith("gpt-"):
        try:
            cache = json.loads((Path.home() / ".codex/models_cache.json").read_text())
        except (OSError, ValueError):
            return None
        rows = cache.get("models") if isinstance(cache, dict) else None
        if not isinstance(rows, list):
            return None
        window = None
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("slug"), str)
                or type(row.get("context_window")) is not int
                or row["context_window"] <= 0
            ):
                return None
            if row["slug"] == model:
                window = row["context_window"]
        return window
    return None


def limits(value):
    soft = int(os.environ.get("ORCH_WORKER_SOFT_TOKENS", 300000))
    hard = int(os.environ.get("ORCH_WORKER_HARD_TOKENS", 450000))
    window = model_window(value)
    hard = min(hard, int(window * 0.6)) if window is not None else max(soft, hard)
    return soft, hard


def used_tokens(value):
    if value.get("tokens") is not None:
        return int(value["tokens"])
    window = model_window(value)
    if window is not None and value.get("remaining") is not None:
        return (100 - float(value["remaining"])) * window / 100
    return None


def stage(value):
    soft, hard = limits(value)
    tokens = used_tokens(value)
    if tokens is not None:
        return "HARD" if tokens >= hard else "SOFT" if tokens >= soft else None
    if value.get("remaining") is not None:
        if 100 - float(value["remaining"]) >= float(
            os.environ.get("ORCH_RECYCLE_PCT", 55)
        ):
            return "SOFT"
    return None


def decision(
    value,
    answer="",
    slot=False,
    elapsed=0,
    idle=False,
    pending=False,
    hard_pending=False,
):
    """Called by the architect with a fresh reading and verified live slot ownership."""
    if slot:
        return "WAIT_SLOT"
    if hard_pending or stage(value) == "HARD":
        return "HANDOFF_CALL_BOUNDARY"
    if answer.strip().removesuffix(".").upper() == "NOW":
        return "HANDOFF"
    finish = re.fullmatch(
        r"FINISH\s+([0-9]+)(k)?\s+(\S.*)", answer.strip(), re.IGNORECASE
    )
    if finish:
        soft, hard = limits(value)
        needed = int(finish[1]) * (1000 if finish[2] else 1)
        return (
            "WAIT_DONE"
            if max(soft, used_tokens(value) or 0) + needed < hard
            else "HANDOFF_CLEAN_POINT"
        )
    if elapsed >= int(os.environ.get("ORCH_WORKER_ASK_TIMEOUT_S", 600)):
        return "ASK" if idle else "REASK_WHEN_IDLE"
    if not answer and (pending or elapsed > 0):
        return "WAIT_REPLY"
    return "ASK" if not answer else "ASK_VALID_REPLY"


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
    import orch_settings

    try:
        os.environ.update(
            orch_settings.resolved_global(
                orch_settings.config_path(os.environ), os.environ
            )
        )
    except orch_settings.ConfigError as error:
        sys.exit(f"ERROR: {error}")
    print(decision(**json.load(sys.stdin)))
