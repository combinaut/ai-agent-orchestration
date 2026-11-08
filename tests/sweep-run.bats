#!/usr/bin/env bats
# shellcheck disable=SC2154,SC2030,SC2031,SC2016 # run --separate-stderr sets stderr; tests reset HEAD_SHA; Python snippets sit in single quotes.

bats_require_minimum_version 1.5.0

setup() {
  ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/sweep-run-bats.XXXXXX")
  export TMP HOME="$TMP/home" XDG_CACHE_HOME="$TMP/cache" PYTHONDONTWRITEBYTECODE=1
  mkdir -p "$HOME" "$TMP/bin"
  for agent in codex claude; do
    ln -s "$ROOT/tests/fixtures/sweep-agent.py" "$TMP/bin/$agent"
  done
  stub_gh
  export PATH="$TMP/bin:$PATH" ORCH_CODEX_WORKER_MODEL=gpt-test ORCH_CLAUDE_TIERS=haiku,sonnet,opus
  export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=t GIT_CONFIG_KEY_1=user.email GIT_CONFIG_VALUE_1=t@example.com
  REPO="$TMP/repo"
  git init -q -b main "$REPO"
  write lib/alpha.py 10
  write docs/guide.md 3
  commit base
  BASE_SHA=$(git -C "$REPO" rev-parse HEAD)
  git -C "$REPO" switch -q -c feature
  write lib/alpha.py 20
  write tests/alpha.bats 5
  write docs/guide.md 6
  commit feature
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  PR=https://github.com/acme/app/pull/7
}

teardown() {
  rm -rf "$TMP"
}

# write <path> <lines>: a file in the repo with that many distinct lines.
write() {
  mkdir -p "$REPO/$(dirname "$1")"
  seq -f "$1 line %g" "$2" >"$REPO/$1"
}

commit() {
  git -C "$REPO" add -A
  git -C "$REPO" commit -qm "$1"
}

# stub_gh: a gh for acme/app#7 that serves $TMP/comments.json when present and saves a posted comment.
stub_gh() {
  cat >"$TMP/bin/gh" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  'api repos/acme/app/pulls/7') echo '{"title":"Add alpha","body":"Adds alpha and its test.","base":{"ref":"main"}}' ;;
  'api --paginate --slurp repos/acme/app/issues/7/comments') cat "$TMP/comments.json" 2>/dev/null || echo '[[]]' ;;
  'api --paginate --slurp repos/acme/app/pulls/7/comments') echo '[[]]' ;;
  'api --paginate --slurp repos/acme/app/pulls/7/reviews') cat "$TMP/reviews.json" 2>/dev/null || echo '[[]]' ;;
  'api -X POST repos/acme/app/issues/7/comments --input -')
    cat >"$TMP/posted.json"
    echo '{"html_url":"https://github.com/acme/app/pull/7#issuecomment-1"}' ;;
  *) echo "gh stub: unexpected $*" >&2; exit 1 ;;
esac
EOF
  chmod +x "$TMP/bin/gh"
}

sweep() {
  cd "$REPO" || return 1
  run --separate-stderr "$ROOT/orch-sweep" "$@"
}

run_sweep() {
  sweep run --pr "$PR" --head "$HEAD_SHA" --base-ref main --record "$TMP/record.json" "$@"
}

# field <expr>: a Python expression over the record r.
field() {
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$TMP/record.json" "$1"
}

calls() {
  python3 -c 'import json,sys; print(sum(1 for l in open(sys.argv[1]) if json.loads(l)["role"] == sys.argv[2]))' "$TMP/agent.calls" "$1"
}

plan() {
  sweep plan --base "$BASE_SHA" --head "$HEAD_SHA"
  [ "$status" -eq 0 ]
  PLAN=$output
}

# slice_of <path>: the name of the slice holding that path.
slice_of() {
  python3 -c 'import json,sys; print(next(s["name"] for s in json.loads(sys.argv[1])["slices"] for f in s["files"] if f["path"] == sys.argv[2]))' "$PLAN" "$1"
}

@test "a test file joins the slice of the source file with the same stem" {
  plan
  [ "$(slice_of tests/alpha.bats)" = "$(slice_of lib/alpha.py)" ]
}

@test "files of different features never share a slice" {
  plan
  [ "$(slice_of docs/guide.md)" != "$(slice_of lib/alpha.py)" ]
}

@test "a feature over 400 lines splits, and a file over 400 lines gets its own slice" {
  write lib/beta.py 300
  write lib/gamma.py 300
  write lib/huge.py 450
  write lib/big.py 350
  write tests/big.bats 100
  commit more
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  plan
  [ "$(slice_of lib/beta.py)" != "$(slice_of lib/gamma.py)" ]
  [ "$(slice_of lib/big.py)" != "$(slice_of tests/big.bats)" ]
  python3 -c 'import json,sys
for s in json.loads(sys.argv[1])["slices"]:
    assert s["lines"] <= 400 or len(s["files"]) == 1, s
    assert s["lines"] == sum(f["lines"] or 0 for f in s["files"]), s
huge = [s for s in json.loads(sys.argv[1])["slices"] if any(f["path"] == "lib/huge.py" for f in s["files"])]
assert len(huge[0]["files"]) == 1' "$PLAN"
}

