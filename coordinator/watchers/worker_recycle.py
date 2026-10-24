"""Worker thresholds and architect decisions; no messaging or process control."""

import json
import os
import re
import sys
from pathlib import Path

# Fallback windows live only here. Unknown models require architect confirmation.
MODEL_WINDOWS = dict.fromkeys(
    (
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-fable-5-1",
        "gpt-5.6-terra",
        "gpt-6.1-sol",
    ),
    200000,
)


def model_window(value):
    if value.get("window"):
        return int(value["window"])
    model = value.get("model", "")
    if model.startswith("gpt-"):
        try:
            cache = json.loads((Path.home() / ".codex/models_cache.json").read_text())
            for row in cache["models"]:
                if row.get("slug") == model and int(row["context_window"]) > 0:
                    return int(row["context_window"])
        except (OSError, KeyError, TypeError, ValueError):
            pass
    return MODEL_WINDOWS.get(model, 200000)


def limits(value):
    soft = int(os.environ.get("ORCH_WORKER_SOFT_TOKENS", 300000))
    hard = int(os.environ.get("ORCH_WORKER_HARD_TOKENS", 450000))
    window = model_window(value)
    if window:
        hard = min(hard, int(int(window) * 0.6))
    return soft, hard


def stage(value):
    soft, hard = limits(value)
    tokens = value.get("tokens")
    if tokens is not None:
        return "HARD" if tokens >= hard else "SOFT" if tokens >= soft else None
    if value.get("remaining") is not None:
        used = 100 - float(value["remaining"])
        if used * model_window(value) / 100 >= hard:
            return "HARD"
        if used >= float(os.environ.get("ORCH_RECYCLE_PCT", 55)):
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
    if answer.strip() == "NOW":
        return "HANDOFF"
    finish = re.fullmatch(r"FINISH ([0-9]+) (\S.*)", answer.strip())
    if finish:
        soft, hard = limits(value)
        return "WAIT_DONE" if soft + int(finish[1]) < hard else "HANDOFF_CLEAN_POINT"
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
