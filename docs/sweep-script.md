# The sweep script

`orch-sweep` (`lib/sweep_run.py`) runs [`/finalize-sweep`](../skills/finalize-sweep/SKILL.md) as a script: it cuts the slices, starts every agent as its own headless process, checks the tree, runs the validators, writes the sweep record and posts the findings comment.

```bash
orch-sweep run --pr <url> --head <sha> --base-ref <remote>/<base branch> [--kind full|text] \
  [--runtime codex|claude] [--model <model>] [--concurrency 4] \
  [--agent-timeout 900] [--timeout 3600] [--record <path>] [--no-comment]
orch-sweep plan --base <rev> [--head <rev>]   # the slice plan as JSON
orch-sweep tree                               # the five tree values and the untracked map's hash, as JSON
orch-sweep allow --pr <url> --head <sha> --what slices-over-25 --reason <text>   # the user's OK, typed on a terminal
orch-sweep post-review --pr <url> --head <sha> --event APPROVE|REQUEST_CHANGES|COMMENT --input -
orch-sweep-record gate --pr <url> --head <sha>   # may this head be approved
```

Run it inside a clean checkout whose `HEAD` is `--head`. It stops with one `ERROR:` line and exit 1 when `HEAD` differs, the tree is dirty, or `--record` or the record cache resolves inside the checkout.

## Agents

Each slice starts one `finalize-code-comments` agent and one `finalize-change-footprint` agent; one more agent checks the PR title and body. Then one validator per 40 findings (at least 1 with findings, at most 12) gives each finding a verdict and a class, `fix` or `nit`. The prompts carry the sub-skill text and the report-only rules.

| Runtime | Command |
|---|---|
| `codex` (default) | `codex exec -m <model> -C <checkout> -s read-only --ephemeral --ignore-user-config -c project_doc_max_bytes=0 --json --output-schema <file> -o <file> -` |
| `claude` | `claude -p --model <model> --output-format json --json-schema <schema> --permission-mode dontAsk --setting-sources user --strict-mcp-config --no-session-persistence --tools Read,Grep,Glob --add-dir <diff folder> --allowedTools Read Grep Glob --disallowedTools Edit Write NotebookEdit` |

The script writes the PR diff and each slice's diff (`git diff --no-ext-diff --no-textconv`) to a temporary folder outside the checkout and names the file in each prompt; the folder is removed when the run ends. Claude agents have no shell. Codex agents have one inside the read-only sandbox: a command can run a program from the checkout (`rg --pre`), but it cannot write files or reach the network.

The model comes from the settings: `models.codex_worker`, else `models.codex_smart`, for Codex; `sonnet` when `providers.claude` lists it or lists no tier, else the first listed tier, for Claude. `--model` overrides it. The record names it.

## Slices

1. A test file (under `test/`, `tests/`, `spec/` or `__tests__/`, or named `test_*`, `*_spec.*`, `*_test.*`, `*.test.*`, `*.spec.*` or `*.bats`) joins the changed source file with the same stem (test markers stripped, `-` read as `_`), the one sharing the most directory names first. A deleted file joins the added or changed file with its stem. A renamed file keeps its old path for the agents.
2. The feature of a source file and the files joined to it is its directory, cut to two levels (`lib`, `skills/review-prs`).
3. Within a feature, these units pack into slices of at most 400 changed lines, largest first. A unit over 400 lines splits into its files; a file over 400 lines gets its own slice. Features never share a slice. Binary files go together in one slice named `binary`.
4. The plan is checked before any agent starts: every changed path in exactly one slice, only single-file slices over 400 lines, binary files only in the binary slice. Over 25 slices the run stops with exit 3.

## Tree state

Before the agents start, after the sweep agents, and after the validators, the script takes five values: `HEAD`, a hash of `git status --porcelain=v1 -z --untracked-files=all`, a hash of `git diff --binary HEAD`, a hash of `git diff --binary --cached HEAD`, and, per untracked file, `<octal mode>:<sha256 of the content>` (a symlink hashes its target path). Any difference stops the run with exit 2 and writes no record.

## Every head

Every run sweeps the whole PR diff from the merge base, also after a fix push. The validators get the findings and verdicts of the earlier sweep comments, to judge `RESPECT_DROP`: each distinct finding and verdict once, its author, file, category and summary cut to 40, 200, 60 and 300 characters, rejections first, at most 60000 characters in all with the notice of how many were left out; a finding that does not fit is skipped and later ones that fit are kept. The 60000 bound is for this history only. A sweep comment with a malformed finding counts as an ordinary comment. They also get the latest 50 other comments, review-thread comments and review summaries, each cut to 4000 characters. An earlier rejection is `RESPECT_DROP` only when its reason still holds; when the reason no longer holds, the validator uses `DISPUTED_DROP`. Nothing in the script itself stops a rejected finding from being raised again.

## Record and comment

