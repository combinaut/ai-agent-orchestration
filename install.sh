#!/usr/bin/env bash
# Installs, updates and repairs the ai-agent-orchestration setup: one clone, symlinked into ~/.claude.
# Safe to run any number of times. Run it without a clone (arguments go after the final _):
#   s=$(curl -fsSL https://raw.githubusercontent.com/combinaut/ai-agent-orchestration/main/install.sh) && bash -c "${s:?empty download}" _
set -eu

REPO_SLUG="combinaut/ai-agent-orchestration"
CLAUDE_DIR="${HOME:?HOME is not set}/.claude"
CONFIG="${ORCH_CONFIG:-$CLAUDE_DIR/orchestration.toml}"
OLD_CONFIG="$CLAUDE_DIR/orchestration.env"
SNIPPET_DIR="$CLAUDE_DIR/orchestration-repo-snippets"
STAMP=$(date +%Y%m%d%H%M%S)
BACKUP_DIR="$CLAUDE_DIR/backups/$STAMP"
# State directories the watchers write to; created under ~/.claude, never linked.
RUNTIME_DIRS="coordinator/watchers/bindings coordinator/watchers/prod-context"
LOCK="$CLAUDE_DIR/.orch-install.lock"
MANIFEST="$CLAUDE_DIR/.orch-links"
MANAGED=""
HOOK_CMD="bash ~/.claude/hooks/orch-update-check.sh"
GUARD_BASH_CMD="python3 ~/.claude/hooks/guard-bash.py"
STATUSLINE_CMD="bash ~/.claude/statusline-command.sh"
# shellcheck disable=SC2016 # jq variables are evaluated by jq, not Bash.
BASH_MATCHER_JQ='def bash_matcher: . as $matcher | ($matcher == null or $matcher == "" or $matcher == "*" or ($matcher | test("(^|[|, ])Bash($|[|, ])")) or (try ("Bash" | test($matcher)) catch false));'
MANAGED_GUARD_RE='^ORCH_CONFIG=.*[[:space:]]+python3[[:space:]]+~/.claude/hooks/guard-bash\.py[[:space:]]*$'
# shellcheck disable=SC2016 # The regex matches a literal $HOME in hook commands.
SUPPORTED_GUARD_RE='^[[:space:]]*(?:(?:(?:[^[:space:]]*/)?env(?:[[:space:]]+(?:[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+|-[^[:space:]]+))*[[:space:]]+)?[^[:space:]]*python[0-9.]*[[:space:]]+)?(?:~/.claude|[\"]?\$HOME/.claude|/[^[:space:]]*\.claude)/hooks/guard-bash\.py\"?[[:space:]]*$'
CHROME_HOST="orch.pr_tabs"
LEGACY_LABEL_PREFIX="com.combinaut.coord-"

n_linked=0
SERVICES_FAILED=0
n_ok=0
n_backup=0
n_skipped=0
WATCHER_MIGRATION_PENDING=0

say() { printf '%-15s %s\n' "$1" "$2"; }
die() {
  echo "ERROR: $*" >&2
  exit 1
}