@test "binary files share one slice of their own" {
  printf '\000\001' >"$REPO/lib/blob.bin"
  printf '\000\002' >"$REPO/docs/pic.bin"
  commit binaries
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  plan
  [ "$(slice_of lib/blob.bin)" = binary ]
  [ "$(slice_of docs/pic.bin)" = binary ]
  [ "$(slice_of lib/alpha.py)" != binary ]
}

@test "a deleted file joins the slice of its replacement, and a rename keeps its old path" {
  git -C "$REPO" rm -q lib/alpha.py
  write app/alpha.rb 40
  git -C "$REPO" mv docs/guide.md docs/manual.md
  commit move
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  plan
  [ "$(slice_of lib/alpha.py)" = "$(slice_of app/alpha.rb)" ]
  python3 -c 'import json,sys
f = [f for s in json.loads(sys.argv[1])["slices"] for f in s["files"] if f["path"] == "docs/manual.md"][0]
assert f["old"] == "docs/guide.md", f' "$PLAN"
}

@test "tree state hashes each untracked file by path, mode and content" {
  echo one >"$REPO/loose.txt"
  sweep tree
  first=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["untracked"]["loose.txt"])' "$output")
  chmod +x "$REPO/loose.txt"
  sweep tree
  moded=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["untracked"]["loose.txt"])' "$output")
  echo two >"$REPO/loose.txt"
  chmod -x "$REPO/loose.txt"
  sweep tree
  changed=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["untracked"]["loose.txt"])' "$output")
  [ "$first" != "$moded" ]
  [ "$first" != "$changed" ]
  [ "$moded" != "$changed" ]
  mv "$REPO/loose.txt" "$REPO/renamed.txt"
  echo one >"$REPO/renamed.txt"
  sweep tree
  python3 -c 'import json,sys; t=json.loads(sys.argv[1]); assert list(t["untracked"]) == ["renamed.txt"], t' "$output"
}

@test "a run starts two sweep agents per slice, one PR-text agent and the validators, and writes a record that passes CI attestation" {
  FAKE_FINDINGS=1 run_sweep --no-comment
  [ "$status" -eq 0 ]
  [ "$(calls sweep-comments)" -eq 2 ]
  [ "$(calls sweep-footprint)" -eq 2 ]
  [ "$(calls pr-text)" -eq 1 ]
  [ "$(calls validator)" -eq 1 ]
  [ "$(field 'r["sweep_agents"]')" -eq 5 ]
  [ "$(field 'len(set(r["sweep_agent_ids"]))')" -eq 5 ]
  [ "$(field 'r["findings"]')" -eq 4 ]
  [ "$(field 'r["model"]')" = gpt-test ]
  [ "$(field 'r["runtime"]')" = codex ]
  [ "$(field 'r["kind"]')" = full ]
  [ "$(field 'r["base"]')" = "$BASE_SHA" ]
  [ "$(field 'r["open_fix"]')" -eq 4 ]
  run --separate-stderr "$ROOT/orch-sweep-record" check --record "$TMP/record.json" --head "$HEAD_SHA" --exact --no-transcripts
  [ "$status" -eq 0 ]
}

@test "codex agents run in a read-only sandbox with the schema" {
  run_sweep --no-comment
  [ "$status" -eq 0 ]
  python3 -c 'import json,sys
for line in open(sys.argv[1]):
    argv = json.loads(line)["argv"]
    assert argv[:3] == ["exec", "-m", "gpt-test"], argv
    assert argv[argv.index("-s") + 1] == "read-only", argv
    assert "--output-schema" in argv and "--ephemeral" in argv, argv' "$TMP/agent.calls"
}

@test "claude agents run headless with no write tool and the worker tier" {
  run_sweep --no-comment --runtime claude
  [ "$status" -eq 0 ]
  [ "$(field 'r["model"]')" = sonnet ]
  python3 -c 'import json,sys
for line in open(sys.argv[1]):
    argv = json.loads(line)["argv"]
    assert argv[:3] == ["-p", "--model", "sonnet"], argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk", argv
    assert argv[argv.index("--setting-sources") + 1] == "user", argv
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob", argv
    assert not any("Bash" in a for a in argv), argv
    assert not argv[argv.index("--add-dir") + 1].startswith(sys.argv[2]), argv
    assert argv[-4:] == ["--disallowedTools", "Edit", "Write", "NotebookEdit"], argv
    assert not any(a.startswith(("Edit", "Write")) for a in argv[argv.index("--allowedTools"):-4]), argv' "$TMP/agent.calls" "$REPO"
}

@test "a text file that is not UTF-8 reaches the agents byte for byte" {
  printf 'caf\351 latin-1\n' >"$REPO/lib/latin.txt"
  commit latin
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  run_sweep --no-comment
  [ "$status" -eq 0 ]
  LC_ALL=C grep -q "^+caf$(printf '\351') latin-1$" "$TMP"/prompts/pr-text.*.diff
}

@test "a file named like a pathspec puts only itself in its slice diff" {
  write '*.py' 2
  commit glob
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  run_sweep --no-comment
  [ "$status" -eq 0 ]
  diff=$(grep -l '^+\*\.py line 1$' "$TMP"/prompts/sweep-comments.*.diff)
  [ -n "$diff" ]
  run ! grep -q 'lib/alpha.py' "$diff"
}

