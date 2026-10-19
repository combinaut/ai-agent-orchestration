#!/usr/bin/env bash
set -euo pipefail

find . -type f \( -name '*.sh' -o -name '*.bash' \) -print0 | xargs -0 shellcheck && shellcheck -s bash tests/*.bats