# backup_name <path>: a free path under $BACKUP_DIR mirroring <path>; its parent exists.
backup_name() {
  local base name n=0
  case "$1" in
    "$CLAUDE_DIR"/*) base="$BACKUP_DIR/${1#"$CLAUDE_DIR"/}" ;;
    "$HOME"/*) base="$BACKUP_DIR/home/${1#"$HOME"/}" ;;
    *) base="$BACKUP_DIR/root$1" ;;
  esac
  mkdir -p "$(dirname "$base")"
  name=$base
  while [ -e "$name" ] || [ -L "$name" ]; do
    n=$((n + 1))
    name="$base.$n"
  done
  printf '%s' "$name"
}

# One install at a time. The lock is a symlink whose target is the owner's pid; exec into the link stage keeps the pid.
take_lock() {
  local owner attempt=0
  mkdir -p "$CLAUDE_DIR"
  while ! ln -s "$$" "$LOCK" 2>/dev/null; do
    owner=$(readlink "$LOCK" 2>/dev/null || true)
    [ "$owner" != $$ ] || break
    if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
      die "another install.sh is running (pid $owner). Run this again when it ends."
    fi
    attempt=$((attempt + 1))
    [ "$attempt" -le 5 ] || die "cannot take $LOCK. If no install.sh is running, remove $LOCK and $LOCK.reclaim."
    if mkdir "$LOCK.reclaim" 2>/dev/null; then
      if [ "$(readlink "$LOCK" 2>/dev/null || true)" = "$owner" ]; then
        rm -f "$LOCK"
        say "took over" "stale lock $LOCK (pid ${owner:-unknown} is gone)"
      fi
      rmdir "$LOCK.reclaim"
    else
      sleep 1
    fi
  done
  trap release_lock EXIT
}

release_lock() {
  [ "$(readlink "$LOCK" 2>/dev/null || true)" != $$ ] || rm -f "$LOCK"
}

usage() {
  cat <<'USAGE'
Usage: install.sh [--env <name>]

Clones or updates the ai-agent-orchestration repo (ORCH_REPO_PATH, default ~/.claude/ai-agent-orchestration),
then symlinks its shared files into ~/.claude. Install, update and repair are the same run.

  --env <name>   Run env/<name>/install.sh (e.g. herdr).
                 Without --env: ORCH_ENV from the config ("none" skips the env step), else herdr.
USAGE
}

ENV_NAME=""
ENV_GIVEN=0
STAGE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env)
      [ $# -ge 2 ] || die "--env needs a name (see --help)"
      ENV_NAME=$2 ENV_GIVEN=1
      shift 2
      ;;
    --env=*) ENV_NAME=${1#--env=} ENV_GIVEN=1 && shift ;;
    --stage=link) STAGE="link" && shift ;;
    -h | --help) usage && exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

if [ -n "${ORCH_CONFIG:-}" ] && [ ! -f "$CONFIG" ]; then
  die "ORCH_CONFIG is not a regular file: $CONFIG"
fi

# config_value <key>: the environment wins, then the config file; a leading ~/ becomes $HOME/.
config_value() {
  local key=$1 value=""
  if [ -n "${!key+x}" ]; then
    value=${!key}
  else
    value=$(config_file_value "$key")
  fi
  case "$value" in \~/*) value="$HOME/${value#\~/}" ;; esac
  printf '%s' "$value"
}

# config_file_value <ORCH_KEY>: the value the config file gives, ignoring the installer's environment (what services load).
# Before the clone exists only install.repo_path is read; before the conversion the old file is sourced.
config_file_value() {
  local lib="${SRC:+$SRC/coordinator/watchers/coord-lib.sh}"
  if [ ! -f "$CONFIG" ]; then
    if [ -z "${ORCH_CONFIG:-}" ] && [ -f "$OLD_CONFIG" ]; then
      # shellcheck disable=SC1090
      (unset "$1" && set +u && . "$OLD_CONFIG" >/dev/null 2>&1 && printf '%s' "${!1:-}") || true
    fi
    return 0
  fi
  if [ "$CONFIG_CACHE" = "$(config_stamp)" ]; then
    local cached="__orch_file_$1"
    printf '%s' "${!cached:-}"
  elif [ -n "$lib" ] && [ -f "$lib" ]; then
    # shellcheck source=coordinator/watchers/coord-lib.sh
    (unset "$1" && . "$lib" && orch_load_config >/dev/null 2>&1 && printf '%s' "${!1:-}") || true
  elif [ "$1" = ORCH_REPO_PATH ] && [ -n "$INSTALL_PYTHON" ]; then
    "$INSTALL_PYTHON" -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb")).get("install", {}).get("repo_path", ""), end="")' "$CONFIG" 2>/dev/null || true
  fi
}

config_stamp() { cksum <"$CONFIG" 2>/dev/null; }

# load_config_cache: reads every value of the config file once; config_file_value uses it while the file is unchanged.
CONFIG_CACHE=""
load_config_cache() {
  local out name
  CONFIG_CACHE=""
  [ -f "$CONFIG" ] && [ -n "$INSTALL_PYTHON" ] && [ -f "${SRC:-}/lib/orch_settings.py" ] || return 0
  out=$("$INSTALL_PYTHON" -I "$SRC/lib/orch_settings.py" global "$CONFIG" 2>/dev/null) || return 0
  for name in ${!__orch_file_@}; do unset "$name"; done
  # shellcheck disable=SC2317,SC2329 # Called by the eval below.
  orch_set_default() { printf -v "__orch_file_$1" '%s' "$2"; }
  eval "$out"
  unset -f orch_set_default
  CONFIG_CACHE=$(config_stamp)
}

# refuse_local_changes <dir> <label>: exits when <dir> has uncommitted edits or commits not on origin.
refuse_local_changes() {
  local dir=$1 label=$2 dirty unpushed
  dirty=$(git -C "$dir" status --porcelain)
  unpushed=$(git -C "$dir" log --oneline HEAD --not --remotes=origin)
  [ -z "$dirty$unpushed" ] && return 0
  {
    echo "REFUSED: $label at $dir has local changes:"
    if [ -n "$dirty" ]; then
      echo "  uncommitted:"
      printf '%s\n' "$dirty" | sed 's/^/    /'
    fi
    if [ -n "$unpushed" ]; then
      echo "  commits not on origin:"
      printf '%s\n' "$unpushed" | sed 's/^/    /'
    fi
    echo "Changes go through a PR. Nothing was updated; commit them to a branch and open a PR, then run install.sh again."
  } >&2
  exit 1
}

# find_python: a Python 3.11 or newer (reading TOML needs tomllib), else exits.
find_python() {
  [ -n "$INSTALL_PYTHON" ] || die "Python 3.11 or newer not found (the TOML config needs it). Fix: $(platform_dependency_fix python3), or export ORCH_PYTHON3=/path/to/python3.11+"
}

preflight() {
  command -v git >/dev/null 2>&1 || die "git not found. Fix: $(platform_dependency_fix git)"
  find_python
  if ! command -v gh >/dev/null 2>&1; then
    say "no gh" "PR watchers need it: $(platform_dependency_fix gh) && gh auth login"
  elif ! gh auth status >/dev/null 2>&1; then
    say "gh logged out" "PR watchers need it: gh auth login"
  fi
  command -v jq >/dev/null 2>&1 || die "jq not found. Fix: $(platform_dependency_fix jq)"
}

sync_clone() {
  local repo=$1 before after upstream
  if [ ! -e "$repo" ]; then
    mkdir -p "$(dirname "$repo")"
    git clone -q "${ORCH_REPO_URL:-https://github.com/$REPO_SLUG.git}" "$repo"
    say "cloned" "$repo"
    return
  fi
  [ -e "$repo/.git" ] || die "$repo exists but is not a git clone. Move it away or set ORCH_REPO_PATH."
  git -C "$repo" fetch -q origin
  refuse_local_changes "$repo" "the ai-agent-orchestration clone"
  upstream=$(git -C "$repo" rev-parse -q --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
  case "$upstream" in
    origin/*) ;;
    "") die "$repo has no upstream branch. Fix: git -C $repo switch main" ;;
    *) die "$repo follows $upstream, not a branch on origin. Fix: git -C $repo branch -u origin/main" ;;
  esac
  before=$(git -C "$repo" rev-parse HEAD)
  git -C "$repo" merge -q --ff-only '@{u}'
  after=$(git -C "$repo" rev-parse HEAD)
  if [ "$before" = "$after" ]; then
    say "up to date" "$repo"
  else
    say "updated" "$repo ${before:0:7}..${after:0:7}"
  fi
}

INSTALL_PYTHON=""
for candidate in ${ORCH_PYTHON3:+"$ORCH_PYTHON3"} python3 ${ORCH_PYTHON_FALLBACKS-/opt/homebrew/bin/python3 /usr/local/bin/python3} python3.14 python3.13 python3.12 python3.11; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if [ "$("$candidate" -c 'import sys; print(sys.version_info >= (3, 11))' 2>/dev/null)" = True ]; then
    INSTALL_PYTHON=$(command -v "$candidate")
    break
  fi
done
ORCH_REPO=$(config_value ORCH_REPO_PATH)
[ -n "$ORCH_REPO" ] || ORCH_REPO="$CLAUDE_DIR/ai-agent-orchestration"

PLATFORM=$(uname -s)
case "$PLATFORM" in
  Darwin) PLATFORM=macos ;;
  Linux) PLATFORM=linux ;;
  *) die "unsupported platform: $PLATFORM" ;;
esac
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
fi
if [ -n "$SRC" ]; then
  # An explicitly executed local installer trusts its adjacent adapter. A downloaded
  # bash -c installer has no source path and must fetch its adapter instead.
  [ "$STAGE" != link ] || [ "$SRC" = "$(cd "$ORCH_REPO" && pwd -P)" ] || die "the link stage runs from $ORCH_REPO, not $SRC"
  if [ "$STAGE" = link ]; then ORCH_REPO=$SRC; fi
  # shellcheck disable=SC1090
  . "$SRC/lib/platform/$PLATFORM.sh"
else
  [ "$STAGE" != link ] || die "the link stage runs from $ORCH_REPO, not $SRC"
  adapter=$(curl -fsSL "https://raw.githubusercontent.com/$REPO_SLUG/main/lib/platform/$PLATFORM.sh") || die "cannot download platform adapter. Fix: check your network and run again"
  [ -n "$adapter" ] || die "empty platform adapter download"
  eval "$adapter"
fi

preflight
if [ "$STAGE" != link ]; then
  take_lock
  sync_clone "$ORCH_REPO"
  export ORCH_REPO_PATH="$ORCH_REPO"
  if [ "$ENV_GIVEN" = 1 ]; then
    exec /bin/bash "$ORCH_REPO/install.sh" --stage=link --env "$ENV_NAME"
  fi
  exec /bin/bash "$ORCH_REPO/install.sh" --stage=link
fi

take_lock
. "$SRC/lib/platform/watcher-state.sh"

# real_parents <path>: a symlinked parent below ~/.claude that points into the clone, or any symlinked
# parent under ~/.claude/coordinator, is backed up and replaced by a real directory.
real_parents() {
  local rel path=$CLAUDE_DIR part target backup
  case "$1" in "$CLAUDE_DIR"/*) ;; *) return 0 ;; esac
  rel=$(dirname "${1#"$CLAUDE_DIR"/}")
  [ "$rel" != . ] || return 0
  while [ -n "$rel" ]; do
    part=${rel%%/*}
    [ "$part" != "$rel" ] && rel=${rel#*/} || rel=""
    path="$path/$part"
    [ -L "$path" ] || continue
    target=$(cd "$path" 2>/dev/null && pwd -P) || target=""
    case "$path/" in
      "$CLAUDE_DIR/coordinator/"*) ;;
      *) case "$target/" in "$ORCH_REPO/"*) ;; *) continue ;; esac ;;
    esac
    backup=$(backup_name "$path")
    mv "$path" "$backup"
    mkdir -p "$path"
    say "backed up" "$path -> $backup (a linked directory; its files are linked one by one)"
    n_backup=$((n_backup + 1))
  done
}