@test "every agent reads its diff from a file outside the checkout, removed after the run" {
  FAKE_FINDINGS=1 run_sweep --no-comment
  [ "$status" -eq 0 ]
  for role in sweep-comments sweep-footprint pr-text validator; do
    ls "$TMP"/prompts/"$role".*.diff >/dev/null
  done
  grep -l '^+lib/alpha.py line 20$' "$TMP"/prompts/sweep-comments.*.diff >/dev/null
  grep -l '^+tests/alpha.bats line 5$' "$TMP"/prompts/pr-text.*.diff >/dev/null
  folder=$(sed -n 's/.* is in \(.*\)\/pr\.diff[;.].*/\1/p' "$TMP"/prompts/pr-text.* | head -1)
  [ -n "$folder" ]
  [[ "$folder" != "$REPO"* ]]
  [ ! -e "$folder" ]
  run ! grep -q 'with git diff' "$TMP"/prompts/sweep-comments.[0-9]*[0-9]
}

@test "the codex model falls back to the smart model" {
  ORCH_CODEX_WORKER_MODEL="" ORCH_CODEX_SMART_MODEL=gpt-smart run_sweep --no-comment
  [ "$status" -eq 0 ]
  [ "$(field 'r["model"]')" = gpt-smart ]
}

@test "no more agents run at once than the concurrency cap" {
  for n in 1 2 3 4 5 6; do write "f$n/x.py" 5; done
  commit many
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  FAKE_SLEEP=0.4 run_sweep --no-comment --concurrency 3
  [ "$status" -eq 0 ]
  [ "$(sort -n "$TMP/live.max" | tail -1)" -le 3 ]
  [ "$(sort -n "$TMP/live.max" | tail -1)" -ge 2 ]
}

@test "more than 25 slices stops before any agent starts" {
  for n in $(seq 1 26); do write "d$n/x.py" 5; done
  commit wide
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  run_sweep --no-comment
  [ "$status" -eq 3 ]
  [[ "$stderr" == "ERROR: "*"slices; the limit is 25"* ]]
  [ ! -e "$TMP/agent.calls" ]
}

@test "a dirty tree stops before any agent starts" {
  echo dirty >>"$REPO/lib/alpha.py"
  run_sweep --no-comment
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: the tree is not clean"* ]]
  [ ! -e "$TMP/agent.calls" ]
}

@test "a head that is not the checkout's HEAD stops" {
  sweep run --pr "$PR" --head "$BASE_SHA" --base-ref main --no-comment
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: HEAD is $HEAD_SHA, not $BASE_SHA" ]]
}

@test "an agent that writes to the tree fails the run with no record" {
  FAKE_WRITE=sweep-footprint FAKE_WRITE_PATH=lib/alpha.py run_sweep
  [ "$status" -eq 2 ]
  [[ "$stderr" == "ERROR: the tree changed while the agents ran (sweep agents): status, diff"* ]]
  [ ! -e "$TMP/record.json" ]
  [ ! -e "$TMP/posted.json" ]
}

@test "an agent that adds an untracked file fails the run" {
  FAKE_WRITE=pr-text FAKE_WRITE_PATH=new.txt run_sweep --no-comment
  [ "$status" -eq 2 ]
}

@test "a validator that writes to the tree fails the run" {
  FAKE_FINDINGS=1 FAKE_WRITE=validator FAKE_WRITE_PATH=lib/alpha.py run_sweep --no-comment
  [ "$status" -eq 2 ]
}

@test "an agent that times out is retried once, then the run fails with no record" {
  FAKE_HANG=pr-text run_sweep --no-comment --agent-timeout 1
  [ "$status" -eq 4 ]
  [ "$(calls pr-text)" -eq 2 ]
  [[ "$stderr" == *"pr-text"*"timed out"* ]]
  [ ! -e "$TMP/record.json" ]
}

@test "a validator that leaves a finding without a verdict fails the run" {
  FAKE_FINDINGS=1 FAKE_DROP_ID=F2 run_sweep --no-comment
  [ "$status" -eq 4 ]
  [[ "$stderr" == *"no verdict for F2"* ]]
}

@test "one validator per 40 findings" {
  FAKE_FINDINGS=21 run_sweep --no-comment
  [ "$status" -eq 0 ]
  [ "$(field 'r["findings"]')" -eq 84 ]
  [ "$(calls validator)" -eq 3 ]
  [ "$(field 'len(r["validator_ids"])')" -eq 3 ]
}

@test "nits and dropped findings are not open fixes" {
  FAKE_FINDINGS=1 FAKE_CLASS=nit run_sweep --no-comment
  [ "$status" -eq 0 ]
  [ "$(field 'r["open_fix"]')" -eq 0 ]
  [ "$(field 'len(r["items"])')" -eq 4 ]
  FAKE_FINDINGS=1 FAKE_VERDICT=FALSE run_sweep --no-comment
  [ "$(field 'len(r["items"])')" -eq 0 ]
  [ "$(field 'len(r["dropped"])')" -eq 4 ]
  [ "$(field 'r["dropped"][0]["verdict"]')" = FALSE ]
  [ "$(field 'r["verdicts"]["FALSE"]')" -eq 4 ]
}

