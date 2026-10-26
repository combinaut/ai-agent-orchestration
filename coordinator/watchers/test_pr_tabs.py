"""Ownership discovery and one-shot native host cache regressions."""

import importlib.util
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_support import IsolatedTestCase

import discovery
import worker_signals

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load("pr_tabs_host_test", ROOT / "chrome/pr-tabs/host/pr_tabs_host.py")


class PrTabsTest(IsolatedTestCase):
    def test_adapter_scopes_branch_to_repo_and_resolves_owner_label(self):
        work = {
            "workspaces": [
                {"id": "a", "label": "Finder"},
                {"id": "b", "label": "Agent::Tooling"},
                {"id": "w1", "owner": "a", "checkout": "/web"},
                {"id": "w2", "owner": "b", "checkout": "/docs"},
                {"id": "orphan", "owner": "gone", "checkout": "/orphan"},
            ]
        }

        def command(*args):
            return (
                "refs/heads/feature"
                if args[3] == "symbolic-ref"
                else "git@github.com:Acme/"
                + ("webapp.git" if args[2] == "/web" else "docs.git")
            )

        self.assertEqual(
            discovery.branch_owners(command, work),
            {"acme/webapp:feature": "Finder", "acme/docs:feature": "Agent::Tooling"},
        )

    def test_same_named_tag_does_not_change_branch_ownership(self):
        with tempfile.TemporaryDirectory() as directory:

            def command(*args):
                return subprocess.run(
                    args, check=True, capture_output=True, text=True
                ).stdout.strip()

            command("git", "init", "--initial-branch=feature", directory)
            command(
                "git",
                "-C",
                directory,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "fixture",
            )
            command(
                "git",
                "-C",
                directory,
                "remote",
                "add",
                "origin",
                "https://github.com/acme/webapp.git",
            )
            command("git", "-C", directory, "tag", "feature")
            work = {
                "workspaces": [
                    {"id": "a", "label": "Finder"},
                    {"id": "w", "owner": "a", "checkout": directory},
                ]
            }
            self.assertEqual(
                discovery.branch_owners(command, work),
                {"acme/webapp:feature": "Finder"},
            )

    def test_conflicting_owners_and_detached_checkouts_are_omitted(self):
        work = {
            "workspaces": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
            + [
                {"id": str(i), "owner": owner, "checkout": path}
                for i, (owner, path) in enumerate(
                    [("a", "/one"), ("b", "/two"), ("a", "/detached")]
                )
            ]
        }

        def command(*args):
            if args[2] == "/detached":
                raise subprocess.CalledProcessError(1, args)
            return (
                "refs/heads/feature"
                if args[3] == "symbolic-ref"
                else "https://github.com/acme/webapp.git"
            )

        self.assertEqual(discovery.branch_owners(command, work), {})

    def test_host_cache_expires_and_missing_or_broken_adapter_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('[install]\nenv = "herdr"\n')
            environ = {
                "ORCH_CONFIG": str(config),
                "PR_TABS_LOG": str(Path(directory) / "host.log"),
                "ORCH_ENV": "herdr",
            }
            with (
                patch.dict(os.environ, environ),
                patch.object(
                    host.subprocess, "run", return_value=SimpleNamespace(stdout="{}")
                ) as run,
            ):
                self.assertIsNone(host.ownership())
                self.assertTrue(run.called)
            good = {"acme/webapp:feature": "Finder"}
            with (
                patch.dict(os.environ, environ),
                patch.object(host, "discover_owners", return_value=good) as discover,
                patch.object(host.time, "time", return_value=100),
            ):
                self.assertEqual(host.ownership(), good)
                discover.return_value = {"acme/webapp:feature": "Tooling"}
                self.assertEqual(host.ownership(), good)
                self.assertEqual(discover.call_count, 1)
                with patch.object(host.time, "time", return_value=131):
                    self.assertEqual(
                        host.ownership(), {"acme/webapp:feature": "Tooling"}
                    )
                with patch.dict(os.environ, {"ORCH_ENV": "none"}):
                    self.assertEqual(host.ownership(), {})

    def test_failed_discovery_is_cached_for_the_normal_ttl(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('[install]\nenv = "herdr"\n')
            environ = {
                "ORCH_CONFIG": str(config),
                "PR_TABS_LOG": str(Path(directory) / "host.log"),
                "ORCH_ENV": "herdr",
            }
            with (
                patch.dict(os.environ, environ),
                patch.object(
                    host.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("herdr", 5),
                ) as run,
                patch.object(host.time, "time", return_value=100),
            ):
                self.assertIsNone(host.ownership())
                self.assertIsNone(host.ownership())
                self.assertEqual(run.call_count, 1)
                with patch.object(host.time, "time", return_value=131):
                    self.assertIsNone(host.ownership())
                    self.assertEqual(run.call_count, 2)

    def test_one_closed_workspace_does_not_discard_other_owners(self):
        work = {
            "workspaces": [
                {"id": "a", "label": "Finder"},
                {"id": "null", "owner": None},
                {"id": "closing", "owner": "a", "checkout": "/closed"},
                {"id": "good", "owner": "a", "checkout": "/web"},
            ]
        }

        def command(*args):
            if args[2] == "/closed":
                raise subprocess.CalledProcessError(
                    128,
                    args,
                    stderr="fatal: cannot change to '/closed': No such file or directory",
                )
            return (
                "refs/heads/feature"
                if args[3] == "symbolic-ref"
                else "git@github.com:acme/webapp.git"
            )

        self.assertEqual(
            discovery.branch_owners(command, work), {"acme/webapp:feature": "Finder"}
        )

    def test_branch_read_failure_preserves_last_good_ownership_protocol(self):
        work = {
            "workspaces": [
                {"id": "a", "label": "Finder"},
                {"id": "w", "owner": "a", "checkout": "/web"},
            ]
        }
        good = {"acme/webapp:feature": "Finder"}
        for detail in (
            "fatal: detected dubious ownership in repository at /web",
            "fatal: cannot change to '/web': Permission denied",
        ):
            with (
                self.subTest(detail=detail),
                tempfile.TemporaryDirectory() as directory,
            ):
                config = Path(directory) / "config.toml"
                config.write_text('[install]\nenv="herdr"\n')
                environ = {
                    "ORCH_CONFIG": str(config),
                    "ORCH_ENV": "herdr",
                    "PR_TABS_LOG": str(Path(directory) / "host.log"),
                }
                failing = False

                def run(args, **kwargs):
                    if args[3] == "symbolic-ref":
                        if failing:
                            raise subprocess.CalledProcessError(
                                128, args, stderr=detail
                            )
                        return SimpleNamespace(stdout="refs/heads/feature")
                    return SimpleNamespace(stdout="https://github.com/acme/webapp.git")

                with (
                    patch.dict(os.environ, environ),
                    patch.object(discovery, "open_work", return_value=work),
                    patch.object(host.subprocess, "run", side_effect=run),
                    patch.object(host.time, "time", return_value=100),
                ):
                    self.assertEqual(host.ownership(), good)
                    failing = True
                    with patch.object(host.time, "time", return_value=131):
                        self.assertIsNone(
                            host.ownership(),
                            "failed scan must retain Chrome last-good ownership",
                        )
                        self.assertIsNone(
                            host.ownership(), "failure is cached for the normal TTL"
                        )
                    self.assertIsNone(
                        json.loads((Path(directory) / "owners.json").read_text())[
                            "owners"
                        ]
                    )

    def test_cache_reuses_one_file_across_config_edits_and_removes_legacy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "config.toml"
            legacy = base / ("owners-" + "0" * 20 + ".json")
            legacy.write_text("{}")
            unrelated = base / "preferences.json"
            unrelated.write_text("keep")
            environ = {
                "ORCH_CONFIG": str(config),
                "PR_TABS_LOG": str(base / "host.log"),
                "ORCH_ENV": "herdr",
            }
            reply = SimpleNamespace(
                stdout=json.dumps({"result": {"workspaces": [], "panes": []}})
            )
            with (
                patch.dict(os.environ, environ),
                patch.object(host.subprocess, "run", return_value=reply) as run,
            ):
                for org in ("acme", "other"):
                    config.write_text('[pr_tabs]\norg = "' + org + '"\n')
                    self.assertEqual(host.ownership(), {})
                self.assertEqual(
                    run.call_count,
                    4,
                    "each changed config takes a fresh shared workspace/pane snapshot",
                )
            self.assertEqual(list(base.glob("owners*.json")), [base / "owners.json"])
            self.assertEqual(unrelated.read_text(), "keep")

    def test_github_origin_host_case_ports_and_exact_host_matching(self):
        work = {
            "workspaces": [
                {"id": "a", "label": "Finder"},
                {"id": "w", "owner": "a", "checkout": "/web"},
            ]
        }
        accepted = (
            "git@github.com:acme/webapp.git",
            "https://GitHub.com/acme/webapp.git",
            "ssh://git@github.com:22/acme/webapp.git",
            "ssh://git@GitHub.COM:2222/acme/webapp.git",
            "https://github.com:443/acme/webapp.git",
            "https://GitHub.com:8443/acme/webapp.git/",
        )
        rejected = (
            "https://github.com.evil.example/acme/webapp.git",
            "ssh://git@notgithub.com:22/acme/webapp.git",
            "https://github.com@evil.example/acme/webapp.git",
            "/srv/github.com/acme/webapp.git",
            "git@github-work:acme/webapp.git",
        )
        for remote in accepted + rejected:

            def command(*args):
                return "refs/heads/feature" if args[3] == "symbolic-ref" else remote

            with self.subTest(remote=remote):
                expected = (
                    {"acme/webapp:feature": "Finder"} if remote in accepted else {}
                )
                self.assertEqual(discovery.branch_owners(command, work), expected)

    def test_host_uses_shared_open_work_discovery(self):
        work = {
            "workspaces": [
                {"id": "a", "label": "Finder", "owner": None, "checkout": None},
                {"id": "w", "label": "Worker", "owner": "a", "checkout": "/web"},
            ],
            "panes": [],
        }

        def run(args, **kwargs):
            self.assertLessEqual(kwargs["timeout"], 5)
            self.assertEqual(kwargs["env"]["HERDR_SOCKET_PATH"], "/fixture/socket")
            return SimpleNamespace(
                stdout="refs/heads/feature"
                if args[3] == "symbolic-ref"
                else "ssh://git@GitHub.com:22/acme/webapp.git"
            )

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('[install]\nenv="herdr"\n')
            environ = {
                "ORCH_CONFIG": str(config),
                "ORCH_ENV": "herdr",
                "ORCH_HERDR_SOCKET": "/fixture/socket",
                "PR_TABS_LOG": str(Path(directory) / "host.log"),
            }
            with (
                patch.dict(os.environ, environ),
                patch.object(discovery, "open_work", return_value=work) as read,
                patch.object(host.subprocess, "run", side_effect=run),
            ):
                self.assertEqual(host.ownership(), {"acme/webapp:feature": "Finder"})
                read.assert_called_once()

    def test_real_shared_snapshot_uses_bulk_panes_and_normalizes_checkout(self):
        calls = []
        workspaces = [
            {"workspace_id": "a", "label": "Finder"},
            {"workspace_id": "null", "tokens": None},
            {"workspace_id": "w", "tokens": {"owner": "a"}},
        ]

        def run(args, **kwargs):
            calls.append(args)
            self.assertLessEqual(kwargs["timeout"], 5)
            self.assertEqual(kwargs["env"]["HERDR_SOCKET_PATH"], "/fixture/socket")
            if args[1:] == ("workspace", "list"):
                value = json.dumps({"result": {"workspaces": workspaces}})
            elif args[1:] == ("pane", "list"):
                value = json.dumps(
                    {"result": {"panes": [{"workspace_id": "w", "cwd": "/docs/sub"}]}}
                )
            elif args[3] == "rev-parse":
                value = "/docs/.git" if "--git-common-dir" in args else "/docs"
            elif args[3] == "symbolic-ref":
                value = "refs/heads/feature"
            else:
                value = "https://GitHub.com:443/acme/docs.git"
            return SimpleNamespace(stdout=value)

        before = {
            key: os.environ.get(key)
            for key in ("ORCH_ENV", "ORCH_HERDR_BIN", "HERDR_SOCKET_PATH")
        }
        with patch.object(host.subprocess, "run", side_effect=run):
            self.assertEqual(
                host.discover_owners(
                    ROOT, {"ORCH_ENV": "herdr", "ORCH_HERDR_SOCKET": "/fixture/socket"}
                ),
                {"acme/docs:feature": "Finder"},
            )
        self.assertEqual(
            [args[1:] for args in calls if args[0] != "git"],
            [("workspace", "list"), ("pane", "list")],
        )
        self.assertEqual(before, {key: os.environ.get(key) for key in before})


FAKE_HERDR = """#!/bin/sh
printf '%s\\n' "$*" >>"$FAKE_HERDR_DIR/calls"
printf '%s\\n' "${HERDR_SOCKET_PATH:-}" >>"$FAKE_HERDR_DIR/sockets"
[ -z "$FAKE_HERDR_SLEEP" ] || sleep "$FAKE_HERDR_SLEEP"
if [ -n "$FAKE_HERDR_FAIL" ]; then echo "$FAKE_HERDR_FAIL" >&2; exit 1; fi
case "$1 $2" in
  "workspace list") cat "$FAKE_HERDR_DIR/workspaces.json" ;;
  "pane list") cat "$FAKE_HERDR_DIR/panes.json" ;;
  *) echo '{"result":{}}' ;;
esac
"""


class PaneJumpTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        herdr = self.base / "fake-herdr"
        herdr.write_text(FAKE_HERDR)
        herdr.chmod(0o755)
        config = self.base / "config.toml"
        config.write_text('[install]\nenv = "herdr"\n')
        environ = {
            "ORCH_CONFIG": str(config),
            "ORCH_ENV": "herdr",
            "HERDR_BIN_PATH": str(herdr),
            "FAKE_HERDR_DIR": str(self.base),
            "PR_TABS_LOG": str(self.base / "host.log"),
        }
        patcher = patch.dict(os.environ, environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.base / "calls").write_text("")
        self.web = self.checkout("web", "feature", "git@github.com:Acme/WebApp.git")
        self.other = self.checkout("other", "main", "https://github.com/acme/webapp")

    def checkout(self, name, branch, origin):
        path = self.base / name
        subprocess.run(
            ["git", "init", "-q", f"--initial-branch={branch}", str(path)], check=True
        )
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", origin], check=True
        )
        return str(path)

    def herdr(self, workspaces, panes):
        rows = [
            {
                "workspace_id": id,
                "tokens": {"owner": owner} if owner else {},
                "worktree": {"checkout_path": path, "repo_root": path},
            }
            for id, owner, path in workspaces
        ]
        (self.base / "workspaces.json").write_text(
            json.dumps({"result": {"workspaces": rows}})
        )
        pane_rows = [
            dict({"workspace_id": workspace, "pane_id": pane}, **extra)
            for workspace, pane, extra in panes
        ]
        (self.base / "panes.json").write_text(
            json.dumps({"result": {"panes": pane_rows}})
        )

    def calls(self):
        return (self.base / "calls").read_text().splitlines()

    def focus_calls(self):
        return [call for call in self.calls() if "focus" in call]

    def test_lookup_reports_which_branches_have_a_pane(self):
        self.herdr(
            [("wA", None, self.web), ("wB", None, self.other)],
            [("wA", "wA:p1", {"agent": "claude"}), ("wB", "wB:p1", {})],
        )
        reply = host.panes(
            {
                "prs": [
                    {"repo": "acme/webapp", "branch": "feature"},
                    {"repo": "ACME/WebApp", "branch": "main"},
                    {"repo": "acme/webapp", "branch": "gone"},
                    {"repo": "acme/docs", "branch": "feature"},
                ]
            }
        )
        self.assertEqual(
            reply["panes"],
            {
                "acme/webapp:feature": {"state": None},
                "acme/webapp:main": {"state": None},
                "acme/webapp:gone": None,
                "acme/docs:feature": None,
            },
        )
        self.assertEqual(self.calls(), ["workspace list", "pane list"])

    def test_focus_routes_worker_to_owner_architect(self):
        self.herdr(
            [
                ("wOther", None, self.other),
                ("wArch", None, self.web),
                ("wWork", "wArch", self.web),
            ],
            [
                (
                    "wOther",
                    "wOther:p1",
                    {"agent": "claude", "tokens": {"role": "architect"}},
                ),
                ("wArch", "wArch:p0", {"agent": "claude"}),
                (
                    "wArch",
                    "wArch:p1",
                    {
                        "agent": "claude",
                        "agent_status": "idle",
                        "tokens": {"role": "architect"},
                    },
                ),
                ("wWork", "wWork:p1", {}),
                ("wWork", "wWork:p2", {"agent": "claude", "agent_status": "working"}),
            ],
        )
        lookup = host.panes({"prs": [{"repo": "acme/webapp", "branch": "feature"}]})
        self.assertEqual(lookup["panes"], {"acme/webapp:feature": {"state": "working"}})
        (self.base / "calls").write_text("")

        reply = host.focus_pane({"repo": "acme/webapp", "branch": "feature"})
        self.assertEqual(reply, {"ok": True, "pane": "wArch:p1"})
        self.assertEqual(
            self.calls(), ["workspace list", "pane list", "agent focus wArch:p1"]
        )

    def test_owner_without_architect_agent_focuses_owner_workspace(self):
        for extra in [{"agent": "claude"}, {"tokens": {"role": "architect"}}]:
            with self.subTest(extra=extra):
                (self.base / "calls").write_text("")
                self.herdr(
                    [("wArch", None, self.other), ("wWork", "wArch", self.web)],
                    [
                        ("wArch", "wArch:p1", extra),
                        ("wWork", "wWork:p1", {"agent": "claude"}),
                    ],
                )
                reply = host.focus_pane({"repo": "acme/webapp", "branch": "feature"})
                self.assertEqual(reply, {"ok": True, "pane": None})
                self.assertEqual(
                    self.calls(),
                    ["workspace list", "pane list", "workspace focus wArch"],
                )

    def test_missing_owner_retains_worker_agent_focus(self):
        for owner in [None, "closed-architect"]:
            with self.subTest(owner=owner):
                (self.base / "calls").write_text("")
                self.herdr(
                    [("wWork", owner, self.web)],
                    [("wWork", "wWork:p1", {"agent": "claude"})],
                )
                reply = host.focus_pane({"repo": "acme/webapp", "branch": "feature"})
                self.assertEqual(reply["pane"], "wWork:p1")
                self.assertEqual(self.focus_calls(), ["agent focus wWork:p1"])

    def test_self_owned_workspace_is_not_a_worker_and_order_breaks_ties(self):
        self.herdr(
            [("wSelf", "wSelf", self.web), ("wLater", None, self.web)],
            [("wSelf", "wSelf:p1", {}), ("wLater", "wLater:p1", {})],
        )
        reply = host.focus_pane({"repo": "acme/webapp", "branch": "feature"})
        self.assertEqual(reply["pane"], "wSelf:p1")
        self.assertEqual(self.focus_calls(), ["workspace focus wSelf"])

    def test_no_matching_pane_focuses_nothing(self):
        self.herdr([("wB", None, self.other)], [("wB", "wB:p1", {"agent": "x"})])
        with self.assertRaisesRegex(host.RequestError, "no Herdr pane"):
            host.focus_pane({"repo": "acme/webapp", "branch": "feature"})
        self.assertEqual(self.focus_calls(), [])

    def test_herdr_missing_not_running_or_slow_is_a_clear_error(self):
        self.herdr([], [])
        request = {"prs": [{"repo": "acme/webapp", "branch": "feature"}]}
        with patch.dict(os.environ, {"HERDR_BIN_PATH": str(self.base / "absent")}):
            with self.assertRaisesRegex(host.RequestError, "^absent not found"):
                host.panes(request)
        with patch.dict(os.environ, {"FAKE_HERDR_FAIL": "server not running"}):
            with self.assertRaisesRegex(
                host.RequestError, "fake-herdr failed: server not running"
            ):
                host.panes(request)
        with (
            patch.dict(os.environ, {"FAKE_HERDR_SLEEP": "3"}),
            patch.object(host, "HERDR_TIMEOUT_SECONDS", 0.3),
        ):
            started = time.monotonic()
            with self.assertRaisesRegex(host.RequestError, "fake-herdr timed out"):
                host.panes(request)
            self.assertLess(time.monotonic() - started, 2)
        with patch.dict(os.environ, {"ORCH_ENV": "none"}):
            with self.assertRaisesRegex(host.RequestError, "install.env"):
                host.panes(request)

    def test_output_over_the_limit_is_refused(self):
        self.herdr([("wA", None, self.web)], [("wA", "wA:p1", {})])
        with patch.object(host, "MAX_COMMAND_BYTES", 16):
            with self.assertRaisesRegex(host.RequestError, "output over 16 bytes"):
                host.panes({"prs": [{"repo": "acme/webapp", "branch": "feature"}]})

    def test_invalid_input_is_refused_before_herdr_runs(self):
        good = {"repo": "acme/webapp", "branch": "feature"}
        for bad in (
            {"prs": []},
            {"prs": [good] * (host.MAX_PANE_LOOKUPS + 1)},
            {"prs": "acme/webapp:feature"},
            {"prs": [{"repo": "acme", "branch": "feature"}]},
            {"prs": [{"repo": "acme/web app", "branch": "feature"}]},
            {"prs": [{"repo": "acme/webapp", "branch": "-x"}]},
            {"prs": [{"repo": "acme/webapp", "branch": "a..b"}]},
            {"prs": [{"repo": "acme/webapp", "branch": "a b"}]},
            {"prs": [{"repo": "acme/webapp", "branch": "a~1"}]},
            {"prs": [{"repo": "acme/webapp", "branch": "x.lock"}]},
            {"prs": [{"repo": "acme/webapp", "branch": "feature\n"}]},
            {"prs": [{"repo": "acme/webapp", "branch": 7}]},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(host.RequestError):
                    host.panes(bad)
        with self.assertRaises(host.RequestError):
            host.focus_pane({"repo": "acme/webapp", "branch": "feat/../x"})
        self.assertEqual(self.calls(), [])

    def test_terminal_app_comes_forward_on_macos_only_when_set(self):
        self.herdr([("wA", None, self.web)], [("wA", "wA:p1", {"agent": "c"})])
        request = {"repo": "acme/webapp", "branch": "feature"}
        opener = self.base / "open"
        opener.write_text(
            '#!/bin/sh\nprintf "open %s\\n" "$*" >>"$FAKE_HERDR_DIR/calls"\n'
        )
        opener.chmod(0o755)
        with (
            patch.object(host, "OPEN", str(opener)),
            patch.object(host.sys, "platform", "darwin"),
        ):
            host.focus_pane(request)
            with patch.dict(os.environ, {"ORCH_PR_TABS_TERMINAL_APP": "Ghostty"}):
                host.focus_pane(request)
            with patch.dict(os.environ, {"ORCH_PR_TABS_TERMINAL_APP": "-n x"}):
                with self.assertRaisesRegex(host.RequestError, "application name"):
                    host.focus_pane(request)
        with (
            patch.object(host, "OPEN", str(opener)),
            patch.object(host.sys, "platform", "linux"),
            patch.dict(os.environ, {"ORCH_PR_TABS_TERMINAL_APP": "Ghostty"}),
        ):
            host.focus_pane(request)
        self.assertEqual(
            [call for call in self.calls() if call.startswith("open")],
            ["open -a Ghostty"],
        )

    def test_lookup_reports_the_agent_state_from_herdr_and_open_questions(self):
        signals = self.base / "signals"
        signals.mkdir()
        cases = {
            "working": ({"agent": "claude", "agent_status": "working"}, "working"),
            "idle": ({"agent": "claude", "agent_status": "idle"}, "idle"),
            "done": ({"agent": "claude", "agent_status": "done"}, "done"),
            "blocked": ({"agent": "claude", "agent_status": "blocked"}, "waiting"),
            "unknown": ({"agent": "claude", "agent_status": "unknown"}, None),
            "no agent": ({"agent_status": "working"}, None),
        }
        for label, (pane, expected) in cases.items():
            with self.subTest(label=label):
                self.herdr([("wA", None, self.web)], [("wA", "wA:p1", pane)])
                with patch.object(worker_signals, "directory", return_value=signals):
                    reply = host.panes(
                        {"prs": [{"repo": "acme/webapp", "branch": "feature"}]}
                    )
                self.assertEqual(
                    reply["panes"], {"acme/webapp:feature": {"state": expected}}
                )

    def test_an_open_orch_signal_question_means_waiting(self):
        signals = self.base / "signals"
        signals.mkdir()
        question = {
            "kind": "question",
            "workspace": "wA",
            "pane": "wA:p1",
            "deadline": 2000,
        }
        self.herdr(
            [("wA", None, self.web)],
            [("wA", "wA:p1", {"agent": "claude", "agent_status": "working"})],
        )
        request = {"prs": [{"repo": "acme/webapp", "branch": "feature"}]}

        def state(record, now=1000, answered=False):
            path = signals / "signal-1.json"
            path.write_text(json.dumps(record))
            answer = path.with_suffix(".answer")
            if answered:
                answer.write_text("a")
            else:
                answer.unlink(missing_ok=True)
            with (
                patch.object(worker_signals, "directory", return_value=signals),
                patch.object(host.time, "time", return_value=now),
            ):
                return host.panes(request)["panes"]["acme/webapp:feature"]["state"]

        self.assertEqual(state(question), "waiting")
        self.assertEqual(state(question, answered=True), "working")
        self.assertEqual(state(question, now=3000), "working")
        self.assertEqual(state(dict(question, cleanup=True)), "working")
        self.assertEqual(state(dict(question, pane="wA:p2")), "working")
        self.assertEqual(state(dict(question, kind="done")), "working")

    def test_any_git_branch_name_is_looked_up(self):
        plus = self.checkout("plus", "feature+fix@2", "https://github.com/acme/webapp")
        self.herdr([("wP", None, plus)], [("wP", "wP:p1", {"agent": "claude"})])
        reply = host.panes(
            {
                "prs": [
                    {"repo": "acme/webapp", "branch": "feature+fix@2"},
                    {"repo": "acme/webapp", "branch": "other;branch"},
                ]
            }
        )
        self.assertEqual(
            reply["panes"],
            {
                "acme/webapp:feature+fix@2": {"state": None},
                "acme/webapp:other;branch": None,
            },
        )

    def test_focus_uses_the_configured_herdr_socket(self):
        self.herdr([("wA", None, self.web)], [("wA", "wA:p1", {"agent": "claude"})])
        with patch.dict(os.environ, {"ORCH_HERDR_SOCKET": "/fixture/socket"}):
            host.focus_pane({"repo": "acme/webapp", "branch": "feature"})
        self.assertEqual(
            (self.base / "sockets").read_text().splitlines(), ["/fixture/socket"] * 3
        )
        self.assertNotEqual(os.environ.get("HERDR_SOCKET_PATH"), "/fixture/socket")

    def test_an_oversized_question_record_is_skipped(self):
        signals = self.base / "signals"
        signals.mkdir()
        record = {
            "kind": "question",
            "workspace": "wA",
            "pane": "wA:p1",
            "deadline": 2000,
            "question": "x" * host.MAX_REQUEST_BYTES,
        }
        (signals / "signal-1.json").write_text(json.dumps(record))
        self.herdr(
            [("wA", None, self.web)],
            [("wA", "wA:p1", {"agent": "claude", "agent_status": "idle"})],
        )
        with (
            patch.object(worker_signals, "directory", return_value=signals),
            patch.object(host.time, "time", return_value=1000),
        ):
            reply = host.panes({"prs": [{"repo": "acme/webapp", "branch": "feature"}]})
        self.assertEqual(reply["panes"], {"acme/webapp:feature": {"state": "idle"}})

    def test_an_orphaned_owner_is_not_a_worker(self):
        self.herdr(
            [
                ("wOld", "closed-architect", self.web),
                ("wArch", None, self.other),
                ("wWorker", "wArch", self.web),
            ],
            [
                ("wOld", "wOld:p1", {"agent": "claude"}),
                ("wArch", "wArch:p1", {"agent": "claude"}),
                ("wWorker", "wWorker:p1", {"agent": "claude"}),
            ],
        )
        reply = host.focus_pane({"repo": "acme/webapp", "branch": "feature"})
        self.assertIsNone(reply["pane"])
        self.assertEqual(self.focus_calls(), ["workspace focus wArch"])

    def test_a_long_valid_branch_does_not_reject_the_lookup(self):
        self.herdr([("wA", None, self.web)], [("wA", "wA:p1", {})])
        long = "b" * 300
        reply = host.panes(
            {
                "prs": [
                    {"repo": "acme/webapp", "branch": long},
                    {"repo": "acme/webapp", "branch": "feature"},
                ]
            }
        )
        self.assertEqual(
            reply["panes"],
            {f"acme/webapp:{long}": None, "acme/webapp:feature": {"state": None}},
        )

    def test_shared_checkouts_are_probed_once_without_clone_roots(self):
        sub = Path(self.web) / "sub"
        sub.mkdir()
        rows = [{"workspace_id": id} for id in ("wA", "wB", "wC")]
        (self.base / "workspaces.json").write_text(
            json.dumps({"result": {"workspaces": rows}})
        )
        pane_rows = [
            {"workspace_id": "wA", "pane_id": "wA:p1", "cwd": self.web},
            {"workspace_id": "wB", "pane_id": "wB:p1", "cwd": self.web},
            {"workspace_id": "wC", "pane_id": "wC:p1", "cwd": str(sub)},
        ]
        (self.base / "panes.json").write_text(
            json.dumps({"result": {"panes": pane_rows}})
        )
        calls, real = [], host.bounded_command

        def counting(deadline):
            command = real(deadline)

            def run(*args):
                calls.append(args[1:] if args[0] != "git" else args[3:])
                return command(*args)

            return run

        with patch.object(host, "bounded_command", counting):
            reply = host.panes({"prs": [{"repo": "acme/webapp", "branch": "feature"}]})
        self.assertEqual(reply["panes"], {"acme/webapp:feature": {"state": None}})
        self.assertEqual(
            calls,
            [
                ("workspace", "list"),
                ("pane", "list"),
                ("rev-parse", "--show-toplevel"),
                ("rev-parse", "--show-toplevel"),
                ("symbolic-ref", "--quiet", "HEAD"),
                ("remote", "get-url", "origin"),
            ],
        )
