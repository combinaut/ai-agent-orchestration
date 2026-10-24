# Tests and contributing

## 🧪 Tests

```bash
tests/run.sh
```

The script runs the Bats suites, the guard tests, the watcher tests and the Chrome extension tests. They run in a sandbox that blocks `launchctl`, `systemctl` and `herdr`, so nothing touches your services.

You need `bats`, `python3` and `node`. Linux also needs `bubblewrap`. Individual test targets are not supported. Details: [`tests/README.md`](../tests/README.md).

## 🐍 Python style

Ruff checks and formats the Python files. The rules and the pinned version (0.17.1) are in `ruff.toml`: ruff's classic defaults (`E4`, `E7`, `E9`, `F`) plus `I` for import order. Markdown is left alone.

- **Check:** `scripts/ci/ruff.sh`. It uses `uvx` when you have it, else a virtualenv in `~/.cache/ruff-0.17.1`.
- **Fix:** `uvx ruff@0.17.1 check --fix . && uvx ruff@0.17.1 format .` (use the version named in `scripts/ci/ruff.sh`).

## 🤝 Contributing

Changes go through a pull request against `main`.

- **CI** runs ShellCheck (`scripts/ci/shellcheck.sh`), Ruff (`scripts/ci/ruff.sh`: `ruff check` and `ruff format --check`), the entrypoint checks with a fake Herdr and a fake GitHub, and `tests/run.sh`. On Linux it also runs the process and unit checks (the systemctl tripwire, hog and balance against real processes, the watcher-unit check). The steps are in `.github/actions/validate/action.yml`. Workflow scripts live in `scripts/ci/`.
- **Platforms:** Validate runs on Ubuntu for every PR. It runs on macOS for pushes to `main` and for PRs that change more than `.md`, `LICENSE` and `docs/` files; a skipped macOS job counts as passed for branch protection. The macOS job times out after 40 minutes, the Ubuntu job after 25.
- **Radar pin:** the Herdr Radar fork is pinned to one commit in `env/herdr/radar.pin`. Move the pin in a PR.
- **PR titles:** a PR title must be a Conventional Commit (`feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `perf`, `build`, `revert`; optional scope; `!` for breaking). A one-commit PR also needs a conventional commit message.
- **Releases:** release-please keeps a release PR open on `main`; merging it tags the version, publishes the GitHub release and updates `CHANGELOG.md` and `version.txt`. The workflow dispatches Validate on the release PR branch, because PRs opened with `GITHUB_TOKEN` trigger no checks; closing and reopening the PR also works.
