#!/usr/bin/env bash
# One-line installer: clones the repo if missing, then runs its install.sh, which updates the clone.
# Runs nothing unless the whole download arrived (curl's exit status gates bash):
#   s=$(curl -fsSL https://raw.githubusercontent.com/combinaut/ai-agent-orchestration/main/get.sh) && bash -c "${s:?empty download}" _
# Installer arguments follow the final _:  ... _ --env herdr
# ORCH_INSTALL_DIR sets the clone folder; otherwise install.sh decides (ORCH_REPO_PATH, the config's repo_path, ~/.claude/ai-agent-orchestration).
set -euo pipefail

REPO_SLUG="combinaut/ai-agent-orchestration"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# same_origin <url>: true when <url> names this repo, as the HTTPS URL, the SSH URL or ORCH_REPO_URL.
same_origin() {
  local want got
  want=${ORCH_REPO_URL:-https://github.com/$REPO_SLUG.git}
  want=${want%/}
  got=${1%/}
  [ "${got%.git}" = "${want%.git}" ] && return 0
  case "${got%.git}" in
    "https://github.com/$REPO_SLUG" | "git@github.com:$REPO_SLUG" | "ssh://git@github.com/$REPO_SLUG") return 0 ;;
  esac
  return 1
}

main() {
  local dir url origin dirty tmp=""

  [ "${BASH_VERSINFO[0]}" -gt 3 ] || { [ "${BASH_VERSINFO[0]}" -eq 3 ] && [ "${BASH_VERSINFO[1]}" -ge 2 ]; } ||
    die "bash 3.2 or newer is required (this is $BASH_VERSION)"
  case "$(uname -s)" in
    Darwin | Linux) ;;
    *) die "unsupported platform: $(uname -s). The installer supports macOS and Linux" ;;
  esac
  command -v git >/dev/null 2>&1 || die "git not found. Install git, then run this again"
  [ -n "${HOME:-}" ] || die "HOME is not set"

  url=${ORCH_REPO_URL:-https://github.com/$REPO_SLUG.git}
  dir=${ORCH_INSTALL_DIR:-}
  case "$dir" in \~/*) dir="$HOME/${dir#\~/}" ;; esac
  if [ -z "$dir" ]; then
    command -v curl >/dev/null 2>&1 || die "curl not found. Install curl, then run this again"
    tmp=$(mktemp "${TMPDIR:-/tmp}/ao-install.XXXXXX")
    curl -fsSL "${ORCH_INSTALLER_URL:-https://raw.githubusercontent.com/$REPO_SLUG/main/install.sh}" -o "$tmp" || {
      rm -f "$tmp"
      die "cannot download install.sh. Check the network and run this again"
    }
    dir=$(/bin/bash "$tmp" --print-repo-path) || dir=""
    rm -f "$tmp"
    [ -n "$dir" ] || die "cannot resolve the clone folder from install.sh"
  fi

  if [ ! -e "$dir" ]; then
    mkdir -p "$(dirname "$dir")"
    git clone -q "$url" "$dir"
    echo "cloned          $dir"
  else
    [ -e "$dir/.git" ] || die "$dir exists but is not a git clone. Move it away or set ORCH_INSTALL_DIR"
    origin=$(git -C "$dir" remote get-url origin 2>/dev/null || true)
    same_origin "$origin" || die "$dir has origin '${origin:-none}', not $REPO_SLUG. Move it away or set ORCH_INSTALL_DIR"
    dirty=$(git -C "$dir" status --porcelain)
    if [ -n "$dirty" ]; then
      {
        echo "REFUSED: $dir has uncommitted changes:"
        printf '%s\n' "$dirty" | sed 's/^/  /'
        echo "Nothing was updated. Commit or discard them, then run this again."
      } >&2
      exit 1
    fi
  fi

  [ -f "$dir/install.sh" ] || die "$dir has no install.sh"
  export ORCH_REPO_PATH="$dir"
  # The clone's install.sh fast-forwards the clone, refusing local commits and upstreams outside origin.
  # With curl | bash, stdin is the script; hand the installer the terminal for its questions.
  if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then
    exec /bin/bash "$dir/install.sh" "$@" </dev/tty
  fi
  exec /bin/bash "$dir/install.sh" "$@"
}

# A cut-off download ends in an unclosed brace, a syntax error that runs nothing.
{ main "$@"; }
