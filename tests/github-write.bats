#!/usr/bin/env bats

setup() {
  CLONE="$BATS_TEST_TMPDIR/clone"
  mkdir -p "$CLONE/lib"
  cp "$BATS_TEST_DIRNAME/../github-write" "$CLONE/github-write"
  printf '%s\n' 'import sys' 'print(*sys.argv[1:], sep="\n")' 'sys.exit(23)' >"$CLONE/lib/github_write.py"
}

@test "github-write forwards each argument and exits with the wrapper status" {
  run "$CLONE/github-write" --timeout 120 pr create --title "two words"
  [ "$status" -eq 23 ]
  [ "$output" = $'--timeout\n120\npr\ncreate\n--title\ntwo words' ]
}

@test "github-write finds its clone through relative and absolute symlinks" {
  mkdir -p "$BATS_TEST_TMPDIR/bin" "$BATS_TEST_TMPDIR/hop"
  ln -s "$CLONE/github-write" "$BATS_TEST_TMPDIR/hop/github-write"
  ln -s ../hop/github-write "$BATS_TEST_TMPDIR/bin/github-write"
  cd "$BATS_TEST_TMPDIR"
  run env PATH="$BATS_TEST_TMPDIR/bin:$PATH" github-write marker
  [ "$status" -eq 23 ]
  [ "$output" = marker ]
}
