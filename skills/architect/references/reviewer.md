# Reviewer brief template

Read this file before you brief an independent reviewer (acceptance gate item 7). Paste the brief below into the reviewer pane, with the PR, the head SHA and the report path filled in.

## Brief

```
You review <PR url> at head <sha>. You did not write it. Do not edit, commit or push.
For every claim the PR makes, and every risk you find, open the code at that head and report one row.
Write <report path> as this table and nothing else before it:

| claim | file:line opened | verdict | evidence | rung | fix the author can apply |

Then run the finalize sweep as a script in this worktree, as one heavy job: heavy-slot take review-<name>-sweep --note "orch sweep" -- orch-sweep run --pr <PR url> --head <sha> --base-ref <remote>/<base branch> --runtime claude
<remote> is the clone's `origin` or `upstream` that is the PR repo (docs/sweep-script.md).
It posts ONE PR comment with the findings, each fix or nit, the full head SHA and the sweep record. Do not run /finalize-sweep by hand and do not post another sweep comment. Add its kept findings below the claims table.
Use the comment for the verdict. An open kept fix finding blocks APPROVE; nits and dropped findings never block.
Post any review only through: orch-sweep post-review --pr <PR url> --head <sha> --event APPROVE|REQUEST_CHANGES|COMMENT --input - (JSON {"body": ..., "comments": [...]} on stdin). It refuses APPROVE unless the sweep gate passes for that exact head.
Every new head gets the sweep again over the whole PR diff.
Goal: when you pass the PR, a human reviewer finds nothing left to clean up.
```

- **claim**: one sentence, from the PR body, a commit message, a comment or your own reading.
- **file:line opened**: a real `file:line` you opened at the head. No line, no row.
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
