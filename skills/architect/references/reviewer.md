# Reviewer brief template

Read this file before you brief an independent reviewer (acceptance gate item 7). Paste the brief below into the reviewer pane, with the PR, the head SHA and the report path filled in.

## Brief

```
You review <PR url> at head <sha>. You did not write it. Do not edit, commit or push.
Reuse the author's or your own acceptable sweep record; the review itself is the independent assessment.
For every claim the PR makes, and every risk you find, open the code at that head and report one row.
Write <report path> as this table and nothing else before it:

| claim | file:line opened | verdict | evidence | rung | fix the author can apply |

When sweep checks are enabled, first run: orch-sweep-record gate --pr <PR url> --head <sha>.
Reuse valid author/reviewer evidence; an open fix needs assessment, not another sweep. Only if evidence is missing or stale, and this is not an unchanged-head re-request, run orch-sweep as one heavy job with the matching origin/upstream base ref. Do not run the sweep on every reviewer pass. review.finalize_commands = [] opts out.
List undisputed kept fixes in the review. Dispute a false kept fix with a visible body line: Sweep dispute <sweep-comment-id>/<finding-id>: <specific code reason>. Nits and dropped findings never block.
Post any review only through: orch-sweep post-review --pr <PR url> --head <sha> --event APPROVE|REQUEST_CHANGES|COMMENT --input - (JSON {"body": ..., "comments": [...]} on stdin). It refuses APPROVE unless the sweep gate passes for that exact head.
Goal: APPROVE only with no undisputed kept fix finding, no other verified blocking finding, and every review claim confirmed. Mark missing evidence unresolved instead of inventing certainty.
```

- **claim**: one sentence, from the PR body, a commit message, a comment or your own reading.
- **file:line opened**: a real `file:line` you opened at the head. For a PR-text claim the code cannot verify, name the claim in the PR title or body and the missing evidence instead; do not invent a code line.
- **verdict**: `confirmed`, `incorrect` or `unresolved`. An `unresolved` row names the missing evidence (what to run, read or ask) in the evidence column.
- **evidence**: what you saw or ran, with the real output. Grade what the code does, not how the author got there.
- **rung**: the highest rung you reached for that fact.
- **fix the author can apply**: an exact change, or `none` for a confirmed row.

## Rungs

1. Someone said so. Worthless alone.
2. You pointed at the line: a real `file:line`.
3. You walked the bad case and it does not reach.
4. You ran it: a script or test that calls the real code and fails loud.
5. You reproduced it in the running system.

Below rung 4, write `unproven`, never `safe`.

## Tool block

Start the reviewer in the reviewed worktree with `--disallowedTools "Edit(./**)" NotebookEdit` (see [dispatch.md](dispatch.md)). The path is relative to the pane's directory, and an `Edit(...)` rule covers the Write tool too, so the reviewer cannot change the tree it reviews through the file tools, yet still writes its report to the path outside it. Bash can still write files, so this is a tripwire, not a sandbox: it stops an accidental edit, not a determined one. Check `git status` in the reviewed worktree after the review.
