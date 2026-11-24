#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$ROOT"
jobs=${ORCH_TEST_JOBS:-1}
[ -n "${ORCH_TEST_JOBS:-}" ] || [ "${CI:-}" != true ] || jobs=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 1)
suites=${AO_TEST_SUITES:-all}
bats_files=()
for suite in $suites; do
  case "$suite" in
    all) bats_files=(tests/*.bats); break ;;
    bats:*)
      file="tests/${suite#bats:}.bats"
      [ -f "$file" ] || { printf 'Unknown suite: %s\n' "$suite" >&2; exit 1; }
      bats_files+=("$file")
      ;;
  esac
done
if [ "${#bats_files[@]}" -gt 0 ]; then
  if [ "$jobs" -gt 1 ] && parallel --version 2>/dev/null | grep -q '^GNU parallel'; then
    bats --jobs "$jobs" "${bats_files[@]}"
  else
    [ "$jobs" -le 1 ] || printf 'GNU parallel not found; bats runs one test at a time\n'
    bats "${bats_files[@]}"
  fi
fi
case " $suites " in *' all '*|*' guard '*) python3 tests/guard-bash.test.py ;; esac
case " $suites " in
  *' all '*|*' watchers '*) (cd coordinator/watchers && python3 run_tests.py) ;;
  *' pr-tabs-host '*) (cd coordinator/watchers && python3 run_tests.py test_pr_tabs.py) ;;
esac