@test "the comment names the full head and carries the record" {
  FAKE_FINDINGS=1 run_sweep
  [ "$status" -eq 0 ]
  python3 -c 'import json,re,sys
body = json.load(open(sys.argv[1]))["body"]
record = json.load(open(sys.argv[2]))
assert body.startswith("<!-- orch-sweep v1 -->"), body
assert sys.argv[3] in body
block = re.search(r"```json\n(.*?)\n```", body, re.S).group(1)
assert json.loads(block) == record
assert body.rstrip().endswith("*(Generated using Codex)*"), body
assert "**Kept**" in body and "| fix |" in body
assert "**Dropped**" not in body' "$TMP/posted.json" "$TMP/record.json" "$HEAD_SHA"
  [ -f "$XDG_CACHE_HOME/review-prs/sweeps/acme/app/7/$HEAD_SHA.json" ]
}

@test "the isolated posting helper imports only the trusted library" {
  printf 'raise RuntimeError("checkout import")\n' >"$REPO/subprocess.py"
  run --separate-stderr /opt/homebrew/bin/python3 -I "$ROOT/lib/github_write_isolated.py" --help
  [ "$status" -eq 0 ]
  [[ "$output" == usage:* ]]
  [[ "$stderr" != *"ModuleNotFoundError: No module named 'github_poll'"* ]]
  [[ "$stderr" != *"checkout import"* ]]
}

@test "runner Git commands ignore checkout hooks, filters, config includes, fsmonitor and textconv" {
  marker="$TMP/git-side-effect"
  mkdir -p "$REPO/hooks"
  printf '#!/bin/sh\nprintf hook >>"%s"\n' "$marker" >"$REPO/hooks/post-index-change"
  chmod +x "$REPO/hooks/post-index-change"
  printf '#!/bin/sh\nprintf filter >>"%s"\ncat\n' "$marker" >"$TMP/filter"
  chmod +x "$TMP/filter"
  printf '#!/bin/sh\nprintf textconv >>"%s"\ncat "$1"\n' "$marker" >"$TMP/textconv"
  chmod +x "$TMP/textconv"
  printf '*.txt filter=evil diff=evil\n' >"$REPO/.gitattributes"
  write lib/probe.txt 1
  commit hostile-git-config
  git -C "$REPO" config core.hooksPath hooks
  git -C "$REPO" config core.fsmonitor "$TMP/filter"
  git -C "$REPO" config filter.evil.clean "$TMP/filter"
  git -C "$REPO" config diff.evil.textconv "$TMP/textconv"
  printf '[core]\nfsmonitor = %s\n' "$TMP/filter" >"$TMP/included.gitconfig"
  git -C "$REPO" config include.path "$TMP/included.gitconfig"
  rm -f "$marker"
  cd "$REPO"
  run --separate-stderr python3 -I "$ROOT/lib/sweep_run.py" tree
  [ "$status" -eq 0 ]
  run python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import sweep_run
assert "core.fsmonitor=false" in sweep_run.GIT_CONFIG
assert any(v.startswith("core.hooksPath=") for v in sweep_run.GIT_CONFIG)
assert "core.attributesFile=/dev/null" in sweep_run.GIT_CONFIG' "$ROOT/lib"
  [ "$status" -eq 0 ]
}

@test "agents use an absolute executable and receive no interpreter injection variables" {
  PYTHONPATH=checkout PYTHONHOME=checkout NODE_OPTIONS=--require=checkout run_sweep --no-comment
  [ "$status" -eq 0 ]
  python3 -c 'import json,os,sys
for line in open(sys.argv[1]):
    call=json.loads(line)
    assert os.path.isabs(call["executable"]), call
    assert all(value is None for value in call["injected"].values()), call' "$TMP/agent.calls"
}

@test "a checkout write immediately before record publication is rejected" {
  cd "$REPO"
  run --separate-stderr python3 -c 'import sys
sys.path.insert(0, sys.argv[1])
import sweep_run
real = sweep_run.compare
calls = 0
def late(before, when):
    global calls
    calls += 1
    if when == "record publication":
        open("lib/alpha.py", "a").write("late write\\n")
    return real(before, when)
sweep_run.compare = late
sys.exit(sweep_run.main(["orch-sweep", *sys.argv[2:]]))' "$ROOT/lib" run --pr "$PR" --head "$HEAD_SHA" --base-ref main --record "$TMP/late.json" --no-comment
  [ "$status" -eq 2 ]
  [[ "$stderr" == *"record publication"* ]]
  [ ! -e "$TMP/late.json" ]
}

@test "a run after a fix push sweeps the whole PR diff again" {
  run_sweep --no-comment
  write lib/delta.py 4
  commit fix
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  run_sweep --no-comment
  [ "$status" -eq 0 ]
  [ "$(field 'r["kind"]')" = full ]
  [ "$(field 'r["base"]')" = "$BASE_SHA" ]
  [ "$(field 'sorted(f["path"] for s in r["plan"] for f in s["files"])')" = "['docs/guide.md', 'lib/alpha.py', 'lib/delta.py', 'tests/alpha.bats']" ]
}

