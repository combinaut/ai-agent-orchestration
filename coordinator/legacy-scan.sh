#!/bin/bash
# Lists lines a PR adds that build on legacy front-end code. Usage: legacy-scan.sh <pr-number> [repo] (defaults to watchers.primary_repo)
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
. "$root/watchers/coord-lib.sh"
orch_load_config
orch_export_config
exec "${ORCH_PYTHON3:-python3}" "$root/watchers/audit.py" "$@"
