#!/usr/bin/env bash
# Lints, typechecks, tests and rebuilds the PR tabs extension; fails when the committed build in extension/ is stale.
set -euo pipefail

cd "$(dirname "$0")/../../chrome/pr-tabs" || exit 1

pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build

stale=$(git status --porcelain --untracked-files=all -- extension)
if [ -n "$stale" ]; then
  printf 'The committed build is stale. Run pnpm build in chrome/pr-tabs and commit extension/:\n%s\n' "$stale" >&2
  git diff --stat -- extension >&2
  exit 1
fi
