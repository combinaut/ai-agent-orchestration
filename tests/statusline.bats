#!/usr/bin/env bats
# shellcheck disable=SC2154 # bats provides status and output after run.
# shellcheck disable=SC2030,SC2031 # run in a helper function sets output; bats runs each test in one shell.

@test "context segment includes remaining percentage and rounded used tokens" {
  run /bin/bash "$BATS_TEST_DIRNAME/../statusline-command.sh" <<'JSON'
{"model":{"display_name":"Claude"},"workspace":{"current_dir":"/tmp"},"context_window":{"remaining_percentage":81,"current_usage":{"input_tokens":120000,"cache_creation_input_tokens":30000,"cache_read_input_tokens":40500}}}
JSON

  [ "$status" -eq 0 ]
  plain=$(printf '%s' "$output" | sed $'s/\033\\[[0-9;]*m//g')
  [[ "$plain" == *"81% 191k"* ]]
}

# Links: OSC 8 hyperlinks on the repo name and the branch.
OSC=$'\033]8;;'
BEL=$'\a'

# make_repo <dir> <branch> [remote-url]: repo on <branch>, origin set to remote-url when given.
make_repo() {
  git init -q -b "$2" "$1"
  git -C "$1" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
  if [ -n "${3:-}" ]; then git -C "$1" remote add origin "$3"; fi
}

# track <dir> <remote> <branch>: the current branch tracks <remote>/<branch>.
track() {
  git -C "$1" config "branch.$(git -C "$1" branch --show-current).remote" "$2"
  git -C "$1" config "branch.$(git -C "$1" branch --show-current).merge" "refs/heads/$3"
}

run_line() {
  run /bin/bash "$BATS_TEST_DIRNAME/../statusline-command.sh" <<JSON
{"model":{"display_name":"Claude"},"workspace":{"current_dir":"$1"}}
JSON
}

strip_all() {
  printf '%s' "$1" | sed -e $'s/\033\\[[0-9;]*m//g' -e $'s/\033\\]8;;[^\a]*\a//g'
}

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$HOME"
  REPO="$BATS_TEST_TMPDIR/proj"
}

@test "ssh remote: repo and branch link to github" {
  make_repo "$REPO" feat/x git@github.com:acme/proj.git
  track "$REPO" origin feat/x
  run_line "$REPO"
  [ "$status" -eq 0 ]
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/feat/x${BEL}"*"feat/x"* ]] || return 1
}

@test "ssh:// remote with a port drops the port" {
  make_repo "$REPO" main ssh://git@github.com:2222/acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/main${BEL}"* ]] || return 1
}

@test "https remote: credentials never reach the output" {
  make_repo "$REPO" main https://bob:s3cret@github.com/acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" != *s3cret* ]] || return 1
  [[ "$output" != *bob* ]] || return 1
}

@test "gitlab host uses /-/tree/" {
  make_repo "$REPO" main git@gitlab.example.com:acme/sub/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://gitlab.example.com/acme/sub/proj/-/tree/main${BEL}"* ]] || return 1
}

@test "gitlab in the owner or repo name of a github remote still uses /tree/" {
  make_repo "$REPO" main git@github.com:gitlab-org/gitlab-foss.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/gitlab-org/gitlab-foss/tree/main${BEL}"* ]] || return 1
}

@test "bitbucket uses /src/" {
  make_repo "$REPO" main git@bitbucket.org:acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://bitbucket.org/acme/proj/src/main${BEL}"* ]] || return 1
}

@test "unknown remote forms stay plain text" {
  make_repo "$REPO" main git@github.com:acme/proj.git
  run_line "$REPO"
  [[ "$output" == *"$OSC"* ]] || return 1
  for url in /srv/git/proj.git file:///srv/git/proj.git "ftp://example.com/acme/proj.git" "https://example.com/proj"; do
    rm -rf "$REPO"
    make_repo "$REPO" main "$url"
    track "$REPO" origin main
    run_line "$REPO"
    [[ "$output" != *$'\033]8;'* ]] || return 1
  done
}

@test "link text with control characters is refused" {
  make_repo "$REPO" main git@github.com:acme/proj.git
  run_line "$REPO"
  [[ "$output" == *"$OSC"* ]] || return 1
  rm -rf "$REPO"
  make_repo "$REPO" main $'https://example.com/acme/pr\aoj'
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" != *$'\033]8;'* ]] || return 1
}

@test "tracking a non-origin remote links that remote with the upstream branch name" {
  make_repo "$REPO" local-name git@github.com:me/fork.git
  git -C "$REPO" remote add upstream git@github.com:acme/proj.git
  track "$REPO" upstream main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/main${BEL}"* ]] || return 1
}

@test "no upstream: repo links to origin, branch stays plain" {
  make_repo "$REPO" feat/x git@github.com:acme/proj.git
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" != *"/tree/"* ]] || return 1
}

