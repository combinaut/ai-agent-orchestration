#!/usr/bin/env bash
# Prints macos=false only when every file of the PR (both sides of a rename) is documentation.
set -uo pipefail

if [ "${EVENT:-}" != pull_request ]; then
  echo macos=true
  exit 0
fi
if ! files=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR/files" --paginate --jq '.[] | .filename, (.previous_filename // empty)') || [ -z "$files" ]; then
  echo macos=true
  exit 0
fi
if grep -qvE '(\.md|^LICENSE)$|^docs/' <<<"$files"; then
  echo macos=true
else
  echo macos=false
fi
