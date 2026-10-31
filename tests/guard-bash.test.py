#!/usr/bin/env python3
"""Policy integration tests plus in-memory mutation checks for guard-bash."""

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ["ORCH_ENV"] = "none"

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks/guard-bash.py"
spec = importlib.util.spec_from_file_location("guard", HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def hook(command, cwd, timeout=20):
    payload = json.dumps(
        {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}
    )
    try:
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(HOOK, "timeout", "", "guard timed out")


def check_installer(repo, root, push_cwd):
    home = root / "install-home"
    bindir = root / "install-bin"
    home.mkdir()
    bindir.mkdir()
    custom_config = root / "selected-orchestration.toml"
    custom_config.write_text('[pr]\nauthor = "me"\n')
    gh = bindir / "gh"
    gh.write_text('#!/bin/sh\n[ "$1 $2" = "auth status" ]\n')
    gh.chmod(0o755)
    env = os.environ.copy()
    env.update(
        HOME=str(home),
        PATH=f"{bindir}:{env['PATH']}",
        ORCH_REPO_PATH=str(repo),
        ORCH_CONFIG=str(custom_config),
    )
    command = ["/bin/bash", str(repo / "install.sh"), "--stage=link"]
    first = subprocess.run(command, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    target = home / ".claude/hooks/guard-bash.py"
    assert target.is_symlink() and target.resolve() == repo / "hooks/guard-bash.py"
    settings = json.loads((home / ".claude/settings.json").read_text())
    command_name = f"ORCH_CONFIG={shlex.quote(str(custom_config))} python3 ~/.claude/hooks/guard-bash.py"
    assert (
        sum(
            h["command"] == command_name
            for entry in settings["hooks"]["PreToolUse"]
            for h in entry["hooks"]
        )
        == 1
    )
    installed_env = env.copy()
    installed_env.pop("ORCH_CONFIG")
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "cwd": str(root / "webapp"),
            "tool_input": {"command": "git grep needle"},
        }
    )
    installed = subprocess.run(
        command_name,
        shell=True,
        executable="/bin/bash",
        input=payload,
        capture_output=True,
        text=True,
        env=installed_env,
    )
    assert installed.returncode == 2, installed.stderr
    for entry in settings["hooks"]["PreToolUse"]:
        for installed_hook in entry["hooks"]:
            if installed_hook.get("command") == command_name:
                installed_hook["command"] = "python3 ~/.claude/hooks/guard-bash.py"
    (home / ".claude/settings.json").write_text(json.dumps(settings))
    second = subprocess.run(command, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert (
        sum(
            h["command"] == command_name
            for entry in settings["hooks"]["PreToolUse"]
            for h in entry["hooks"]
        )
        == 1
    )

    matcher_home = root / "matcher-home"
    (matcher_home / ".claude").mkdir(parents=True)
    (matcher_home / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Read",
                            "hooks": [{"type": "command", "command": command_name}],
                        }
                    ]
                }
            }
        )
    )
    matcher_env = env.copy()
    matcher_env["HOME"] = str(matcher_home)
    third = subprocess.run(command, env=matcher_env, capture_output=True, text=True)
    assert third.returncode == 0, third.stderr
    matcher_settings = json.loads((matcher_home / ".claude/settings.json").read_text())
    entries = matcher_settings["hooks"]["PreToolUse"]
    assert (
        sum(h["command"] == command_name for entry in entries for h in entry["hooks"])
        == 2
    )
    assert (
        sum(
            h["command"] == command_name
            for entry in entries
            if entry.get("matcher") == "Bash"
            for h in entry["hooks"]
        )
        == 1
    )

    wildcard_home = root / "wildcard-home"
    (wildcard_home / ".claude").mkdir(parents=True)
    (wildcard_home / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": command_name}],
                        }
                    ]
                }
            }
        )
    )
    wildcard_env = env.copy()
    wildcard_env["HOME"] = str(wildcard_home)
    wildcard = subprocess.run(command, env=wildcard_env, capture_output=True, text=True)
    assert wildcard.returncode == 0, wildcard.stderr
    wildcard_settings = json.loads(
        (wildcard_home / ".claude/settings.json").read_text()
    )
    assert (
        sum(
            h["command"] == command_name
            for entry in wildcard_settings["hooks"]["PreToolUse"]
            for h in entry["hooks"]
        )
        == 1
    )

    quoted_home = root / "quoted-home"
    (quoted_home / ".claude").mkdir(parents=True)
    quoted_command = 'python3 "$HOME/.claude/hooks/guard-bash.py"'
    (quoted_home / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": quoted_command}],
                        }
                    ]
                }
            }
        )
    )
    quoted_env = env.copy()
    quoted_env["HOME"] = str(quoted_home)
    quoted = subprocess.run(command, env=quoted_env, capture_output=True, text=True)
    assert quoted.returncode == 0, quoted.stderr
    quoted_settings = json.loads((quoted_home / ".claude/settings.json").read_text())
    quoted_commands = [
        hook["command"]
        for entry in quoted_settings["hooks"]["PreToolUse"]
        for hook in entry["hooks"]
    ]
    assert quoted_command in quoted_commands and command_name in quoted_commands

    for name, matcher, hook_command in [
        ("absolute", "Bash", "/usr/bin/python3 {home}/.claude/hooks/guard-bash.py"),
        ("quoted-absolute", "Bash", 'python3 "{home}/.claude/hooks/guard-bash.py"'),
        ("regex", "^Bash$", "env MODE=test python3 ~/.claude/hooks/guard-bash.py"),
        ("path-env", "Bash", "/usr/bin/env python3 ~/.claude/hooks/guard-bash.py"),
        ("versioned", "Bash", "python3.12 ~/.claude/hooks/guard-bash.py"),
        ("unquoted-home", "Bash", "python3 $HOME/.claude/hooks/guard-bash.py"),
    ]:
        shape_home = root / (name + "-home")
        (shape_home / ".claude").mkdir(parents=True)
        (shape_home / ".claude/settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": matcher,
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": hook_command.format(home=shape_home),
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )
        shape_env = env.copy()
        shape_env["HOME"] = str(shape_home)
        shaped = subprocess.run(command, env=shape_env, capture_output=True, text=True)
        assert shaped.returncode == 0, shaped.stderr
        shape_commands = [
            hook["command"]
            for entry in json.loads((shape_home / ".claude/settings.json").read_text())[
                "hooks"
            ]["PreToolUse"]
            for hook in entry["hooks"]
        ]
        assert (
            hook_command.format(home=shape_home) in shape_commands
            and command_name in shape_commands
        )

    direct_home = root / "direct-home"
    (direct_home / ".claude").mkdir(parents=True)
    (direct_home / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "~/.claude/hooks/guard-bash.py",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    direct_env = env.copy()
    direct_env["HOME"] = str(direct_home)
    direct = subprocess.run(command, env=direct_env, capture_output=True, text=True)
    assert direct.returncode == 0, direct.stderr
    direct_commands = [
        h["command"]
        for entry in json.loads((direct_home / ".claude/settings.json").read_text())[
            "hooks"
        ]["PreToolUse"]
        for h in entry["hooks"]
    ]
    assert direct_commands == ["~/.claude/hooks/guard-bash.py", command_name], (
        direct_commands
    )
    push = json.dumps(
        {
            "tool_name": "Bash",
            "cwd": str(push_cwd),
            "tool_input": {"command": "git push origin HEAD:main"},
        }
    )
    protected = subprocess.run(
        ["/bin/bash", "-c", command_name],
        input=push,
        env=direct_env,
        capture_output=True,
        text=True,
    )
    assert protected.returncode == 2, (protected.returncode, protected.stderr)

    broken_repo = root / "broken-repo"
    shutil.copytree(repo, broken_repo, ignore=shutil.ignore_patterns(".git"))
    (broken_repo / "hooks/guard-bash.py").write_text("import sys\nsys.exit(0)\n")
    broken_home = root / "broken-home"
    broken_home.mkdir()
    broken_env = env.copy()
    broken_env.update(HOME=str(broken_home), ORCH_REPO_PATH=str(broken_repo))
    broken = subprocess.run(
        ["/bin/bash", str(broken_repo / "install.sh"), "--stage=link"],
        env=broken_env,
        capture_output=True,
        text=True,
    )
    assert broken.returncode != 0 and "Bash is unguarded" in broken.stderr, (
        broken.returncode,
        broken.stderr,
    )
    for name, guard_source in [
        ("silent-deny", "import sys\nsys.exit(2)\n"),
        ("missing", None),
    ]:
        bad_repo = root / (name + "-repo")
        shutil.copytree(repo, bad_repo, ignore=shutil.ignore_patterns(".git"))
        if guard_source is None:
            (bad_repo / "hooks/guard-bash.py").unlink()
        else:
            (bad_repo / "hooks/guard-bash.py").write_text(guard_source)
        bad_home = root / (name + "-home")
        bad_home.mkdir()
        bad_env = env.copy()
        bad_env.update(HOME=str(bad_home), ORCH_REPO_PATH=str(bad_repo))
        bad = subprocess.run(
            ["/bin/bash", str(bad_repo / "install.sh"), "--stage=link"],
            env=bad_env,
            capture_output=True,
            text=True,
        )
        assert bad.returncode != 0 and "Bash is unguarded" in bad.stderr, (
            name,
            bad.returncode,
            bad.stderr,
        )

    for name, hook_command in [
        ("literal-home", "python3 '$HOME/.claude/hooks/guard-bash.py'"),
        ("quoted-tilde", 'python3 "~/.claude/hooks/guard-bash.py"'),
        ("echo-mention", "echo guard-bash.py"),
        ("copy-mention", "cp guard-bash.py /tmp/guard-bash.py"),
        ("wrapper-mention", 'python3 wrapper.py "$HOME/.claude/hooks/guard-bash.py"'),
        ("masked-guard", "python3 ~/.claude/hooks/guard-bash.py || true"),
        ("python2", "python2 ~/.claude/hooks/guard-bash.py"),
        ("newline-registration", 'python3\n"$HOME/.claude/hooks/guard-bash.py"'),
        ("short-circuit", "true||/usr/bin/python3 ~/.claude/hooks/guard-bash.py"),
        ("other-home", "python3 /old-home/.claude/hooks/guard-bash.py"),
        (
            "env-control-operator",
            'env MODE=test&&true python3 "$HOME/.claude/hooks/guard-bash.py"',
        ),
        ("env-option", "env --help python3 ~/.claude/hooks/guard-bash.py"),
        ("open-quote", 'python3 "$HOME/.claude/hooks/guard-bash.py'),
        ("close-quote", 'python3 $HOME/.claude/hooks/guard-bash.py"'),
    ]:
        literal_home = root / (name + "-home")
        (literal_home / ".claude").mkdir(parents=True)
        (literal_home / ".claude/settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": hook_command}],
                            }
                        ]
                    }
                }
            )
        )
        literal_env = env.copy()
        literal_env["HOME"] = str(literal_home)
        literal = subprocess.run(
            command, env=literal_env, capture_output=True, text=True
        )
        assert literal.returncode == 0, literal.stderr
        assert (
            len(
                json.loads((literal_home / ".claude/settings.json").read_text())[
                    "hooks"
                ]["PreToolUse"]
            )
            == 2
        )

    invalid_home = root / "invalid-home"
    (invalid_home / ".claude").mkdir(parents=True)
    (invalid_home / ".claude/settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": {}}})
    )
    invalid_env = env.copy()
    invalid_env["HOME"] = str(invalid_home)
    invalid = subprocess.run(command, env=invalid_env, capture_output=True, text=True)
    assert invalid.returncode == 0 and "not a supported hooks object" in invalid.stdout
    assert json.loads((invalid_home / ".claude/settings.json").read_text()) == {
        "hooks": {"PreToolUse": {}}
    }

    nested_invalid_home = root / "nested-invalid-home"
    (nested_invalid_home / ".claude").mkdir(parents=True)
    malformed_settings = {
        "hooks": {"SessionStart": ["bad"], "PreToolUse": [{"hooks": ["bad"]}]}
    }
    (nested_invalid_home / ".claude/settings.json").write_text(
        json.dumps(malformed_settings)
    )
    nested_invalid_env = env.copy()
    nested_invalid_env["HOME"] = str(nested_invalid_home)
    nested_invalid = subprocess.run(
        command, env=nested_invalid_env, capture_output=True, text=True
    )
    assert (
        nested_invalid.returncode == 0
        and "not a supported hooks object" in nested_invalid.stdout
    )
    assert (
        json.loads((nested_invalid_home / ".claude/settings.json").read_text())
        == malformed_settings
    )

    no_matcher_home = root / "no-matcher-home"
    (no_matcher_home / ".claude").mkdir(parents=True)
    (no_matcher_home / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"hooks": [{"type": "command", "command": command_name}]}
                    ]
                }
            }
        )
    )
    no_matcher_env = env.copy()
    no_matcher_env["HOME"] = str(no_matcher_home)
    no_matcher = subprocess.run(
        command, env=no_matcher_env, capture_output=True, text=True
    )
    assert no_matcher.returncode == 0, no_matcher.stderr
    assert (
        len(
            json.loads((no_matcher_home / ".claude/settings.json").read_text())[
                "hooks"
            ]["PreToolUse"]
        )
        == 1
    )