# link_entry <source> <dest>: dest becomes a symlink to source; anything else in the way is backed up.
link_entry() {
  local src=$1 dest=$2 backup
  real_parents "$dest"
  MANAGED="$MANAGED$dest
"
  if [ -L "$dest" ] && [ "$(readlink "$dest")" = "$src" ]; then
    say "already linked" "$dest"
    n_ok=$((n_ok + 1))
    return
  fi
  if [ -L "$dest" ] || [ -e "$dest" ]; then
    backup=$(backup_name "$dest")
    mv "$dest" "$backup"
    say "backed up" "$dest -> $backup"
    n_backup=$((n_backup + 1))
  fi
  mkdir -p "$(dirname "$dest")"
  ln -s "$src" "$dest"
  say "linked" "$dest -> $src"
  n_linked=$((n_linked + 1))
}

skip() {
  say "skipped" "$1"
  n_skipped=$((n_skipped + 1))
}

# Links this installer made on an earlier run (listed in $MANIFEST) whose source left the clone.
remove_stale_links() {
  local link target
  [ -f "$MANIFEST" ] || return 0
  while IFS= read -r link; do
    [ -L "$link" ] || continue
    target=$(readlink "$link")
    case "$target" in "$ORCH_REPO"/*) ;; *) continue ;; esac
    [ -e "$target" ] && continue
    rm "$link"
    say "removed stale" "$link"
  done <"$MANIFEST"
}

write_manifest() {
  printf '%s' "$MANAGED" >"$MANIFEST.tmp"
  mv "$MANIFEST.tmp" "$MANIFEST"
}

link_shared_files() {
  local d f name backup agents owned real claude_real
  for d in heavy-slots context-usage; do
    if [ ! -d "$CLAUDE_DIR/$d" ]; then
      mkdir -p "$CLAUDE_DIR/$d"
      say "created" "$CLAUDE_DIR/$d/"
    fi
  done
  for d in "$ORCH_REPO"/skills/*/; do
    [ -d "$d" ] || continue
    name=$(basename "$d")
    # The shared agents folder entry is ours when it resolves into the clone or is what ~/.claude/skills/<name> links to.
    agents="$HOME/.agents/skills/$name"
    owned=""
    if [ -L "$agents" ] || [ -e "$agents" ]; then
      real=$(cd "$agents" 2>/dev/null && pwd -P) || real=""
      claude_real=$(cd "$CLAUDE_DIR/skills/$name" 2>/dev/null && pwd -P) || claude_real=""
      case "$real/" in "$ORCH_REPO/"*) owned=1 ;; esac
      [ -z "$real" ] || [ "$real" != "$claude_real" ] || owned=1
    fi
    link_entry "$ORCH_REPO/skills/$name" "$CLAUDE_DIR/skills/$name"
    if [ -n "$owned" ] && [ ! -L "$agents" ] && [ "$real" = "$ORCH_REPO/skills/$name" ]; then
      say "already linked" "$agents (through a linked parent)"
      n_ok=$((n_ok + 1))
    elif [ -n "$owned" ]; then
      link_entry "$ORCH_REPO/skills/$name" "$agents"
    elif [ -L "$agents" ] || [ -e "$agents" ]; then
      say "collision" "$agents (another skill of the same name; left as it is)"
    fi
    f="$CLAUDE_DIR/commands/$name.md"
    if [ -L "$f" ] || [ -e "$f" ]; then
      backup=$(backup_name "$f")
      mv "$f" "$backup"
      say "backed up" "$f -> $backup (replaced by the /$name skill)"
      n_backup=$((n_backup + 1))
    fi
  done
  # Real directories, linked files: the watchers write state next to themselves.
  while IFS= read -r f; do
    link_entry "$ORCH_REPO/$f" "$CLAUDE_DIR/$f"
  done < <(cd "$ORCH_REPO" && find coordinator -type f ! -name '*.pyc' | LC_ALL=C sort)
  for d in $RUNTIME_DIRS; do
    [ -d "$ORCH_REPO/$(dirname "$d")" ] || continue
    real_parents "$CLAUDE_DIR/$d/."
    [ ! -d "$CLAUDE_DIR/$d" ] || continue
    mkdir -p "$CLAUDE_DIR/$d"
    say "created" "$CLAUDE_DIR/$d/"
  done
  for f in "$ORCH_REPO"/rules/*.md; do
    [ -e "$f" ] || continue
    link_entry "$f" "$CLAUDE_DIR/rules/$(basename "$f")"
  done
  link_entry "$ORCH_REPO/statusline-command.sh" "$CLAUDE_DIR/statusline-command.sh"
  link_entry "$ORCH_REPO/heavy-slot" "$HOME/.local/bin/heavy-slot"
  link_entry "$ORCH_REPO/ask-user-question" "$HOME/.local/bin/ask-user-question"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) say "PATH setup" "Add export PATH=\"\$HOME/.local/bin:\$PATH\" to your shell startup file (e.g. $(platform_shell_profile)); then open a new shell for heavy-slot commands." ;;
  esac
  link_entry "$ORCH_REPO/worker-reports/machine-rules.md" "$CLAUDE_DIR/worker-reports/machine-rules.md"
  link_entry "$ORCH_REPO/hooks/orch-update-check.sh" "$CLAUDE_DIR/hooks/orch-update-check.sh"
  link_entry "$ORCH_REPO/hooks/guard-bash.py" "$CLAUDE_DIR/hooks/guard-bash.py"
}

# Backups an earlier installer left beside the skills move under $BACKUP_DIR.
move_skill_backups() {
  local entry target
  [ ! -L "$CLAUDE_DIR/skills" ] || return 0
  for entry in "$CLAUDE_DIR"/skills/*.bak.*; do
    [[ ${entry##*/} =~ \.bak\.[0-9]{14}(\.[0-9]+)?$ ]] || continue
    target=$(backup_name "$entry")
    mv "$entry" "$target"
    say "moved backup" "$entry -> $target"
  done
}