@test "no upstream and no origin: no links" {
  make_repo "$REPO" main git@github.com:acme/proj.git
  run_line "$REPO"
  [[ "$output" == *"$OSC"* ]] || return 1
  rm -rf "$REPO"
  make_repo "$REPO" main
  run_line "$REPO"
  [[ "$output" == *"proj"* ]] || return 1
  [[ "$output" != *$'\033]8;'* ]] || return 1
}

@test "upstream on the local repo (.) gives no branch link" {
  make_repo "$REPO" main git@github.com:acme/proj.git
  git -C "$REPO" branch other
  track "$REPO" . other
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" != *"/tree/"* ]] || return 1
}

@test "detached HEAD: repo links, no branch link" {
  make_repo "$REPO" main git@github.com:acme/proj.git
  track "$REPO" origin main
  git -C "$REPO" checkout -q --detach
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" != *"/tree/"* ]] || return 1
}

@test "linked worktree: the worktree segment links to the branch view" {
  make_repo "$REPO" main git@github.com:acme/proj.git
  git -C "$REPO" worktree add -q -b feat/wt "$BATS_TEST_TMPDIR/wt"
  track "$BATS_TEST_TMPDIR/wt" origin feat/wt
  run_line "$BATS_TEST_TMPDIR/wt"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/feat/wt${BEL}"*"wt"* ]] || return 1
}

@test "branch name special characters are percent-encoded, slash kept" {
  make_repo "$REPO" 'feat/a#b%d+e&f' git@github.com:acme/proj.git
  track "$REPO" origin 'feat/a#b%d+e&f'
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/feat/a%23b%25d%2Be%26f${BEL}"* ]] || return 1
}

@test "visible text is the same with and without links" {
  make_repo "$REPO" feat/x git@github.com:acme/proj.git
  track "$REPO" origin feat/x
  run_line "$REPO"
  linked_raw=$output
  linked=$(strip_all "$output")
  git -C "$REPO" remote remove origin
  run_line "$REPO"
  plain=$(strip_all "$output")
  [ "$linked" = "$plain" ]
  [ "$plain" = "Claude | proj | "$'\xee\x9c\xa5'" feat/x" ]
  [[ "$linked_raw" == *"$OSC"* ]] || return 1
}

@test "non-ASCII branch name is encoded byte by byte (bash 3.2 safe)" {
  make_repo "$REPO" 'café' git@github.com:acme/proj.git
  track "$REPO" origin 'café'
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/caf%C3%A9${BEL}"* ]] || return 1
}

@test "non-ASCII branch name is encoded the same under a UTF-8 locale" {
  make_repo "$REPO" 'café' git@github.com:acme/proj.git
  track "$REPO" origin 'café'
  LC_ALL=nl_NL.UTF-8 run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/caf%C3%A9${BEL}"* ]] || return 1
}

@test "a semicolon in the branch name is encoded" {
  make_repo "$REPO" 'a;b' git@github.com:acme/proj.git
  track "$REPO" origin 'a;b'
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/a%3Bb${BEL}"* ]] || return 1
}

# plain_remotes <url>...: each remote gives no link (a github remote gives one, as the control).
plain_remotes() {
  rm -rf "$REPO"
  make_repo "$REPO" main git@github.com:acme/proj.git
  run_line "$REPO"
  [[ "$output" == *"$OSC"* ]] || return 1
  for url in "$@"; do
    rm -rf "$REPO"
    make_repo "$REPO" main "$url"
    track "$REPO" origin main
    run_line "$REPO"
    [[ "$output" != *$'\033]8;'* ]] || { echo "linked: $url"; return 1; }
  done
}

@test "malformed remotes never put part of a credential in a link" {
  make_repo "$REPO" main 'https://user:tok/en@github.com/acme/proj.git'
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" != *tok* ]] || return 1
  [[ "$output" != *$'\033]8;'* ]] || return 1
  plain_remotes 'https://user:tok/en@github.com/acme/proj.git' 'user:token@host.example:o/r.git' 'ssh://user:pa/ss@host.example/o/r' 'user:p@ss@github.com:o/r.git' 'a@b@host.example:o/r.git' 'git@host.example:a:b/r.git'
}

@test "host without a dot (ssh alias) stays plain" {
  plain_remotes git@github-work:acme/proj.git
}

@test "empty host stays plain" {
  plain_remotes https:///acme/proj.git
}

@test "Azure DevOps: ssh stays plain, https links the repo only" {
  plain_remotes git@ssh.dev.azure.com:v3/org/proj/repo
  make_repo "$BATS_TEST_TMPDIR/az" main https://org@dev.azure.com/org/proj/_git/repo
  track "$BATS_TEST_TMPDIR/az" origin main
  run_line "$BATS_TEST_TMPDIR/az"
  [[ "$output" == *"${OSC}https://dev.azure.com/org/proj/_git/repo${BEL}"* ]] || return 1
  [[ "$output" != *"/tree/"* ]] || return 1
}

