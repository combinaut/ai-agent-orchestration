#!/usr/bin/env bash
# Dispatches validate.yml on each release PR branch listed in the release-please "prs" JSON ($PRS).
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 1
root=$(pwd -P)

jq -r '.[].headBranchName' <<<"$PRS" | while read -r branch; do
  python3 "$root/lib/github_write.py" --timeout 240 workflow run validate.yml --repo "$GITHUB_REPOSITORY" --ref "$branch"
done