interactive() { [ -t 0 ] && [ -t 1 ]; }

# ask <prompt> <default>: sets ANSWER from the terminal; the default when the answer is empty.
ask() {
  printf '%s [%s]: ' "$1" "$2"
  read -r ANSWER || ANSWER=""
  echo
  ANSWER=${ANSWER:-$2}
}

# detect_default <ORCH_KEY>: a value found on this machine, else empty.
detect_default() {
  local value=""
  case "$1" in
    ORCH_PR_AUTHOR) value=$(gh api user -q .login 2>/dev/null || true) ;;
    ORCH_CODEX_WORKER_MODEL | ORCH_CODEX_SMART_MODEL)
      value=$(sed -n 's/^model[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${CODEX_HOME:-$HOME/.codex}/config.toml" 2>/dev/null | head -n 1)
      ;;
    ORCH_HERDR_BIN)
      value=$(PATH="$PATH:$HOME/.local/bin" type -P herdr 2>/dev/null || true)
      case "$value" in
        */*) value="$(cd "$(dirname "$value")" 2>/dev/null && pwd -P)/${value##*/}" ;;
        *) value="" ;;
      esac
      [ -f "$value" ] && [ -x "$value" ] || value=""
      ;;
    ORCH_HERDR_SOCKET)
      value="${XDG_CONFIG_HOME:-$HOME/.config}/herdr/herdr.sock"
      [ -S "$value" ] || value=""
      ;;
  esac
  printf '%s' "$value"
}

# write_config_key <ORCH_KEY> <value>: sets the key in $CONFIG, keeping every other line.
write_config_key() {
  settings_cli set "$CONFIG" "$1" "$2" || die "cannot set $1 in $CONFIG"
  say "set" "$CONFIG: $1"
}

# settings_cli <args>: runs the settings module with the installer's Python 3.11 or newer.
settings_cli() { "$INSTALL_PYTHON" -I "$ORCH_REPO/lib/orch_settings.py" "$@"; }

# First run on a terminal: asks for each empty or placeholder key; "-" leaves it empty.
ask_config_keys() {
  local key value
  interactive || return 0
  for key in ORCH_PR_AUTHOR ORCH_CODEX_WORKER_MODEL ORCH_CODEX_SMART_MODEL ORCH_HERDR_BIN ORCH_HERDR_SOCKET; do
    value=$(config_value "$key")
    [ -n "$value" ] || value=$(unset "$key" && config_value "$key")
    case "$value" in
      "" | "<"*">") ;;
      *)
        printf -v "$key" '%s' "$value"
        continue
        ;;
    esac
    ask "$key (- leaves it empty)" "$(detect_default "$key")"
    if [ -z "$ANSWER" ] || [ "$ANSWER" = - ]; then
      [ -n "$value" ] || continue
      ANSWER=""
    fi
    write_config_key "$key" "$ANSWER"
    printf -v "$key" '%s' "$ANSWER"
  done
}

# convert_old_config: with no orchestration.toml, orchestration.env is converted and renamed to orchestration.env.bak.
convert_old_config() {
  local backup="$OLD_CONFIG.bak" line report n=0
  [ -z "${ORCH_CONFIG:-}" ] && [ -f "$OLD_CONFIG" ] || return 0
  if [ -L "$CONFIG" ] || [ -e "$CONFIG" ]; then
    say "warning" "$OLD_CONFIG is not read any more; $CONFIG wins. Remove $OLD_CONFIG when nothing in it is missing from $CONFIG"
    return 0
  fi
  if ! report=$(settings_cli migrate "$OLD_CONFIG" "$CONFIG" "$SNIPPET_DIR") || [ ! -f "$CONFIG" ]; then
    die "cannot convert $OLD_CONFIG; it is unchanged. Fix it, or write $CONFIG from $ORCH_REPO/config/orchestration.toml.example"
  fi
  while IFS= read -r line; do
    say "migrated" "$line"
  done <<<"$report"
  while [ -e "$backup" ] || [ -L "$backup" ]; do
    n=$((n + 1))
    backup="$OLD_CONFIG.bak.$n"
  done
  mv "$OLD_CONFIG" "$backup"
  say "moved" "$OLD_CONFIG -> $backup"
}

# open_work_clones: the clones of the open work, one per line; none when the env adapter cannot answer.
open_work_clones() {
  local bin socket
  bin=$(placeholder_value ORCH_HERDR_BIN)
  socket=$(placeholder_value ORCH_HERDR_SOCKET)
  # shellcheck disable=SC2030 # Set for the discovery call only.
  (
    [ -z "$bin" ] || export HERDR_BIN_PATH="$bin"
    [ -z "$socket" ] || export HERDR_SOCKET_PATH="$socket"
    ORCH_ENV="${ENV_NAME:-$(config_value ORCH_ENV)}" "$INSTALL_PYTHON" "$ORCH_REPO/coordinator/watchers/discovery.py" clones 2>/dev/null
  ) || true
}