with tempfile.TemporaryDirectory(prefix="guard-bash-tests-") as directory:
    root = Path(directory)
    test_home = root / "home"
    (test_home / ".claude/hooks").mkdir(parents=True)
    os.environ["HOME"] = str(test_home)
    os.environ.pop("ORCH_CONFIG", None)
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(
        repo,
        "-c",
        "user.name=Guard Test",
        "-c",
        "user.email=guard@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )
    bare = root / "origin.git"
    bare.mkdir()
    git(bare, "init", "--bare")
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "branch", "feature")
    git(repo, "branch", "--set-upstream-to=origin/main", "feature")
    wt = root / "worktree"
    git(repo, "worktree", "add", str(wt), "feature")
    isolated = root / "isolated"
    isolated.mkdir()
    git(isolated, "init", "-b", "feature")
    git(isolated, "remote", "add", "origin", str(bare))
    remote_named_main = root / "remote-named-main"
    remote_named_main.mkdir()
    git(remote_named_main, "init", "-b", "feature")
    git(remote_named_main, "remote", "add", "origin", str(bare))
    git(remote_named_main, "remote", "add", "main", str(bare))
    upstream_wt = root / "upstream-worktree"
    upstream_wt.mkdir()
    git(upstream_wt, "init", "-b", "feature")
    git(upstream_wt, "remote", "add", "upstream", str(bare))
    git(upstream_wt, "config", "branch.feature.remote", "upstream")
    git(upstream_wt, "config", "branch.feature.merge", "refs/heads/main")
    matching = root / "matching"
    matching.mkdir()
    git(matching, "init", "-b", "main")
    git(
        matching,
        "-c",
        "user.name=Guard Test",
        "-c",
        "user.email=guard@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )
    git(matching, "branch", "feature")
    git(matching, "checkout", "feature")
    git(matching, "remote", "add", "origin", str(bare))
    git(matching, "config", "push.default", "matching")
    configured_matching = root / "configured-matching"
    configured_matching.mkdir()
    git(configured_matching, "init", "-b", "feature")
    git(configured_matching, "remote", "add", "origin", str(bare))
    git(configured_matching, "config", "remote.origin.push", ":")
    push_remote = root / "push-remote"
    push_remote.mkdir()
    git(push_remote, "init", "-b", "feature")
    git(push_remote, "remote", "add", "origin", str(bare))
    git(push_remote, "remote", "add", "deploy", str(bare))
    git(push_remote, "config", "branch.feature.pushRemote", "deploy")
    git(push_remote, "config", "remote.deploy.push", "HEAD:main")
    push_default = root / "push-default"
    push_default.mkdir()
    git(push_default, "init", "-b", "feature")
    git(push_default, "remote", "add", "origin", str(bare))
    git(push_default, "remote", "add", "deploy", str(bare))
    git(push_default, "config", "remote.pushDefault", "deploy")
    git(push_default, "config", "remote.deploy.push", "HEAD:main")
    webapp = root / "webapp"
    webapp.mkdir()
    git(webapp, "init", "-b", "main")
    git(webapp, "remote", "add", "origin", "git@github.com:acme/webapp.git")
    other = root / "other"
    other.mkdir()
    git(other, "init", "-b", "main")
    git(other, "remote", "add", "origin", "https://github.com/combinaut/not-webapp.git")
    separate = root / "separate"
    subprocess.run(
        [
            "git",
            "init",
            "-q",
            f"--separate-git-dir={root / 'separate.git'}",
            str(separate),
        ],
        check=True,
    )
    (separate / ".orchestration.toml").write_text("[guard]\nbig_repo = true\n")
    sub_source = root / "sub-source"
    sub_source.mkdir()
    git(sub_source, "init", "-b", "main")
    git(
        sub_source,
        "-c",
        "user.name=Guard Test",
        "-c",
        "user.email=guard@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )
    superproject = root / "superproject"
    superproject.mkdir()
    git(superproject, "init", "-b", "main")
    git(
        superproject,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "-q",
        "add",
        str(sub_source),
        "module",
    )
    (superproject / "module/.orchestration.toml").write_text(
        "[guard]\nbig_repo = true\n"
    )
    repo_file = webapp / ".orchestration.toml"
    local_file = webapp / ".orchestration.local.toml"
    (webapp / "app").mkdir()
    assert hook("git grep needle", webapp).returncode == 0
    repo_file.write_text("[guard]\nbig_repo = true\n")
    assert hook("git grep needle", webapp).returncode == 2
    assert hook("git grep needle", webapp / "app").returncode == 2
    assert hook("git grep needle", other).returncode == 0
    os.environ["ORCH_BIG_REPO"] = "0"
    assert hook("git grep needle", webapp).returncode == 0
    del os.environ["ORCH_BIG_REPO"]
    local_file.write_text("[guard]\nbig_repo = false\n")
    assert hook("git grep needle", webapp).returncode == 0
    local_file.unlink()
    no_tomllib = subprocess.run(
        [
            sys.executable,
            "-c",
            'import runpy, sys; sys.modules["tomllib"] = None; sys.argv = [sys.argv[1]]; runpy.run_path(sys.argv[0], run_name="__main__")',
            str(HOOK),
        ],
        input=json.dumps(
            {
                "tool_name": "Bash",
                "cwd": str(webapp),
                "tool_input": {"command": "git grep needle"},
            }
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert (
        no_tomllib.returncode == 2
        and "No git grep in a configured big repo" in no_tomllib.stderr
    ), no_tomllib.stderr
    good_config = repo_file.read_text()
    try:
        for broken in (
            "[guard\n",
            '[guard]\nbig_repo = "yes"\n',
            "[heavy_slots]\nmax = 2\n",
            b"[guard]\nbig_repo = true\n# \xff\n",
        ):
            repo_file.write_bytes(
                broken if isinstance(broken, bytes) else broken.encode()
            )
            result = hook("git grep needle", webapp)
            assert (
                result.returncode == 2
                and "Cannot load the repo settings" in result.stderr
            ), (broken, result.stderr)
            assert (
                "heavy_slots" not in str(broken)
                or "heavy_slots.max: is a global setting" in result.stderr
            ), result.stderr
            assert (
                hook("git cat-file --batch-all-objects --batch", webapp).returncode == 2
            )
            assert (
                hook("git ls-tree -r HEAD | git cat-file --batch", webapp).returncode
                == 2
            )
            assert hook("git cat-file -p HEAD", webapp).returncode == 0
            assert hook("git status", webapp).returncode == 0
    finally:
        repo_file.write_text(good_config)
    msg = root / "message.txt"
    msg.write_text("Co-Authored-By: Claude <bot@anthropic.com>")
    oversized_msg = root / "oversized-message.txt"
    oversized_msg.write_text("x" * (1024 * 1024) + "Co-Authored-By: Claude")
    oversized_plain = root / "oversized-plain.txt"
    oversized_plain.write_text("x" * (1024 * 1024 + 1))
    original_read_bytes = Path.read_bytes
    Path.read_bytes = lambda self: (_ for _ in ()).throw(
        AssertionError("unbounded read")
    )
    try:
        assert (
            guard.file_text(str(oversized_plain), root) == guard.FILE_TOO_LARGE_REASON
        )
    finally:
        Path.read_bytes = original_read_bytes
    graphql_input = root / "graphql-input.json"
    graphql_input.write_text(
        '{"query":"mutation { updatePullRequest(input:{body:"Co-Authored-By: Claude"}) { clientMutationId } }"}'
    )
    fifo = root / "message.fifo"
    os.mkfifo(fifo)
    clean = root / "clean.txt"
    clean.write_text("A plain commit message")
    cases = []

    def case(name, command, deny=False, cwd=repo):
        cases.append((name, command, deny, cwd))

    for ref in (
        "main",
        "master",
        "HEAD:main",
        "x:refs/heads/main",
        "+main",
        "feature:master",
    ):
        case("push " + ref, "git push origin " + ref, True)
    case(
        "push protected ref with glued redirect",
        "git push origin HEAD:main>/dev/null",
        True,
    )
    case("push --repo protected ref", "git push --repo=origin main", True, isolated)
    case(
        "push --repo protected ref with remote named main",
        "git push --repo=origin main",
        True,
        remote_named_main,
    )
    case("push all", "git push --all origin", True, isolated)
    case("push mirror", "git push --mirror origin", True, isolated)
    case("push matching refspec", "git push origin :", True, isolated)
    case("push forced matching refspec", "git push origin +:", True, isolated)
    case(
        "push wildcard protected refs",
        "git push origin 'refs/heads/*:refs/heads/*'",
        True,
        isolated,
    )
    for flag in ("-f", "--force", "--force-with-lease", "--force-if-includes"):
        case("force " + flag, "git push " + flag + " origin HEAD:main", True)
    case("bare main", "git push", True)
    case("origin main", "git push origin", True)
    case("source-only HEAD on main", "git push origin HEAD", True)
    case("redirected main push", "git push origin 2>&1", True)
    case("configured protected push", "git -c remote.origin.push=HEAD:main push", True)
    case("upstream main", "git push", True, wt)
    case("upstream main with remote", "git push origin", True, wt)
    case("non-origin upstream main", "git push upstream", True, upstream_wt)
    case("matching push default", "git push origin", True, matching)
    case("configured matching push", "git push origin", True, configured_matching)
    case("branch push remote protected push", "git push", True, push_remote)
    case("default push remote protected push", "git push", True, push_default)
    case("cd push", f"cd {wt} && git push", True, root)
    case("git -C push", f"git -C {wt} push", True, root)
    case("env push", "env MODE=test git push origin HEAD:main", True)
    case("pipeline push", "echo ok | git push origin HEAD:main", True)
    case("env option push", "env -i git push origin HEAD:main", True)
    case("command option push", "command -- git push origin HEAD:main", True)
    case("env split-string push", "env -S 'git push origin HEAD:main'", True)
    case(
        "env long split-string push",
        "env --split-string='git push origin HEAD:main'",
        True,
    )
    for name, command in [
        ("env combined short options", "env -i0 git push origin HEAD:main"),
        ("command combined short options", "command -pp git push origin HEAD:main"),
        ("exec combined short options", "exec -cl git push origin HEAD:main"),
        ("time combined short options", "time -avq git push origin HEAD:main"),
        (
            "timeout combined short options",
            "timeout -sTERM -k5 60 git push origin HEAD:main",
        ),
        ("nice attached short option", "nice -n5 git push origin HEAD:main"),
        ("nohup option terminator", "nohup -- git push origin HEAD:main"),
        ("xargs combined short options", "xargs -tn 1 git push origin HEAD:main"),
    ]:
        case(name, command, True)
    case(
        "quoted semicolon refspec push", "git push origin ';' HEAD:main", True, isolated
    )
    case("ordinary echo guarded words", "echo git push origin HEAD:main")
    case("continued push", "git push \\\n+ origin HEAD:main", True)
    case("keyword push", "if true; then git push origin HEAD:main; fi", True)
    case("grouped push", "{ git push origin HEAD:main; }", True)
    case("negated push", "! git push origin HEAD:main", True)
    case("timeout push", "timeout 60 git push origin HEAD:main", True)
    case("unknown git option push", "git -P -C . push origin HEAD:main", True)
    case("ANSI quoted push", "echo $'it\\'s'; git push origin HEAD:main", True)
    case("subshell push", f"(cd {wt}; git push)", True, root)
    for wrapper, options in guard.WRAPPER_OPTIONS.items():
        for option, arity in options.items():
            if wrapper == "env" and option in ("-S", "--split-string"):
                continue
            if wrapper == "command" and option in ("-v", "-V"):
                continue
            prefix = [wrapper, option]
            if arity == 1:
                case(
                    f"{wrapper} {option} separate value",
                    " ".join(
                        prefix
                        + ["VALUE"]
                        + (["60"] if wrapper == "timeout" else [])
                        + ["git", "push", "origin", "HEAD:main"]
                    ),
                    True,
                )
                attached = (
                    option + "=VALUE" if option.startswith("--") else option + "VALUE"
                )
                case(
                    f"{wrapper} {option} attached value",
                    " ".join(
                        [wrapper, attached]
                        + (["60"] if wrapper == "timeout" else [])
                        + ["git", "push", "origin", "HEAD:main"]
                    ),
                    True,
                )
            elif arity == "optional":
                case(
                    f"{wrapper} {option} optional",
                    " ".join(prefix + ["git", "push", "origin", "HEAD:main"]),
                    True,
                )
                case(
                    f"{wrapper} {option} optional attached",
                    " ".join(
                        [
                            wrapper,
                            option + "VALUE",
                            "git",
                            "push",
                            "origin",
                            "HEAD:main",
                        ]
                    ),
                    True,
                )
            else:
                case(
                    f"{wrapper} {option}",
                    " ".join(
                        prefix
                        + (["60"] if wrapper == "timeout" else [])
                        + ["git", "push", "origin", "HEAD:main"]
                    ),
                    True,
                )
    for name, command in [
        ("Claude trailer", 'git commit -m "Co-Authored-By: Claude"'),
        (
            "anthropic trailer",
            'git commit --message="co-authored-by: Bot <b@anthropic.com>"',
        ),
        ("generated bracket", 'gh pr create --body "Generated with [Claude Code]"'),
        ("generated plain", 'gh pr edit --title "Generated with Claude Code"'),
        ("robot", 'gh pr create --body "🤖 Generated"'),
        ("session", 'git commit -m "claude.ai/code/session_123"'),
        ("commit file", f"git commit -F {msg}"),
        ("PR file", f"gh pr edit --body-file {msg}"),
        ("API raw", 'gh api repos/a/b/pulls -f body="Generated with Claude Code"'),
        ("API file", f"gh api repos/a/b/pulls/1 -X PATCH -F body=@{msg}"),
        ("direct heredoc", "git commit -F - <<'EOF'\nCo-Authored-By: Claude\nEOF"),
        (
            "substitution heredoc",
            "git commit -m \"$(cat <<'EOF'\nGenerated with Claude Code\nEOF\n)\"",
        ),
    ]:
        case(name, command, True)
    case("commit trailer", 'git commit --trailer "Co-authored-by: Claude"', True)
    for name, command in [
        (
            "Codex trailer",
            'git commit -m "x" -m "Co-authored-by: Codex <codex@openai.com>"',
        ),
        (
            "API issues endpoint",
            'gh api -X PATCH repos/a/b/issues/27 -f body="Co-Authored-By: Claude"',
        ),
        (
            "API queried issues endpoint",
            "gh api -X PATCH 'repos/a/b/issues/27?foo=bar' -f body=\"Co-Authored-By: Claude\"",
        ),
        (
            "API piped input",
            'printf \'{"body":"Co-Authored-By: Claude"}\' | gh api repos/a/b/pulls/1 -X PATCH --input -',
        ),
        (
            "API here-string input",
            'gh api repos/a/b/pulls/1 -X PATCH --input - <<< \'{"body":"Co-Authored-By: Claude"}\'',
        ),
        (
            "GraphQL inline PR update",
            'gh api graphql -f query="mutation { updatePullRequest(input:{pullRequestId:"x", body:"Co-Authored-By: Claude"}) { clientMutationId } }"',
        ),
        (
            "GraphQL variable PR update",
            'gh api graphql -f query="mutation($b:String!){updatePullRequest(input:{pullRequestId:"x",body:$b}){clientMutationId}}" -f b="Co-Authored-By: Claude"',
        ),
        ("GraphQL input file", f"gh api graphql --input {graphql_input}"),
        ("PR merge body", 'gh pr merge 27 --squash --body "Co-Authored-By: Claude"'),
        ("here string", 'git commit -F - <<< "Co-Authored-By: Claude"'),
        (
            "piped commit message",
            'printf "Co-Authored-By: Claude\\n" | git commit -F -',
        ),
        (
            "same command message file",
            "cat > /tmp/msg <<'EOF'\nCo-Authored-By: Claude\nEOF\ngit commit -F /tmp/msg",
        ),
        ("oversized message file", f"git commit -F {oversized_msg}"),
        ("oversized plain message file", f"git commit -F {oversized_plain}"),
        ("FIFO message file", f"git commit -F {fifo}"),
        (
            "ChatGPT trailer",
            'git commit -m "Co-authored-by: ChatGPT <noreply@chatgpt.com>"',
        ),
        ("ChatGPT generated using", 'git commit -m "Generated using ChatGPT"'),
        ("Copilot generated by", 'git commit -m "Generated-by: Copilot"'),
    ]:
        case(name, command, True)
    case(
        "combined commit message option",
        'git commit -am "Co-Authored-By: Claude"',
        True,
    )
    case("PR short body", 'gh pr create -b "Generated with Claude Code"', True)
    case("PR short title", 'gh pr edit -t "Generated with Claude Code"', True)
    case(
        "PR global repo option",
        'gh --repo owner/repo pr create --body "Generated with Claude Code"',
        True,
    )
    case("shared fetch", "git fetch --depth=2 origin", True)
    case("worktree pull", "git pull --unshallow", True, wt)
    case("shared deepen fetch", "git fetch --deepen 50 origin", True, wt)
    case("shared deepen pull", "git pull --deepen=50", True, wt)
    case("shared shallow since", "git fetch --shallow-since=2025-01-01", True)
    case("shared shallow exclude", "git fetch --shallow-exclude=main", True, wt)
    for sub in ("", "push", "pop", "drop", "clear", "apply"):
        case("stash " + sub, "git stash " + sub, True, wt)
    case("stash primary with worktree", "git stash push", True, repo)
    for command in (
        "pkill -f vite",
        'pkill -f "bin/dev-server" -P $$',
        "pkill -KILL -P $$",
        "pkill puma",
        "pgrep -f rails | xargs kill",
        "pgrep -f rails | xargs -r kill",
        "pgrep -f rails | xargs -n1 kill -9",
        "pgrep -f rails | xargs -0 kill",
        "killall ruby",
        "killall node",
        "killall puma",
    ):
        case(command, command, True)
    PAD = ": " + "x" * 1000 + "\\n"
    scripts = root / "scripts"
    scripts.mkdir()
    killing = scripts / "kills.sh"
    killing.write_text("#!/bin/bash\npkill -f vite\n")
    killing.chmod(0o755)
    clean_script = scripts / "clean.sh"
    clean_script.write_text("#!/bin/bash\necho ok\n")
    clean_script.chmod(0o755)
    incident = (
        "S=/private/tmp/scratch\ncat > $S/repro.sh <<'EOF'\n#!/bin/bash\n"
        "pkill -f 'sleep 30' -u $(id -u) 2>/dev/null; true\nEOF\n"
        "chmod +x $S/repro.sh; "
    )
    for name, command in [
        ("incident heredoc script run directly", incident + "$S/repro.sh $S 2>&1"),
        ("incident heredoc script run with bash", incident + "bash $S/repro.sh $S"),
        ("heredoc script run with source", incident + "source $S/repro.sh"),
        ("tee heredoc script run", "tee run.sh <<'EOF'\npkill -f vite\nEOF\nsh run.sh"),
        ("disk script via bash", f"bash {killing}"),
        ("disk script via path", f"{killing}"),
        ("disk script via dot", f". {killing}"),
        ("BSD option order after pattern", "pkill -f 'sleep 30' -u $(id -u)"),
        ("BSD option order with literal uid", "pkill -f '^x$' -u 501"),
    ]:
        case(name, command, True)
    for name, command in [
        (
            "notes heredoc mentioning pkill",
            "cat > notes.md <<'EOF'\npkill -f vite\nEOF",
        ),
        (
            "heredoc script written but not run",
            "cat > a.sh <<'EOF'\npkill -f vite\nEOF\nchmod +x a.sh",
        ),
        ("disk script without a kill", f"bash {clean_script}"),
        ("disk script missing", f"bash {scripts}/missing.sh"),
    ]:
        case(name, command)
    for name, command, cwd in [
        ("inline bash -c pkill", "bash -c 'pkill -f vite'", repo),
        ("inline sh -c pkill", 'sh -c "pkill -f vite"', repo),
        ("inline eval pkill", "eval 'pkill -f vite'", repo),
        ("heredoc to bash pkill", "bash <<'EOF'\npkill -f vite\nEOF", repo),
        ("bash relative script", "bash kills.sh", scripts),
        ("sh relative script", "sh kills.sh", scripts),
        ("cd then bash relative script", f"cd {scripts} && bash kills.sh", repo),
        ("cd then direct script", f"cd {scripts} && ./kills.sh", repo),
        ("literal variable script", f"S={scripts}; bash $S/kills.sh", repo),
        (
            "braced literal variable script",
            f"S='{scripts}' && bash ${{S}}/kills.sh",
            repo,
        ),
        ("nested inline shell pkill", "bash -c \"sh -c 'pkill -f vite'\"", repo),
    ]:
        case(name, command, True, cwd)
    for name, command, cwd in [
        ("inline bash -c without kill", "bash -c 'echo ok'", repo),
        ("relative script without kill", "bash clean.sh", scripts),
        ("bare word is a PATH lookup", "kills.sh", scripts),
        (
            "command substitution variable is skipped",
            f"S=$(echo {scripts}); bash $S/kills.sh",
            repo,
        ),
        (
            "literal variable script without kill",
            f"S={scripts}; bash $S/clean.sh",
            repo,
        ),
    ]:
        case(name, command, False, cwd)
    fixtures = {
        "pipe.sh": "#!/bin/bash\npgrep -f vite | xargs kill\n",
        "killall.sh": "#!/bin/bash\nkillall -m vite\n",
        "killall_own.sh": "#!/bin/bash\nkillall myapp\n",
        "apos.sh": "#!/bin/bash\necho don't\n",
        "apos_kill.sh": "#!/bin/bash\necho don't\npkill -f vite\n",
        "tool.js": "#!/usr/bin/env node\n// don't panic\npkill -f vite\n",
        "env_bash.sh": "#!/usr/bin/env bash\npkill -f vite\n",
        "lib.sh": "stopdev() { pkill -f vite; }\n",
        "eval": "pkill -f vite\n",
        "outer.sh": f"#!/bin/bash\n{killing}\n",
        "exec_log.sh": "#!/bin/bash\nexec >/dev/null 2>&1\necho started\n",
        "sudo_v.sh": "#!/bin/bash\nsudo -v\necho ok\n",
        "exec_nested.sh": f"#!/bin/bash\nexec 2>&1\n{killing}\n",
        "env_s.sh": "#!/usr/bin/env -S bash -e\npkill -f vite\n",
        "d1.sh": f"bash {scripts}/d2.sh\n",
        "d2.sh": f"bash {scripts}/d3.sh\n",
        "d3.sh": "pkill -f vite\n",
        "e1.sh": f"bash {scripts}/e2.sh\n",
        "e2.sh": f"bash {scripts}/e3.sh\n",
        "e3.sh": f"bash {scripts}/e4.sh\n",
        "e4.sh": "pkill -f vite\n",
        "mid.sh": "echo ok\n" * 2000 + "pkill -f vite\n",
        "three.sh": "cat <<EOF\nx\nEOF\n" * 3 + "pkill -f vite\n",
        "seventy.sh": "cat <<EOF\nx\nEOF\n" * 70 + "pkill -f vite\n",
        "fan.sh": f"bash {scripts}/fan.sh\n" * 120,
        "-dash.sh": "pkill -f vite\n",
    }
    for fixture, text in fixtures.items():
        (scripts / fixture).write_text(text)
    (scripts / "rs.sh").write_text("killall \x1e node\n")
    (scripts / "us.sh").write_text("echo \x1f\n")
    (scripts / "seen-safe.sh").write_text("echo safe\n")
    for marker in ("\x1d", "\x1e", "\x1f"):
        name = f"kill{marker}.sh"
        reference = f"bash ./{name}"
        (scripts / name).write_text("pkill -f vite\n")
        (scripts / f"ref{marker}.sh").write_text(reference + "\n")
        for label, command in (
            ("inline", f"bash -c {shlex.quote(reference)}"),
            ("disk", f"bash ./ref{marker}.sh"),
            ("printf", f"printf '%s\\n' {shlex.quote(reference)} | sh"),
            (
                "written",
                f"echo {shlex.quote(reference)} > marker-ref.sh; bash marker-ref.sh",
            ),
        ):
            case(
                f"literal marker filename {ord(marker):x} via {label}",
                command,
                True,
                scripts,
            )
    (scripts / "dash").mkdir()
    (scripts / "dash" / "-").write_text("pkill -f vite\n")
    (scripts / "bin.sh").write_bytes(b"#!/bin/bash\npkill -f vite\n\0")
    os.mkfifo(scripts / "fifo.sh")
    late = scripts / "late.sh"
    late.write_text("echo ok\n" * 12000 + "pkill -f vite\n")
    big = scripts / "big.sh"
    big.write_text('if [ -n "$X" ]; then echo "checking $X" >&5; fi\n' * 6000)
    for name in ("a40.sh", "b40.sh"):
        (scripts / name).write_text("echo ok\n" * 5000)
    many = scripts / "many.sh"
    many.write_text("cat <<EOF\n" * 6000)
    repro = "cat > repro.sh <<'EOF'\npkill -f vite\nEOF\n"
    for name, command in [
        ("unparsable script then pkill", f"bash {scripts}/apos.sh; pkill -f vite"),
        ("unparsable script holding pkill", f"bash {scripts}/apos_kill.sh"),
        ("heredoc script run by dot slash", repro + "chmod +x repro.sh && ./repro.sh"),
        (
            "heredoc script written to dot slash",
            repro.replace("> r", "> ./r") + "bash repro.sh",
        ),
        (
            "heredoc script via quoted variable",
            f'S={scripts}\ncat > "$S"/r.sh <<\'EOF\'\npkill -f vite\nEOF\nbash "$S"/r.sh',
        ),
        (
            "heredoc script via braced variable",
            f'S={scripts}\ncat > "${{S}}/r.sh" <<\'EOF\'\npkill -f vite\nEOF\nbash "$S/r.sh"',
        ),
        (
            "heredoc script via unset variable",
            "cat > $NOPE_VAR/r.sh <<'EOF'\npkill -f vite\nEOF\nbash $NOPE_VAR/r.sh",
        ),
        (
            "heredoc script run after cd",
            f"cat > {scripts}/r.sh <<'EOF'\npkill -f vite\nEOF\ncd {scripts} && bash r.sh",
        ),
        (
            "heredoc script written after cd",
            f"cd {scripts} && cat > r.sh <<'EOF'\npkill -f vite\nEOF\nbash r.sh",
        ),
        (
            "heredoc script after an earlier redirect",
            "echo hi > log.txt && " + repro + "bash repro.sh",
        ),
        (
            "tee with an option",
            "tee --append r.sh <<'EOF'\npkill -f vite\nEOF\nbash r.sh",
        ),
        (
            "tee after the option terminator",
            "tee -- -guard.sh <<'EOF'\npkill -f vite\nEOF\nbash ./-guard.sh",
        ),
        (
            "tee after the option terminator, kill -1",
            "tee -- -guard.sh <<'EOF'\nkill -9 -1\nEOF\nsh -- -guard.sh",
        ),
        ("printf %s written script", "printf '%s' 'pkill -f vite' > k.sh; bash k.sh"),
        ("printf %q followed by a literal", "printf '%qkill -f vite\\n' p | sh"),
        (
            "echo last -eE flag keeps escapes in a paired append",
            r"""printf 'echo "\x22\n' > m.sh; echo -eE 'echo "\x22"
pkill -f vite
# "' >> m.sh; bash m.sh""",
        ),
        (
            "echo last separate -E flag keeps escapes in a paired append",
            r"""printf 'echo "\x22\n' > m.sh; echo -e -E 'echo "\x22"
pkill -f vite
# "' >> m.sh; bash m.sh""",
        ),
        (
            "decoded reading revisits a rewritten disk script",
            r"""echo -e '\0145cho "pkill -f vite" > seen-safe.sh; bash seen-safe.sh' | sh""",
        ),
        (
            "printf %b preserves quote escapes",
            r"""printf '%b\n' 'echo \"\n\x70kill -f vite\n\"' | sh""",
        ),
        ("echo after a lone dash", "echo - 'pkill -f vite' | sh"),
        (
            "printf format reuse past the scan budget",
            "printf '"
            + "#" * 4000
            + "%s\\n' "
            + "a " * 150
            + "| sh; echo 'pkill -f vite' | sh",
        ),
        (
            "printf reused format, kill argument past the cut",
            "printf '"
            + " " * 4000
            + "%s\\n' "
            + "a " * 99
            + "'pkill -f vite' "
            + "a " * 50
            + "| sh",
        ),
        ("printf argument position 2", "printf 'kill -9 %2$s\\n' x -1 | sh"),
        (
            "printf arguments swapped by position",
            "printf '%2$s %1$s\\n' -1 'kill -9' | sh",
        ),
        (
            "printf one argument repeated past the cut",
            "printf '" + "%1$s" * 100 + "' " + "a" * 60000 + " | sh; bash kills.sh",
        ),
        ("printf %d with a leading blank", "printf 'kill -9 %d\\n' ' -1' | sh"),
        ("printf width splits a word", "printf 'pkill%2s-f vite\\n' | sh"),
        ("printf width splits a pid", "printf 'kill -9%3d\\n' -1 | sh"),
        (
            "printf width splits a word in a written script",
            "printf 'pkill%2s-f vite\\n' > k.sh; bash k.sh",
        ),
        (
            "printf position too long to read",
            "printf '%" + "9" * 4301 + "$s\\n' x; bash kills.sh",
        ),
        (
            "printf kill built from format and argument past the cut",
            "printf '" + PAD + "p%s\\n' " + "x " * 70 + "'kill -f vite' | sh",
        ),
        (
            "printf kill built from two arguments past the cut",
            "printf '" + PAD + "%s%s\\n' " + "true '' " * 70 + "pk 'ill -f vite' | sh",
        ),
        (
            "printf %b kill past the cut",
            "printf '" + PAD + "%b\\n' " + "true " * 70 + "'\\160kill -f vite' | sh",
        ),
        (
            "printf long width splits a word",
            "printf 'pkill%99999999999s-f vite\\n' | sh",
        ),
        (
            "printf width of thousands of digits splits a word",
            "printf 'pkill%" + "9" * 4301 + "s-f vite\\n' | sh",
        ),
        (
            "printf long position, kill in an argument",
            "printf '%9999999999$s' x 'pkill -f vite' | sh",
        ),
        (
            "printf cut text written, then catted to a shell",
            "printf '" + PAD + "%s\\n' " + "x " * 70 + " > big.txt; cat big.txt | sh",
        ),
        (
            "printf cut text written, appended to, then run",
            "printf '"
            + PAD
            + "%s\\n' "
            + "x " * 70
            + " > big.sh; echo done >> big.sh; bash big.sh",
        ),
        (
            "append whose printf text has a lone quote",
            "echo 'p\"k\"ill -f vite' > a.sh; printf '%1$s%1$s\\n' '\"' >> a.sh; bash a.sh",
        ),
        (
            "cat of a printf text with a lone quote",
            "printf 'p\"k\"ill -f vite\\n%1$s%1$s\\n' '\"' > a.sh; echo hi > b.sh; cat a.sh b.sh | sh",
        ),
        (
            "printf cut text written and run",
            "printf '" + PAD + "%s\\n' " + "x " * 70 + "> big.sh; bash big.sh",
        ),
        (
            "printf quote escape leaves a lone quote",
            'printf \'p"k"ill -f vite\\n\\"\\n\' | sh',
        ),
        (
            "printf single quote escape leaves a lone quote",
            "printf 'p\"k\"ill -f vite\\n\\'\"'\"'\\n' | sh",
        ),
        (
            "printf cut inside a quoted argument",
            "printf 'p\"k\"ill -f vite\\n%s\\n' \"'" + "x" * 70000 + "'\" | sh",
        ),
        ("printf precision on a number", "printf 'kill -9 %.2d\\n' -1 | sh"),
        ("printf single quote escape", "printf \"p\\'\\'kill -f vite\\n\" | sh"),
        (
            "printf escape makes the variant byte",
            "printf 'killall \\036 \\156ode\\n' | sh",
        ),
        (
            "echo -e hex escape makes the variant byte",
            "echo -e 'killall \\x1e \\x6eode' | sh",
        ),
        (
            "echo -e variant byte in a written script",
            "echo -e 'killall \\x1e \\x6eode' > w.sh; bash w.sh",
        ),
        (
            "printf variant byte in kill arguments",
            "printf 'kill -9 \\036 -1\\n' > w.sh; zsh w.sh",
        ),
        (
            "quote kill found only in the kept escape reading",
            'printf \'p"\\x6b"ill -f vite\\n\\"\\n\' | sh',
        ),
        (
            "single quote kill found only in the kept escape reading",
            "printf 'p\"\\x6b\"ill -f vite\\n\\'\"'\"'\\n' | sh",
        ),
        (
            "cat of a lone quote script and a quoted kill",
            "printf '\\\"\\n' > a.sh; echo 'p\"k\"ill -f vite' > b.sh; cat a.sh b.sh | sh",
        ),
        (
            "kept escape reading before a conversion",
            'printf \'p"\\x6b"ill -f vite\\n\\"%s\\n\' x | sh',
        ),
        ("printf width of a kill word", "printf 'pk%1sll -f vite\\n' i | sh"),
        ("printf width of a pid", "printf 'kill -9 -%1d\\n' 1 | sh"),
        (
            "append wrapped across readings",
            "printf '\"\\x22\\\"\\n' > a.sh; printf 'p\"\\x6b\"ill -f vite\\n' >> a.sh; bash a.sh",
        ),
        ("separator byte typed in a heredoc", "bash <<'EOF'\nkillall \x1e node\nEOF"),
        ("separator byte typed in eval", "eval 'killall \x1e node'"),
        ("separator byte typed in bash -c", "bash -c 'killall \x1e node'"),
        ("separator byte typed in a here-string", "bash <<< 'killall \x1e node'"),
        ("separator byte in a script on disk", "bash rs.sh"),
        ("separator byte typed in echo", "echo 'killall \x1e node' | sh"),
        (
            "separator byte typed in a printf argument",
            "printf '%f\\n' 'killall \x1e node' | sh",
        ),
        (
            "cat of two files past the scan limit",
            "printf ': "
            + "x" * 40000
            + "\\n' > a.sh; printf ': "
            + "x" * 40000
            + "\\n' > b.sh; cat a.sh b.sh | sh",
        ),
        ("printf zero flag on a pid", "printf 'kill -9 %05d\\n' -1 | sh"),
        (
            "printf left-justified width after a pid",
            "printf 'kill -9 %-3dx\\n' -1 | sh",
        ),
        (
            "append whose readings pair across texts",
            'printf \'"\\x22\\"\\n\' > m.sh; printf \'p"k"ill -f vite \\"\\n\' >> m.sh; bash m.sh',
        ),
        (
            "cat whose readings pair across texts",
            'printf \'"\\x22\\"\\n\' > m.sh; printf \'p"k"ill -f vite \\"\\n\' > n.sh; cat m.sh n.sh | sh',
        ),
        (
            "append of an echo whose readings pair across texts",
            'printf \'"\\x22\\"\\n\' > m.sh; echo -e \'p"k"ill -f vite \\"\' >> m.sh; bash m.sh',
        ),
        (
            "echo -e keeps a backslash quote the shell keeps",
            'echo -e \'p"k"il\\x6c -f vite \\"\' | sh',
        ),
        (
            "echo keeps a backslash quote the shell keeps",
            'echo \'p"k"il\\x6c -f vite \\"\' | sh',
        ),
        (
            "echo -E in a shell that prints the option and reads escapes",
            "echo -E 'x\\0012pkill -f vite' | sh",
        ),
        (
            "echo -n in a shell that prints the option and reads escapes",
            "echo -n 'x\\0012pkill -f vite' | sh",
        ),
        (
            "echo -e kill before a hex quote escape",
            "echo -e 'p\"k\"ill -f vite\\n\\x27' | sh",
        ),
        (
            "append of an echo -e with an escape to a printf that pairs across texts",
            'printf \'"\\x22\\"\\n\' > m.sh; echo -e \'p"k"il\\x6c -f vite \\"\' >> m.sh; bash m.sh',
        ),
        (
            "append of an echo -e with an escape to a printf with a backslash quote",
            'printf \'echo "\\"\\n\' > m.sh; echo -e \'p"k"il\\x6c -f vite \\"\' >> m.sh; bash m.sh',
        ),
        (
            "single-quoted kill word before a lone double quote",
            r"""echo 'p'"'"'k'"'"'ill -f vite\n"' | sh""",
        ),
        (
            "backslash in a kill word of a text that does not parse",
            r"""echo 'p\kill -f vite\n"' | sh""",
        ),
        (
            "backslash in a kill word of an echo -e text that does not parse",
            r"""echo -e 'p\kill -f vite\n"' | sh""",
        ),
        (
            "zsh echo -E append reads nothing",
            "printf '\"\\x22\\\"\\n' > m.sh; echo -E 'p\"k\"ill -f vite \\x27' >> m.sh; bash m.sh",
        ),
        (
            "zsh echo -E cat reads nothing",
            "printf '\"\\x22\\\"\\n' > m.sh; echo -E 'p\"k\"ill -f vite \\x27' > n.sh; cat m.sh n.sh | sh",
        ),
        (
            "zsh echo -nE append reads nothing",
            "printf '\"\\x22\\\"\\n' > m.sh; echo -nE 'p\"k\"ill -f vite \\x27' >> m.sh; bash m.sh",
        ),
        (
            "echo octal without a leading zero append",
            "printf '\"\\x22\\\"\\n' > m.sh; echo 'p\"k\"ill -f vite \\47' >> m.sh; bash m.sh",
        ),
        (
            "sh heredoc writes and runs a script",
            "sh <<'EOF'\nprintf 'echo \"\\\"\\n' > m.sh; echo 'p\"k\"il\\x6c -f vite' >> m.sh\nsh m.sh\nEOF",
        ),
        (
            "sh heredoc writes two scripts and cats them",
            "sh <<'EOF'\nprintf 'echo \"\\\"\\n' > m.sh; echo 'p\"k\"il\\x6c -f vite' > n.sh\ncat m.sh n.sh | sh\nEOF",
        ),
        (
            "echo -eE append, bash takes the last option",
            "printf 'echo \"\\\"\\n' > m.sh; echo -eE 'p\"k\"ill -f vite \\x27' >> m.sh; bash m.sh",
        ),
        (
            "echo -e -E append, bash takes the last option",
            "printf 'echo \"\\\"\\n' > m.sh; echo -e -E 'p\"k\"ill -f vite \\x27' >> m.sh; bash m.sh",
        ),
        (
            "echo -e octal without a leading zero append",
            "printf 'echo \"\\\"\\n' > m.sh; echo -e 'p\"k\"ill -f vite \\47' >> m.sh; bash m.sh",
        ),
        (
            "echo without -e whose escape bash keeps, append",
            "printf 'echo \"\\\"\\n' > m.sh; echo 'p\"k\"ill -f vite \\x27' >> m.sh; bash m.sh",
        ),
        (
            "plain echo append, the escape read in the zsh reading alone",
            "printf 'echo \\\"\\n' > m.sh; echo '\\x70kill -f vite # \"' >> m.sh; bash m.sh",
        ),
        (
            "echo -e piped to a shell, the text no shell reads is the only kill",
            "echo -e ''\"'\"'\\\\pkill -f vite\\47' | sh",
        ),
        (
            "echo -e append to a printf quote, the unread text is the only kill",
            "echo -e \"'pkill -f vite\" >> m.sh; printf '\\47\\n' >> m.sh; sh m.sh",
        ),
        (
            "macOS sh append, echo escapes read with a printf quote read",
            "printf 'echo \"\\\"\\n' > m.sh; echo '\\x70kill -f vite # \"' >> m.sh; sh m.sh",
        ),
        (
            "append of a plain echo to a printf with a backslash quote",
            'printf \'echo "\\"\\n\' > m.sh; echo \'p"k"ill -f vite \\"\' >> m.sh; bash m.sh',
        ),
        (
            "append of an echo with an escape to a printf that pairs across texts",
            'printf \'"\\x22\\"\\n\' > m.sh; echo \'p"k"il\\x6c -f vite \\"\' >> m.sh; bash m.sh',
        ),
        (
            "cat of a printf and an echo whose readings pair across texts",
            'printf \'"\\x22\\"\\n\' > m.sh; echo \'p"k"ill -f vite \\"\' > n.sh; cat m.sh n.sh | sh',
        ),
        (
            "cat of a printf and a heredoc whose readings pair across texts",
            "printf 'echo \"\\\"\\n' > m.sh; cat > q.sh <<'EOF'\np\"k\"ill -f vite\nEOF\ncat m.sh q.sh | sh",
        ),
        (
            "heredoc append to a printf whose readings pair across texts",
            "printf 'echo \"\\\"\\n' > m.sh; cat >> m.sh <<'EOF'\np\"k\"ill -f vite\nEOF\nbash m.sh",
        ),
        (
            "repeated echo text and a script, each under the scan budget alone",
            "echo '" + "\n".join([": " + "x" * 1000] * 180) + "' | sh; bash kills.sh",
        ),
        ("cat of two disk files past the scan limit", "cat a40.sh b40.sh | sh"),
        (
            "append to a disk file past the scan limit",
            "echo 'echo done' >> big.sh; bash big.sh",
        ),
        (
            "append to a disk file past the scan limit, sourced",
            "echo 'alias ll=ls' >> big.sh && source big.sh",
        ),
        (
            "many appends past the scan limit, then run",
            "echo hi > a.sh; " + ("printf '" + PAD + "' >> a.sh; ") * 100 + "bash a.sh",
        ),
        (
            "tee --append to a script on disk",
            "tee --append kills.sh <<'EOF'\necho done\nEOF\nbash kills.sh",
        ),
        (
            "tee combined option append",
            "tee -ia kills.sh <<'EOF'\necho done\nEOF\nbash kills.sh",
        ),
        (
            "append of a kill to a clean script",
            "echo 'pkill -f vite' >> clean.sh; bash clean.sh",
        ),
        ("printf grouping flag", 'printf "%\'s %\'s" pkill "-f vite" | sh'),
        (
            "printf escaped positional dollars",
            'printf "%2\\$s %1\\$s" "-f vite" pkill | sh',
        ),
        (
            "printf unknown percent before conversion",
            "printf '%! %s' pkill '-f vite' | sh",
        ),
        (
            "printf unknown percent after conversion",
            "printf '%s %!' pkill '-f vite' | sh",
        ),
        ("printf two grouping flags", "printf \"%''s %s %s\" kill -9 -1 | sh"),
        ("printf unmodelled conversion joins arguments", "printf '%z' kill -9 -1 | sh"),
        (
            "printf unclosed zero width",
            "printf '%" + "0" * 28 + "!' pkill | sh",
        ),
        (
            "state seen restored after the readings",
            'bash -c \'echo "echo safe > kills.sh; bash kills.sh" | sh; bash -c "bash kills.sh"\'',
        ),
        ("printf length modifier", "printf 'kill -9 %ld\\n' -1 | sh"),
        ("printf argument position", "printf '%1$s' 'pkill -f vite' | sh"),
        ("printf %d with leading zeros", "printf 'kill -9 %d\\n' -01 | sh"),
        ("printf %d with a decimal point", "printf 'kill -9 %d\\n' -1.0 | sh"),
        (
            "printf quote escape in the format",
            "env printf 'p\\\"\\\"kill -f vite\\n' | sh",
        ),
        (
            "printf width past the scan budget",
            "printf 'pkill -f vite\\n%600000s' x | sh",
        ),
        (
            "printf precision past the scan budget",
            "printf 'pkill -f vite\\n%.600000d' 1 | sh",
        ),
        (
            "printf width too big to build",
            "printf '%9223372036854775807s' x; bash kills.sh",
        ),
        ("append to a script on disk", "echo 'echo done' >> kills.sh; bash kills.sh"),
        ("append then shell stdin", "echo 'echo done' >> kills.sh; sh < kills.sh"),
        (
            "tee append to a script on disk",
            "tee -a kills.sh <<'EOF'\necho done\nEOF\nbash kills.sh",
        ),
        (
            "append after a written kill",
            "echo 'pkill -f vite' > w.sh; echo 'echo done' >> w.sh; bash w.sh",
        ),
        (
            "printf width argument before the kill line",
            "printf '%*s\\nkill -9 %s\\n' 1 x -1 | sh",
        ),
        (
            "printf argument position on its own line",
            "printf '%1$s\\n' 'pkill -f vite' | sh",
        ),
        ("printf %d with a hex number", "printf 'kill -9 %d\\n' -0x1 | sh"),
        (
            "wrapper alone piped to a shell, then a script",
            "command | sh; bash kills.sh",
        ),
        ("printf by path", "/usr/bin/printf '%s' 'pkill -f vite' | sh"),
        ("echo -E piped to a shell", "echo -E 'pkill -f vite' | sh"),
        (
            "printf octal escape without a leading zero",
            "printf '\\160kill -f vite\\n' | sh",
        ),
        ("printf octal newline", "printf 'echo hi\\12pkill -f vite\\n' | sh"),
        ("printf one-digit hex newline", "printf 'echo hi\\xapkill -f vite\\n' | sh"),
        ("printf flag on %s", "printf '%-s' 'pkill -f vite' | sh"),
        ("printf width argument taken in place", "printf 'kill -9 %*s\\n' 0 -1 | sh"),
        (
            "printf format without conversions and an argument",
            "printf 'pkill -f vite\\n' x | sh",
        ),
        (
            "printf format with the kill operand as an argument",
            "printf 'kill -9 %s\\n' -1 > k.sh; bash k.sh",
        ),
        ("echo -e written script", "echo -e 'pkill -f vite' > k.sh; bash k.sh"),
        ("echo -n written script", "echo -n 'pkill -f vite' > k.sh; bash k.sh"),
        ("printf %s piped to a shell", "printf '%s' 'pkill -f vite' | sh"),
        ("echo -e piped to a shell", "echo -e 'pkill -f vite' | sh"),
        ("echo -e unicode escape", "echo -e '\\u0070kill -f vite' > k.sh; bash k.sh"),
        (
            "echo -e code point past the Unicode range",
            "echo -e '\\U00110000pkill -f vite' > k.sh; bash k.sh",
        ),
        (
            "echo -e octal NUL inside a word",
            "echo -e 'pk\\000ill -f vite' > k.sh; bash k.sh",
        ),
        ("echo -e NUL inside a word", "echo -e 'pk\\x00ill -f vite' > k.sh; bash k.sh"),
        ("printf %d with a number", "printf 'kill -9 %d\\n' -1 > k.sh; bash k.sh"),
        ("printf %d with a word", "printf 'pkill -f vite %d\\n' x > k.sh; bash k.sh"),
        (
            "printf %d with a character constant",
            "printf 'pkill -f vite %d\\n' \"'x\" > k.sh; bash k.sh",
        ),
        ("echo -e hex escape", "echo -e '\\x70kill -f vite' > k.sh; bash k.sh"),
        ("echo -e octal escape", "echo -e '\\0160kill -f vite' > k.sh; bash k.sh"),
        (
            "printf escape in the format",
            "printf '\\x70kill -f vite\\n' > k.sh; bash k.sh",
        ),
        ("printf %b escape in an argument", "printf '%b' '\\x70kill -f vite' | sh"),
        ("printf format reused for each argument", "printf '%s ' kill -9 -1 | sh"),
        (
            "printf escape before a conversion",
            "printf '\\x70kill %s\\n' '-f vite' | sh",
        ),
        ("printf percent sign before the kill", "printf '%%\\nkill -9 %s\\n' -1 | sh"),
        ("printf width taken from an argument", "printf '%*s' 1 'pkill -f vite' | sh"),
        ("printf after the option terminator", "printf -- '%s' 'pkill -f vite' | sh"),
        (
            "printf conversion not modelled scans each argument",
            "printf '%a %s\\n' 1 'pkill -f vite' > k.sh; bash k.sh",
        ),
        (
            "tee file after a file",
            "tee out.txt -guard.sh <<'EOF'\npkill -f vite\nEOF\nbash ./-guard.sh",
        ),
        (
            "tee file named -",
            "tee - <<'EOF'\npkill -f vite\nEOF\nbash ./-",
        ),
        (
            "tee file named --",
            "tee -- -- <<'EOF'\npkill -f vite\nEOF\nbash ./--",
        ),
        (
            "cat dash file after a file piped to a shell",
            "cat /dev/null -dash.sh | sh",
        ),
        (
            "source after the option terminator",
            "source -- -dash.sh",
        ),
        (
            "dot after the option terminator",
            ". -- -dash.sh",
        ),
        (
            "echo written script read by cat into a shell",
            "echo 'pkill -f vite' > w.sh; cat w.sh | bash",
        ),
        (
            "echo written script as shell stdin",
            "echo 'pkill -f vite' > w.sh; bash < w.sh",
        ),
        (
            "tee written dash script read by cat",
            "tee -- -w.sh <<'EOF'\npkill -f vite\nEOF\ncat -- -w.sh | sh",
        ),
        (
            "wrapped cat piped to a shell",
            "command cat ./-dash.sh | sh",
        ),
        (
            "sudo cat piped to a shell",
            "sudo cat ./-dash.sh | sh",
        ),
        ("echo written script", "echo 'pkill -f vite' > k.sh && bash k.sh"),
        ("printf written script", "printf 'pkill -f vite\\n' > k.sh; sh k.sh"),
        ("appended echo script", "echo 'pkill -f vite' >> k.sh; bash k.sh"),
        ("stdin from a file", f"bash < {scripts}/kills.sh"),
        ("cat piped to a shell", f"cat {scripts}/kills.sh | bash"),
        ("cat after the option terminator piped to a shell", "cat -- -dash.sh | sh"),
        ("here-string to a shell", "bash <<< 'pkill -f vite'"),
        ("echo piped to a shell", "echo 'pkill -f vite' | sh"),
        ("script run by inline shell", f"bash -c 'bash {scripts}/kills.sh'"),
        ("relative script run by inline shell", "sh -c './kills.sh'"),
        ("script run by a script", f"bash {scripts}/outer.sh"),
        ("long option before the script", f"bash --norc {scripts}/kills.sh"),
        (
            "another long option before the script",
            f"bash --noprofile {scripts}/kills.sh",
        ),
        ("option value before the script", f"bash -o errexit {scripts}/kills.sh"),
        ("exported variable script", f"export S={scripts}; bash $S/kills.sh"),
        (
            "variable reassigned after the run",
            f"S={scripts}; bash $S/kills.sh; S=/nonexistent",
        ),
        ("pipeline kill in a script", f"bash {scripts}/pipe.sh"),
        ("killall -m in a script", f"bash {scripts}/killall.sh"),
        ("shebang names env bash", f"{scripts}/env_bash.sh"),
        ("node file run through bash", f"bash {scripts}/tool.js"),
        ("sourced library that only defines a kill", f"source {scripts}/lib.sh"),
        ("file named eval run by path", "./eval"),
        (
            "bare exec in an inline program",
            "bash -c 'exec 2>&1'; git push origin HEAD:main",
        ),
        ("bare env in an inline program", "bash -c env; git push origin HEAD:main"),
        (
            "bare exec piped to a shell",
            "echo 'exec 2>&1' | bash; git push origin HEAD:main",
        ),
        (
            "script with exec redirects then push",
            f"bash {scripts}/exec_log.sh; git push origin HEAD:main",
        ),
        (
            "script with sudo -v then push",
            f"bash {scripts}/sudo_v.sh; git push origin HEAD:main",
        ),
        (
            "this repo's heavy-slot then push",
            f"{ROOT}/heavy-slot count; git push origin HEAD:main",
        ),
        ("exec then a nested kill script", f"bash {scripts}/exec_nested.sh"),
        ("depth 3 run is followed", f"bash {scripts}/d1.sh"),
        ("kill inside the scan limit", f"bash {scripts}/mid.sh"),
        ("kill after a few heredocs", f"bash {scripts}/three.sh"),
        ("ampersand redirect script", "echo 'pkill -f vite' &> k.sh; bash k.sh"),
        ("printf escape script", "printf 'echo hi\\npkill -f vite' > k.sh; sh k.sh"),
        (
            "kill script behind two pipe stages",
            f"echo hi | cat {scripts}/kills.sh | bash",
        ),
        ("option group ending in o", f"bash -euo pipefail {scripts}/kills.sh"),
        ("option group ending in o with x", f"bash -euxo pipefail {scripts}/kills.sh"),
        ("env -S shebang names a shell", f"{scripts}/env_s.sh"),
        ("declare -x variable script", f"declare -x S={scripts}; bash $S/kills.sh"),
        ("readonly variable script", f"readonly S={scripts}; bash $S/kills.sh"),
        ("builtin source", f"builtin source {scripts}/kills.sh"),
        (
            "PWD names the written script",
            "cat > r.sh <<'EOF'\npkill -f vite\nEOF\nbash \"$PWD/r.sh\"",
        ),
        (
            "PWD assigned by the command",
            f"PWD={scripts}/..; bash $PWD/scripts/kills.sh",
        ),
        ("plus n runs the script", f"bash +n {scripts}/kills.sh"),
        ("PWD follows cd", f"cd {scripts} && bash ${{PWD}}/kills.sh"),
    ]:
        case(name, command, True, scripts)
    case("unparsable script then stash", f"bash {scripts}/apos.sh; git stash", True, wt)
    case("node file then stash", f"{scripts}/tool.js; git stash", True, wt)
    for name, command in [
        ("node shebang file run directly", f"{scripts}/tool.js"),
        ("syntax check of a written script", repro + "bash -n repro.sh"),
        (
            "stdin shell with a positional argument",
            "bash -s kills.sh <<'EOF'\necho hi\nEOF",
        ),
        ("eval is not a file lookup", "eval echo hi"),
        ("inline program named like a file", "bash -c kills.sh"),
        ("kill beyond the depth limit", f"bash {scripts}/e1.sh"),
        ("kill after many heredocs", f"bash {scripts}/seventy.sh"),
        ("inline program with a script name after it", "bash -c 'echo ok' kills.sh"),
        (
            "variable reassigned before the run",
            f"S=/nonexistent; bash $S/kills.sh; S={scripts}",
        ),
        ("binary file holding the words", f"bash {scripts}/bin.sh"),
        ("kill after the scan limit", f"bash {scripts}/late.sh"),
        ("FIFO as the script", f"bash {scripts}/fifo.sh"),
        ("killall of an own process in a script", f"bash {scripts}/killall_own.sh"),
        ("script that only echoes in a pipe", "echo ok | bash"),
        ("piped file without a kill", f"cat {scripts}/clean.sh | bash"),
        (
            "tee file after a file, never run",
            "tee out.txt -notes.md <<'EOF'\npkill -f vite\nEOF",
        ),
        (
            "cat of a missing dash file",
            "cat -- -clean.sh | sh",
        ),
        ("wrapper alone piped to a shell", "command | sh"),
        (
            "cat of standard input",
            "cd dash && cat - | sh",
        ),
        (
            "written clean script read by cat",
            "echo hi > w.sh; cat w.sh | bash",
        ),
        (
            "pipe into tee after the option terminator",
            "git log --oneline | tee -- log.txt",
        ),
        (
            "printf %s of a harmless line, run",
            "printf '%s\\n' 'echo hi' > k.sh; bash k.sh",
        ),
        ("echo -e of a kill, never run", "echo -e 'pkill -f vite' > notes.md"),
        ("overwrite of a script on disk", "echo 'echo hi' > kills.sh; bash kills.sh"),
        ("echo a lone dash", "echo - hi | sh"),
        (
            "echo of a backslash quote inside a kill word, piped to a shell",
            "echo 'p\\\"kill -f vite' | sh",
        ),
        (
            "echo of an unterminated quote piped to a shell",
            "echo 'echo \"unterminated' | sh",
        ),
        (
            "echo of an apostrophe piped to a shell",
            "echo 'say \"it'\"'\"'s\" done' | sh",
        ),
        (
            "echo of a sentence with an apostrophe piped to a shell",
            'echo "don\'t wait for me" | sh',
        ),
        ("wide printf padding piped to a shell", "printf 'echo %70000s\\n' x | sh"),
        (
            "big printf to a file, never run",
            "printf '" + PAD + "%s\\n' " + "x " * 70 + "> big.txt",
        ),
        (
            "big printf to a pipe that is no shell",
            "printf '" + PAD + "%s\\n' " + "x " * 70 + "| wc -l",
        ),
        (
            "big printf written, script run is another file",
            "printf '" + PAD + "%s\\n' " + "x " * 70 + "> big.txt; bash clean.sh",
        ),
        (
            "printf width and precision",
            "printf '%-10s|%5d|%.2s\\n' a 3 xyz > table.txt",
        ),
        ("printf separator byte inside a word", "printf 'p\\036kill -f vite\\n' | sh"),
        ("printf separator byte inside a pid", "printf 'kill -9 \\036-1\\n' | sh"),
        ("unit separator byte in a script on disk", "bash us.sh"),
        ("cat of a file of 64 KB or more", "cat big.sh | sh"),
        ("printf group separator byte, run", "printf 'echo \\035\\n' | sh"),
        ("echo separator byte after a kill word", "echo 'pkill\x1e -f vite' | sh"),
        (
            "printf argument separator byte after a kill word",
            "printf '%f\\n' 'pkill\x1e -f vite' | sh",
        ),
        ("printf unit separator byte, run", "printf 'echo \\037\\n' | sh"),
        (
            "printf unit separator byte in a written script, run",
            "printf 'echo \\037\\n' > a.sh; bash a.sh",
        ),
        (
            "tee after the option terminator, never run",
            "tee -- -notes.md <<'EOF'\npkill -f vite\nEOF",
        ),
    ]:
        case(name, command, False, scripts)
    zeros = "0" * 65536
    for command, deny in (
        ("printf '%" + zeros + "!'", False),
        ("printf '%" + zeros + "!' > out.txt; pkill -f vite", True),
        ("printf '%" + zeros + "!' | wc -c", False),
        ("echo '%" + zeros + "!' > out.txt", False),
    ):
        start = time.perf_counter()
        result = hook(command, scripts, timeout=1)
        elapsed = time.perf_counter() - start
        assert result.returncode == (2 if deny else 0), (result, elapsed)
        assert elapsed < 1, f"64 KB zero format took {elapsed:.3f}s"
        print(f"64 KB zero format {command[:12]!r}: {elapsed:.3f}s")
    start = time.perf_counter()
    result = hook(" " * 128000 + "git push origin main", scripts, timeout=1)
    elapsed = time.perf_counter() - start
    assert result.returncode == 2, (result, elapsed)
    assert elapsed < 1, f"128000 blanks took {elapsed:.3f}s"
    print(f"128000 blanks before a push: {elapsed:.3f}s")
    for name, word in (("v", "v" * 128000), ("vp", "vp" * 64000)):
        start = time.perf_counter()
        result = hook(f"command -{word}$; git push origin main", scripts, timeout=1)
        elapsed = time.perf_counter() - start
        assert result.returncode == 2, (result, elapsed)
        assert elapsed < 1, f"128000 option letters {name} took {elapsed:.3f}s"
        print(f"128000 option letters {name} before a push: {elapsed:.3f}s")
    started = time.perf_counter()
    log_path = Path.home() / ".claude/hooks/guard-bash.log"
    assert not guard.evaluate(f"bash {scripts}/fan.sh", repo)
    cat_many = "cat " + f"{scripts}/big.sh " * 100 + "| bash"
    assert not guard.evaluate(cat_many, repo)
    for command in (
        "cat a40.sh b40.sh | sh",
        "echo 'echo done' >> big.sh; bash big.sh",
        "echo 'alias ll=ls' >> big.sh && source big.sh",
    ):
        reason = guard.evaluate(command, scripts)
        assert reason and "script text" in reason and "printf" not in reason, reason
    reason = guard.evaluate("printf '" + PAD + "%s\\n' " + "x " * 70 + "| sh", scripts)
    assert reason and "printf output" in reason, reason
    assert list(guard.echoed_text(["echo", "\\x70"]).readings) == [
        "\\x70",
        "\\x70",
        "p",
        "\\x70",
        "p",
    ], "echo without -e: slot 1 keeps the text, slots 2 and 4 read it"
    for options in (["-e"], ["-Ee"], ["-n", "-e"], ["-e", "-n"]):
        slots = guard.echoed_text(["echo", *options, "\\x70"]).readings
        assert slots[1] == "p", f"echo {options}: slot 1 reads"
    slots = guard.echoed_text(["echo", "-E", "\\x70"]).readings
    assert slots[1:3] == ("\\x70", "p"), "echo -E: slot 1 keeps the text, slot 2 reads"
    for options in (["-eE"], ["-e", "-E"], ["-E", "-e", "-E"]):
        assert guard.echoed_text(["echo", *options, "\\x70"]).readings[1] == "\\x70", (
            f"echo {options}: the final -E disables bash escapes"
        )
    assert guard.echoed_text(["echo", "-n", "\\x70"]).readings[4] == "-n p", (
        "echo slot 4 keeps the option words"
    )
    assert list(guard.echoed_text(["printf", 'x\\"y']).readings) == [
        'x\\"y',
        'x"y',
        'x\\"y',
        'x\\"y',
        'x"y',
        'x\\"y',
    ], "printf: slots 1 and 4 read the quote, slot 3 keeps the text"
    assert guard.printf_text("%%", [])[1]
    assert guard.printf_text("%'s", ["echo hi"]) == ("echo hi", True)
    assert guard.printf_text("%''s", ["echo hi"]) == ("echo hi", True)
    assert not guard.printf_text("%! %s", ["echo hi"])[1]
    assert not guard.printf_text("%s %!", ["echo hi"])[1]
    pairs = guard.joined(
        [guard.ScriptText(["a", "b", "c", "d"]), guard.ScriptText(["x", "y", "z"])]
    ).readings
    assert pairs == ("a\nx", "b\ny", "c\nz", "d\nx"), "a missing slot reads the first"
    long_piece = guard.capped(guard.ScriptText(["a" * 200000, "b"]))
    assert long_piece.readings == ("a" * guard.SCRIPT_SCAN_LIMIT, "b")
    assert long_piece.too_large == guard.SCRIPT_TOO_LARGE_REASON
    with patch.object(guard, "printf_value", wraps=guard.printf_value) as conversion:
        text, modelled = guard.printf_text("%1$s" * 10000, ["x" * 60000])
    assert conversion.call_count == 2, "printf must stop converting at the size limit"
    assert text == "x" * guard.SCRIPT_SCAN_LIMIT and not modelled
    assert text.too_large == guard.PRINTF_TOO_LARGE_REASON
    text, modelled = guard.printf_text("x" * guard.SCRIPT_SCAN_LIMIT, [])
    assert len(text) == guard.SCRIPT_SCAN_LIMIT and modelled
    for format_ in (
        "x" * (guard.SCRIPT_SCAN_LIMIT + 1),
        "x" * guard.SCRIPT_SCAN_LIMIT + "%%",
    ):
        text, modelled = guard.printf_text(format_, [])
        assert text == "x" * guard.SCRIPT_SCAN_LIMIT and not modelled
        assert text.too_large == guard.PRINTF_TOO_LARGE_REASON
    assert time.perf_counter() - started < 3, "scan budget is not applied"
    assert not log_path.exists() or "scan-budget" not in log_path.read_text()
    for number in range(40):
        (scripts / f"bulk{number}.sh").write_text("echo ok\n" * 7500)
    bulk = "; ".join(f"bash {scripts}/bulk{number}.sh" for number in range(40))
    assert "budget exhausted" in guard.evaluate(bulk, repo)
    assert "scan-budget" in log_path.read_text()
    harmless = "echo -e '" + "echo \\x41\\x42 " * 4000 + "'"
    exhausted = (harmless + " | sh; ") * 3
    reason = guard.evaluate(exhausted + "printf 'pkill -f vite\\n' | sh", scripts)
    assert reason and "budget exhausted" in reason, reason
    assert not guard.evaluate((harmless + " >> run.log; ") * 3, scripts)
    assert not guard.evaluate((harmless + " | wc -c; ") * 3, scripts)
    with patch.object(guard.time, "monotonic", side_effect=[0, 6]):
        reason = guard.evaluate("echo hi | sh", scripts)
    assert reason and "budget exhausted" in reason, reason
    log_before = log_path.read_text()
    saved_runs = guard.runs

    def failing_runs(*args, **kwargs):
        raise RuntimeError("forced scan error")

    guard.runs = failing_runs
    try:
        assert guard.evaluate("bash x.sh; git push origin HEAD:main", repo)
    finally:
        guard.runs = saved_runs
    log_new = log_path.read_text()[len(log_before) :]
    assert "scan-error: RuntimeError: forced scan error" in log_new, log_new

    def logged(command, cwd=repo):
        before = log_path.read_text() if log_path.exists() else ""
        result = guard.evaluate(command, cwd)
        return result, log_path.read_text()[len(before) :]

    bulk_five = "; ".join(f"bash {scripts}/bulk{number}.sh" for number in range(5))
    result, new_log = logged(f"{bulk_five}; bash {scripts}/kills.sh")
    assert result and "scan-budget" not in new_log, "scan budget is below 512 KB"

    nest = scripts / "nest"
    for folder in ("vs", "seen/a", "seen/b"):
        (nest / folder).mkdir(parents=True)
    for name, text in {
        "vs/p.sh": "bash r.sh\n",
        "vs/r.sh": "bash X.sh\n",
        "vs/q.sh": "bash X.sh\n",
        "vs/X.sh": "bash K.sh\n",
        "vs/K.sh": "pkill -f vite\n",
        "vs/two.sh": 'echo ok | bash\necho "bash K.sh" | bash\n',
        "seen/outer.sh": "cd a\nbash ../lib.sh\ncd ../b\nbash ../lib.sh\n",
        "seen/lib.sh": "bash ./step.sh\n",
        "seen/a/step.sh": "echo ok\n",
        "seen/b/step.sh": "pkill -f vite\n",
    }.items():
        (nest / name).write_text(text)
    for name, command, cwd in [
        ("file seen at another depth", "bash p.sh; bash q.sh", nest / "vs"),
        ("inline code seen twice", "bash two.sh", nest / "vs"),
        ("file seen from another folder", "bash outer.sh", nest / "seen"),
    ]:
        case(name, command, True, cwd)

    read_files = []
    saved_text = guard.script_text

    def counting_text(path):
        read_files.append(path)
        return saved_text(path)

    guard.script_text = counting_text
    try:
        assert not guard.evaluate(cat_many, repo)
    finally:
        guard.script_text = saved_text
    assert len(read_files) == 1, f"cat read {len(read_files)} files past the limit"

    for name, command in [
        ("option group with o first", f"bash -oe pipefail {scripts}/kills.sh"),
        ("option group with O last", f"bash -eO extglob {scripts}/kills.sh"),
        ("option group with O first", f"bash -Oe extglob {scripts}/kills.sh"),
        ("single dash norc", "bash -norc kills.sh"),
        ("single dash posix", "bash -posix kills.sh"),
        ("single dash login", "bash -login kills.sh"),
        ("single dash noediting", "bash -noediting kills.sh"),
        ("single dash noprofile", "bash -noprofile kills.sh"),
        ("single dash restricted", "bash -restricted kills.sh"),
        ("two o options in one group", "bash -oO pipefail extglob kills.sh"),
        ("zsh option name attached", "zsh -oerrexit kills.sh"),
        ("ksh option name attached", "ksh -oerrexit kills.sh"),
        ("zsh path option name attached", "/bin/zsh -oerrexit kills.sh"),
        ("zsh option name without a space", "zsh -oposixargzero kills.sh"),
        ("zsh -oposixargzero then inline pi", "zsh -oposixargzero -c 'pi -p hello'"),
        ("zsh -eO then inline pi", "zsh -eO -c 'pi -p hello'"),
        ("zsh -O then inline pi", "zsh -O -c 'pi -p hello'"),
        ("zsh -restricted then inline pi", "zsh -restricted 'pi -p hello'"),
        ("zsh -rcfile then inline pi", "zsh -rcfile -c 'pi -p hello'"),
        ("ksh -rcfile then inline pi", "ksh -rcfile -c 'pi -p hello'"),
        ("zsh attached option then inline pi", "zsh -opipefail -c 'pi -p hello'"),
        ("ksh attached option then inline pi", "ksh -opipefail -c 'pi -p hello'"),
        ("zsh attached option then inline kill", "zsh -eopipefail -c 'pkill -f vite'"),
        ("zsh option name in the next word", "zsh -o pipefail -c 'pi -p hello'"),
        ("ksh grouped option then its name", "ksh -eo errexit kills.sh"),
        ("zsh option name with n", "zsh -onounset kills.sh"),
        ("zsh option name with c and n", "zsh -onoclobber kills.sh"),
        ("ksh option name with c, then a program", "ksh -onoclobber 'pi -p hello'"),
        ("ksh runs a missing script as a program", "ksh 'pi -p hello'"),
        ("ksh -o value is optional before -c", "ksh -eo -c 'pi -p hello'"),
        (
            "ksh -o value is optional before -s",
            "ksh -eo -s arg <<'EOF'\npi -p hello\nEOF",
        ),
        ("ksh -o value is optional before +e", "ksh -o +e -c 'pi -p hello'"),
        ("ksh lone -o before -s", "ksh -o -s arg <<'EOF'\npi -p hello\nEOF"),
        (
            "ksh heredoc after an option name",
            "ksh -o errexit <<'EOF'\npkill -f vite\nEOF",
        ),
        ("bash two letters then option name", "bash -oe pipefail -c 'pi -p hello'"),
        ("sh two letters then option name", "sh -oe pipefail -c 'pi -p hello'"),
        ("single dash rcfile", "bash -rcfile /dev/null kills.sh"),
        ("single dash init-file", "bash -init-file /dev/null kills.sh"),
        ("single dash verbose", "bash -verbose kills.sh"),
        ("single dash protected", "bash -protected kills.sh"),
        ("verbose inline kill", "bash -verbose -c 'pkill -f vite'"),
        ("verbose inline kill in sh", "sh -verbose -c 'pkill -f vite'"),
        ("verbose inline pi start", "bash -verbose -c 'pi -p hello'"),
        ("verbose inline pi start in sh", "sh -verbose -c 'pi -p hello'"),
        ("typeset variable", f"typeset -x S={scripts}; bash $S/kills.sh"),
    ]:
        case(name, command, True, scripts)

    assert guard.SCRIPT_SCAN_SECONDS == 5
    saved_seconds = guard.SCRIPT_SCAN_SECONDS
    guard.SCRIPT_SCAN_SECONDS = -1
    try:
        result, new_log = logged(f"bash {scripts}/kills.sh", repo)
        assert result and "budget exhausted" in result and "scan-budget" in new_log, (
            result,
            new_log,
        )
        result, new_log = logged(f"bash {scripts}/kills.sh; git stash", wt)
        assert result and "scan-budget" in new_log, (result, new_log)
    finally:
        guard.SCRIPT_SCAN_SECONDS = saved_seconds

    guard.runs = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("forced"))
    try:
        result, new_log = logged("bash x.sh; git push origin HEAD:main")
    finally:
        guard.runs = saved_runs
    assert result and "scan-error: ValueError: forced" in new_log, (result, new_log)
    saved_entries = guard.command_entries

    def failing_entries(*args, **kwargs):
        raise RuntimeError("forced parse error")

    guard.command_entries = failing_entries
    try:
        result, new_log = logged("git push origin HEAD:main")
    finally:
        guard.command_entries = saved_entries
    assert result and "scan-error: RuntimeError: forced parse error" in new_log, (
        result,
        new_log,
    )
    result, new_log = logged("bash ~a\0b.sh; bash x.sh")
    assert "scan-error: ValueError" in new_log, new_log
    before = log_path.read_text()
    assert guard.guard_script_kill('echo "unterminated', repo, set()) is None
    assert log_path.read_text() == before, "unparsable command text was logged"
    marker = "m" * 256
    assert guard.bounded(f"echo ok\ncat <<{marker}\nx\n") == (
        f"echo ok\ncat <<{marker}\nx\n"
    )
    for before, operator in (
        ("cat ", "<<"),
        ("x=$(cat ", "<<-"),
        ("cat ", "<< '"),
        ("cat ", '<<"'),
    ):
        cut = guard.bounded(f"echo ok\n{before}{operator}{marker}m\npkill -f vite\n")
        assert cut == f"echo ok\n{before}", (before, operator, cut)
    pieces = "\\\n".join(["m" * 100, "m" * 100, "m" * 57])
    assert guard.bounded(f"echo ok\ncat <<{pieces}\npkill -f vite\n") == "echo ok\ncat "
    operators = guard.bounded("cat <\\\n<x\n" * 70)
    assert operators.count("<<") == guard.SCRIPT_HEREDOC_LIMIT, operators.count("<<")
    (scripts / "marker.sh").write_text(f"pkill -f vite\ncat <<{marker}m\nx\n")
    case("kill before a long marker", f"bash {scripts}/marker.sh", True)
    started = time.perf_counter()
    assert not guard.evaluate(f"bash {big}", repo)
    assert not guard.evaluate(f"bash {many}", repo)
    assert time.perf_counter() - started < 1, "script scan is slow"
    saved_tmp = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = str(scripts.parent)
    try:
        assert guard.evaluate("bash $TMPDIR/scripts/kills.sh", repo)
    finally:
        os.environ.pop("TMPDIR") if saved_tmp is None else os.environ.update(
            TMPDIR=saved_tmp
        )
    home = scripts / "home"
    home.mkdir()
    (home / "kills.sh").write_text("pkill -f vite\n")
    saved_home = os.environ["HOME"]
    os.environ["HOME"] = str(home)
    try:
        for text in ("bash $HOME/kills.sh", "bash ${HOME}/kills.sh", "bash ~/kills.sh"):
            assert guard.evaluate(text, repo), text
    finally:
        os.environ["HOME"] = saved_home
    case("pgrep full kill", "pgrep --full rails | xargs kill", True)
    case("ps grep kill", "ps aux | grep vite | awk '{print $2}' | xargs kill", True)
    for name, command in [
        ("substitution pgrep kill", "kill $(pgrep -f vite)"),
        ("backtick pgrep kill", "kill -9 `pgrep -f rails`"),
        ("while pgrep kill", "pgrep -f vite | while read p; do kill $p; done"),
        ("bracket grep kill", "ps aux | grep [v]ite | awk '{print $2}' | xargs kill"),
        ("awk ps kill", "ps -ef | awk '/vite/ {print $2}' | xargs kill"),
        ("substitution ps kill", "kill $(ps aux | grep vite | awk '{print $2}')"),
    ]:
        case(name, command, True)
    for name, command in [
        ("kill -TERM -1", "kill -TERM -1"),
        ("kill -9 -1", "kill -9 -1"),
        ("kill -KILL -1", "kill -KILL -1"),
        ("kill -SIGTERM -1", "kill -SIGTERM -1"),
        ("kill -s TERM -1", "kill -s TERM -1"),
        ("kill -n 9 -1", "kill -n 9 -1"),
        ("kill -- -1", "kill -- -1"),
        ("kill -9 -- -1", "kill -9 -- -1"),
        ("kill with a pid then -1", "kill -TERM 1234 -1"),
        ("kill by path -1", "/bin/kill -TERM -1"),
        ("kill -1 on every process", "kill -1 -1"),
        ("inline bash kill -1", "bash -c 'kill -9 -1'"),
        ("sudo kill -1", "sudo kill -9 -1"),
        ("written script kill -1", "cat > k.sh <<'EOF'\nkill -TERM -1\nEOF\nbash k.sh"),
        ("kill -1 then another pid", "kill -9 -1 1234"),
        ("kill -1 in an if block", "if true; then kill -9 -1; fi"),
        ("kill -1 in a substitution", "x=$(kill -9 -1)"),
    ]:
        case(name, command, True)
    for name, command in [
        ("kill signal 1 to a pid", "kill -1 1234"),
        ("kill -HUP pid", "kill -HUP 1234"),
        ("kill -TERM pid", "kill -TERM 1234"),
        ("kill -l", "kill -l"),
        ("kill job", "kill %1"),
        ("kill -1 alone", "kill -1"),
        ("kill -s takes -1 as its signal", "kill -s -1 1234"),
        ("kill -n takes -1 as its signal", "kill -n -1 1234"),
        ("ls -a -1 is not a kill", "ls -a -1"),
        ("kill -1 text not run", "echo 'kill -9 -1' >| notes.md"),
        ("zsh does not run a missing script as text", "zsh -onoclobber 'pi -p hello'"),
        ("zsh -o takes -c as its value", "zsh -eo -c 'pi -p hello'"),
        ("bash -o takes -c as its value", "bash -o -c 'pi -p hello'"),
    ]:
        case(name, command)
    case("killall regex vite", 'killall -m "vit.*"', True)
    case("killall bundle", "killall bundle", True)
    case("killall regex rails", 'killall -m "rail.*"', True)
    case("killall clustered regex rails", 'killall -qm "rail.*"', True)
    for branch in ("feature", "fix-main-menu", "domain"):
        case("allow push " + branch, "git push -u origin " + branch)
    case("force feature", "git push --force-with-lease origin HEAD:feature")
    case("unresolved bare push", "git push origin", cwd=isolated)
    case("chain cd feature", f"cd {wt} && git push -u origin feature", cwd=root)
    case(
        "comment attribution",
        'gh pr comment 1 --body "*(Generated using Claude Code)*"',
    )
    case("issue comment", 'gh issue comment 1 --body "🤖 Generated"')
    case(
        "comment then PR edit",
        'gh pr comment 36 --body "x *(Generated using Claude Code)*" && gh pr edit 36 --add-label ready',
    )
    case(
        "commit then comment",
        'git commit -m "fix: x" && gh pr comment 1 --body "y *(Generated using Claude Code)*"',
    )
    case(
        "API issue collection",
        'gh api repos/a/b/issues -f title="x" -f body="Generated with Claude Code"',
    )
    case("issue create", 'gh issue create --body "Generated with Claude Code"')
    case(
        "review API",
        'gh api repos/a/b/pulls/1/comments -f body="Generated with Claude Code"',
    )
    case(
        "API GET", 'gh api repos/a/b/pulls -X GET -f body="Generated with Claude Code"'
    )
    case("plain commit", 'git commit -m "Fix main menu"')
    case("plain file", f"git commit -F {clean}")
    case("plain PR", 'gh pr create --title "Fix" --body "Details"')
    case("plain heredoc", "git commit -F - <<'EOF'\nA plain commit\nEOF")
    case(
        "plain substitution heredoc",
        "git commit -m \"$(cat <<'EOF'\nA plain commit\nEOF\n)\"",
    )
    case("stash list worktree", "git stash list", cwd=wt)
    case("stash show worktree", "git stash show stash@{0}", cwd=wt)
    case("stash root", "git stash push", cwd=isolated)
    case("normal fetch", "git fetch origin", cwd=wt)
    case("isolated depth", "git fetch --depth=2 origin", cwd=isolated)
    case("PID kill", "kill -TERM 12345")
    case("PID check", "kill -0 12345")
    case("pidfile kill", 'kill "$(cat tmp/pids/server-1.pid)"')
    case(
        "pidfile liveness check",
        "pid=$(cat tmp/pids/vite.pid); ps -p $pid && kill -TERM $pid",
    )
    case(
        "quoted pidfile liveness check",
        'ps -p "$(cat tmp/pids/dev-instance.pid)" && kill -TERM "$(cat tmp/pids/dev-instance.pid)"',
    )
    case(
        "pidfile command liveness check",
        "PID=$(cat tmp/pids/puma.pid); ps -p $PID -o command= ; kill $PID",
    )
    case("pgrep process name", "pgrep ruby")
    case("pgrep -fl listing only", 'pgrep -fl "review-prs/scripts/prs.py watch"')
    case("pgrep -f count only", "pgrep -f bin/dev-server | wc -l")
    case("PID kill before pgrep full", "kill -TERM 12345; pgrep -f rails")
    case("nonserver killall", "killall ExampleScratchApp")
    case("normal status", "git status")
    case("ps grep kill count", "ps aux | grep node | grep -c kill")
    case(
        "quoted broad kill text",
        "printf 'ps aux | grep vite | xargs kill\\n' | grep vite",
    )
    case(
        "multiple quoted broad kill text",
        "printf '%s\\n' 'ps aux | grep vite | xargs kill' | grep vite",
    )
    case(
        "substitution broad kill",
        "printf '%s\\n' \"$(pgrep -f vite)\" | xargs kill",
        True,
    )
    case("webapp git grep", "git grep needle", True, webapp)
    case("webapp git -C grep", f"git -C {webapp} grep needle", True, root)
    case("webapp cd grep", f"cd {webapp} && git grep needle", True, root)
    case("webapp cd terminator grep", f"cd -- {webapp} && git grep needle", True, root)
    case("webapp cd physical grep", f"cd -P {webapp} && git grep needle", True, root)
    case(
        "failed conditional cd webapp grep",
        f"false && cd {other}; git grep needle",
        True,
        webapp,
    )
    case(
        "skipped conditional cd webapp grep",
        f"true || cd {other}; git grep needle",
        True,
        webapp,
    )
    case("failed cd webapp grep", "cd /missing || git grep needle", True, webapp)
    case(
        "webapp cat-file batch tree",
        "git ls-tree -r HEAD | git cat-file --batch",
        True,
        webapp,
    )
    case(
        "webapp continued bundled cat-file tree",
        "git ls-tree \\\n-rt HEAD | git cat-file --batch",
        True,
        webapp,
    )
    case(
        "webapp separate tree and cat-file",
        "git ls-tree -r HEAD >/tmp/tree; git cat-file --batch </tmp/objects",
        cwd=webapp,
    )
    case(
        "webapp cat-file batch-check tree",
        "git ls-tree --full-tree -r HEAD | git cat-file --batch-check",
        True,
        webapp,
    )
    case(
        "webapp formatted cat-file tree",
        "git ls-tree -r HEAD | git cat-file '--batch-check=%(objectname)'",
        True,
        webapp,
    )
    case(
        "webapp clustered recursive cat-file tree",
        "git ls-tree -rz HEAD | git cat-file --batch",
        True,
        webapp,
    )
    case(
        "webapp batch all objects",
        "git cat-file --batch-all-objects --batch-check",
        True,
        webapp,
    )
    case(
        "webapp all-history cat-file",
        "git rev-list --objects --all | git cat-file --batch-check",
        True,
        webapp,
    )
    case(
        "webapp rev-list objects head",
        "git rev-list --objects HEAD | git cat-file --batch-check",
        True,
        webapp,
    )
    case(
        "webapp rev-list objects branches",
        "git rev-list --objects --branches | git cat-file --batch-check",
        True,
        webapp,
    )
    case(
        "webapp ls-files batch",
        "git ls-files --format='%(objectname)' | git cat-file --batch",
        True,
        webapp,
    )
    case(
        "webapp process-substitution history batch",
        "git cat-file --batch-check < <(git rev-list --objects --all)",
        True,
        webapp,
    )
    case(
        "webapp nonrecursive formatted root tree",
        "git ls-tree --full-tree --format='%(objectname)' HEAD | git cat-file --batch-check",
        cwd=webapp,
    )
    case(
        "webapp config-spoofed grep",
        "git -c remote.origin.url=https://github.com/combinaut/other.git grep needle",
        True,
        webapp,
    )
    case(
        "webapp git -C cat-file tree",
        f"git -C {webapp} ls-tree -r HEAD | git -C {webapp} cat-file --batch",
        True,
        root,
    )
    case(
        "webapp git dir batch all objects",
        f"git -C {webapp}/.git cat-file --batch-all-objects --batch",
        True,
        root,
    )
    case("webapp git dir grep", f"cd {webapp}/.git && git grep needle", True, root)
    case(
        "webapp git dir with an outside work tree",
        f"git --git-dir={webapp}/.git --work-tree={other} cat-file --batch-all-objects --batch",
        True,
        root,
    )
    case(
        "other git dir with the webapp work tree",
        f"git --git-dir={other}/.git --work-tree={webapp} grep needle",
        cwd=root,
    )
    case("separate git dir checkout grep", "git grep needle", True, separate)
    case(
        "separate git dir with an outside work tree",
        f"git --git-dir={root}/separate.git --work-tree={other} cat-file --batch-all-objects --batch",
        True,
        root,
    )
    case("submodule grep", "git grep needle", True, superproject / "module")
    case(
        "submodule git dir with an outside work tree",
        f"git --git-dir={superproject}/.git/modules/module --work-tree={other} grep needle",
        True,
        root,
    )
    case("non-webapp grep", "git grep needle", cwd=other)
    case("leave webapp before grep", f"cd {other} && git grep needle", cwd=webapp)
    case(
        "successful and cd leaves webapp",
        f"true && cd {other}; git grep needle",
        cwd=webapp,
    )
    case(
        "successful or cd leaves webapp",
        f"false || cd {other}; git grep needle",
        cwd=webapp,
    )
    case("plain cat-file", "git cat-file -p HEAD", cwd=webapp)
    for name, command in [
        ("pgrep kill semicolon", "pgrep -f rails | xargs kill; true"),
        ("pgrep kill subshell", "(pgrep -f rails | xargs kill)"),
        (
            "ps grep kill semicolon",
            "ps aux | grep vite | awk '{print $2}' | xargs kill; true",
        ),
        ("BSD ps ax kill", "ps ax | grep vite | awk '{print $1}' | xargs kill"),
        ("BSD ps x kill", "ps x | grep node | awk '{print $1}' | xargs kill"),
        ("user ps kill", "ps -u \"$USER\" | grep vite | awk '{print $2}' | xargs kill"),
        (
            "BSD ps axo kill",
            "ps axo pid,comm | grep puma | awk '{print $1}' | xargs kill -9",
        ),
        (
            "for ps kill",
            "for p in $(ps ax | grep vite | awk '{print $1}'); do kill $p; done",
        ),
        (
            "quoted substitution ps kill",
            "kill \"$(ps aux | grep vite | awk '{print $2}')\"",
        ),
        ("pgrep name kill", "pgrep puma | xargs kill"),
        ("pgrep exact kill", "pgrep -x node | xargs kill"),
        ("shell string broad kill", "bash -c 'pgrep -f vite | xargs kill'"),
        ("shell heredoc broad kill", "bash <<'EOF'\npgrep -f vite | xargs kill\nEOF"),
        (
            "double-quoted comment substitution kill",
            'gh pr comment 1 --body "$(pgrep -f vite | xargs kill)"',
        ),
        ("shell string substitution kill", 'sh -c "kill \\$(pgrep -f vite)"'),
        ("eval broad kill", "eval 'pgrep -f vite | xargs kill'"),
        ("xargs shell program kill", "pgrep -f vite | xargs sh -c 'kill $0'"),
        ("login shell program kill", "bash -lc 'pgrep -f vite | xargs kill'"),
        (
            "stdin shell heredoc kill",
            "bash -s <<'EOF'\npgrep -f vite | xargs kill\nEOF",
        ),
        (
            "self-referencing reassignment kill",
            'pids=$(pgrep -f vite); pids="$pids"; kill $pids',
        ),
        (
            "conditional reassignment kill",
            "pids=$(pgrep -f vite); false && pids=$(cat tmp/pids/vite.pid); kill $pids",
        ),
        ("apostrophe comment then push", "# Don't run this\ngit push origin HEAD:main"),
        (
            "apostrophe comment then attribution",
            '# it\'s fine\ngit commit -m "Co-Authored-By: Claude"',
        ),
        ("quote comment then pkill", '# say "hi\npkill -f vite'),
        ("mid-word hash then push", "x=a#b; git push origin HEAD:main"),
        (
            "prefix assignment keeps listing variable",
            "pids=$(pgrep -f vite); pids=12345 true; kill -TERM $pids",
        ),
        (
            "subshell assignment keeps listing variable",
            "pids=$(pgrep -f vite); (pids=12345); kill -TERM $pids",
        ),
        (
            "piped assignment keeps listing variable",
            "pids=$(pgrep -f vite); pids=12345 | cat; kill -TERM $pids",
        ),
        ("pidfile-looking pattern after --", "pgrep -f -- '-F|node' | xargs kill"),
        (
            "long pidfile-looking pattern after --",
            "pgrep -f -- '--pidfile=x|node' | xargs kill",
        ),
        ("xargs kill with explicit PID", "pgrep -f node | xargs kill -TERM 12345"),
        (
            "xargs batched kill with explicit PID",
            "pgrep -f node | xargs -n 20 kill -TERM 12345",
        ),
        (
            "xargs path kill with explicit PID",
            "pgrep -f node | xargs /bin/kill -TERM 12345",
        ),
        ("redirect before xargs kill", "pgrep -f node | xargs 2>/dev/null kill -TERM"),
        ("redirect before xargs", "pgrep -f node | 2>/dev/null xargs kill -TERM"),
        ("redirect before pgrep", "2>/dev/null pgrep -f node | xargs kill -TERM"),
        (
            "redirect after xargs option with explicit PID",
            "pgrep -f node | xargs -r 2>/dev/null kill -TERM 12345",
        ),
        (
            "redirect after xargs env",
            "pgrep -f node | xargs -r env 2>/dev/null kill -TERM",
        ),
        (
            "substitution in redirect target kill",
            'echo hi > "$(pgrep -f vite | xargs kill)"',
        ),
        (
            "substitution redirect then push",
            "echo $(date) > /tmp/x; git push origin main",
        ),
        (
            "substitution redirect then commit attribution",
            'echo $(date) > /tmp/x; git commit -m "Co-Authored-By: Claude"',
        ),
        ("subshell redirect then push", "(true) > /dev/null; git push origin main"),
        ("pgrep full any name kill", "pgrep -f server | xargs kill"),
        ("pgrep full variable kill", 'pgrep -f "$APP" | xargs kill'),
        ("pgrep full regex kill", "pgrep -f v.te | xargs kill"),
        ("pgrep full unlisted name kill", "pgrep -f redis-server | xargs kill"),
        (
            "quoted hash before substitution kill",
            "printf '#' $(pgrep -f vite | xargs kill)",
        ),
        ("path xargs kill", "pgrep -f vite | /usr/bin/xargs kill"),
        ("path pgrep xargs kill", "/usr/bin/pgrep -f vite | /usr/bin/xargs /bin/kill"),
        ("path env push", "/usr/bin/env git push origin HEAD:main"),
        ("path nohup push", "/usr/bin/nohup git push origin HEAD:main"),
        ("sudo pkill", "sudo pkill -f vite"),
        ("sudo substitution kill", "sudo kill $(pgrep -f vite)"),
        ("variable pgrep kill", "pids=$(pgrep -f vite); kill $pids"),
        ("quoted semicolon xargs delimiter kill", "pgrep -f vite | xargs -d ';' kill"),
        ("quoted ampersand xargs delimiter kill", "pgrep -f vite | xargs -d '&' kill"),
        ("quoted pipe xargs replace kill", "pgrep -f vite | xargs -I '|' kill '|'"),
        (
            "API template value graphql inline body",
            "gh api repos/a/b/pulls/1 -X PATCH -f body='Co-Authored-By: Claude' --template graphql",
        ),
        (
            "API template value graphql file body",
            f"gh api repos/a/b/pulls/1 -X PATCH -F body=@{msg} --template graphql",
        ),
        (
            "API template value graphql stdin body",
            'gh api repos/a/b/pulls/1 -X PATCH --input - --template graphql <<< \'{"body":"Co-Authored-By: Claude"}\'',
        ),
        (
            "API header naming comments",
            "gh api -X PATCH repos/a/b/pulls/1 -H 'X-Note: /comments' --input - <<< '{\"body\":\"Co-Authored-By: Claude\"}'",
        ),
        (
            "API query naming comments",
            "gh api -X PATCH 'repos/a/b/pulls/1?x=/comments' --input - <<< '{\"body\":\"Co-Authored-By: Claude\"}'",
        ),
        (
            "PR here-string body file",
            'gh pr create --title t --body-file - <<< "Co-Authored-By: Claude"',
        ),
        ("bare Codex trailer", 'git commit -m "x" -m "Co-authored-by: Codex"'),
        (
            "unparseable API attribution",
            'gh api repos/a/b/pulls -f body="Co-Authored-By: Claude" \'unterminated',
        ),
        (
            "commit here-string naming a comment",
            'git commit -F - <<< "fix; see gh pr comment 1 Co-Authored-By: Claude"',
        ),
    ]:
        case(name, command, True)
    redirect_targets = {"<&": "0", ">&": "1", "<<<": "''", "<<": "EOF"}
    input_operators = ("<", "<>", "<<<", "<<", "<&")
    for operator in ("<", ">", ">>", "<>", "<<<", "<<", "<&", ">&", ">|", "&>", "&>>"):
        target = redirect_targets.get(operator, "/dev/null")
        prefixes = (
            [""]
            if operator.startswith("&")
            else ["2", "{fd}"] + ([] if operator in input_operators else [""])
        )
        for prefix in prefixes:
            for spacing in ("", " "):
                redirect = f"{prefix}{operator}{spacing}{target}"
                for position, command in [
                    (
                        "before the listing",
                        f"{redirect} pgrep -f node | xargs -r -n 20 env nohup kill -TERM 12345",
                    ),
                    (
                        "after the listing",
                        f"pgrep -f node {redirect} | xargs -r -n 20 env nohup kill -TERM 12345",
                    ),
                    (
                        "before xargs",
                        f"pgrep -f node | {redirect} xargs -r -n 20 env nohup kill -TERM 12345",
                    ),
                    (
                        "between xargs options",
                        f"pgrep -f node | xargs -r {redirect} -n 20 env nohup kill -TERM 12345",
                    ),
                    (
                        "between wrappers",
                        f"pgrep -f node | xargs -r -n 20 env {redirect} nohup kill -TERM 12345",
                    ),
                    (
                        "before kill",
                        f"pgrep -f node | xargs -r -n 20 env nohup {redirect} kill -TERM 12345",
                    ),
                    (
                        "after kill args",
                        f"pgrep -f node | xargs -r -n 20 env nohup kill -TERM 12345 {redirect}",
                    ),
                ]:
                    case(f"redirect {redirect!r} {position}", command, True)
    glued_redirects = {
        ">": "/dev/null",
        ">>": "/dev/null",
        ">|": "/dev/null",
        ">&": "2",
        "<": "/dev/null",
        "<>": "/dev/null",
        "<<<": "''",
        "<&": "0",
        "&>": "/dev/null",
        "&>>": "/dev/null",
    }
    for control in ("|", "|&", ";", "&&", "||", "&", "\n", "("):
        for operator, target in glued_redirects.items():
            if control == "&" and operator.startswith(">"):
                continue
            for spacing in ("", " "):
                redirect = f"{operator}{spacing}{target}"

                def joined(command):
                    return (
                        f"({redirect} {command})"
                        if control == "("
                        else f"true{control}{redirect} {command}"
                    )

                for context, command in [
                    ("kill", "kill $(pgrep -f node)"),
                    ("push", "git push origin main"),
                    ("commit", 'git commit -m "x Co-Authored-By: Claude"'),
                ]:
                    case(
                        f"control {control!r} redirect {redirect!r} {context}",
                        joined(command),
                        True,
                    )
                if control in ("|", "|&") and not operator.startswith("<"):
                    case(
                        f"pipe {control!r} redirect {redirect!r} xargs kill",
                        f"pgrep -f node {control}{redirect} xargs kill -TERM",
                        True,
                    )
    case("pipe with fd 0 replaced", "pgrep -f node |<>/dev/null xargs kill -TERM")
    case("unquoted backtick unlisted name kill", "kill `pgrep -f redis-server`", True)
    case(
        "unquoted backtick quoted pattern kill", "kill `pgrep -f 'redis server'`", True
    )
    case("quoted redirect word as refspec", "git push origin '>' main", True, isolated)
    for name, command in [
        (
            "here-string replaces the PID stream",
            "pgrep -f node | xargs kill -TERM <<< 12345",
        ),
        (
            "file replaces the PID stream",
            "pgrep -f node | xargs -r kill -TERM 0< tmp/pids/vite.pid",
        ),
        (
            "quoted redirect characters",
            "printf '%s\\n' '2<>' '<<<' '&>>' '>|' | grep -c x",
        ),
        ("number before spaced redirect is a target", "kill -TERM 12345 >/dev/null"),
    ]:
        case(name, command)
    for executable in (
        "cursor-agent",
        "agent",
        "/opt/bin/cursor-agent",
        "/opt/bin/agent",
    ):
        for flag in ("--list-models", "--help", "--version"):
            case(f"cursor info {executable} {flag}", f"{executable} {flag}")
            case(
                f"cursor pane info {executable} {flag}",
                f"herdr pane run w1:p2 '{executable} {flag}'",
            )
        for args in ("", " --model x --yolo", " -f", " -p hi"):
            command = executable + args
            case(f"cursor executable {command}", command, True)
            for wrapper in ("command", "exec", "sudo", "nohup", "xargs", "env FOO=1"):
                case(f"cursor {wrapper} {command}", f"{wrapper} {command}", True)
            case(f"cursor eval {command}", f"eval '{command}'", True)
            for verb in ("run", "send-text"):
                case(
                    f"cursor pane {verb} {command}",
                    f"herdr pane {verb} w1:p2 '{command}'",
                    True,
                )
        case(
            f"cursor substitution {executable}",
            f"herdr pane run w1:p2 \"$(printf '{executable} --yolo')\"",
            True,
        )
    for command in (
        "cursor agent --yolo",
        "command cursor agent --yolo",
        "env FOO=1 cursor agent --yolo",
        "exec cursor agent --yolo",
        "/opt/bin/cursor agent --yolo",
    ):
        case(f"cursor command {command}", command, True)
        case(
            f"cursor pane command {command}",
            f"herdr pane run w1:p2 '{command}'",
            True,
        )
    for command in ("cursor .", "cursor --version", "cursor agent --help"):
        case(f"allowed cursor command {command}", command)
    for command in (
        "command -v pi",
        "command -v cursor-agent",
        "command -V pi",
        "command -V cursor-agent",
        "command -pv pi",
        "command -vp pi",
        "command -p -v pi",
        "command -pp -v pi",
        "command -ppv pi",
        "command -p -p -v pi",
        "command -v pi;\techo ok",
        "command -v -- pi",
        "command -v pi cursor-agent",
        "command -v pi cursor-agent herdr",
        "command -v pkill",
        "command -pv git push origin HEAD:main",
        "command -v a_B.9+c/d:e,f%g@h-i pi",
        "command -v pi >/dev/null",
        "command -v pi > /dev/null",
        "command -v pi >>/dev/null",
        "command -v pi 2>/dev/null",
        "command -v pi &>/dev/null",
        "command -v pi &>>/dev/null",
        "command -v pi >&2",
        "command -v pi 2>&1",
        "command -v pi >>/dev/null 2>&1",
        "command -v pi >/dev/null 2>&1 && echo ok",
        "command -v pi >/dev/null&&echo ok",
        "command -v pi >/dev/null||echo missing",
        "command -v pi || echo missing",
        "command -v pi || exit 1",
        "command -v pi || false",
        "command -v pi && true",
        "command -v pi && echo ok || echo missing",
        "command -v pi; command -v pi",
        "command -v pi;",
        "if command -v cursor-agent >/dev/null 2>&1; then echo y; fi",
        "if ! command -v pi >/dev/null 2>&1; then echo missing; fi",
        "if command -v pi; then echo y; else echo n; fi",
        "if command -v pi\nthen echo y\nelse echo n\nfi",
        "if command -v pi\nthen\n  echo y\nelse\n  echo n\nfi",
        "if command -v pi; then echo y; elif command -v x; then echo z; else echo n; fi",
    ):
        case(f"command lookup {command}", command)
    for command in (
        "command pi",
        "command -p pi",
        "command -- pi",
        "command -p -- pi",
        "command -x pi",
        "command -v -x pi",
        "command -vx pi",
        "command cursor-agent -p hi",
        "command pkill -f vite",
        "command kill -9 -1",
        "command git push origin main",
        "env command pi",
        "command -v pi; pi",
        "command -v pi && pi",
        "command -v pi || pi",
        "command -v x &>/dev/null pi",
        "command -v x &>/dev/null git push origin main",
        "command -v pi; command pi",
        "command -v cursor-agent && cursor-agent -p hi",
        "command -v herdr && herdr agent start x --kind pi --pane p",
        "command -v herdr && herdr agent start x --kind cursor --pane p",
        "command -v kill -9 -1; kill -9 -1",
        "command -v git && git push origin main",
        "$(command -v pi) --model x",
        '"$(command -v cursor-agent)" -p hi',
        "`command -v pi` --model x",
        "x=$(command -v pi); $x",
        'eval "$(command -v pi)"',
        "bash -c 'command -v pi && pi'",
        "bash -c 'command -v pi'",
        'bash -c "$(command -v pi)"',
        "command -v pi | bash",
        "command -v pi | sh",
        "command -v pi 2>&1 | cat",
        "command -v pi > /dev/null | bash",
        "command -v pi |& bash",
        "{ command -v pi; } | bash",
        "( command -v pi ) | bash",
        "command -v pi > f; bash f",
        "command -v pi >> f; bash f",
        "command -v pi >&f; bash f",
        "command -v pi >&2x; bash 2x",
        "command -v pi >&3",
        "env command -v pi",
        "timeout 5 command -v pi",
        "./command -v cursor-agent -p hi",
        "env -P command -v pi",
        "nohup command -v pi",
        "if { command -v git } then git push origin main; fi",
        "{ command -v x } always { git push origin main }",
        "if { command -v pi } then pi; fi",
        "bash =(command -v pi)",
        'x=(command -v pi); "${x[3]}"',
        "command -v pi; ${(Q)_}",
        'n=_; command -v pi; "${!n}"',
        "command -v x ${ pi; }",
        'function -T command { shift; "$@"; }; command -v pi',
        "functions[command]='shift; \"$@\"'; command -v pi",
        "comm''and() { shift; \"$@\"; }; command -v pi",
        "eval 'comm''and() { shift; \"$@\"; }'; command -v pi",
        "command -v pi; eval '\"$''_\"'",
        "aliases[command]=env\ncommand -v pi",
        "source f; command -v pi",
        ". ./f; command -v pi",
        "x=`command -v pi`; $x",
        'command -v cursor-agent; "$_" -p hi',
        'command -v pi; "$_"',
        'command -v pi; "${_}" hello',
        'command -v pi; bash -c "$_"',
        'command -v pi; eval "$_"',
        'command() { shift; "$@"; }; command -v cursor-agent -p hi',
        'command() { shift; "$@"; }; command -v pi',
        'function command { shift; "$@"; }; command -v pi',
        "eval 'command() { shift; \"$@\"; }'; command -v pi",
        "alias command=env\ncommand -v pi",
        "shopt -s expand_aliases\nalias command='env -S'\ncommand -v pi",
        "{ command -v pi; } > f; bash f",
        "exec >f; command -v pi; bash f",
        "command -v pi >/dev/null/x",
        "command -v pi >'/dev/null'",
        "bash <(command -v pi)",
        "command -v x \\\ngit push origin main",
        "command -v x \\\npi",
        "command -v x \\\ngit commit -m 'Co-Authored-By: Claude'",
        "command -v x >/dev/null \\\ngit push origin main",
        "command -v y $'\\'';x=\\' git push origin main",
        "command -v y $'\\'';x=\\' pi",
        "command -v y $'\\''\nx=\\' git push origin main",
        'command -v pi >&1"x"; bash 1x',
        "command -v pi >&2'x'; bash 2x",
        "command -v pi >&1,x; bash 1,x",
        "command -v pi >&1\\x; bash 1x",
        "command -v pi >/dev/nullx",
        "command -v pi >&1x",
        "command -v pi >&2x",
        "'.' ./f; command -v pi",
        "\\. ./f; command -v pi",
        "sou''rce f; command -v pi",
        "e''val 'comm''and(''){ shift; \"$@\"; }'; command -v pi",
        "trap '\"$''_\"' EXIT; command -v pi",
        "disable command; command -v pi",
        "enable -n command; command -v pi",
        "FPATH=.; autoload command; command -v pi",
        "x=sou; ${x}rce ./f; command -v pi",
        'declare -n r=_; command -v pi; "$r"',
        "if git push origin main; then command -v pi; fi",
        "command -v pi & pi",
        "command -v pi &! pi",
        "command -v pi &| pi",
        "command -v pi }",
        "{ command -v x >/dev/null } always { git push origin main }",
        'command -v pi || echo "missing"',
        "{ command -v pi; }",
        "x=1 command -v pi",
        "command -v pi; exec cat",
        "command -v pi <<< x",
        "command -v pi # x",
        "command -v pi *",
        "command source ./f; command -v pi",
        "command -p source ./f; command -v pi",
        "command -p -p make; command -v pi",
        "command . ./f; command -v pi",
        "command builtin source ./f; command -v pi",
        "command eval source ./f; command -v pi",
        "command -v pi; command make",
        "command env -v git push origin main",
        "command time -v git push origin main",
        "command nice -v pi",
        "command command -v pi",
        "command -v -x env -v git push origin main",
        "command -v -x time -v git push origin main",
        "command -V -x nice -v pi",
        "command -pv -f nohup -v pi",
        "command -v pi $PATH",
    ):
        case(f"command run or captured lookup {command}", command, True)
    for command in (
        "command -v pi",
        "command -v pi; pi",
        "$(command -v pi)",
        "command -v -x env -v git push origin main",
    ):
        case(
            f"cursor pane command lookup {command}",
            f"herdr pane run w1:p2 '{command}'",
            True,
        )
    case(
        "generated agent argument is not a command",
        'herdr pane run p "$(printf echo) $(printf agent)"',
    )
    for name, command in (
        (
            "printf argument",
            "herdr pane run w1:p2 \"$(printf '%s --yolo' cursor-agent)\"",
        ),
        (
            "printf hex escape",
            "herdr pane run w1:p2 \"$(printf 'cursor-agent\\x20--yolo')\"",
        ),
        (
            "echo -e hex escape",
            "herdr pane run w1:p2 \"$(echo -e 'cursor-agent\\x20--yolo')\"",
        ),
        (
            "echo hex escape",
            "herdr pane run w1:p2 \"$(echo 'cursor-agent\\x20--yolo')\"",
        ),
        (
            "printf two arguments",
            "herdr pane send-text w1:p2 \"$(printf '%s %s' agent --yolo)\"",
        ),
        (
            "backtick printf argument",
            "herdr pane run w1:p2 \"`printf '%s --yolo' cursor-agent`\"",
        ),
        ("pi printf argument", "herdr pane run w1:p2 \"$(printf '%s' pi)\""),
        (
            "pi printf argument with flag",
            "herdr pane run w1:p2 \"$(printf '%s --provider x' pi)\"",
        ),
        ("pi printf hex escape", "herdr pane run w1:p2 \"$(printf 'p\\x69')\""),
    ):
        case(f"pane text reads every echo/printf reading: {name}", command, True)
    for name, command in (
        ("echo -n escape", "herdr pane run w1:p2 \"$(echo -n 'p\\x69')\""),
        ("printf quotes kept", 'herdr pane run w1:p2 "$(printf \'\\";p\\x69;\\"\')"'),
    ):
        case(f"pane text reads reading 2: {name}", command, True)
    for name, command in (
        (
            "reading 0 only: escapes break the parse",
            "herdr pane run w1:p2 \"$(echo -e 'p\\0151\\x22\\npi')\"",
        ),
        (
            "reading 3 only: nothing read",
            'herdr pane run w1:p2 "$(echo -ne \'pi --yolo\\" --yolo\\x22\')"',
        ),
    ):
        case(f"pane text reads reading 0 and 3: {name}", command, True)
    big = "a" * 66000
    # A printf cut at 64 KB fails closed, so the harmless last row is denied on purpose.
    for name, command in (
        ("printf argument", f"herdr pane run w1:p2 \"$(printf '%s;p\\x69' {big})\""),
        ("printf %b pi", f"herdr pane run w1:p2 \"$(printf '%b' '{big};p\\x69')\""),
        ("send-text", f"herdr pane send-text w1:p2 \"$(printf '%s;p\\x69' {big})\""),
        ("harmless text", f"herdr pane run w1:p2 \"$(printf '%s' {big})\""),
    ):
        case(f"pane text cut at 64 KB fails closed: {name}", command, True)
    for name, command in (
        ("printf argument", "herdr pane run w1:p2 \"$(printf '%s' ls)\""),
        ("echo", 'herdr pane run w1:p2 "$(echo hello)"'),
    ):
        case(f"pane text allows an ordinary substitution: {name}", command)
    for command in (
        "herdr agent list",
        "orch-agents",
        "ssh-agent -s",
        "echo agent",
        "printf '%s' cursor-agent",
        "~/.claude/coordinator/cursor-start.sh w p worker",
        "~/.claude/coordinator/cursor-start.sh w p smart --no-trust",
    ):
        for wrapper in ("", "command ", "env FOO=1 ", "sudo ", "nohup ", "xargs "):
            case(f"cursor command position {wrapper}{command}", wrapper + command)
            case(
                f"cursor pane command position {wrapper}{command}",
                f"herdr pane run w1:p2 '{wrapper}{command}'",
            )
    for name, command in [
        (
            "cursor start",
            "herdr agent start w --kind cursor --pane w1:p2 -- --model arbitrary --yolo",
        ),
        ("cursor equals kind", "herdr agent start w --kind=cursor --pane w1:p2"),
        ("cursor trimmed kind", "herdr agent start w --kind ' Cursor ' --pane w1:p2"),
        (
            "cursor wrapped start",
            "env FOO=1 herdr --socket /tmp/s agent start w --kind cursor --pane w1:p2",
        ),
        (
            "cursor shell start",
            "bash -c 'herdr agent start w --kind cursor --pane w1:p2'",
        ),
        (
            "cursor pane start",
            "herdr pane run w1:p2 'herdr agent start w --kind cursor --pane w1:p3'",
        ),
        (
            "cursor variable kind",
            'K=cursor; herdr agent start w --kind "$K" --pane w1:p2',
        ),
        (
            "herdr pi start on DeepSeek",
            "herdr agent start w --kind pi --pane w1:p2 -- --provider deepseek --model x",
        ),
        (
            "herdr pi start with equals kind",
            "herdr agent start w --kind=pi --pane w1:p2 -- --model deepseek/x",
        ),
        (
            "herdr pi start on a local provider",
            "herdr agent start w --kind pi --pane w1:p2 -- --provider lmstudio --model qwen/q",
        ),
        (
            "herdr pi start without a provider",
            "herdr agent start w --kind pi --pane w1:p2",
        ),
        (
            "env wrapped herdr pi start",
            "env FOO=1 herdr agent start w --kind pi --pane w1:p2 -- --provider deepseek",
        ),
        ("pi with provider flag", "pi --provider deepseek --model x"),
        ("pi with qualified model", 'pi -p --model DeepSeek/x "hi"'),
        ("absolute pi path on DeepSeek", "/opt/bin/pi --provider deepseek"),
        (
            "herdr pi start with an upper-case kind",
            "herdr agent start w --kind PI --pane w1:p2 -- --provider lmstudio --model q",
        ),
        (
            "herdr pi start with a mixed-case equals kind",
            "herdr agent start w --kind=Pi --pane w1:p2",
        ),
        (
            "herdr pi start with a variable kind",
            'K=pi; herdr agent start w --kind "$K" --pane w1:p2',
        ),
        (
            "herdr pi start with an ANSI-C kind",
            "herdr agent start w --kind $'pi' --pane w1:p2",
        ),
        (
            "herdr pane run typing pi",
            "herdr pane run w1:p2 'pi --provider lmstudio --model q'",
        ),
        (
            "herdr pane run typing pi as words",
            "herdr pane run w1:p2 pi --provider deepseek --model x",
        ),
        ("herdr pane run typing pi after cd", "herdr pane run w1:p2 'cd /tmp && pi'"),
        (
            "herdr pane run typing pi after an assignment",
            "herdr pane run w1:p2 'FOO=1 pi'",
        ),
        ("herdr pane send-text typing pi", "herdr pane send-text w1:p2 'pi --model q'"),
        (
            "herdr prompt switching a live model",
            "herdr agent prompt piw '/model lmstudio/qwen'",
        ),
        (
            "herdr prompt switching a live model in upper case",
            "herdr agent prompt piw '/MODEL deepseek/x' --wait",
        ),
        (
            "herdr pane send-text switching a live model",
            "herdr pane send-text w1:p2 '/model deepseek/x'",
        ),
        (
            "herdr start of another kind on DeepSeek",
            "herdr agent start w --kind opencode --pane w1:p2 -- --model DeepSeek/x",
        ),
        (
            "herdr pi start after a global option",
            "herdr --socket /tmp/s agent start w --kind pi --pane w1:p2",
        ),
        (
            "bash -c herdr pi start",
            "bash -c 'herdr agent start w --kind pi --pane w1:p2'",
        ),
        ("sh -c pi on DeepSeek", 'sh -c "pi --provider deepseek --model x"'),
        ("zsh -c pi on DeepSeek", "zsh -c 'pi --provider deepseek'"),
        ("eval herdr pi start", "eval 'herdr agent start w --kind pi --pane w1:p2'"),
        (
            "quoted substitution running pi on DeepSeek",
            'echo "$(pi --provider deepseek --model x -p hi)"',
        ),
        (
            "pane text starting a herdr pi start",
            "herdr pane run w1:p2 'herdr agent start w --kind pi --pane w1:p3 -- --provider deepseek --model x'",
        ),
        (
            "pane text running pi in bash -c",
            """herdr pane run w1:p2 'bash -c "pi --provider deepseek --model x"'""",
        ),
        (
            "pane text running pi under timeout",
            "herdr pane run w1:p2 'timeout 600 pi --provider deepseek --model x'",
        ),
        ("pane text running pi under nice", "herdr pane run w1:p2 'nice -n 5 pi'"),
        (
            "pane text after a double dash",
            "herdr pane run w1:p2 -- 'pi --provider lmstudio --model q'",
        ),
        ("send-text after a double dash", "herdr pane send-text w1:p2 -- pi --model q"),
        (
            "prompt opening the scoped-models selector",
            "herdr agent prompt piw '/scoped-models'",
        ),
        ("prompt opening the settings selector", "herdr agent prompt piw '/settings'"),
        ("kind with a trailing space", "herdr agent start w --kind 'pi ' --pane w1:p2"),
        ("kind with a leading space", "herdr agent start w --kind ' pi' --pane w1:p2"),
        (
            "equals kind with an escaped space",
            "herdr agent start w --kind=pi\\  --pane w1:p2",
        ),
        ("bare pi", "pi"),
        (
            "pi on DeepSeek with help after a double dash",
            "pi --provider deepseek --model x -- --help",
        ),
        ("pi with help as an option value", "pi --append-system-prompt --help"),
        (
            "pi with a version flag after a model",
            "pi --provider deepseek --model x --version",
        ),
        (
            "pane text running pi with help after a double dash",
            "herdr pane run w1:p2 'pi --provider deepseek --model x -- --help'",
        ),
        (
            "pane text running pi with help as an option value",
            "herdr pane run w1:p2 'pi --append-system-prompt --help'",
        ),
        ("pi with a prompt and no provider", "pi -p 'summarize this repo'"),
        ("pi with a model only on DeepSeek", "pi --model deepseek-v4-pro"),
        (
            "double-quoted substitution in a commit message",
            'git commit -m "docs: $(pi --provider deepseek) is denied"',
        ),
        (
            "nested shell running a bypassed codex start",
            'bash -c "herdr agent start w --kind codex --pane w1:p2 -- --no-daemon --dangerously-bypass-hook-trust"',
        ),
        ("double-quoted backticks running pi", 'echo "`pi --provider deepseek`"'),
        (
            "nested shell running herdr pi start",
            "bash -c \"sh -c 'herdr agent start w --kind pi --pane w1:p2'\"",
        ),
    ]:
        case(name, command, True)
    for name, command in [
        (
            f"herdr codex start with {flag}",
            f"herdr agent start w --kind codex --pane w1:p2 -- --no-daemon {flag} --model m",
        )
        for flag in [
            "--dangerously-bypass-hook-trust",
            "-s danger-full-access",
            "-sdanger-full-access",
            "--sandbox danger-full-access",
            "--sandbox=danger-full-access",
            "--add-dir /",
            "--add-dir=/",
            "-c sandbox_mode=danger-full-access",
            "-csandbox_mode=danger-full-access",
            "--config sandbox_permissions=[]",
            "--config=sandbox_mode=x",
            "--dangerously-bypass-approvals-and-sandbox",
            "--yolo",
            "--profile u -c profiles.u.sandbox_mode=x",
            "$F",
            "$(echo --yolo)",
            "`echo --yolo`",
            "'${X:---yolo}'",
            "$'\\x2d-yolo'",
            "-c$'sandbox_mode=x'",
            '-c \'hooks.state={"/w/.codex/hooks.json:pre_tool_use:0:0"={trusted_hash="sha256:x"}}\'',
            "-c features.hooks=false",
            "-c ' features . hooks=false'",
            "-c default_permissions=x",
            "--disable hooks",
            "--disable=hooks",
            "--profile u",
            "--profile=u",
            "-p u",
            "-pu",
            "-c 'profiles.u={sandbox_mode=\"danger-full-access\"}'",
            "-c ' profiles.u={approval_policy=\"never\"}'",
            "-c=profiles.u={sandbox_mode=1}",
            "--config=' profiles.u={sandbox_mode=1}'",
            "-c 'profiles.u.approval_policy=\"never\"'",
            "-c ' profiles . \"u\" . sandbox_mode=x'",
            "-c sandbox_workspace_write.network_access=true",
            "--config=profiles.u.sandbox_mode=x",
            "--full-auto",
            "--approve-for-me",
            "-a never",
            "-anever",
            "--ask-for-approval never",
            "--ask-for-approval=never",
            "-c ' sandbox_mode=x'",
            "-c ' \"sandbox_mode\"=x'",
            "-c approval_policy=never",
            "-c permissions.p.x=1",
            "-c=sandbox_mode=x",
            "--config ' approval_policy=never'",
            "--config=' sandbox_mode=x'",
        ]
    ]:
        case(name, command, True)
    for name, command in [
        ("pi start step", "~/.claude/coordinator/pi-start.sh w w1:p2 worker"),
        ("cursor start step", "~/.claude/coordinator/cursor-start.sh w w1:p2 worker"),
        ("herdr claude start", "herdr agent start w --kind claude --pane w1:p2"),
        (
            "herdr codex start",
            "herdr agent start w --kind codex --pane w1:p2 -- --no-daemon --model m",
        ),
        (
            "herdr codex start with a project doc setting",
            "herdr agent start w --kind codex --pane w1:p2 -- --no-daemon --model m -c project_doc_max_bytes=0",
        ),
        ("codex launcher", "~/.claude/coordinator/codex-start.sh w w1:p2 worker"),
        ("codex smart launcher", "~/.claude/coordinator/codex-start.sh w w1:p2 smart"),
        (
            "herdr codex start with a reasoning effort",
            "herdr agent start w --kind codex --pane w1:p2 -- --no-daemon --model m -c model_reasoning_effort=high",
        ),
        ("herdr agent list", "herdr agent list"),
        (
            "herdr prompt to a pi worker",
            'herdr agent prompt aao-pi "use deepseek notes" --wait',
        ),
        (
            "pi on a local provider",
            "pi --provider lmstudio --model qwen/qwen3.6-35b-a3b",
        ),
        ("pi listing models", "pi --list-models"),
        ("pi listing DeepSeek models", "pi --list-models deepseek"),
        ("pi help", "pi --help"),
        ("pi version", "pi --version"),
        (
            "pi on a local provider with a prompt",
            "pi --provider lmstudio --model q -p 'hi'",
        ),
        (
            "commit message with a single-quoted substitution",
            "git commit -m 'docs: $(pi --provider deepseek) is denied'",
        ),
        (
            "PR comment with single-quoted backticks",
            "gh pr comment 38 --body 'run `pi --provider deepseek` only after opt-in'",
        ),
        (
            "herdr start of an agent named after DeepSeek",
            "herdr agent start deepseek-review --kind claude --pane w1:p2 -- --model claude-sonnet-5-5",
        ),
        (
            "herdr start with a DeepSeek path for --cd",
            "herdr agent start w --kind codex --pane w1:p2 -- --no-daemon --cd ~/work/deepseek-eval",
        ),
        (
            "herdr pane run of a test command",
            "herdr pane run w1:p2 'npm test && echo done'",
        ),
        (
            "herdr pane send-text naming a pi log",
            "herdr pane send-text w1:p2 'cat pi.log'",
        ),
        ("herdr pane run of another command", 'herdr pane run w1:p2 "ls pi-docs"'),
        ("herdr pane run mentioning pi in prose", "herdr pane run w1:p2 'echo pi'"),
        (
            "herdr pane send-text without a model switch",
            "herdr pane send-text w1:p2 'model list'",
        ),
        (
            "herdr prompt mentioning /model in prose",
            "herdr agent prompt piw 'do not use /model'",
        ),
        ("bash -c of an unrelated command", "bash -c 'ls -la'"),
        ("quoted substitution of an unrelated command", 'echo "$(date)"'),
        ("grep for deepseek", "grep -r deepseek docs/"),
    ]:
        case(name, command)
    footer = "*(Generated using Claude Code)*"
    for name, command in [
        (
            "multi-line comment footer",
            f'gh pr comment 36 --body "Addressed in fb655d7.\n\n{footer}" && gh pr edit 36 --add-label ready',
        ),
        (
            "table comment footer",
            f'gh pr comment 36 --body "| a | b |\n|---|---|\n\n{footer}" && gh pr edit 36 --add-label ready',
        ),
        (
            "ampersand comment footer",
            f'gh pr comment 36 --body "A & B\n\n{footer}" && gh pr edit 36 --add-label ready',
        ),
        (
            "global repo comment footer",
            f'gh --repo acme/webapp pr comment 1 --body "x\n\n{footer}" && gh pr edit 1 --add-label ready',
        ),
        (
            "heredoc comment footer",
            f"git commit -m \"fix: x\" && gh pr comment 1 --body-file - <<'EOF'\nDone.\n\n{footer}\nEOF",
        ),
        (
            "GraphQL thread reply footer",
            'git commit -m "fix: x" && gh api graphql -f query=\'mutation{addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:"x",body:"Fixed. '
            + footer
            + "\"}){clientMutationId}}'",
        ),
        (
            "comment quoting kill pipeline",
            "gh pr comment 1 --body 'The guard blocks pgrep -f vite | xargs kill now'",
        ),
        (
            "comment opening with kill pipeline",
            "gh pr comment 1 --body 'pgrep -f vite | xargs kill is blocked'",
        ),
        (
            "heredoc comment quoting kill pipeline",
            "gh pr comment 1 --body-file - <<'EOF'\nThe guard blocks pgrep -f vite | xargs kill now\nEOF",
        ),
        (
            "grep for kill pipeline text",
            "grep -F 'pgrep -f vite | xargs kill' script.sh",
        ),
        ("pgrep piped to grep for kill text", "pgrep -f vite | grep 'xargs kill'"),
        (
            "single-quoted comment with backticks",
            "gh pr comment 1 --body 'Run `pgrep -f vite | xargs kill` to see the block'",
        ),
        ("stdin device body file", "gh pr create --title t --body-file /dev/stdin"),
        (
            "listing then pidfile kill",
            "pgrep -f vite > /dev/null && kill -TERM $(cat tmp/pids/vite.pid)",
        ),
        (
            "pidfile pgrep then pidfile kill",
            "pgrep -F tmp/pids/vite.pid && kill -TERM $(cat tmp/pids/vite.pid)",
        ),
        ("pidfile pgrep kill", "pgrep -F tmp/pids/vite.pid | xargs kill -TERM"),
        (
            "child listing then pidfile kill",
            "pgrep -P $$ node; kill -TERM $(cat tmp/pids/node.pid)",
        ),
        ("ps count then own PID check", "ps aux | grep -c node && kill -0 $$"),
        (
            "ps saved then pidfile kill",
            "ps aux | grep vite > ps.txt; kill -TERM $(cat tmp/pids/vite.pid)",
        ),
        ("rg for kill pipeline", "rg 'pgrep -f vite | xargs kill' docs/"),
        (
            "grep notes for kill pipeline",
            "cat notes.md | grep 'pgrep -f vite | xargs kill'",
        ),
        (
            "less file named like a kill pipeline",
            "less 'ps aux | grep vite | xargs kill'",
        ),
        ("commented-out substitution", "printf safe # $(pgrep -f vite | xargs kill)"),
        (
            "shell positional argument text",
            "bash -c 'printf \"%s\\n\" \"$1\"' -- 'pgrep -f vite | xargs kill'",
        ),
        (
            "second -c as positional argument",
            "bash -c 'printf \"%s\\n\" \"$@\"' -- -c 'pgrep -f vite | xargs kill'",
        ),
        (
            "shell program listing then pidfile kill",
            "bash -c 'pgrep -f vite >/dev/null; kill -TERM \"$(cat tmp/pids/vite.pid)\"'",
        ),
        (
            "reassigned variable pidfile kill",
            'pids=$(pgrep -f vite); pids=$(cat tmp/pids/vite.pid); kill -TERM "$pids"',
        ),
        (
            "script reading heredoc data",
            "bash script.sh <<'EOF'\npgrep -f vite | xargs kill\nEOF",
        ),
        ("env PID kill", "env kill -TERM 12345"),
        ("timeout PID kill", "timeout 5 kill -TERM 12345"),
        ("trailing comment with apostrophe", "git status # don't push"),
        (
            "queried comment footer then commit",
            "gh api 'repos/a/b/issues/3/comments?x=1' -f body=\"Fixed. *(Generated using Claude Code)*\" && git commit -m ok",
        ),
        (
            "review reply footer then commit",
            'gh api repos/a/b/pulls/36/comments/5/replies -f body="Fixed. *(Generated using Claude Code)*" && git commit -m ok',
        ),
        ("repeated xargs options", "pgrep -f vite | xargs " + "-pp " * 30 + "ki"),
    ]:
        case(name, command)
    failed = []
    for name, command, deny, cwd in cases:
        result = hook(command, cwd)
        expected = 2 if deny else 0
        if result.returncode != expected:
            failed.append(name)
            print(
                "FAIL",
                name,
                "expected",
                expected,
                "got",
                result.returncode,
                result.stderr.strip(),
            )
    print(
        f"Tests: {len(cases)} cases ({sum(c[2] for c in cases)} deny, {sum(not c[2] for c in cases)} allow); {len(failed)} failures"
    )
    if failed:
        sys.exit(1)
    assert (
        "removeprefix" not in HOOK.read_text()
        and "removesuffix" not in HOOK.read_text()
    )
    for disabled, witness in [
        ("guard_push", "push main"),
        ("guard_attribution", "Codex trailer"),
        ("guard_memory", "shared deepen fetch"),
        ("guard_broad_kill", "substitution pgrep kill"),
        ("guard_written_script", "incident heredoc script run directly"),
        ("guard_disk_script", "disk script via bash"),
        ("guard_inline_shell", "inline bash -c pkill"),
        ("guard_big_repo_reads", "webapp batch all objects"),
        ("guard_pi", "herdr pi start on DeepSeek"),
    ]:
        caught = [
            name
            for name, command, deny, cwd in cases
            if bool(guard.evaluate(command, cwd, disabled=(disabled,))) != deny
        ]
        assert witness in caught, (disabled, caught)
        print(
            f"Mutation: disable {disabled} -> {len(caught)} test failures; witness: {witness}"
        )
    for bad in (
        "{",
        "{}",
        '{"cwd":123,"tool_input":{"command":"git status"}}',
        json.dumps(
            {"cwd": str(repo), "tool_input": {"command": 'git commit -m "unterminated'}}
        ),
    ):
        result = subprocess.run(
            [sys.executable, str(HOOK)], input=bad, capture_output=True, text=True
        )
        assert result.returncode == 0 and not result.stderr
    times = []
    for _ in range(20):
        start = time.perf_counter()
        result = hook("git status", repo)
        assert result.returncode == 0
        times.append((time.perf_counter() - start) * 1000)
    print(
        f"git status allow timing: median {sorted(times)[10]:.2f} ms; min {min(times):.2f} ms; max {max(times):.2f} ms (20 process invocations)"
    )
    log = test_home / ".claude/hooks/guard-bash.log"
    log.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_input": {"command": "git push origin main"}}),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and not result.stderr
    entry = log.read_text()
    assert re.match(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\S* guard-bash fail-open: KeyError: 'cwd' command='git push origin main'\n$",
        entry,
    ), entry
    print("Fail-open log:", entry.strip())
    for command in [
        "herdr agent start w --kind codex --pane w1:p2 -- --no-daemon --yolo",
        'bash -c "herdr agent start w --kind codex --pane w1:p2 -- --no-daemon --yolo"',
    ]:
        assert "codex-start.sh" in guard.evaluate(command, repo), command
    start = time.perf_counter()
    guard.evaluate("pgrep -f vite | xargs " + "-pp " * 40 + "ki", repo)
    assert time.perf_counter() - start < 2, "xargs option scan is not linear"
    check_installer(ROOT, root, repo)
    print(
        "Installer: hook linked and PreToolUse Bash entry remains exactly once after a second install"
    )
