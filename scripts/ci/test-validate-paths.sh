#!/usr/bin/env bash
set -euo pipefail

extension_only() {
  for path in "$@"; do
    case "$path" in chrome/*|*.md|LICENSE|docs/*) ;; *) return 1 ;; esac
  done
}

extension_only chrome/pr-tabs/src/core.ts docs/guide.md
! extension_only chrome/pr-tabs/src/core.ts scripts/ci/pr-tabs.sh
