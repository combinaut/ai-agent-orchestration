#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y shellcheck bats bubblewrap
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
