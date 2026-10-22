#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 1

# Keep in step with required-version in ruff.toml and docs/contributing.md.
version=0.17.1

if command -v uvx >/dev/null; then
  ruff() { uvx "ruff@$version" "$@"; }
else
  venv="${XDG_CACHE_HOME:-$HOME/.cache}/ruff-$version"
  [ -x "$venv/bin/ruff" ] || { python3 -m venv "$venv" && "$venv/bin/pip" install --quiet "ruff==$version"; }
  ruff() { "$venv/bin/ruff" "$@"; }
fi

ruff check . && ruff format --check .
