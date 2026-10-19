#!/usr/bin/env bash
set -euo pipefail

state_dir=$(mktemp -d)
: >"$state_dir/no-config"
ORCH_CONFIG="$state_dir/no-config" ORCH_HOG_GB=0.001 coordinator/watchers/coord-hog-watch.sh --state-dir "$state_dir"
test -s "$state_dir/hog.log"
