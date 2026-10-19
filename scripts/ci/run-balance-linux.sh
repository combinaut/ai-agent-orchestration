#!/usr/bin/env bash
set -euo pipefail

state_dir=$(mktemp -d)
: >"$state_dir/no-config"
ORCH_CONFIG="$state_dir/no-config" coordinator/watchers/coord-balance-watch.sh --state-dir "$state_dir"
test -s "$state_dir/balance.json"
python3 -c 'import json,sys; raw=json.load(open(sys.argv[1]))["before"]; assert "load5" not in raw["unreadable"] and "free" not in raw["unreadable"]; assert isinstance(raw["cpu_sample"]["ticks"], dict) and isinstance(raw["cpu_sample"]["uptime"], (int, float))' "$state_dir/balance.json"
