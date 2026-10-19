#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 1

find . -type f \( -name '*.sh' -o -name '*.bash' \) -print0 | xargs -0 shellcheck && shellcheck -s bash tests/*.bats
