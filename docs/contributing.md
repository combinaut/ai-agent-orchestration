# Tests and contributing

## Tests

```bash
tests/run.sh
```

It runs the Bats suites, the guard tests, the watcher tests and the Chrome extension tests in a sandbox that blocks `launchctl`, `systemctl` and `herdr`, so nothing touches your services. You need `bats`, `python3` and `node`; Linux also needs `bubblewrap`. Individual test targets are not supported. Details: [`tests/README.md`](../tests/README.md).

## Contributing

Changes go through a pull request against `main`; CI runs ShellCheck and `tests/run.sh`. The Herdr Radar fork is pinned to one commit in `env/herdr/radar.pin`; move the pin in a PR. Validate runs on Ubuntu for every PR and on macOS for pushes to `main` and PRs that change more than `.md`, `LICENSE` and `docs/` files (skipped counts as passed for branch protection); each job times out after 25 minutes.