# upgrade_repo_files: rewrites legacy.extra_pattern and legacy.exempt_paths as rules in the personal files of the
# open-work clones, of legacy.map_repo, of the clones $CONFIG still lists in audit.git_watch_repos, of their main checkouts, and in the snippets.
upgrade_repo_files() {
  local clone file files=() clones=() line report map
  map=$(config_value ORCH_LEGACY_MAP_REPO)
  [ -z "$map" ] || map=$(placeholder_value ORCH_LEGACY_MAP_REPO)
  while IFS= read -r clone; do
    [ -z "$clone" ] || clones+=("$clone")
  done <<<"$(open_work_clones; printf '%s\n' "$map")"
  for file in "$SNIPPET_DIR"/*/*.toml; do
    [ ! -f "$file" ] || files+=("$file")
  done
  report=$(settings_cli upgrade --watched "$CONFIG" ${files[@]+"${files[@]}"} --clones ${clones[@]+"${clones[@]}"}) || die "cannot convert the removed legacy settings (see above); edit the file by hand (docs/settings.md, \"Legacy rules\")"
  [ -n "$report" ] || return 0
  while IFS= read -r line; do
    say "migrated" "$line"
  done <<<"$report"
}

# drop_removed_keys: removes the settings the open work replaced from $CONFIG, with one printed line.
drop_removed_keys() {
  local dropped
  if ! dropped=$(settings_cli drop "$CONFIG" 2>&1); then
    say "warning" "${dropped#ERROR: }"
    return 0
  fi
  [ -z "$dropped" ] || say "dropped" "$CONFIG: $dropped (repos and architects come from the open work)"
}

personal_config() {
  local example="$ORCH_REPO/config/orchestration.toml.example" k v fill="" new="" have error
  convert_old_config
  if [ -L "$CONFIG" ] || [ -e "$CONFIG" ]; then
    say "kept" "$CONFIG (yours, never overwritten)"
  else
    mkdir -p "$(dirname "$CONFIG")"
    cp "$example" "$CONFIG"
    say "created" "$CONFIG"
  fi
  local removed
  removed=$(settings_cli drop-removed "$CONFIG" 2>&1) || die "${removed#ERROR: }"
  [ -z "$removed" ] || say "migrated" "$removed"
  upgrade_repo_files
  drop_removed_keys
  have=$(settings_cli keys "$CONFIG" 2>&1) || die "${have#ERROR: }"
  have=" $have "
  load_config_cache
  local missing_project_keys=""
  for k in ORCH_PR_TABS_ORG ORCH_PR_TABS_DEFAULT_REPO ORCH_PR_TABS_OPEN_ALL; do
    case "$have" in *" $k "*) ;; *) missing_project_keys="$missing_project_keys $k" ;; esac
  done
  [ -z "$missing_project_keys" ] || say "configure" "Project features are off until you configure missing keys in $CONFIG:$missing_project_keys"
  for k in ORCH_PR_AUTHOR ORCH_CODEX_WORKER_MODEL ORCH_CODEX_SMART_MODEL ORCH_HERDR_BIN ORCH_HERDR_SOCKET; do
    v=$(config_value "$k")
    case "$v" in "" | "<"*">") fill="$fill $k" ;; esac
  done
  for k in $(settings_cli keys "$example"); do
    case "$have" in *" $k "*) ;; *) new="$new $k" ;; esac
  done
  [ -z "$fill" ] || say "fill in" "$CONFIG:$fill"
  [ -z "$new" ] || say "new keys" "in $example, not in your config (defaults apply):$new"
  say "repo settings" "per repo in .orchestration.toml (committed) and .orchestration.local.toml (git-ignored); see $ORCH_REPO/docs/settings.md"
}

# Adds our hooks, and the status line when none is set. Touches nothing else.
merge_settings() {
  local settings="$CLAUDE_DIR/settings.json" has_hook has_guard managed_guard current_guard current filter="." tmp created=0 backup
  GUARD_BASH_CMD="ORCH_CONFIG=$(printf '%q' "$(placeholder_value ORCH_CONFIG)") python3 ~/.claude/hooks/guard-bash.py"
  if [ ! -e "$settings" ]; then
    printf '{}\n' >"$settings"
    say "created" "$settings"
    created=1
  fi
  if ! jq -e 'def hook_entries: type == "array" and all(type == "object" and ((.hooks? // []) | type == "array" and all(type == "object"))); type == "object" and ((.hooks? // {}) | type == "object") and ((.hooks.SessionStart? // []) | hook_entries) and ((.hooks.PreToolUse? // []) | hook_entries and all((.matcher? == null) or (.matcher | type == "string")))' "$settings" >/dev/null 2>&1; then
    skip "$settings is not a supported hooks object; add hooks by hand"
    return
  fi
  has_hook=$(jq --arg c "$HOOK_CMD" '[.hooks.SessionStart[]?.hooks[]?.command] | index($c) != null' "$settings")
  if [ "$has_hook" = true ]; then
    say "already set" "$settings: SessionStart hook"
  else
    filter="$filter | .hooks.SessionStart = ((.hooks.SessionStart // []) + [{hooks: [{type: \"command\", command: \$hook}]}])"
  fi
  has_guard=$(jq --arg guard "$GUARD_BASH_CMD" --arg managed "$MANAGED_GUARD_RE" --arg supported "$SUPPORTED_GUARD_RE" --arg literal "'\$HOME/.claude/hooks/guard-bash.py'" --arg quoted '"~/.claude/hooks/guard-bash.py"' "$BASH_MATCHER_JQ [.hooks.PreToolUse[]? | select(.matcher | bash_matcher) | .hooks[]?.command | select(type == \"string\") | select(. == \"python3 ~/.claude/hooks/guard-bash.py\" or . == \$guard or test(\$managed) or (test(\$supported) and (contains(\$literal) | not) and (contains(\$quoted) | not)))] | any" "$settings")
  managed_guard=$(jq --arg guard "$GUARD_BASH_CMD" --arg managed "$MANAGED_GUARD_RE" "$BASH_MATCHER_JQ [.hooks.PreToolUse[]? | select(.matcher | bash_matcher) | .hooks[]?.command | select(type == \"string\") | select(. == \"python3 ~/.claude/hooks/guard-bash.py\" or (. != \$guard and test(\$managed)))] | any" "$settings")
  current_guard=$(jq --arg guard "$GUARD_BASH_CMD" "$BASH_MATCHER_JQ [.hooks.PreToolUse[]? | select(.matcher | bash_matcher) | .hooks[]?.command | select(. == \$guard)] | any" "$settings")
  if [ "$has_guard" = true ]; then
    if [ "$managed_guard" = true ]; then
      filter="$filter | .hooks.PreToolUse |= map(.matcher as \$matcher | if (\$matcher | bash_matcher) then (.hooks |= map(if .command == \"python3 ~/.claude/hooks/guard-bash.py\" or ((.command | type) == \"string\" and (.command | test(\$managed))) then .command = \$guard else . end)) else . end)"
    elif [ "$current_guard" = true ]; then
      say "already set" "$settings: PreToolUse Bash hook"
    else
      filter="$filter | .hooks.PreToolUse = ((.hooks.PreToolUse // []) + [{matcher: \"Bash\", hooks: [{type: \"command\", command: \$guard}]}])"
      say "added" "$settings: PreToolUse Bash hook ($GUARD_BASH_CMD)"
    fi
  else
    filter="$filter | .hooks.PreToolUse = ((.hooks.PreToolUse // []) + [{matcher: \"Bash\", hooks: [{type: \"command\", command: \$guard}]}])"
  fi
  current=$(jq -r '.statusLine.command // empty' "$settings")
  if [ -z "$current" ]; then
    filter="$filter | .statusLine = {type: \"command\", command: \$status}"
  elif [ "$current" = "$STATUSLINE_CMD" ]; then
    say "already set" "$settings: statusLine"
  else
    skip "$settings: statusLine is yours ($current); ours is \"$STATUSLINE_CMD\""
  fi
  [ "$filter" != "." ] || return 0
  if [ "$created" = 0 ]; then
    backup=$(backup_name "$settings")
    cp "$settings" "$backup"
    say "backed up" "$settings -> $backup"
    n_backup=$((n_backup + 1))
  fi
  tmp=$(mktemp)
  jq --arg hook "$HOOK_CMD" --arg guard "$GUARD_BASH_CMD" --arg status "$STATUSLINE_CMD" --arg managed "$MANAGED_GUARD_RE" "$BASH_MATCHER_JQ $filter" "$settings" >"$tmp"
  cat "$tmp" >"$settings"
  rm -f "$tmp"
  [ "$has_hook" = true ] || say "added" "$settings: SessionStart hook ($HOOK_CMD)"
  [ "$has_guard" = true ] || say "added" "$settings: PreToolUse Bash hook ($GUARD_BASH_CMD)"
  [ -n "$current" ] || say "added" "$settings: statusLine ($STATUSLINE_CMD)"
}

# The linked guard must deny a forbidden command with exit 2 and the attribution reason on stderr.
verify_guard() {
  local status=0 reason
  reason=$(printf '%s' '{"tool_name":"Bash","cwd":"/","tool_input":{"command":"git commit -m \"Co-Authored-By: Claude\""}}' |
    python3 "$CLAUDE_DIR/hooks/guard-bash.py" 2>&1 >/dev/null) || status=$?
  case "$status:$reason" in
    "2:No AI attribution"*) say "verified" "$CLAUDE_DIR/hooks/guard-bash.py denies a forbidden command" ;;
    *) die "$CLAUDE_DIR/hooks/guard-bash.py did not deny a forbidden command (exit $status: ${reason:-no reason}); Bash is unguarded" ;;
  esac
}

# placeholder_value <ORCH_KEY>: the config value with $HOME expanded, else the key's default.
placeholder_value() {
  local v
  if [ "$1" = ORCH_CONFIG ]; then
    case "$CONFIG" in
      /*) v=$CONFIG ;;
      *) v="$(pwd -P)/$CONFIG" ;;
    esac
    printf '%s' "$v"
    return
  fi
  if [ "$1" = ORCH_SERVICE_PYTHON ]; then
    [ -z "$SERVICE_PYTHON" ] || { printf '%s' "$SERVICE_PYTHON" && return; }
    v=$(placeholder_value ORCH_PYTHON3)
    [ -n "$v" ] && [ "$("$v" -c 'import sys; print(sys.version_info >= (3, 11))' 2>/dev/null)" = True ] || v=$INSTALL_PYTHON
    printf '%s' "$v"
    return
  fi
  v=$(config_value "$1")
  if [ -z "$v" ]; then
    case "$1" in
      ORCH_CLAUDE_DIR) v=$CLAUDE_DIR ;;
      ORCH_PATH) v="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" ;;
      ORCH_PYTHON3) v=python3 ;;
      ORCH_LEGACY_MAP_REPO) v=$(legacy_map_default_repo) || return 2 ;;
    esac
  fi
  # shellcheck disable=SC2016
  local braced='${HOME}' plain='$HOME'
  v=${v//"$braced"/"$HOME"}
  v=${v//"$plain"/"$HOME"}
  if [ "$1" = ORCH_PYTHON3 ] && [ -n "$v" ]; then
    case "$v" in /*) ;; *) v=$(command -v "$v" || true) ;; esac
  fi
  printf '%s' "$v"
}

# render_service <template> <start command>: renders one service template beside its link.
render_service() {
  local t=$1 start=$2 dest key value script missing tmp backup unit
  dest="$CLAUDE_DIR/${t#"$ORCH_REPO"/}"
  dest=${dest%.template}
  script="" missing=""
  for key in $(grep -o '@ORCH_[A-Z0-9_]*@' "$t" | tr -d @ | sort -u); do
    value=$(placeholder_value "$key") || die "cannot resolve $key for $t"
    if [ -z "$value" ]; then
      missing="$missing $key"
      continue
    fi
    value=$(platform_escape_service_value "$value")
    value=$(printf '%s' "$value" | sed 's/[\\|&]/\\&/g')
    script="$script s|@$key@|$value|g;"
  done
  [ -z "$missing" ] || die "ORCH_WATCHERS_SERVICE=1 needs$missing set in $CONFIG (used by $t)"
  tmp=$(mktemp)
  sed "$script" "$t" >"$tmp"
  if [ -f "$dest" ] && [ ! -L "$dest" ] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    say "already set" "$dest"
    n_ok=$((n_ok + 1))
    RENDER_CHANGED=0
    return
  fi
  RENDER_CHANGED=1
  if [ -L "$dest" ] || [ -e "$dest" ]; then
    backup=$(backup_name "$dest")
    mv "$dest" "$backup"
    say "backed up" "$dest -> $backup"
    n_backup=$((n_backup + 1))
  fi
  mkdir -p "$(dirname "$dest")"
  mv "$tmp" "$dest"
  chmod 644 "$dest"
  while IFS= read -r key; do
    mkdir -p "$key"
  done < <(sed -n 's|.*<string>\(/.*\)/[^/]*\.std[a-z]*</string>.*|\1|p' "$dest" | sort -u)
  unit=$(basename "${dest%.*}")
  case "$dest" in *.timer) unit=$(basename "$dest") ;; esac
  say "rendered" "$dest (start: $start $(printf '%q' "$dest"); stop: $(platform_service_stop_hint "$unit"))"
  n_linked=$((n_linked + 1))
}

# Renders only the current platform's watcher service templates beside their links.
render_services() {
  local t dest found=0 enabled service_dir template_suffix start_hint label
  SERVICES="" CHANGED_SERVICES=""
  enabled=$(config_value ORCH_WATCHERS_SERVICE)
  service_dir=$(platform_service_dir)
  template_suffix=$(platform_service_suffix)
  start_hint=$(platform_service_start_hint)
  while IFS= read -r t; do
    found=1
    [ "$enabled" = 1 ] || continue
    case "$t" in
      *coord-watcher-audit.* | *coord-watcher-pulls.*)
        if [ -z "$(config_file_value ORCH_PR_AUTHOR)" ]; then
          label=$(basename "$t" .template)
          skip "${label%.*} watcher (set ORCH_PR_AUTHOR in $CONFIG)"
          continue
        fi
        ;;
    esac
    render_service "$t" "$start_hint"
    dest="$CLAUDE_DIR/${t#"$ORCH_REPO"/}"
    SERVICES="$SERVICES${dest%.template}
"
    [ "$RENDER_CHANGED" = 0 ] || CHANGED_SERVICES="$CHANGED_SERVICES${dest%.template}
"
  done < <(find "$service_dir" -name "*$template_suffix" -type f 2>/dev/null | LC_ALL=C sort)
  if [ "$found" = 0 ]; then
    [ "$enabled" != 1 ] || skip "watcher services (ORCH_WATCHERS_SERVICE=1, but $service_dir has no templates yet)"
    return 0
  fi
  [ "$enabled" = 1 ] || skip "watcher services (set ORCH_WATCHERS_SERVICE=\"1\" in $CONFIG to render them)"
}

# Registers every rendered watcher service so it runs now and starts at login; removes our services no template renders.
# When the checkout's revision or the config file differs from the last registration, running watchers restart.
register_services() {
  local file current changed status revision marker updated=0 failed=0
  if [ "$WATCHER_MIGRATION_PENDING" = 1 ]; then
    [ -z "$SERVICES" ] || skip "watcher service registration (finish the migration above, then run install.sh again)"
    return 0
  fi
  if [ -z "$SERVICES" ]; then
    if ! platform_remove_stale_services ""; then
      rm -f "$CLAUDE_DIR/coordinator/watchers/.registered-revision"
      SERVICES_FAILED=1
    fi
    return 0
  fi
  current=$(printf '%s' "$SERVICES" | sed 's|.*/||' | LC_ALL=C sort)
  if ! platform_remove_stale_services "$current"; then
    rm -f "$CLAUDE_DIR/coordinator/watchers/.registered-revision"
    skip "watcher service registration (stop the services named above, then run install.sh again)"
    SERVICES_FAILED=1
    return 0
  fi
  marker="$CLAUDE_DIR/coordinator/watchers/.registered-revision"
  revision="$(git -C "$ORCH_REPO" rev-parse HEAD 2>/dev/null || echo unknown) $(cksum <"$CONFIG" 2>/dev/null | cut -d' ' -f1)"
  [ "$(cat "$marker" 2>/dev/null)" = "$revision" ] || updated=1
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    changed=0
    printf '%s' "$CHANGED_SERVICES" | grep -qxF "$file" && changed=1
    status=0
    platform_register_service "$file" "$changed" || status=$?
    case "$status" in
      0)
        say "registered" "$file (runs now and at login)"
        n_linked=$((n_linked + 1))
        ;;
      2)
        case "$updated:$file" in
          "1:$CLAUDE_DIR/coordinator/watchers/"*)
            if platform_restart_service "$file"; then
              say "restarted" "$file (new code)"
            else
              say "not restarted" "$file"
              failed=1
            fi
            ;;
          *) say "already set" "$file (registered)" ;;
        esac
        n_ok=$((n_ok + 1))
        ;;
      3) failed=1 ;;
      *)
        say "not registered" "$file (run: $(platform_service_start_hint) $(printf '%q' "$file"))"
        failed=1
        ;;
    esac
  done <<<"$SERVICES"
  if [ "$failed" = 1 ]; then
    SERVICES_FAILED=1
    rm -f "$marker"
  else
    printf '%s\n' "$revision" >"$marker"
  fi
}

