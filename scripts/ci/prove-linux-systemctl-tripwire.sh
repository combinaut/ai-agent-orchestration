#!/usr/bin/env bash
set -euo pipefail

. tests/platform/linux.sh
proof=$(mktemp)
trap 'rm -f "$proof" "$proof.log"' EXIT
printf '#!/bin/bash\n/usr/bin/systemctl --user stop fixture 2>/dev/null || true\n' >"$proof"
if run_isolated_tests "$PWD" "$proof" >"$proof.log" 2>&1; then
  echo 'runner systemctl tripwire did not deny execution' >&2
  exit 1
fi
cat "$proof.log"
grep -F '/usr/bin/systemctl --user stop fixture' "$proof.log"
