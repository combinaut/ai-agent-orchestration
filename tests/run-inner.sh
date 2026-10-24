#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$ROOT"
jobs=${ORCH_TEST_JOBS:-1}
[ -n "${ORCH_TEST_JOBS:-}" ] || [ "${CI:-}" != true ] || jobs=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 1)
if [ "$jobs" -gt 1 ] && parallel --version 2>/dev/null | grep -q '^GNU parallel'; then
  bats --jobs "$jobs" tests/
else
  [ "$jobs" -le 1 ] || printf 'GNU parallel not found; bats runs one test at a time\n'
  bats tests/
fi
python3 tests/guard-bash.test.py
(cd coordinator/watchers && python3 run_tests.py)
