#!/usr/bin/env bash
# Prints macos=false only when every file of the PR (both sides of a rename) is documentation.
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1

if [ "${EVENT:-}" != pull_request ]; then
  echo macos=true
  exit 0
fi
if ! files=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR/files" --paginate --jq '.[] | .filename, (.previous_filename // empty)') || [ -z "$files" ]; then
  echo macos=true
  exit 0
fi
if [ "$(wc -l <<<"$files")" -ge 3000 ] || grep -qvE '(\.md|^LICENSE)$|^docs/' <<<"$files"; then
  echo macos=true
else
  echo macos=false
fi