# legacy_map_default_repo: the clone of the open work whose repo settings turn legacy.scan on, else empty.
legacy_map_default_repo() {
  local path scan clones
  clones=$(open_work_clones)
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    scan=${ORCH_LEGACY_SCAN-}
    if [ -z "${ORCH_LEGACY_SCAN+x}" ]; then
      scan=$(settings_cli repo "$path") || return 2
      case "$scan" in *"orch_set_default ORCH_LEGACY_SCAN 1"*) scan=1 ;; *) scan=0 ;; esac
    fi
    if [ "$scan" = 1 ]; then
      printf '%s' "$path"
      return
    fi
  done <<<"$clones"
}

# Renders the daily legacy map units of the current platform for registration; off until a repo to map is known.
render_legacy_map_service() {
  local t start dest repo
  [ "$(config_value ORCH_WATCHERS_SERVICE)" = 1 ] || return 0
  repo=$(placeholder_value ORCH_LEGACY_MAP_REPO) || die "cannot read the repo settings of the open-work clones (see above); fix them or set legacy.map_repo in $CONFIG"
  if [ -z "$repo" ]; then
    skip "daily legacy map job (set ORCH_LEGACY_MAP_REPO in $CONFIG to the local clone to map)"
    return 0
  fi
  while IFS= read -r t; do
    start=$(platform_service_start_hint)
    case "$t" in *.service.template) start="systemctl --user link" ;; esac
    render_service "$t" "$start"
    dest="$CLAUDE_DIR/${t#"$ORCH_REPO"/}"
    SERVICES="$SERVICES${dest%.template}
