#!/bin/bash
# Claude Code status line: model, cwd, git branch, output style, context remaining.
input=$(cat)

model=$(echo "$input" | jq -r '.model.display_name // empty')
dir=$(echo "$input" | jq -r '.workspace.current_dir // empty')
style=$(echo "$input" | jq -r '.output_style.name // empty')
remaining=$(echo "$input" | jq -r '.context_window.remaining_percentage // empty')
session_id=$(echo "$input" | jq -r '.session_id // empty')
tokens=$(echo "$input" | jq -r '.context_window.current_usage // empty | try (((.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)) | if type == "number" and . == floor then . else empty end) catch empty')

# Context sensors for the Coordinator: token count and remaining percentage per session.
if [ -n "$session_id" ] && [ -n "$remaining" ]; then
  { mkdir -p "$HOME/.claude/context-usage" && printf '%s\n' "$remaining" > "$HOME/.claude/context-usage/$session_id"; } 2>/dev/null \
    || echo "statusline: cannot write $HOME/.claude/context-usage/$session_id" >&2
fi

if [ -n "$session_id" ] && [ -n "$tokens" ]; then
  { mkdir -p "$HOME/.claude/context-usage" && printf '%s\n' "$tokens" > "$HOME/.claude/context-usage/$session_id.tokens"; } 2>/dev/null \
    || echo "statusline: cannot write $HOME/.claude/context-usage/$session_id.tokens" >&2
fi

# Usage sensor: model id, session cost and the 7-day limit, only the fields the input has (never a 0 for a missing one).
# cost.total_cost_usd is cumulative and resets only on /clear. Written atomically: temp file in the same dir, then mv.
if [ -n "$session_id" ]; then
  usage=$(echo "$input" | jq -c '{model: .model.id, cost_usd: .cost.total_cost_usd, seven_day_pct: .rate_limits.seven_day.used_percentage, seven_day_resets_at: .rate_limits.seven_day.resets_at} | with_entries(select(.value != null))' 2>/dev/null)
  if [ -n "$usage" ] && [ "$usage" != "{}" ]; then
    { mkdir -p "$HOME/.claude/context-usage" && tmp=$(mktemp "$HOME/.claude/context-usage/.$session_id.usage.XXXXXX") \
      && printf '%s\n' "$usage" > "$tmp" && mv "$tmp" "$HOME/.claude/context-usage/$session_id.usage"; } 2>/dev/null \
      || echo "statusline: cannot write $HOME/.claude/context-usage/$session_id.usage" >&2
  fi
fi

dir_name=$(basename "$dir" 2>/dev/null)

