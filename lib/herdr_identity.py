"""Architect labels shared by Herdr discovery and interactive pane lookup."""


def architect_labels(row):
    name = row.get("name") or ""
    role = (row.get("tokens") or {}).get("role")
    if role in ("worker", "coordinator") or (
        role != "architect" and not name.startswith("Architect::")
    ):
        return set()
    return {name, row.get("terminal_title_stripped")} - {None, ""}