@test "a stopped run stops its running agents" {
  cd "$REPO"
  FAKE_SLEEP=30 "$ROOT/orch-sweep" run --pr "$PR" --head "$HEAD_SHA" --base-ref main --no-comment >/dev/null 2>&1 &
  pid=$!
  for _ in $(seq 100); do
    [ "$(find "$TMP/live" -type f 2>/dev/null | wc -l)" -ge 2 ] && break
    sleep 0.1
  done
  agents=$(ls "$TMP/live")
  [ -n "$agents" ]
  kill -TERM "$pid"
  code=0
  wait "$pid" || code=$?
  [ "$code" -eq 143 ]
  for agent in $agents; do
    ! kill -0 "$agent" 2>/dev/null
  done
}

@test "a stopped run stops agents that ignore SIGTERM within the heavy-slot grace" {
  cd "$REPO"
  FAKE_IGNORE_TERM=1 FAKE_SLEEP=60 "$ROOT/orch-sweep" run --pr "$PR" --head "$HEAD_SHA" --base-ref main --no-comment >/dev/null 2>&1 &
  pid=$!
  for _ in $(seq 100); do
    [ "$(find "$TMP/live" -type f 2>/dev/null | wc -l)" -ge 2 ] && break
    sleep 0.1
  done
  agents=$(ls "$TMP/live")
  [ "$(echo "$agents" | wc -l)" -ge 2 ]
  start=$SECONDS
  kill -TERM "$pid"
  code=0
  wait "$pid" || code=$?
  elapsed=$((SECONDS - start))
  survived=$(for agent in $agents; do if kill -0 "$agent" 2>/dev/null; then echo "$agent"; fi; done)
  for agent in $survived; do
    kill -9 "$agent"
  done
  [ "$code" -eq 143 ]
  [ "$elapsed" -lt 10 ]
  [ -z "$survived" ]
}

@test "codex agents load no user or project config from the checkout" {
  run_sweep --no-comment
  [ "$status" -eq 0 ]
  python3 -c 'import json,sys
calls = [json.loads(line) for line in open(sys.argv[1])]
codex = [c["argv"] for c in calls if c["kind"] == "codex"]
assert codex, calls
for argv in codex:
    assert "--ignore-user-config" in argv, argv
    assert argv[argv.index("-c") + 1] == "project_doc_max_bytes=0", argv' "$TMP/agent.calls"
}

@test "the comment lists dropped findings in their own table" {
  FAKE_FINDINGS=1 FAKE_VERDICT=FALSE run_sweep
  [ "$status" -eq 0 ]
  python3 -c 'import json,sys
body = json.load(open(sys.argv[1]))["body"]
assert "**Dropped**" in body and "**Kept**" not in body, body
assert body.count("| FALSE |") == 4, body' "$TMP/posted.json"
}

@test "a record path inside the checkout stops before any agent starts" {
  sweep run --pr "$PR" --head "$HEAD_SHA" --base-ref main --no-comment --record "$REPO/lib/alpha.py"
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: --record "*"/repo/lib/alpha.py is inside the checkout"* ]]
  [ ! -e "$TMP/agent.calls" ]
  XDG_CACHE_HOME="$REPO/.cache" run_sweep --no-comment
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: the record cache "*"/repo/.cache/review-prs/"*" is inside the checkout"* ]]
}

@test "a text file under a directory named binary does not take the binary slice name" {
  write binary/main.py 5
  commit dir
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  plan
  [ "$(slice_of binary/main.py)" != binary ]
  run_sweep --no-comment
  [ "$status" -eq 0 ]
}

@test "a timed-out agent's whole process group is stopped" {
  FAKE_CHILD=pr-text FAKE_HANG=pr-text run_sweep --no-comment --agent-timeout 2
  [ "$status" -eq 4 ]
  [ -s "$TMP/child.pids" ]
  while read -r child; do
    ! kill -0 "$child" 2>/dev/null
  done <"$TMP/child.pids"
}

@test "earlier sweep outcomes reach the validators however many comments follow" {
  cd "$REPO"
  python3 -c 'import json,sys
record = {"tool": "orch-sweep", "items": [], "dropped": [{"id": "F9", "file": "lib/alpha.py", "line": 2, "category": "redundant", "class": "nit", "verdict": "RESPECT_DROP", "summary": "OLD-REJECTED-MARK"}]}
body = "<!-- orch-sweep v1 -->\n" + "x" * 5000 + "\n```json\n" + json.dumps(record) + "\n```\n"
later = [{"user": {"login": "someone"}, "body": f"comment {n}"} for n in range(60)]
json.dump([[{"user": {"login": "reviewer"}, "body": body}] + later], open(sys.argv[1], "w"))' "$TMP/comments.json"
  FAKE_FINDINGS=1 run_sweep --no-comment
  [ "$status" -eq 0 ]
  grep -q "OLD-REJECTED-MARK" "$TMP"/prompts/validator.*
}

@test "a sweep comment whose record lists are not lists counts as an ordinary comment" {
  cd "$REPO"
  python3 -c 'import json,sys
def body(n, record):
    return f"<!-- orch-sweep v1 -->\nMALFORMED-MARK-{n}\n```json\n{json.dumps(record)}\n```\n"
records = [{"tool": "orch-sweep", "items": None, "dropped": []}, {"tool": "orch-sweep", "items": [], "dropped": 7}]
json.dump([[{"user": {"login": "someone"}, "body": body(n, r)} for n, r in enumerate(records)]], open(sys.argv[1], "w"))' "$TMP/comments.json"
  FAKE_FINDINGS=1 run_sweep --no-comment
  [ "$status" -eq 0 ]
  grep -q "MALFORMED-MARK-0" "$TMP"/prompts/validator.*
  grep -q "MALFORMED-MARK-1" "$TMP"/prompts/validator.*
}

