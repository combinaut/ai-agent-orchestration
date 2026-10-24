---
name: finalize-code-comments
description: Sweep added or modified comments for redundant explanations and iteration artifacts. Use when a workflow, worker brief, or user requests the comment sweep at the end of a piece of work before opening or updating a PR. Do not start a sweep for unrelated edits.
user-invocable: true
argument-hint: "[<base-ref>] [<path>…]"
---

# Finalize Code Comments

AI-assisted development produces comments as a side effect of iterating: the model explains what it just changed, justifies a decision to the reviewer, or narrates the difference from a previous version of the code. Those comments made sense *during* the session, but they are noise the moment the work is final — the next reader sees only the final code and has no "previous version" to compare against.

This skill sweeps every comment **added or modified on the current branch** and removes or rewrites two kinds of clutter: iteration artifacts, and comments that simply shouldn't exist because the code already says it.

## Input

`$ARGUMENTS` (optional): a base ref to diff against (default: merge-base with the default branch), followed by file paths to limit the sweep to. If both are given, the ref comes first: `/finalize-code-comments <ref> <path> [<path>…]`. A token that `git rev-parse --verify --quiet <token>^{commit}` resolves is the base ref; anything else (or anything that exists on disk) is a path. At most one ref is accepted.

## The two tests

For each comment, ask — in this order:

> **1. Does this comment need to exist at all?** The readers are programmers; they can read code. If the line is trivial, or the meaning is clear from the names, types, and surrounding code, the comment is redundant — delete it. The default for any comment is *no comment*; it has to earn its place by saying something the code cannot.

> **2. Would it make sense to someone reading the final code with zero knowledge of how it got here?** If understanding the comment requires knowing about a previous version, a review discussion, a plan step, or the fact that an AI/session produced the code, it fails — delete or rewrite.

## What to remove

1. **Change narration** — describes the delta, not the code. Tell-tale words: "now", "no longer", "instead of", "rather than", "previously", "changed to", "moved from", "was".
   - ❌ `// so they are generated at build time rather than committed` (the reader of the final code doesn't know they were ever committed)
   - ❌ `# now also handles the nil case`
2. **Reviewer-directed justification** — arguing the change is correct or safe. That belongs in the PR body, not the code.
   - ❌ `// this is safe because the caller already validates above`
   - ❌ `# matches the existing pattern in FooController`
3. **Process references** — plan steps, review feedback, session mechanics.
   - ❌ `// Step 3: wire up the store`
   - ❌ `# per review feedback, extracted to a helper`
4. **Restating the code** — the comment says what the next line visibly does, or labels a step a reader would infer by just following the code.
   - ❌ `// loop over each client and collect the results`
   - ❌ `# find the user` above `user = User.find(id)`
5. **Comments on trivial code** — guards, early returns, simple assignments, standard idioms. Even an accurate comment is noise when the code is self-evident; section-header comments narrating an obvious sequence of steps go too.
6. **Verbose reasoning dumps** — multi-line essays explaining a decision at a length the decision doesn't warrant. Compress to the one durable constraint, or delete.

## What to keep (never touch these)

- **Constraints the code can't express**: gotchas, ordering requirements, external-system quirks, "why" comments that stand alone in the final state.
- **Structured doc comments** that tools consume: JSDoc, YARD/RDoc, docstrings, `@deprecated` markers.
- **Directive comments**: linter disables, `@ts-check`, `@ts-expect-error`, magic comments (`# frozen_string_literal`), shebangs, and any justification attached to them.
- **TODOs that reference a ticket.** A bare TODO with no ticket is a judgment call — flag it in the summary rather than deleting silently.
- **Comments that predate the branch.** Only comments added or modified in the branch diff are in scope. Never "improve" pre-existing comments — that's scope creep.
- **The repo's own comment rules.** If the repo documents a comment style (a style guide, `CLAUDE.md`, `AGENTS.md`), rewrite surviving comments to it.

## Rewrite vs. delete

- If a comment is **pure artifact**, delete it (and any orphaned blank line).
- If it **mixes** durable information with narration, rewrite it to keep only the durable part:
  - Before: `// Regenerates the registry on disk before the bundler reads the module graph, so it is build-time generated rather than committed.`
  - After: `// Regenerates the registry before the bundler reads the module graph.`
- When in doubt between a short rewrite and deletion, prefer deletion — the bar is "strictly required", not "possibly useful".
- Never change code, only comments. If a removal reveals the code is genuinely unclear without it, that's a naming/structure smell — flag it in the summary instead of keeping the narration.

## Process

1. Determine the diff scope with a single command that covers committed, staged, **and** unstaged changes: `git diff $(git merge-base HEAD <base>) -- <paths>` (base defaults to the default branch; ref and paths from `$ARGUMENTS`). The scope is the complete PR diff, not the last commit. Also run `git ls-files --others --exclude-standard -- <paths>` to pick up untracked files, which `git diff` misses.
2. For each changed file, read the diff and identify every added or modified **syntactic comment** — text the language's parser treats as a comment (including HTML `<!-- -->`, `//`, `#`, and block comments whose body changed). A comment marker inside a non-comment context is out of scope: `#` in a string literal, URL, or scalar value; `//` in a URL or regex; heredoc content. When in doubt, read the surrounding file context — never edit anything that isn't unambiguously a comment.
3. Apply the tests above to each and present the findings as a table; wait for approval before changing anything:

   ```
   FINDING                                  FILE            CATEGORY     RECOMMENDATION
   ---------------------------------------- --------------- ------------ --------------
   "now also handles nil" narrates a delta  parser.rb:41    narration    Delete
   Multi-line rationale for the retry loop  client.py:88    dump         Rewrite to one line
   Bare TODO                                worker.js:12    judgment     Flag
   ```

4. Apply the approved edits only. Run the relevant linters on the touched files — comment removal can leave blank-line or line-length violations.
5. Report counts of deleted / rewritten / deliberately kept comments, with a short list of the rewrites and any flagged judgment calls (bare TODOs, code left unclear). Do **not** commit or push unless asked.
