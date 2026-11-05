# The sweep script

`orch-sweep` (`lib/sweep_run.py`) runs [`/finalize-sweep`](../skills/finalize-sweep/SKILL.md) as a script: it cuts the slices, starts every agent as its own headless process, checks the tree, runs the validators, writes the sweep record and posts the findings comment.

```bash
orch-sweep run --pr <url> --head <sha> --base-ref <remote>/<base branch> \
  [--runtime codex|claude] [--model <model>] [--concurrency 4] \
  [--agent-timeout 900] [--timeout 3600] [--record <path>] [--no-comment]
orch-sweep plan --base <rev> [--head <rev>]   # the slice plan as JSON
orch-sweep tree                               # the five tree values as JSON
```

Run it inside a clean checkout whose `HEAD` is `--head`. It stops with one `ERROR:` line and exit 1 when `HEAD` differs, the tree is dirty, or `--record` or the record cache resolves inside the checkout.

## Agents

Each slice starts one `finalize-code-comments` agent and one `finalize-change-footprint` agent; one more agent checks the PR title and body. Then one validator per 40 findings (at least 1 with findings, at most 12) gives each finding a verdict and a class, `fix` or `nit`. The prompts carry the sub-skill text and the report-only rules.

| Runtime | Command |
|---|---|
| `codex` (default) | `codex exec -m <model> -C <checkout> -s read-only --ephemeral --ignore-user-config -c project_doc_max_bytes=0 --json --output-schema <file> -o <file> -` |
| `claude` | `claude -p --model <model> --output-format json --json-schema <schema> --permission-mode dontAsk --strict-mcp-config --no-session-persistence --allowedTools Read Grep Glob "Bash(git diff:*)" "Bash(git show:*)" "Bash(git ls-files:*)" "Bash(git merge-base:*)" "Bash(rg:*)" --disallowedTools Edit Write NotebookEdit` |

The model comes from the settings: `models.codex_worker`, else `models.codex_smart`, for Codex; `sonnet` when `providers.claude` lists it, else the first listed tier, for Claude. `--model` overrides it. The record names it.

## Slices

1. A test file (under `test/`, `tests/`, `spec/` or `__tests__/`, or named `test_*`, `*_spec.*`, `*_test.*`, `*.test.*`, `*.spec.*` or `*.bats`) joins the changed source file with the same stem (test markers stripped, `-` read as `_`), the one sharing the most directory names first. A deleted file joins the added or changed file with its stem. A renamed file keeps its old path for the agents.
2. The feature of a source file and the files joined to it is its directory, cut to two levels (`lib`, `skills/review-prs`).
3. Within a feature, these units pack into slices of at most 400 changed lines, largest first. A unit over 400 lines splits into its files; a file over 400 lines gets its own slice. Features never share a slice. Binary files go together in one slice named `binary`.
4. The plan is checked before any agent starts: every changed path in exactly one slice, only single-file slices over 400 lines, binary files only in the binary slice. Over 25 slices the run stops with exit 3.

## Tree state

Before the agents start, after the sweep agents, and after the validators, the script takes five values: `HEAD`, a hash of `git status --porcelain=v1 -z --untracked-files=all`, a hash of `git diff --binary HEAD`, a hash of `git diff --binary --cached HEAD`, and, per untracked file, `<octal mode>:<sha256 of the content>` (a symlink hashes its target path). Any difference stops the run with exit 2 and writes no record.

## Every head

Every run sweeps the whole PR diff from the merge base, also after a fix push. The validators get every finding and verdict of each earlier sweep comment, plus the latest 50 other comments and review-thread comments (each cut to 4000 characters), to judge `RESPECT_DROP`. Nothing in the script itself stops a rejected finding from being raised again.

## Record and comment

The record holds the fields `/finalize-sweep` names, plus `tool`, `version`, `pr`, `runtime`, `model`, `kind` (`full`), `tree`, `plan`, `pr_text`, `sweep_agent_ids` (the Codex thread or Claude session id of each sweep agent), `items` (the kept findings, each `fix` or `nit`), `dropped` (the other findings, with their verdicts), `open_fix` (the count of kept `fix` findings) and `deviation`. `orch-sweep-record check` checks those fields when `tool` is `orch-sweep`; `--exact` also requires the record's head to be the given head.

The script writes the record to `~/.cache/review-prs/sweeps/<owner>/<repo>/<number>/<head>.json` (and `--record`), and posts one comment through `lib/github_write.py`: the marker `<!-- orch-sweep v1 -->`, the full head SHA, a table of kept findings and one of dropped findings, the record in a fenced `json` block, and the runtime's attribution trailer. `--no-comment` skips the post. The comment stays within GitHub's 65536 characters: the record goes in compact when the indented one does not fit, then the dropped table and then the kept table are left out (the comment says so, and the record still holds every finding). When the record alone does not fit, the run exits 5 without posting and keeps the record file.

## Capacity

| Bound | Value |
|---|---|
| Agents per sweep | `2 x slices + 1` sweep agents, then `ceil(findings / 40)` validators, at most 12; with the 25-slice cap, at most 63 |
| Agents at once | `--concurrency`, default 4, a bounded pool |
| Per agent | `--agent-timeout`, default 900 s; on expiry the agent's process group gets SIGTERM, then SIGKILL for whatever is left |
| Per sweep | `--timeout`, default 3600 s; no agent starts after it |
| Retries | each agent retries at most once; the whole run allows at most `slices` retries; the first failure stops new agents and the run exits 4 |
| GitHub reads | 45 s each |
| Stopping | SIGINT, SIGTERM or SIGHUP stops every running agent's process group; the run exits 128 + the signal |

Run it as one heavy job: `heavy-slot take <name>-sweep --note "orch sweep" -- orch-sweep run ...`.

## Exit codes

`0` record written (and comment posted); `1` usage, checkout or GitHub error; `2` the tree changed; `3` over 25 slices; `4` an agent failed after its retry; `5` the record does not fit in one comment; `128 + n` stopped by signal `n`.