@test "an agent's background child is stopped when the agent succeeds" {
  FAKE_CHILD=pr-text run_sweep --no-comment
  [ "$status" -eq 0 ]
  [ -s "$TMP/child.pids" ]
  while read -r child; do
    if kill -0 "$child" 2>/dev/null; then
      kill -9 "$child"
      false
    fi
  done <"$TMP/child.pids"
}

@test "an agent whose background child keeps its output open finishes when the agent exits" {
  FAKE_PIPE_CHILD=pr-text run_sweep --no-comment --agent-timeout 20
  [ "$status" -eq 0 ]
  [ -s "$TMP/child.pids" ]
  while read -r child; do
    if kill -0 "$child" 2>/dev/null; then
      kill -9 "$child"
      false
    fi
  done <"$TMP/child.pids"
}

@test "an agent that answers after a few seconds succeeds" {
  FAKE_SLEEP=2.5 run_sweep --no-comment
  [ "$status" -eq 0 ]
}

@test "a gh that closes its output and keeps running times out" {
  printf '#!/usr/bin/env bash\nexec >&-\nsleep 1000\n' >"$TMP/bin/gh"
  run --separate-stderr python3 -c 'import signal, sys
sys.path.insert(0, sys.argv[1])
import sweep_run
signal.alarm(20)
sweep_run.GH_TIMEOUT = 2
try:
    sweep_run.gh("api", "x")
except sweep_run.Failure as error:
    print(error)' "$ROOT/lib"
  [ "$status" -eq 0 ]
  [ "$output" = "gh api x timed out after 2s" ]
}

@test "a gh whose child keeps its output open times out and the child is stopped" {
  printf '#!/usr/bin/env bash\nsleep 1000 &\necho $! >"%s/gh-child.pid"\nsleep 1000\n' "$TMP" >"$TMP/bin/gh"
  run --separate-stderr python3 -c 'import signal, sys
sys.path.insert(0, sys.argv[1])
import sweep_run
signal.alarm(20)
sweep_run.GH_TIMEOUT = 2
try:
    sweep_run.gh("api", "x")
except sweep_run.Failure as error:
    print(error)' "$ROOT/lib"
  [ "$status" -eq 0 ]
  [ "$output" = "gh api x timed out after 2s" ]
  child=$(cat "$TMP/gh-child.pid")
  if kill -0 "$child" 2>/dev/null; then
    kill -9 "$child"
    false
  fi
}

gh_setsid_child() {
  cat >"$TMP/bin/gh" <<EOF
#!/usr/bin/env bash
python3 -c 'import os, sys, time
os.setsid()
open(sys.argv[1], "w").write(str(os.getpid()))
time.sleep(1000)' "$TMP/gh-child.pid" 2>/dev/null 3>&- 4>&- 5>&- &
$1
EOF
  chmod +x "$TMP/bin/gh"
  run --separate-stderr python3 -c 'import signal, sys, time
sys.path.insert(0, sys.argv[1])
import sweep_run
signal.alarm(10)
sweep_run.GH_TIMEOUT = 2
start = time.monotonic()
try:
    sweep_run.gh("api", "x")
except sweep_run.Failure as error:
    print(error, round(time.monotonic() - start) <= 3)' "$ROOT/lib"
  child=$(cat "$TMP/gh-child.pid")
  kill -9 "$child" 2>/dev/null || true
  [ "$status" -eq 0 ]
  [ "$output" = "gh api x timed out after 2s True" ]
}

@test "a gh whose child leaves its process group and keeps its output open times out" {
  gh_setsid_child "sleep 1000"
}

@test "a gh that exits at once while a child outside its group keeps its output open times out" {
  gh_setsid_child "exit 0"
}

@test "a gh that exits leaves no child behind" {
  printf '#!/usr/bin/env bash\nsleep 1000 >/dev/null 2>&1 &\necho $! >"%s/gh-child.pid"\necho ok\n' "$TMP" >"$TMP/bin/gh"
  run --separate-stderr python3 -c 'import sys
sys.path.insert(0, sys.argv[1])
import sweep_run
print(sweep_run.gh("api", "x").strip())' "$ROOT/lib"
  [ "$status" -eq 0 ]
  [ "$output" = ok ]
  child=$(cat "$TMP/gh-child.pid")
  if kill -0 "$child" 2>/dev/null; then
    kill -9 "$child"
    false
  fi
}

