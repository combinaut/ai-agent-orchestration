#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 1

state_dir=$(mktemp -d)
: >"$state_dir/no-config"
# shellcheck disable=SC2016 # The generated stub expands these variables at runtime.
printf '#!/bin/sh\ncat "$HERDR_AGENTS"\n' >"$state_dir/herdr"
chmod +x "$state_dir/herdr"
HERDR_ENV=1 HERDR_BIN_PATH="$state_dir/herdr" HERDR_AGENTS="$PWD/coordinator/watchers/fixtures/workers.json" ORCH_CONFIG="$state_dir/no-config" coordinator/watchers/coord-worker-watch.sh --state-dir "$state_dir"
python3 -c 'import json,sys; state=json.load(open(sys.argv[1])); assert state["fails"] == 0 and state["before"]["present"]' "$state_dir/workers.json"
