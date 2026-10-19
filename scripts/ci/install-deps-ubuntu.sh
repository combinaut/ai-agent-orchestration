#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 1

sudo apt-get update
sudo apt-get install -y shellcheck bats bubblewrap ripgrep parallel
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