@test "a record that fails its own check writes no file and keeps the older record" {
  run_sweep --no-comment
  [ "$status" -eq 0 ]
  cache="$XDG_CACHE_HOME/review-prs/sweeps/acme/app/7"
  cp "$cache/$HEAD_SHA.json" "$TMP/older.json"
  cd "$REPO"
  run --separate-stderr python3 -c 'import sys
sys.path.insert(0, sys.argv[1])
import sweep_record, sweep_run
sweep_record.FIELDS += ("required_by_this_test",)
sys.exit(sweep_run.main(["orch-sweep", *sys.argv[2:]]))' "$ROOT/lib" run --pr "$PR" --head "$HEAD_SHA" --base-ref main --record "$TMP/new.json" --no-comment
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"the record fails its own check"*"missing field required_by_this_test"* ]]
  cmp "$cache/$HEAD_SHA.json" "$TMP/older.json"
  [ ! -e "$TMP/new.json" ]
  [ "$(find "$cache" -mindepth 1 -maxdepth 1 -exec basename {} \;)" = "$HEAD_SHA.json" ]
  [ -z "$(find "$TMP" -mindepth 1 -maxdepth 1 -name '.*')" ]
}

@test "a relative record path is relative to the directory the sweep starts in" {
  name=$(basename "$TMP")
  cd "$REPO/lib"
  run --separate-stderr "$ROOT/orch-sweep" run --pr "$PR" --head "$HEAD_SHA" --base-ref main --no-comment --record "../../$name/record.json"
  [ "$status" -eq 0 ]
  [ -s "$TMP/$name/record.json" ]
  [ ! -e "$TMP/record.json" ]
}

@test "no agent starts once the sweep deadline has passed" {
  cd "$REPO"
  run python3 -c 'import sys, time
sys.path.insert(0, sys.argv[1])
import sweep_run
plain = sweep_run.command
def slow(*args):
    time.sleep(0.3)
    return plain(*args)
sweep_run.command = slow
runner = sweep_run.Runner("codex", "gpt-test", 1, 900, time.monotonic() + 0.1, 0, sys.argv[2])
try:
    runner.once({"name": "j", "schema": {}, "prompt": "ROLE: pr-text\n", "check": lambda a: a})
    print("STARTED")
except sweep_run.AgentError as error:
    print(error)' "$ROOT/lib" "$TMP"
  [ "$output" = "j: the sweep passed its time limit" ]
  [ ! -e "$TMP/agent.calls" ]
}

@test "a sweep comment with a malformed finding counts as an ordinary comment" {
  cd "$REPO"
  python3 -c 'import json,sys
def body(n, record):
    return f"<!-- orch-sweep v1 -->\nMALFORMED-MARK-{n}\n```json\n{json.dumps(record)}\n```\n"
good = {"id": "F1", "file": "lib/a.py", "line": 3, "category": "comment", "class": "fix", "verdict": "FALSE", "summary": "s"}
records = [{"tool": "orch-sweep", "items": [7], "dropped": []}, {"tool": "orch-sweep", "items": [None], "dropped": []},
           {"tool": "orch-sweep", "items": ["bad"], "dropped": []}, {"tool": "orch-sweep", "items": [], "dropped": [{"id": "F2", "summary": "FABRICATED"}]},
           {"tool": "orch-sweep", "items": [], "dropped": [dict(good, verdict="MADE_UP")]}, {"tool": "orch-sweep", "items": [], "dropped": [dict(good, line="3")]}]
json.dump([[{"user": {"login": "someone"}, "body": body(n, r)} for n, r in enumerate(records)]], open(sys.argv[1], "w"))' "$TMP/comments.json"
  FAKE_FINDINGS=1 run_sweep --no-comment
  [ "$status" -eq 0 ]
  for n in 0 1 2 3 4 5; do
    grep -q "MALFORMED-MARK-$n" "$TMP"/prompts/validator.*
  done
  run ! grep -q "Earlier sweep outcomes" "$TMP"/prompts/validator.*
}

@test "earlier sweep outcomes stay within one budget, each finding once, rejections first" {
  cd "$REPO"
  python3 -c 'import json,sys
def finding(n, verdict, cls="fix"):
    return {"id": f"F{n}", "file": "lib/a.py", "line": n, "category": "c", "class": cls, "verdict": verdict, "summary": f"S{n:04d}-" + "y" * 400}
comments = []
for round in range(10):
    kept = [finding(round * 100 + n, "CONFIRMED_NEW") for n in range(50)]
    rejected = [finding(5000 + round * 100 + n, "FALSE") for n in range(50)]
    record = {"tool": "orch-sweep", "items": kept, "dropped": rejected + [finding(9999, "RESPECT_DROP", "nit")]}
    comments.append({"user": {"login": "reviewer"}, "body": "<!-- orch-sweep v1 -->\n```json\n" + json.dumps(record) + "\n```\n"})
json.dump([comments], open(sys.argv[1], "w"))' "$TMP/comments.json"
  FAKE_FINDINGS=1 run_sweep --no-comment
  [ "$status" -eq 0 ]
  prompt=$(printf '%s\n' "$TMP"/prompts/validator.[0-9]*[0-9] | head -1)
  [ "$(wc -c <"$prompt")" -lt 120000 ]
  [ "$(grep -c 'S9999-' "$prompt")" -eq 1 ]
  grep -q 'S5000-' "$prompt"
  grep -q 'earlier sweep outcomes left out' "$prompt"
}