# https://host/owner/repo for an ssh, ssh:// or https remote URL, credentials dropped; fails otherwise.
web_url() {
  local u=$1 rest authority host path
  case $u in
    https://*) rest=${u#https://}; authority=${rest%%/*}; host=${authority##*@}; path=${rest#*/} ;;
    ssh://*) rest=${u#ssh://}; authority=${rest%%/*}; host=${authority##*@}; host=${host%%:*}; path=${rest#*/} ;;
    *://*) return 1 ;;
    *:*)
      # [user@]host:path. git's rule: a "/" before the first ":" makes it a local path
      [[ ${u%%:*} != */* ]] || return 1
      host=${u%%:*}; host=${host#*@}; path=${u#*:} ;;
    *) return 1 ;;
  esac
  host=$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')
  path=${path%/}; path=${path%.git}
  [[ $host == *.* && $path == */* && $path != /* && $path != \~/* && $path != *[@:]* ]] || return 1
  [[ $host != ssh.dev.azure.com ]] || return 1
  rest="https://$host/$path"
  [[ $rest =~ ^[A-Za-z0-9._~/:%+-]+$ ]] || return 1
  printf '%s' "$rest"
}

# Percent-encode everything except unreserved characters and "/".
url_encode() {
  local s=$1 out="" c i
  local LC_ALL=C
  for ((i = 0; i < ${#s}; i++)); do
    c=${s:i:1}
    case $c in
      [A-Za-z0-9._~/-]) out+=$c ;;
      *) out+=$(printf '%%%02X' "$(($(printf '%d' "'$c") & 255))") ;;
    esac
  done
  printf '%s' "$out"
}

# OSC 8 hyperlink: link TEXT to URL; plain TEXT when URL is empty.
link() {
  if [ -n "$1" ]; then printf '\033]8;;%s\a%s\033]8;;\a' "$1" "$2"; else printf '%s' "$2"; fi
}

branch=""
worktree=""
repo_url=""
branch_url=""
if [ -n "$dir" ] && git -C "$dir" --no-optional-locks rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch=$(git -C "$dir" --no-optional-locks branch --show-current 2>/dev/null)
  toplevel=$(git -C "$dir" --no-optional-locks rev-parse --show-toplevel 2>/dev/null)
  remote=""
  remote_branch=""
  if [ -n "$branch" ]; then
    # git's own upstream rule: empty when there is no usable upstream
    IFS='|' read -r remote merge < <(git -C "$dir" --no-optional-locks for-each-ref --format='%(upstream:remotename)|%(upstream:remoteref)' "refs/heads/$branch" 2>/dev/null)
    remote_branch=${merge#refs/heads/}
    if [ "$remote" = "." ]; then remote=""; remote_branch=""; fi
  fi
  [ -n "$remote" ] || remote=origin
  remote_url=$(git -C "$dir" --no-optional-locks remote get-url "$remote" 2>/dev/null)
  if [ -n "$remote_url" ] && repo_url=$(web_url "$remote_url"); then
    if [ -n "$remote_branch" ]; then
      repo_host=${repo_url#https://}
      repo_host=${repo_host%%/*}
      repo_host=${repo_host%%:*}
      case $repo_host in
        *gitlab*) branch_url="$repo_url/-/tree/$(url_encode "$remote_branch")" ;;
        bitbucket.org) branch_url="$repo_url/src/$(url_encode "$remote_branch")" ;;
        *github*) branch_url="$repo_url/tree/$(url_encode "$remote_branch")" ;;
        *) branch_url="" ;;
      esac
    fi
  else
    repo_url=""
  fi
  if [ -n "$toplevel" ]; then
    dir_name=$(basename "$toplevel")
    # Linked worktree: git-dir differs from git-common-dir; show repo/worktree
    git_dir=$(git -C "$dir" --no-optional-locks rev-parse --absolute-git-dir 2>/dev/null)
    common_dir=$(git -C "$dir" --no-optional-locks rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
    if [ -n "$git_dir" ] && [ -n "$common_dir" ] && [ "$git_dir" != "$common_dir" ]; then
      worktree=$dir_name
      dir_name=$(basename "$(dirname "$common_dir")")
    fi
  fi
fi

ctx=""
if [ -n "$remaining" ]; then
  pct=$(printf '%.0f' "$remaining" 2>/dev/null || echo "$remaining")
  if [ "$pct" -le 15 ] 2>/dev/null; then
    ctx_color='1;31'
  elif [ "$pct" -le 35 ] 2>/dev/null; then
    ctx_color='1;33'
  else
    ctx_color='0;37'
  fi
  ctx=$(printf '\033[%sm%s%%\033[0m' "$ctx_color" "$pct")
  if [ -n "$tokens" ]; then
    ctx="$ctx $(printf '\033[%sm%sk\033[0m' "$ctx_color" "$(( (tokens + 500) / 1000 ))")"
  fi
fi

segments=()
[ -n "$model" ] && segments+=("$(printf '\033[32m%s\033[0m' "$model")")
[ -n "$ctx" ] && segments+=("$ctx")
[ -n "$dir_name" ] && segments+=("$(printf '\033[33m%s\033[0m' "$(link "$repo_url" "$dir_name")")")
# \xef\x90\x82 = U+F402 Nerd Font oct-repo-forked (worktree)
[ -n "$worktree" ] && segments+=("$(printf '\033[34m\xef\x90\x82 %s\033[0m' "$(link "$branch_url" "$worktree")")")
# \xee\x9c\xa5 = U+E725 Nerd Font git-branch (devicons)
if [ -n "$branch" ] && [ -z "$worktree" ]; then
  segments+=("$(printf '\033[36m\xee\x9c\xa5 %s\033[0m' "$(link "$branch_url" "$branch")")")
fi
if [ -n "$style" ] && [ "$style" != "default" ]; then
  segments+=("$(printf '\033[35m%s\033[0m' "$style")")
fi

output=""
for segment in "${segments[@]}"; do
  if [ -z "$output" ]; then
    output="$segment"
  else
    output="$output $(printf '\033[2m|\033[0m') $segment"
  fi
done

printf '%s\n' "$output"
