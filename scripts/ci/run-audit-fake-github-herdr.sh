#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 1

state_dir=$(mktemp -d)
: >"$state_dir/no-config"
# shellcheck disable=SC2016 # The generated stub expands these variables at runtime.
printf '#!/bin/sh\ncase "$*" in *"/contents/"*) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;; esac\ncat "$AUDIT_GITHUB"\n' >"$state_dir/gh"
# shellcheck disable=SC2016 # The generated stub expands these variables at runtime.
printf '#!/bin/sh\ncat "$AUDIT_HERDR"\n' >"$state_dir/herdr"
chmod +x "$state_dir/gh" "$state_dir/herdr"
python3 -c 'import json; pr=json.load(open("coordinator/watchers/fixtures/audit-pr.json")); print(json.dumps({"data":{"search":{"nodes":[pr],"pageInfo":{"hasNextPage":False}}}}))' >"$state_dir/github.json"
printf '{"result":{"workspaces":[]}}' >"$state_dir/herdr.json"
PATH="$state_dir:$PATH" AUDIT_GITHUB="$state_dir/github.json" AUDIT_HERDR="$state_dir/herdr.json" HERDR_BIN_PATH="$state_dir/herdr" ORCH_CONFIG="$state_dir/no-config" ORCH_PR_AUTHOR=author ORCH_PR_REPOS=owner/product coordinator/watchers/coord-audit-watch.sh --state-dir "$state_dir"
python3 -c 'import json,sys; state=json.load(open(sys.argv[1])); assert state["fails"] == 0 and not state["before"]["unreadable"]' "$state_dir/audit.json"