"
    [ "$RENDER_CHANGED" = 0 ] || CHANGED_SERVICES="$CHANGED_SERVICES${dest%.template}
"
  done < <(find "$ORCH_REPO/coordinator/service/$PLATFORM" -name '*.template' -type f 2>/dev/null | LC_ALL=C sort)
}

# write_rendered <dest> <mode>: stdin becomes dest; an equal file stays, anything else in the way is backed up.
write_rendered() {
  local dest=$1 mode=$2 tmp backup
  mkdir -p "$(dirname "$dest")"
  tmp=$(mktemp "$dest.XXXXXX")
  cat >"$tmp"
  chmod "$mode" "$tmp"
  if [ -f "$dest" ] && [ ! -L "$dest" ] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    chmod "$mode" "$dest"
    say "already set" "$dest"
    n_ok=$((n_ok + 1))
    return
  fi
  if [ -L "$dest" ] || [ -e "$dest" ]; then
    backup=$(backup_name "$dest")
    mv "$dest" "$backup"
    say "backed up" "$dest -> $backup"
    n_backup=$((n_backup + 1))
  fi
  mv "$tmp" "$dest"
  say "rendered" "$dest"
  n_linked=$((n_linked + 1))
}

# Registers the PR tabs native messaging host with Chrome when a Chrome profile exists.
register_chrome_host() {
  local chrome_dir python launcher id
  chrome_dir=$(platform_chrome_dir)
  if [ ! -d "$chrome_dir" ]; then
    skip "Chrome PR tabs host (no Chrome profile at $chrome_dir)"
    return 0
  fi
  python=$(placeholder_value ORCH_SERVICE_PYTHON)
  if [ -z "$python" ]; then
    skip "Chrome PR tabs host (no Python 3.11 or newer found; set watchers.python3)"
    return 0
  fi
  id=$(tr -d '[:space:]' <"$ORCH_REPO/chrome/pr-tabs/extension.id")
  launcher="$CLAUDE_DIR/chrome-pr-tabs/pr-tabs-host"
  write_rendered "$launcher" 755 < <(
    printf '#!/bin/bash\nexport PATH=%q\nexport ORCH_CONFIG=%q\nexport ORCH_PYTHON3=%q\nexec %q %q\n' "$(placeholder_value ORCH_PATH)" "$(placeholder_value ORCH_CONFIG)" "$python" "$python" "$ORCH_REPO/chrome/pr-tabs/host/pr_tabs_host.py"
  )
  write_rendered "$chrome_dir/NativeMessagingHosts/$CHROME_HOST.json" 644 < <(
    jq -n --arg name "$CHROME_HOST" --arg path "$launcher" --arg origin "chrome-extension://$id/" \
      '{name: $name, description: "gh access for the PR tabs extension", path: $path, type: "stdio", allowed_origins: [$origin]}'
  )
  guide_chrome_load "$chrome_dir" "$id"
}

# chrome_extension_loaded <chrome dir> <id>: a profile's Preferences or Secure Preferences lists the extension.
chrome_extension_loaded() {
  local prefs
  for prefs in "$1"/*/Preferences "$1"/*/"Secure Preferences"; do
    [ -f "$prefs" ] || continue
    jq -e --arg id "$2" '.extensions.settings[$id] != null' "$prefs" >/dev/null 2>&1 && return 0
  done
  return 1
}

# Until a profile has the extension, opens chrome://extensions, copies the extension path and prints the steps.
guide_chrome_load() {
  local path="$ORCH_REPO/chrome/pr-tabs/extension" what=paste
  chrome_extension_loaded "$1" "$2" && return 0
  platform_gui_session || return 0
  platform_open_chrome_extensions || return 0
  printf '%s' "$path" | platform_copy_to_clipboard || what=$path
  say "load once" "$(platform_load_unpacked_steps "$what")"
}

