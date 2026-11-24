# CI selection

`Validate` always runs ShellCheck, Ruff and the CI dependency-map tests.
`scripts/ci/select-tests.py` selects the remaining checks from changed paths:

| Paths | Selected checks |
| --- | --- |
| `lib/sweep_record.py`, `orch-sweep`, `orch-sweep-record` | sweep-record and sweep-run Bats |
| `lib/sweep_run.py`, `docs/sweep-script.md` | sweep-run Bats |
| `chrome/pr-tabs/**` | extension lint, types, tests and build |
| `chrome/pr-tabs/host/**` | also chrome-host Bats and native-host Python tests |
| Extension manifest | also chrome-host Bats (checks the extension ID) |
| `tests/*.bats` | that Bats suite |
| Guard source/test | guard Python tests; source also selects installer Bats |
| Watcher `test_*.py` | watcher Python suites and the corresponding integration check where mapped |
| Hog/balance/workers/audit shell entrypoints | watcher Python suites, corresponding integration check, Linux units, installer Bats |
| Watcher fixtures | watcher Python suites, installer Bats and hog/balance/workers/audit integration checks |
| `*/service/linux/**` | Linux units, installer Bats, watcher Python suites |
| Markdown and LICENSE | installer, review-prs, todo-pane, sweep-run Bats and watcher Python suites that read documentation |
| Anything else, including broadly shared libraries, installer and CI infrastructure | full suite |

macOS runs for shell scripts, hooks, native-host code, macOS service templates,
platform adapters, test isolation helpers, shell Bats suites (except the Python
sweep suites) and CI infrastructure. Unknown paths
also enable macOS unless they are Python or documentation. Pure Python changes,
extension UI changes and prose run on Linux. Suite selection applies on both
platforms; Linux-only integrations stay on Linux.

PRs compare their head with the merge base of the PR's base and head. Pushes to
main compare the event's previous and new main SHAs. Renames include both the old
and new path; deletions count too. Unavailable comparison history selects the
full suite on both platforms. Manual dispatch always selects everything.

The required job names remain `Validate (macos-latest)` and
`Validate (ubuntu-latest)`. A macOS job skipped by its job condition is a successful
required check in GitHub. A failed selection job triggers full validation on both
platforms. There are no workflow-level path filters that could leave required
checks pending. Avoiding unnecessary macOS runners saves their higher minute cost.
