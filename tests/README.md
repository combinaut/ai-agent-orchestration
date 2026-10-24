# Test isolation

Tests run under an OS sandbox during collection and execution. Bats suite setup
puts logging-only service-manager stubs first on PATH for test execution, including
files that do not load `test_helper`. Collection relies on the OS sandbox.
macOS denies host service-manager and
Herdr execution with `sandbox-exec`; Linux uses Bubblewrap tripwires over those
paths and an empty fixture runtime directory as a second layer.

Python's module-level fixture installs logging fakes for `launchctl`, `systemctl`,
and `herdr` before test discovery. A discovery check requires every test case to
inherit `IsolatedTestCase`; existing command mocks still provide scenario-specific
responses.

Run the full suite with `tests/run.sh`. Bats runs one test at a time, or `ORCH_TEST_JOBS` tests at once (needs GNU parallel); with `CI=true` it runs one test per CPU. Individual test targets are not supported.
Bats suite setup and the Python fixture require the runner's sandbox marker.
The Python runner installs fakes before discovery imports any test modules.

The Chrome PR tabs tests run with Vitest in `chrome/pr-tabs` (`pnpm test`); see [`docs/pr-tabs.md`](../docs/pr-tabs.md#-develop).