resolve_link_path() {
  local path=$1 link dir hops=0 max_hops=40
  [ -L "$path" ] || { printf '%s\n' "$path"; return; }
  while [ -L "$path" ]; do
    hops=$((hops + 1))
    [ "$hops" -le "$max_hops" ] || return 1
    link=$(readlink "$path") || return
    case "$link" in
      /*) path=$link ;;
      *)
        dir=$(cd -P "$(dirname "$path")" && pwd) || return
        path="$dir/$link"
        ;;
    esac
  done
  dir=$(cd -P "$(dirname "$path")" && pwd) || return
  printf '%s/%s\n' "$dir" "$(basename "$path")"
}

claude_md_mode() {
  local mode
  mode=$(stat -f '%Lp' "$1" 2>/dev/null) || mode=""
  case "$mode" in
    '' | *[!0-7]*) mode=$(stat -c '%a' "$1") ;;
  esac
  case "$mode" in
    '' | *[!0-7]*) return 1 ;;
  esac
  printf '%s\n' "$mode"
}

replace_claude_md_block() (
  target=$1 start_line=$2 end_line=$3 snippet=$4 mode=$5 tmp=""
  # shellcheck disable=SC2317,SC2329 # Called by the traps below.
  cleanup() { [ -z "$tmp" ] || rm -f "$tmp"; }
  trap cleanup EXIT
  trap 'cleanup; exit 1' HUP INT TERM
  tmp=$(mktemp "$target.tmp.XXXXXX") || exit
  if [ "$start_line" -gt 1 ]; then
    head -n "$((start_line - 1))" "$target" >"$tmp"
  fi
  cat "$snippet" >>"$tmp"
  tail -n "+$((end_line + 1))" "$target" >>"$tmp"
  chmod "$mode" "$tmp"
  mv "$tmp" "$target"
  tmp=""
)

append_claude_md_block() (
  target=$1 snippet=$2 mode=${3:-} tmp=""
  # shellcheck disable=SC2317,SC2329 # Called by the traps below.
  cleanup() { [ -z "$tmp" ] || rm -f "$tmp"; }
  trap cleanup EXIT
  trap 'cleanup; exit 1' HUP INT TERM
  tmp=$(mktemp "$target.tmp.XXXXXX") || exit
  if [ -f "$target" ]; then
    cat "$target" >"$tmp"
    if [ -s "$target" ] && [ "$(tail -c 1 "$target" | wc -l | tr -d ' ')" = 0 ]; then
      printf '\n' >>"$tmp"
    fi
  fi
  cat "$snippet" >>"$tmp"
  [ -z "$mode" ] || chmod "$mode" "$tmp"
  mv "$tmp" "$target"
  tmp=""
)

claude_md_hint() {
  local claude_md snippet marker_info marker_state start_line end_line backup mode
  local start='<!-- orchestration:start (managed by ai-agent-orchestration install.sh; edits inside are replaced) -->'
  local end='<!-- orchestration:end -->'
  snippet="$ORCH_REPO/snippets/CLAUDE.md.snippet"
  if ! claude_md=$(resolve_link_path "$CLAUDE_DIR/CLAUDE.md"); then
    say "skipped" "$CLAUDE_DIR/CLAUDE.md: cannot resolve symlink; leaving it unchanged"
    return
  fi

  marker_info=$(LC_ALL=C awk -v start="$start" -v end="$end" '
    { line = $0; sub(/\r$/, "", line) }
    line == start { starts++; if (!start_line) start_line = NR }
    line == end { ends++; if (!start_line) end_before = 1; else if (!end_line) end_line = NR }
    END {
      if (!starts && !ends) print "absent"
      else if (starts != 1 || ends != 1 || end_before || !end_line) print "invalid"
      else print "managed:" start_line ":" end_line
    }
  ' "$claude_md" 2>/dev/null || printf '%s' absent)
  marker_state=${marker_info%%:*}

  if [ "$marker_state" = managed ]; then
    start_line=${marker_info#*:}
    end_line=${start_line#*:}
    start_line=${start_line%%:*}
    if sed -n "${start_line},${end_line}p" "$claude_md" | tr -d '\r' | cmp -s - "$snippet"; then
      say "already set" "$claude_md: orchestration sections"
      return
    fi

    backup=$(backup_name "$claude_md")
    cp -p "$claude_md" "$backup"
    say "backed up" "$claude_md -> $backup"
    n_backup=$((n_backup + 1))
    mode=$(claude_md_mode "$claude_md") || die "cannot determine mode for $claude_md"
    replace_claude_md_block "$claude_md" "$start_line" "$end_line" "$snippet" "$mode"
    say "updated" "$claude_md: orchestration sections"
  elif [ "$marker_state" = invalid ]; then
    say "skipped" "$claude_md: orchestration markers are incomplete or ambiguous; fix the start and end markers, then rerun install.sh"
  elif [ -f "$claude_md" ] && grep -F -q "Agent orchestration: architect, never worker" "$claude_md"; then
    say "wrap once" "$claude_md: surround the existing orchestration text with $start and $end"
  else
    if [ -f "$claude_md" ]; then
      backup=$(backup_name "$claude_md")
      cp -p "$claude_md" "$backup"
      say "backed up" "$claude_md -> $backup"
      n_backup=$((n_backup + 1))
      mode=$(claude_md_mode "$claude_md") || die "cannot determine mode for $claude_md"
    else
      mode=""
    fi
    append_claude_md_block "$claude_md" "$snippet" "$mode"
    say "added" "$claude_md: orchestration sections"
  fi
}

run_env() {
  local envs="" d
  if [ "$ENV_GIVEN" = 0 ]; then
    ENV_NAME=$(config_value ORCH_ENV)
    [ -n "$ENV_NAME" ] || ENV_NAME=herdr
  fi
  for d in "$ORCH_REPO"/env/*/; do
    [ -f "$d/install.sh" ] && envs="$envs $(basename "$d")"
  done
  if [ "$ENV_GIVEN" = 0 ] && [ "$ENV_NAME" = none ]; then
    skip "env step (available:$envs)"
    return
  fi
  [ -f "$ORCH_REPO/env/$ENV_NAME/install.sh" ] || die "unknown env: $ENV_NAME (available:$envs)"
  ENV_DIR="$ORCH_REPO/env/$ENV_NAME"
  # shellcheck disable=SC1090
  (
    . "$ENV_DIR/install.sh"
    env_check
    env_install
  )
}

remove_stale_links
move_skill_backups
link_shared_files
write_manifest
personal_config
merge_settings
verify_guard
run_env
ask_config_keys
load_config_cache
SERVICE_PYTHON=""
SERVICE_PYTHON=$(placeholder_value ORCH_SERVICE_PYTHON)
render_services
render_legacy_map_service
register_chrome_host
platform_migrate_legacy_watchers
register_services
claude_md_hint

echo
# shellcheck disable=SC2031
if [ "$SERVICES_FAILED" = 1 ]; then
  # shellcheck disable=SC2031
  echo "Failed: $n_linked linked, $n_ok already linked, $n_backup backed up, $n_skipped skipped; a watcher service was not removed, registered or restarted (see above), run install.sh again." >&2
  exit 1
fi
# shellcheck disable=SC2031
echo "Done: $n_linked linked, $n_ok already linked, $n_backup backed up, $n_skipped skipped."