@test "local-path forms stay plain; sourcehut ~user still links" {
  plain_remotes git@host.example:/srv/git/proj.git git@host.example:~/proj.git me@work.example/repos:old/proj.git
  make_repo "$BATS_TEST_TMPDIR/sr" main git@git.sr.ht:~user/repo
  run_line "$BATS_TEST_TMPDIR/sr"
  [[ "$output" == *"${OSC}https://git.sr.ht/~user/repo${BEL}"* ]] || return 1
}

@test "branch.<b>.merge without a remote gives no branch link; a removed upstream remote falls back to origin" {
  make_repo "$REPO" feat/x git@github.com:acme/proj.git
  git -C "$REPO" config branch.feat/x.merge refs/heads/feat/x
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" != *"/tree/"* ]] || return 1
  track "$REPO" gone feat/x
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" != *"/tree/"* ]] || return 1
}

@test "an upstream that is not a branch (tag ref) gives no branch link" {
  make_repo "$REPO" feat/x git@github.com:acme/proj.git
  track "$REPO" origin refs/tags/v1
  git -C "$REPO" config branch.feat/x.merge refs/tags/v1
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" != *"/tree/"* ]] || return 1
}

@test "host names are matched case-insensitively" {
  make_repo "$REPO" main git@GitLab.com:acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://gitlab.com/acme/proj/-/tree/main${BEL}"* ]] || return 1
  rm -rf "$REPO"
  make_repo "$REPO" main git@BitBucket.org:acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://bitbucket.org/acme/proj/src/main${BEL}"* ]] || return 1
  plain_remotes git@SSH.DEV.AZURE.COM:v3/org/proj/repo
}

@test "an explicit https port does not change the host routing" {
  make_repo "$REPO" main https://bitbucket.org:443/acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://bitbucket.org:443/acme/proj/src/main${BEL}"* ]] || return 1
  rm -rf "$REPO"
  make_repo "$REPO" main https://dev.azure.com:443/org/proj/_git/repo
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://dev.azure.com:443/org/proj/_git/repo${BEL}"* ]] || return 1
  [[ "$output" != *"/tree/"* ]] || return 1
}

@test "unknown hosts keep the repo link but get no branch link" {
  make_repo "$REPO" main git@gitea.example.com:acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://gitea.example.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" != *"/tree/"* && "$output" != *"/src/"* ]] || return 1
}

@test "GitHub Enterprise host (github in the name) uses /tree/" {
  make_repo "$REPO" main git@github.corp.example:acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.corp.example/acme/proj/tree/main${BEL}"* ]] || return 1
}

@test "scp-style remote without user@ is linked" {
  make_repo "$REPO" main github.com:acme/proj.git
  track "$REPO" origin main
  run_line "$REPO"
  [[ "$output" == *"${OSC}https://github.com/acme/proj${BEL}proj"* ]] || return 1
  [[ "$output" == *"${OSC}https://github.com/acme/proj/tree/main${BEL}"* ]] || return 1
}

@test "scp-style forms without user@ that are local paths or have no dotted host stay plain" {
  plain_remotes ./a:b a/b:c c:foo
}

# Usage sensor: <session-id>.usage holds only the fields the input has.
usage_file() { printf '%s' "$HOME/.claude/context-usage/$1.usage"; }

@test "usage file holds model id, cost and the 7-day limit when present" {
  run /bin/bash "$BATS_TEST_DIRNAME/../statusline-command.sh" <<'JSON'
{"session_id":"s1","model":{"id":"claude-opus-5-5","display_name":"Opus 5.5"},"workspace":{"current_dir":"/tmp"},"cost":{"total_cost_usd":1.25},"rate_limits":{"seven_day":{"used_percentage":42.5,"resets_at":1893456000}}}
JSON
  [ "$status" -eq 0 ]
  [ "$(jq -c . "$(usage_file s1)")" = '{"model":"claude-opus-5-5","cost_usd":1.25,"seven_day_pct":42.5,"seven_day_resets_at":1893456000}' ]
  [ "$(find "$HOME/.claude/context-usage" -type f | wc -l | tr -d " ")" = 1 ]
}

@test "usage file leaves out rate_limits when the input has none, never writes 0" {
  run /bin/bash "$BATS_TEST_DIRNAME/../statusline-command.sh" <<'JSON'
{"session_id":"s2","model":{"id":"claude-sonnet-5-5"},"workspace":{"current_dir":"/tmp"},"cost":{"total_cost_usd":0}}
JSON
  [ "$status" -eq 0 ]
  [ "$(jq -c 'keys' "$(usage_file s2)")" = '["cost_usd","model"]' ]
  [ "$(jq '.cost_usd' "$(usage_file s2)")" = 0 ]
}

@test "usage file uses model.id, not display_name, and is absent when no field is present" {
  run /bin/bash "$BATS_TEST_DIRNAME/../statusline-command.sh" <<'JSON'
{"session_id":"s3","model":{"display_name":"Opus 5.5"},"workspace":{"current_dir":"/tmp"}}
JSON
  [ "$status" -eq 0 ]
  [ ! -e "$(usage_file s3)" ]
}
