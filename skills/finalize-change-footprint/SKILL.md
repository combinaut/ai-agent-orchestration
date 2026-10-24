---
name: finalize-change-footprint
description: Review the branch diff for duplicated tests, leftovers, dead code, and unnecessary churn. Use when a workflow, worker brief, or user requests a footprint sweep at the end of a piece of work or review cycle before a PR. Present verified removal candidates for approval; do not start a sweep for unrelated edits.
user-invocable: true
argument-hint: "[<base-ref>] [<path>…]"
---

# Finalize Change Footprint

A PR drifts while it is being reviewed. Each round adds a fix on top of the last one: a test for the new edge case beside an older test that already covered it, a guard for an input nothing sends, an import left behind by a revert, a description that still talks about the previous layout. Every change made sense in its round. Together they make the diff larger than the change needs, and a larger diff is harder to review and riskier to merge.

This skill reads the complete PR diff as a whole, as if it were written in one go, and finds what the final change does not need.

Comments are out of scope here; `/finalize-code-comments` covers them. Run that one as well.

## Input

`$ARGUMENTS` (optional): a base ref to diff against (default: merge-base with the default branch), followed by file paths to limit the sweep to. If both are given, the ref comes first: `/finalize-change-footprint <ref> <path> [<path>…]`. A token that `git rev-parse --verify --quiet <token>^{commit}` resolves is the base ref; anything else (or anything that exists on disk) is a path. At most one ref is accepted.

## The test

For each added or changed piece, ask:

> **Would this be in the PR if the final change had been written in one sitting?** If it exists only because of the order the work happened in — a fix superseded by a later fix, a test written before a broader one, a branch that was reverted — it fails.

## What to look for

1. **Duplicated or overlapping tests.** Two tests asserting the same contract, often one written early and a broader one added in a later round. Keep the one that asserts more and name it as the cover for the one you remove. Read whole test files, not only the diff: an added test can duplicate one that was already there.
   - ❌ `it "shows the empty state when there are no results"` beside a later `it "shows the empty state and hides the pagination when there are no results"`: the later one asserts everything the first does.
2. **Leftovers from reverted or reworked approaches.** Imports, helpers, variables, styles, fixtures, or test setup that only the abandoned version used. Read the branch's commit list (`git log --oneline <base>..HEAD`) for reverts, fix-on-fix commits, and review-response commits, and check what each left behind.
   - ❌ an import or fixture that only a reverted commit used.
3. **Machinery for cases nothing exercises.** Observers, listeners, guards, options, or fallbacks added during review for an input no current caller sends. Name the callers you checked. When none produces the case, recommend removing it and adding it when a caller needs it. This applies to internal code only: an input on a public surface (an API parameter, a CLI flag, a config key) or one the linked issue asks for stays, even with no caller in the repo.
   - ❌ an observer keeping a derived value in sync with content that every caller renders once and replaces wholesale to change.
4. **Code the branch made dead, or found dead in the lines it touches.** Rules no element can match, classes no template uses, variables nothing reads, files nothing imports. Verify with a search (the class, the import path, the variable) before calling anything dead.
   - ❌ a style rule for an element's empty state when the template removes the element whenever it would be empty.
5. **Churn that changes nothing.** Reformatting, reordering, or renames in lines the change does not otherwise need. Revert them to shrink the diff. A rename that makes code the change touches clearer is not churn.
   - ❌ re-wrapping a literal in a function the change otherwise leaves alone.
6. **Repetition the branch introduced.** The same literal, fallback, or expression added several times where one variable or helper would do. Recommend it only when it reads better, not just to reduce a count.
   - ❌ the same fallback value written into several rules where one variable would carry it.
7. **Misplaced additions.** Code inserted between a comment and the line it describes, a test in the wrong group, a doc entry in the wrong section.
8. **Stale descriptions.** Docs, fixtures, and examples that describe a state a later commit reverted or changed. Report a stale PR description as well; editing it is outward-facing, so it waits for approval like everything else.

## What to keep

- **Anything that changes behaviour.** This skill shrinks the diff; it does not redesign. A real fix stays even when it arrived late.
- **Tests that pin distinct contracts.** Overlapping setup is not duplication; two tests are duplicates only when they share setup and inputs and one's assertions are a subset of the other's.
- **Cheap, standard defensive code.** Say why it stays.
- **Code outside the diff.** Pre-existing problems go in the report as follow-up candidates, not into the PR.

## Process

1. Determine the scope: `git diff $(git merge-base HEAD <base>) -- <paths>` (committed, staged, and unstaged), `git ls-files --others --exclude-standard -- <paths>`, and `git log --oneline <base>..HEAD`. The scope is the complete PR diff, not the last commit.
2. Read every changed file's diff, every untracked file from step 1 in full (they have no diff), and every touched test file in full.
3. For each candidate, verify before proposing: search for usages, name the test that covers a duplicate, name the callers checked for an unexercised case.
4. Present the findings as a table and wait for approval before changing anything:

   ```
   FINDING                                     FILE                       CATEGORY     RECOMMENDATION
   ------------------------------------------- -------------------------- ------------ --------------
   Repeats the broader empty-state example     orders_spec.rb             duplicate    Remove
   Import only the reverted listener used      widget.js                  leftover     Remove
   Comment split from its example              widget.spec.js             misplaced    Move
   Fallback on the text column                 widget.css                 defensive    Keep
   ```

5. Apply only the approved changes, then run the linters and tests for the touched surfaces. When removing a duplicate test, break the behaviour it pinned, confirm the covering test fails, then restore it.
6. Report what was removed, moved, or kept, and why. Do **not** commit or push unless asked.