@test "the earlier outcomes stay within the history limit, the left-out notice included" {
  cd "$REPO"
  run python3 -c 'import sys
sys.path.insert(0, sys.argv[1])
import sweep_run
f = {"id": "F1", "file": "a.py", "line": 1, "category": "c", "class": "fix", "verdict": "FALSE", "summary": "s"}
one = len(sweep_run.history([("r", [f])])) + 1
full = sweep_run.history([("r", [dict(f, line=n) for n in range(sweep_run.HISTORY_LIMIT // one + 10)])])
assert len(full) <= sweep_run.HISTORY_LIMIT, len(full)
assert "left out" in full.splitlines()[-1], full.splitlines()[-1]
print("ok")' "$ROOT/lib"
  [ "$output" = ok ]
}

@test "one oversized earlier finding neither pushes out the findings after it nor passes its field limits" {
  cd "$REPO"
  run python3 -c 'import sys
sys.path.insert(0, sys.argv[1])
import sweep_run
f = {"id": "F1", "file": "a.py", "line": 1, "category": "c", "class": "fix", "verdict": "FALSE", "summary": "s"}
h = sweep_run.history([("attacker", [dict(f, file="a" * 61000, category="c" * 61000)]), ("reviewer", [dict(f, id="F2", summary="IMPORTANT-REJECTION")])])
assert "IMPORTANT-REJECTION" in h, h[:300]
assert any(line.startswith("- attacker: aaa") for line in h.splitlines()), h[:300]
assert max(map(len, h.splitlines())) < 1000, max(map(len, h.splitlines()))
print("ok")' "$ROOT/lib"
  [ "$output" = ok ]
}

@test "an earlier finding that does not fit is skipped and a later one that fits is kept" {
  cd "$REPO"
  run python3 -c 'import sys
sys.path.insert(0, sys.argv[1])
import sweep_run
f = {"id": "F1", "file": "a.py", "line": 1, "category": "c", "class": "fix", "verdict": "FALSE", "summary": "s"}
one = len(sweep_run.history([("r", [dict(f, line=10000)])])) + 1
fill = [dict(f, line=10000 + n) for n in range((sweep_run.HISTORY_LIMIT - 300) // one)]
h = sweep_run.history([("r", fill + [dict(f, line=99999, summary="L" * 299), dict(f, line=99998, summary="SHORT-ONE")])])
lines = h.splitlines()
assert len(h) <= sweep_run.HISTORY_LIMIT, len(h)
assert "SHORT-ONE" in h, lines[-2:]
assert "LLLL" not in h
assert lines[-1].startswith("- 1 earlier sweep outcomes left out"), lines[-1]
print("ok")' "$ROOT/lib"
  [ "$output" = ok ]
}

@test "a GitHub read larger than the download limit stops with one error" {
  cd "$REPO"
  python3 -c 'import json,sys
big = [{"user": {"login": "someone"}, "body": "x" * 60000} for _ in range(600)]
json.dump([big], open(sys.argv[1], "w"))' "$TMP/comments.json"
  run_sweep --no-comment
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: gh api --paginate --slurp repos/acme/app/issues/7/comments printed more than 33554432 bytes"* ]]
  [ ! -e "$TMP/agent.calls" ]
}

@test "review summaries reach the validators" {
  cd "$REPO"
  echo '[[{"user": {"login": "copilot"}, "state": "COMMENTED", "body": "REVIEW-SUMMARY-REJECT: we rejected this"}, {"user": {"login": "x"}, "state": "APPROVED", "body": ""}]]' >"$TMP/reviews.json"
  FAKE_FINDINGS=1 run_sweep --no-comment
  [ "$status" -eq 0 ]
  grep -q "Review by copilot (COMMENTED):" "$TMP"/prompts/validator.*
  grep -q "REVIEW-SUMMARY-REJECT" "$TMP"/prompts/validator.*
}

@test "a changed path that is not UTF-8 stops with one error" {
  blob=$(echo x | git -C "$REPO" hash-object -w --stdin)
  tree=$( (git -C "$REPO" ls-tree -z HEAD; printf '100644 blob %s\t\377.txt\0' "$blob") | git -C "$REPO" mktree -z)
  HEAD_SHA=$(git -C "$REPO" commit-tree "$tree" -p HEAD -m bad)
  plan_run() { sweep plan --base "$BASE_SHA" --head "$HEAD_SHA"; }
  plan_run
  [ "$status" -eq 1 ]
  [[ "$stderr" == "ERROR: git diff printed a path that is not UTF-8"* ]]
  [[ "$stderr" != *Traceback* ]]
}

@test "a large sweep still posts one comment under the size limit that carries the whole record" {
  FAKE_FINDINGS=25 FAKE_PAD=300 run_sweep
  [ "$status" -eq 0 ]
  python3 -c 'import json,re,sys
body = json.load(open(sys.argv[1]))["body"]
assert len(body) <= 65536, len(body)
block = re.search(r"```json\n(.*?)\n```", body, re.S).group(1)
assert json.loads(block) == json.load(open(sys.argv[2]))' "$TMP/posted.json" "$TMP/record.json"
}

@test "a record too large for one comment fails before posting and keeps the record" {
  FAKE_FINDINGS=40 FAKE_PAD=1500 run_sweep
  [ "$status" -eq 5 ]
  [[ "$stderr" == "ERROR: the sweep comment needs"*"characters; GitHub allows 65536"* ]]
  [ ! -e "$TMP/posted.json" ]
  [ -s "$TMP/record.json" ]
}
