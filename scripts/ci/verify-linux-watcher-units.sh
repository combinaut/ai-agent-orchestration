#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 1

units=$(mktemp -d)
watcher_dir="$units/home with space/.claude/coordinator/watchers"
mkdir -p "$watcher_dir"
touch "$watcher_dir/coord-audit-watch.sh" "$watcher_dir/coord-worker-watch.sh" "$watcher_dir/coord-balance-watch.sh" "$watcher_dir/coord-hog-watch.sh" "$watcher_dir/coord-host-watch.sh" "$watcher_dir/context-watch.sh" "$watcher_dir/notify-inbox.py" "$watcher_dir/notify-inbox.sh"
chmod +x "$watcher_dir/coord-audit-watch.sh" "$watcher_dir/coord-worker-watch.sh" "$watcher_dir/coord-balance-watch.sh" "$watcher_dir/coord-hog-watch.sh" "$watcher_dir/coord-host-watch.sh" "$watcher_dir/context-watch.sh" "$watcher_dir/notify-inbox.py" "$watcher_dir/notify-inbox.sh"
sed "s|@ORCH_CLAUDE_DIR@|$units/home with space/.claude|g; s|@ORCH_CONFIG@|$units/custom config.toml|g; s|@ORCH_SERVICE_PYTHON@|/usr/bin/python3|g; s|@ORCH_PYTHON3@|/usr/bin/python3|g; s|@ORCH_HERDR_BIN@|/usr/bin/herdr|g; s|@ORCH_HERDR_SOCKET@|$units/socket|g; s|@ORCH_PATH@|/usr/bin:/bin|g" coordinator/watchers/service/linux/orch.coord-watcher-context.service.template >"$units/context.service"
sed "s|@ORCH_CLAUDE_DIR@|$units/home with space/.claude|g; s|@ORCH_CONFIG@|$units/custom config.toml|g; s|@ORCH_SERVICE_PYTHON@|/usr/bin/python3|g; s|@ORCH_PYTHON3@|/usr/bin/python3|g; s|@ORCH_HERDR_BIN@|/usr/bin/herdr|g; s|@ORCH_HERDR_SOCKET@|$units/socket|g; s|@ORCH_PATH@|/usr/bin:/bin|g" coordinator/watchers/service/linux/orch.coord-watcher-inbox-context.service.template >"$units/inbox.service"
sed "s|@ORCH_CLAUDE_DIR@|$units/home with space/.claude|g; s|@ORCH_CONFIG@|$units/custom config.toml|g; s|@ORCH_SERVICE_PYTHON@|/usr/bin/python3|g; s|@ORCH_HERDR_BIN@|/usr/bin/herdr|g; s|@ORCH_HERDR_SOCKET@|$units/socket|g; s|@ORCH_PATH@|/usr/bin:/bin|g" coordinator/watchers/service/linux/orch.coord-watcher-host.service.template >"$units/host.service"
sed "s|@ORCH_CLAUDE_DIR@|$units/home with space/.claude|g; s|@ORCH_CONFIG@|$units/custom config.toml|g; s|@ORCH_SERVICE_PYTHON@|/usr/bin/python3|g; s|@ORCH_HERDR_BIN@|/usr/bin/herdr|g; s|@ORCH_HERDR_SOCKET@|$units/socket|g; s|@ORCH_PATH@|/usr/bin:/bin|g" coordinator/watchers/service/linux/orch.coord-watcher-hog.service.template >"$units/hog.service"
sed "s|@ORCH_CLAUDE_DIR@|$units/home with space/.claude|g; s|@ORCH_CONFIG@|$units/custom config.toml|g; s|@ORCH_SERVICE_PYTHON@|/usr/bin/python3|g; s|@ORCH_HERDR_BIN@|/usr/bin/herdr|g; s|@ORCH_HERDR_SOCKET@|$units/socket|g; s|@ORCH_PATH@|/usr/bin:/bin|g" coordinator/watchers/service/linux/orch.coord-watcher-balance.service.template >"$units/balance.service"
sed "s|@ORCH_CLAUDE_DIR@|$units/home with space/.claude|g; s|@ORCH_CONFIG@|$units/custom config.toml|g; s|@ORCH_SERVICE_PYTHON@|/usr/bin/python3|g; s|@ORCH_HERDR_BIN@|/usr/bin/herdr|g; s|@ORCH_HERDR_SOCKET@|$units/socket|g; s|@ORCH_PATH@|/usr/bin:/bin|g" coordinator/watchers/service/linux/orch.coord-watcher-workers.service.template >"$units/workers.service"
sed "s|@ORCH_CLAUDE_DIR@|$units/home with space/.claude|g; s|@ORCH_CONFIG@|$units/custom config.toml|g; s|@ORCH_SERVICE_PYTHON@|/usr/bin/python3|g; s|@ORCH_HERDR_BIN@|/usr/bin/herdr|g; s|@ORCH_HERDR_SOCKET@|$units/socket|g; s|@ORCH_PATH@|/usr/bin:/bin|g" coordinator/watchers/service/linux/orch.coord-watcher-audit.service.template >"$units/audit.service"
touch "$units/home with space/.claude/coordinator/legacy-map.sh"
chmod +x "$units/home with space/.claude/coordinator/legacy-map.sh"
for kind in service timer; do
  sed "s|@ORCH_CLAUDE_DIR@|$units/home with space/.claude|g; s|@ORCH_LEGACY_MAP_REPO@|$units/webapp|g; s|@ORCH_SERVICE_PYTHON@|/usr/bin/python3|g; s|@ORCH_PATH@|/usr/bin:/bin|g" coordinator/service/linux/orch.coord-legacy-map.$kind.template >"$units/legacy-map.$kind"
done
systemd-analyze verify "$units/context.service" "$units/legacy-map.service" "$units/legacy-map.timer" "$units/inbox.service" "$units/host.service" "$units/hog.service" "$units/balance.service" "$units/workers.service" "$units/audit.service"