The record holds the fields `/finalize-sweep` names, plus `tool`, `version`, `pr`, `runtime`, `model`, `kind` (`full` or `text`), `tree`, `plan`, `pr_text`, `sweep_agent_ids` (the Codex thread or Claude session id of each sweep agent), `items` (the kept findings, each `fix` or `nit`), `dropped` (the other findings, with their verdicts), `open_fix` (the count of kept `fix` findings) and `deviation`. `orch-sweep-record check` checks those fields when `tool` is `orch-sweep` (`version` 1, `deviation` null or the user's allow (see Deviations), `pr` a pull request URL, every finding typed, with an id used once, no validator id equal to a sweep agent id, and `open_fix` an integer); `--exact` also requires the record's head to be the given head.

The script writes the record to `~/.cache/review-prs/sweeps/<owner>/<repo>/<number>/<head>.json` (and `--record`), and posts one comment through `lib/github_write.py`: the marker `<!-- orch-sweep v1 -->`, the full head SHA, a table of kept findings and one of dropped findings, the record in a fenced `json` block, and the runtime's attribution trailer. `--no-comment` skips the post. The comment stays within GitHub's 65536 characters: the record goes in compact when the indented one does not fit, then the dropped table and then the kept table are left out (the comment says so, and the record still holds every finding). When the record alone does not fit, the run exits 5 without posting and keeps the record file.

## Capacity

| Bound | Value |
|---|---|
| Agents per sweep | `2 x slices + 1` sweep agents, then `ceil(findings / 40)` validators, at most 12; with the 25-slice cap, at most 63 agent jobs, and with retries at most 88 agent processes |
| Agents at once | `--concurrency`, default 4, a bounded pool |
| Per agent | `--agent-timeout`, default 900 s, cut to what is left of the sweep; when the agent exits or times out, its process group gets SIGTERM, then SIGKILL after at most 5 s for whatever is left |
| Per sweep | `--timeout`, default 3600 s; no agent starts after it |
| Retries | a failed first attempt retries once when the shared budget of `slices` retries (at least 1, so a text sweep has 1) has one left; the first job that fails for good (its retry failed, or no retry was left) stops new jobs, and the run exits 4 |
| GitHub reads | 45 s and 32 MiB each, with the deadline on the read itself; `gh` runs in its own process group, which is killed at the time limit and after the read; a child that leaves the group is not waited for; an oversized read stops the run with exit 1; a timed-out read exits 6 |
| Stopping | SIGINT, SIGTERM or SIGHUP sends SIGTERM to every running agent's process group at once, then SIGKILL to what is left after 5 s, inside heavy-slot's 10 s grace; the run exits 128 + the signal |

Run it as one heavy job: `heavy-slot take <name>-sweep --note "orch sweep" -- orch-sweep run ...`.

## Text sweeps

`--kind text` sweeps the PR title and body at a head that already has a full sweep: it starts only the PR-text agent and the validators, and writes a record with `kind: text`, `slices: 0` and `plan: []`, kept at `<head>.text.json` in the cache. Without a full record of that head in the cache it stops with exit 1.

## The gate

`orch-sweep-record gate --pr <url> --head <sha>` runs in the PR's clone and passes (exit 0) when the PR head is `<sha>` and the newest `orch-sweep` comment by the `gh` login for that exact head holds a full record that:

- passes the script-record schema and Git checks: the base resolves and is an ancestor of the head;
- has the base the PR's current base branch gives: the merge base of the head and `<remote>/<base>`, the remote being the clone's `origin` or `upstream` that is the PR repo (the local `<base>` when neither is), so a PR retargeted after the sweep fails until a new sweep;
- equals the copy in `~/.cache/review-prs/sweeps/<owner>/<repo>/<number>/<head>.json`;
- has no open kept fix finding.

When the newest sweep comment for that head is a text sweep, its record must pass the same checks against `<head>.text.json` with no open fix finding, and the full record may hold open fix findings only in the `pr-text` category; an older text sweep is not checked. The newest record's `pr_text_sha256`, the hash of the title and body that sweep read, must equal the live title and body; after an edit, `orch-sweep run --kind text` sweeps the new text. A finding exits 1 with one `ERROR:` line; a gate that cannot read GitHub, git or the local record copy exits 2 with `ERROR: cannot check the gate: ...`, and `post-review` exits 2 the same way and posts nothing.

`orch-sweep-record check` additionally validates session-bound validator transcripts when the record has findings; the gate does not run that transcript phase.

The local copy shows that the comment's record is the one a run wrote; anyone who can write the cache and post as the `gh` login can fake both.

`orch-sweep post-review` reads `{"body": "...", "comments": [...]}` on stdin and posts the review pinned to `--head` through `lib/github_write.py`. For APPROVE it runs the gate first and posts nothing when the gate fails.

## Deviations

The only deviation is a sweep over 25 slices. The user allows it for one head with `orch-sweep allow`, which reads a typed `yes` from `/dev/tty` and stops without a terminal; it writes `<head>.allow.json` next to the record copy. A run over 25 slices continues only when that file names `slices-over-25` and the head; the record's `deviation` then holds the reason, the time and the file's sha256, and the comment says "Deviation approved by the user". The gate accepts that record only while the file's hash still matches. A terminal can be faked (`script`), so this proves a terminal answered, not who typed.

## Exit codes

`0` record written (and the comment posted, unless `--no-comment`); `1` usage, checkout or GitHub error; `2` the tree changed; `3` over 25 slices; `4` an agent failed after its retry; `5` the record does not fit in one comment; `6` a GitHub read failed or timed out (a later run can succeed); `128 + n` stopped by signal `n`.
