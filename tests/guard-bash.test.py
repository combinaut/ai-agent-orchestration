#!/usr/bin/env python3
"""Policy integration tests plus in-memory mutation checks for guard-bash."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import re
import shutil
import subprocess
import sys
import tempfile
import time
os.environ['ORCH_ENV'] = 'none'

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT/'hooks/guard-bash.py'
spec = importlib.util.spec_from_file_location('guard', HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def hook(command, cwd):
    payload = json.dumps({'tool_name': 'Bash', 'cwd': str(cwd), 'tool_input': {'command': command}})
    try:
        return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(HOOK, 'timeout', '', 'guard timed out')


def check_installer(repo, root, push_cwd):
    home = root/'install-home'; bindir = root/'install-bin'
    home.mkdir(); bindir.mkdir()
    custom_config = root/'selected-orchestration.toml'
    custom_config.write_text('[pr]\nauthor = "me"\n')
    gh = bindir/'gh'
    gh.write_text('#!/bin/sh\n[ "$1 $2" = "auth status" ]\n')
    gh.chmod(0o755)
    env = os.environ.copy()
    env.update(HOME=str(home), PATH=f'{bindir}:{env["PATH"]}', ORCH_REPO_PATH=str(repo), ORCH_CONFIG=str(custom_config))
    command = ['/bin/bash', str(repo/'install.sh'), '--stage=link']
    first = subprocess.run(command, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    target = home/'.claude/hooks/guard-bash.py'
    assert target.is_symlink() and target.resolve() == repo/'hooks/guard-bash.py'
    settings = json.loads((home/'.claude/settings.json').read_text())
    command_name = f'ORCH_CONFIG={shlex.quote(str(custom_config))} python3 ~/.claude/hooks/guard-bash.py'
    assert sum(h['command'] == command_name for entry in settings['hooks']['PreToolUse'] for h in entry['hooks']) == 1
    installed_env = env.copy()
    installed_env.pop('ORCH_CONFIG')
    payload = json.dumps({'tool_name': 'Bash', 'cwd': str(root/'webapp'), 'tool_input': {'command': 'git grep needle'}})
    installed = subprocess.run(command_name, shell=True, executable='/bin/bash', input=payload, capture_output=True, text=True, env=installed_env)
    assert installed.returncode == 2, installed.stderr
    for entry in settings['hooks']['PreToolUse']:
        for installed_hook in entry['hooks']:
            if installed_hook.get('command') == command_name:
                installed_hook['command'] = 'python3 ~/.claude/hooks/guard-bash.py'
    (home/'.claude/settings.json').write_text(json.dumps(settings))
    second = subprocess.run(command, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    settings = json.loads((home/'.claude/settings.json').read_text())
    assert sum(h['command'] == command_name for entry in settings['hooks']['PreToolUse'] for h in entry['hooks']) == 1

    matcher_home = root/'matcher-home'; (matcher_home/'.claude').mkdir(parents=True)
    (matcher_home/'.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Read', 'hooks': [{'type': 'command', 'command': command_name}]}]}}))
    matcher_env = env.copy(); matcher_env['HOME'] = str(matcher_home)
    third = subprocess.run(command, env=matcher_env, capture_output=True, text=True)
    assert third.returncode == 0, third.stderr
    matcher_settings = json.loads((matcher_home/'.claude/settings.json').read_text())
    entries = matcher_settings['hooks']['PreToolUse']
    assert sum(h['command'] == command_name for entry in entries for h in entry['hooks']) == 2
    assert sum(h['command'] == command_name for entry in entries if entry.get('matcher') == 'Bash' for h in entry['hooks']) == 1

    wildcard_home = root/'wildcard-home'; (wildcard_home/'.claude').mkdir(parents=True)
    (wildcard_home/'.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': command_name}]}]}}))
    wildcard_env = env.copy(); wildcard_env['HOME'] = str(wildcard_home)
    wildcard = subprocess.run(command, env=wildcard_env, capture_output=True, text=True)
    assert wildcard.returncode == 0, wildcard.stderr
    wildcard_settings = json.loads((wildcard_home/'.claude/settings.json').read_text())
    assert sum(h['command'] == command_name for entry in wildcard_settings['hooks']['PreToolUse'] for h in entry['hooks']) == 1

    quoted_home = root/'quoted-home'; (quoted_home/'.claude').mkdir(parents=True)
    quoted_command = 'python3 "$HOME/.claude/hooks/guard-bash.py"'
    (quoted_home/'.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': quoted_command}]}]}}))
    quoted_env = env.copy(); quoted_env['HOME'] = str(quoted_home)
    quoted = subprocess.run(command, env=quoted_env, capture_output=True, text=True)
    assert quoted.returncode == 0, quoted.stderr
    quoted_settings = json.loads((quoted_home/'.claude/settings.json').read_text())
    quoted_commands = [hook['command'] for entry in quoted_settings['hooks']['PreToolUse'] for hook in entry['hooks']]
    assert quoted_command in quoted_commands and command_name in quoted_commands

    for name, matcher, hook_command in [
        ('absolute', 'Bash', '/usr/bin/python3 {home}/.claude/hooks/guard-bash.py'),
        ('quoted-absolute', 'Bash', 'python3 "{home}/.claude/hooks/guard-bash.py"'),
        ('regex', '^Bash$', 'env MODE=test python3 ~/.claude/hooks/guard-bash.py'),
        ('path-env', 'Bash', '/usr/bin/env python3 ~/.claude/hooks/guard-bash.py'),
        ('versioned', 'Bash', 'python3.12 ~/.claude/hooks/guard-bash.py'),
        ('unquoted-home', 'Bash', 'python3 $HOME/.claude/hooks/guard-bash.py'),
    ]:
        shape_home = root/(name+'-home'); (shape_home/'.claude').mkdir(parents=True)
        (shape_home/'.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': matcher, 'hooks': [{'type': 'command', 'command': hook_command.format(home=shape_home)}]}]}}))
        shape_env = env.copy(); shape_env['HOME'] = str(shape_home)
        shaped = subprocess.run(command, env=shape_env, capture_output=True, text=True)
        assert shaped.returncode == 0, shaped.stderr
        shape_commands = [hook['command'] for entry in json.loads((shape_home/'.claude/settings.json').read_text())['hooks']['PreToolUse'] for hook in entry['hooks']]
        assert hook_command.format(home=shape_home) in shape_commands and command_name in shape_commands

    direct_home = root/'direct-home'; (direct_home/'.claude').mkdir(parents=True)
    (direct_home/'.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': '~/.claude/hooks/guard-bash.py'}]}]}}))
    direct_env = env.copy(); direct_env['HOME'] = str(direct_home)
    direct = subprocess.run(command, env=direct_env, capture_output=True, text=True)
    assert direct.returncode == 0, direct.stderr
    direct_commands = [h['command'] for entry in json.loads((direct_home/'.claude/settings.json').read_text())['hooks']['PreToolUse'] for h in entry['hooks']]
    assert direct_commands == ['~/.claude/hooks/guard-bash.py', command_name], direct_commands
    push = json.dumps({'tool_name': 'Bash', 'cwd': str(push_cwd), 'tool_input': {'command': 'git push origin HEAD:main'}})
    protected = subprocess.run(['/bin/bash', '-c', command_name], input=push, env=direct_env, capture_output=True, text=True)
    assert protected.returncode == 2, (protected.returncode, protected.stderr)

    broken_repo = root/'broken-repo'
    shutil.copytree(repo, broken_repo, ignore=shutil.ignore_patterns('.git'))
    (broken_repo/'hooks/guard-bash.py').write_text('import sys\nsys.exit(0)\n')
    broken_home = root/'broken-home'; broken_home.mkdir()
    broken_env = env.copy(); broken_env.update(HOME=str(broken_home), ORCH_REPO_PATH=str(broken_repo))
    broken = subprocess.run(['/bin/bash', str(broken_repo/'install.sh'), '--stage=link'], env=broken_env, capture_output=True, text=True)
    assert broken.returncode != 0 and 'Bash is unguarded' in broken.stderr, (broken.returncode, broken.stderr)
    for name, guard_source in [('silent-deny', 'import sys\nsys.exit(2)\n'), ('missing', None)]:
        bad_repo = root/(name+'-repo')
        shutil.copytree(repo, bad_repo, ignore=shutil.ignore_patterns('.git'))
        if guard_source is None:
            (bad_repo/'hooks/guard-bash.py').unlink()
        else:
            (bad_repo/'hooks/guard-bash.py').write_text(guard_source)
        bad_home = root/(name+'-home'); bad_home.mkdir()
        bad_env = env.copy(); bad_env.update(HOME=str(bad_home), ORCH_REPO_PATH=str(bad_repo))
        bad = subprocess.run(['/bin/bash', str(bad_repo/'install.sh'), '--stage=link'], env=bad_env, capture_output=True, text=True)
        assert bad.returncode != 0 and 'Bash is unguarded' in bad.stderr, (name, bad.returncode, bad.stderr)

    for name, hook_command in [
        ('literal-home', "python3 '$HOME/.claude/hooks/guard-bash.py'"),
        ('quoted-tilde', 'python3 "~/.claude/hooks/guard-bash.py"'),
        ('echo-mention', 'echo guard-bash.py'),
        ('copy-mention', 'cp guard-bash.py /tmp/guard-bash.py'),
        ('wrapper-mention', 'python3 wrapper.py "$HOME/.claude/hooks/guard-bash.py"'),
        ('masked-guard', 'python3 ~/.claude/hooks/guard-bash.py || true'),
        ('python2', 'python2 ~/.claude/hooks/guard-bash.py'),
        ('newline-registration', 'python3\n"$HOME/.claude/hooks/guard-bash.py"'),
        ('short-circuit', 'true||/usr/bin/python3 ~/.claude/hooks/guard-bash.py'),
        ('other-home', 'python3 /old-home/.claude/hooks/guard-bash.py'),
        ('env-control-operator', 'env MODE=test&&true python3 "$HOME/.claude/hooks/guard-bash.py"'),
        ('env-option', 'env --help python3 ~/.claude/hooks/guard-bash.py'),
        ('open-quote', 'python3 "$HOME/.claude/hooks/guard-bash.py'),
        ('close-quote', 'python3 $HOME/.claude/hooks/guard-bash.py"'),
    ]:
        literal_home = root/(name+'-home'); (literal_home/'.claude').mkdir(parents=True)
        (literal_home/'.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': hook_command}]}]}}))
        literal_env = env.copy(); literal_env['HOME'] = str(literal_home)
        literal = subprocess.run(command, env=literal_env, capture_output=True, text=True)
        assert literal.returncode == 0, literal.stderr
        assert len(json.loads((literal_home/'.claude/settings.json').read_text())['hooks']['PreToolUse']) == 2

    invalid_home = root/'invalid-home'; (invalid_home/'.claude').mkdir(parents=True)
    (invalid_home/'.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': {}}}))
    invalid_env = env.copy(); invalid_env['HOME'] = str(invalid_home)
    invalid = subprocess.run(command, env=invalid_env, capture_output=True, text=True)
    assert invalid.returncode == 0 and 'not a supported hooks object' in invalid.stdout
    assert json.loads((invalid_home/'.claude/settings.json').read_text()) == {'hooks': {'PreToolUse': {}}}

    nested_invalid_home = root/'nested-invalid-home'; (nested_invalid_home/'.claude').mkdir(parents=True)
    malformed_settings = {'hooks': {'SessionStart': ['bad'], 'PreToolUse': [{'hooks': ['bad']}]}}
    (nested_invalid_home/'.claude/settings.json').write_text(json.dumps(malformed_settings))
    nested_invalid_env = env.copy(); nested_invalid_env['HOME'] = str(nested_invalid_home)
    nested_invalid = subprocess.run(command, env=nested_invalid_env, capture_output=True, text=True)
    assert nested_invalid.returncode == 0 and 'not a supported hooks object' in nested_invalid.stdout
    assert json.loads((nested_invalid_home/'.claude/settings.json').read_text()) == malformed_settings

    no_matcher_home = root/'no-matcher-home'; (no_matcher_home/'.claude').mkdir(parents=True)
    (no_matcher_home/'.claude/settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'hooks': [{'type': 'command', 'command': command_name}]}]}}))
    no_matcher_env = env.copy(); no_matcher_env['HOME'] = str(no_matcher_home)
    no_matcher = subprocess.run(command, env=no_matcher_env, capture_output=True, text=True)
    assert no_matcher.returncode == 0, no_matcher.stderr
    assert len(json.loads((no_matcher_home/'.claude/settings.json').read_text())['hooks']['PreToolUse']) == 1


with tempfile.TemporaryDirectory(prefix='guard-bash-tests-') as directory:
    root = Path(directory)
    test_home = root/'home'; (test_home/'.claude/hooks').mkdir(parents=True)
    os.environ['HOME'] = str(test_home)
    os.environ.pop("ORCH_CONFIG", None)
    repo = root/'repo'; repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, '-c', 'user.name=Guard Test', '-c', 'user.email=guard@example.invalid', 'commit', '--allow-empty', '-m', 'initial')
    bare = root/'origin.git'; bare.mkdir(); git(bare, 'init', '--bare')
    git(repo, 'remote', 'add', 'origin', str(bare))
    git(repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(repo, 'branch', 'feature')
    git(repo, 'branch', '--set-upstream-to=origin/main', 'feature')
    wt = root/'worktree'; git(repo, 'worktree', 'add', str(wt), 'feature')
    isolated = root/'isolated'; isolated.mkdir(); git(isolated, 'init', '-b', 'feature'); git(isolated, 'remote', 'add', 'origin', str(bare))
    remote_named_main = root/'remote-named-main'; remote_named_main.mkdir(); git(remote_named_main, 'init', '-b', 'feature'); git(remote_named_main, 'remote', 'add', 'origin', str(bare)); git(remote_named_main, 'remote', 'add', 'main', str(bare))
    upstream_wt = root/'upstream-worktree'; upstream_wt.mkdir(); git(upstream_wt, 'init', '-b', 'feature'); git(upstream_wt, 'remote', 'add', 'upstream', str(bare)); git(upstream_wt, 'config', 'branch.feature.remote', 'upstream'); git(upstream_wt, 'config', 'branch.feature.merge', 'refs/heads/main')
    matching = root/'matching'; matching.mkdir(); git(matching, 'init', '-b', 'main'); git(matching, '-c', 'user.name=Guard Test', '-c', 'user.email=guard@example.invalid', 'commit', '--allow-empty', '-m', 'initial'); git(matching, 'branch', 'feature'); git(matching, 'checkout', 'feature'); git(matching, 'remote', 'add', 'origin', str(bare)); git(matching, 'config', 'push.default', 'matching')
    configured_matching = root/'configured-matching'; configured_matching.mkdir(); git(configured_matching, 'init', '-b', 'feature'); git(configured_matching, 'remote', 'add', 'origin', str(bare)); git(configured_matching, 'config', 'remote.origin.push', ':')
    push_remote = root/'push-remote'; push_remote.mkdir(); git(push_remote, 'init', '-b', 'feature'); git(push_remote, 'remote', 'add', 'origin', str(bare)); git(push_remote, 'remote', 'add', 'deploy', str(bare)); git(push_remote, 'config', 'branch.feature.pushRemote', 'deploy'); git(push_remote, 'config', 'remote.deploy.push', 'HEAD:main')
    push_default = root/'push-default'; push_default.mkdir(); git(push_default, 'init', '-b', 'feature'); git(push_default, 'remote', 'add', 'origin', str(bare)); git(push_default, 'remote', 'add', 'deploy', str(bare)); git(push_default, 'config', 'remote.pushDefault', 'deploy'); git(push_default, 'config', 'remote.deploy.push', 'HEAD:main')
    webapp = root/'webapp'; webapp.mkdir(); git(webapp, 'init', '-b', 'main')
    git(webapp, 'remote', 'add', 'origin', 'git@github.com:acme/webapp.git')
    other = root/'other'; other.mkdir(); git(other, 'init', '-b', 'main')
    git(other, 'remote', 'add', 'origin', 'https://github.com/combinaut/not-webapp.git')
    repo_file = webapp/'.orchestration.toml'
    local_file = webapp/'.orchestration.local.toml'
    (webapp/'app').mkdir()
    assert hook("git grep needle", webapp).returncode == 0
    repo_file.write_text('[guard]\nbig_repo = true\n')
    assert hook("git grep needle", webapp).returncode == 2
    assert hook("git grep needle", webapp/'app').returncode == 2
    assert hook("git grep needle", other).returncode == 0
    os.environ["ORCH_BIG_REPO"] = "0"
    assert hook("git grep needle", webapp).returncode == 0
    del os.environ["ORCH_BIG_REPO"]
    local_file.write_text('[guard]\nbig_repo = false\n')
    assert hook("git grep needle", webapp).returncode == 0
    local_file.unlink()
    no_tomllib = subprocess.run([sys.executable, '-c', 'import runpy, sys; sys.modules["tomllib"] = None; sys.argv = [sys.argv[1]]; runpy.run_path(sys.argv[0], run_name="__main__")', str(HOOK)],
                                input=json.dumps({'tool_name': 'Bash', 'cwd': str(webapp), 'tool_input': {'command': 'git grep needle'}}), capture_output=True, text=True, timeout=20)
    assert no_tomllib.returncode == 2 and 'No git grep in a configured big repo' in no_tomllib.stderr, no_tomllib.stderr
    good_config = repo_file.read_text()
    try:
        for broken in ('[guard\n', '[guard]\nbig_repo = "yes"\n', '[heavy_slots]\nmax = 2\n', b'[guard]\nbig_repo = true\n# \xff\n'):
            repo_file.write_bytes(broken if isinstance(broken, bytes) else broken.encode())
            result = hook("git grep needle", webapp)
            assert result.returncode == 2 and 'Cannot load the repo settings' in result.stderr, (broken, result.stderr)
            assert hook("git cat-file --batch-all-objects --batch", webapp).returncode == 2
            assert hook("git ls-tree -r HEAD | git cat-file --batch", webapp).returncode == 2
            assert hook("git cat-file -p HEAD", webapp).returncode == 0
            assert hook("git status", webapp).returncode == 0
    finally:
        repo_file.write_text(good_config)
    msg = root/'message.txt'; msg.write_text('Co-Authored-By: Claude <bot@anthropic.com>')
    oversized_msg = root/'oversized-message.txt'; oversized_msg.write_text('x'*(1024*1024) + 'Co-Authored-By: Claude')
    oversized_plain = root/'oversized-plain.txt'; oversized_plain.write_text('x'*(1024*1024 + 1))
    original_read_bytes = Path.read_bytes
    Path.read_bytes = lambda self: (_ for _ in ()).throw(AssertionError('unbounded read'))
    try:
        assert guard.file_text(str(oversized_plain), root) == guard.FILE_TOO_LARGE_REASON
    finally:
        Path.read_bytes = original_read_bytes
    graphql_input = root/'graphql-input.json'; graphql_input.write_text('{"query":"mutation { updatePullRequest(input:{body:\"Co-Authored-By: Claude\"}) { clientMutationId } }"}')
    fifo = root/'message.fifo'; os.mkfifo(fifo)
    clean = root/'clean.txt'; clean.write_text('A plain commit message')
    cases = []
    def case(name, command, deny=False, cwd=repo):
        cases.append((name, command, deny, cwd))
    for ref in ('main', 'master', 'HEAD:main', 'x:refs/heads/main', '+main', 'feature:master'):
        case('push '+ref, 'git push origin '+ref, True)
    case('push protected ref with glued redirect', 'git push origin HEAD:main>/dev/null', True)
    case('push --repo protected ref', 'git push --repo=origin main', True, isolated)
    case('push --repo protected ref with remote named main', 'git push --repo=origin main', True, remote_named_main)
    case('push all', 'git push --all origin', True, isolated)
    case('push mirror', 'git push --mirror origin', True, isolated)
    case('push matching refspec', 'git push origin :', True, isolated)
    case('push forced matching refspec', 'git push origin +:', True, isolated)
    case('push wildcard protected refs', "git push origin 'refs/heads/*:refs/heads/*'", True, isolated)
    for flag in ('-f', '--force', '--force-with-lease', '--force-if-includes'):
        case('force '+flag, 'git push '+flag+' origin HEAD:main', True)
    case('bare main', 'git push', True); case('origin main', 'git push origin', True); case('source-only HEAD on main', 'git push origin HEAD', True)
    case('redirected main push', 'git push origin 2>&1', True)
    case('configured protected push', 'git -c remote.origin.push=HEAD:main push', True)
    case('upstream main', 'git push', True, wt); case('upstream main with remote', 'git push origin', True, wt)
    case('non-origin upstream main', 'git push upstream', True, upstream_wt)
    case('matching push default', 'git push origin', True, matching)
    case('configured matching push', 'git push origin', True, configured_matching)
    case('branch push remote protected push', 'git push', True, push_remote)
    case('default push remote protected push', 'git push', True, push_default)
    case('cd push', f'cd {wt} && git push', True, root); case('git -C push', f'git -C {wt} push', True, root)
    case('env push', 'env MODE=test git push origin HEAD:main', True); case('pipeline push', 'echo ok | git push origin HEAD:main', True)
    case('env option push', 'env -i git push origin HEAD:main', True); case('command option push', 'command -- git push origin HEAD:main', True)
    case('env split-string push', "env -S 'git push origin HEAD:main'", True)
    case('env long split-string push', "env --split-string='git push origin HEAD:main'", True)
    for name, command in [
        ('env combined short options', 'env -i0 git push origin HEAD:main'),
        ('command combined short options', 'command -pv git push origin HEAD:main'),
        ('exec combined short options', 'exec -cl git push origin HEAD:main'),
        ('time combined short options', 'time -avq git push origin HEAD:main'),
        ('timeout combined short options', 'timeout -sTERM -k5 60 git push origin HEAD:main'),
        ('nice attached short option', 'nice -n5 git push origin HEAD:main'),
        ('nohup option terminator', 'nohup -- git push origin HEAD:main'),
        ('xargs combined short options', 'xargs -tn 1 git push origin HEAD:main'),
    ]:
        case(name, command, True)
    case('quoted semicolon refspec push', "git push origin ';' HEAD:main", True, isolated)
    case('ordinary echo guarded words', 'echo git push origin HEAD:main')
    case('continued push', 'git push \\\n+ origin HEAD:main', True)
    case('keyword push', 'if true; then git push origin HEAD:main; fi', True)
    case('grouped push', '{ git push origin HEAD:main; }', True)
    case('negated push', '! git push origin HEAD:main', True)
    case('timeout push', 'timeout 60 git push origin HEAD:main', True)
    case('unknown git option push', 'git -P -C . push origin HEAD:main', True)
    case('ANSI quoted push', "echo $'it\\'s'; git push origin HEAD:main", True)
    case('subshell push', f'(cd {wt}; git push)', True, root)
    for wrapper, options in guard.WRAPPER_OPTIONS.items():
        for option, arity in options.items():
            if wrapper == 'env' and option in ('-S', '--split-string'):
                continue
            prefix = [wrapper, option]
            if arity == 1:
                case(f'{wrapper} {option} separate value', ' '.join(prefix+['VALUE']+(['60'] if wrapper == 'timeout' else [])+['git', 'push', 'origin', 'HEAD:main']), True)
                attached = option+'=VALUE' if option.startswith('--') else option+'VALUE'
                case(f'{wrapper} {option} attached value', ' '.join([wrapper, attached]+(['60'] if wrapper == 'timeout' else [])+['git', 'push', 'origin', 'HEAD:main']), True)
            elif arity == 'optional':
                case(f'{wrapper} {option} optional', ' '.join(prefix+['git', 'push', 'origin', 'HEAD:main']), True)
                case(f'{wrapper} {option} optional attached', ' '.join([wrapper, option+'VALUE', 'git', 'push', 'origin', 'HEAD:main']), True)
            else:
                case(f'{wrapper} {option}', ' '.join(prefix+(['60'] if wrapper == 'timeout' else [])+['git', 'push', 'origin', 'HEAD:main']), True)
    for name, command in [('Claude trailer', 'git commit -m "Co-Authored-By: Claude"'), ('anthropic trailer', 'git commit --message="co-authored-by: Bot <b@anthropic.com>"'), ('generated bracket', 'gh pr create --body "Generated with [Claude Code]"'), ('generated plain', 'gh pr edit --title "Generated with Claude Code"'), ('robot', 'gh pr create --body "🤖 Generated"'), ('session', 'git commit -m "claude.ai/code/session_123"'), ('commit file', f'git commit -F {msg}'), ('PR file', f'gh pr edit --body-file {msg}'), ('API raw', 'gh api repos/a/b/pulls -f body="Generated with Claude Code"'), ('API file', f'gh api repos/a/b/pulls/1 -X PATCH -F body=@{msg}'), ('direct heredoc', "git commit -F - <<'EOF'\nCo-Authored-By: Claude\nEOF"), ('substitution heredoc', 'git commit -m "$(cat <<\'EOF\'\nGenerated with Claude Code\nEOF\n)"')]:
        case(name, command, True)
    case('commit trailer', 'git commit --trailer "Co-authored-by: Claude"', True)
    for name, command in [
        ('Codex trailer', 'git commit -m "x" -m "Co-authored-by: Codex <codex@openai.com>"'),
        ('API issues endpoint', 'gh api -X PATCH repos/a/b/issues/27 -f body="Co-Authored-By: Claude"'),
        ('API queried issues endpoint', "gh api -X PATCH 'repos/a/b/issues/27?foo=bar' -f body=\"Co-Authored-By: Claude\""),
        ('API piped input', "printf '{\"body\":\"Co-Authored-By: Claude\"}' | gh api repos/a/b/pulls/1 -X PATCH --input -"),
        ('API here-string input', "gh api repos/a/b/pulls/1 -X PATCH --input - <<< '{\"body\":\"Co-Authored-By: Claude\"}'"),
        ('GraphQL inline PR update', 'gh api graphql -f query="mutation { updatePullRequest(input:{pullRequestId:\"x\", body:\"Co-Authored-By: Claude\"}) { clientMutationId } }"'),
        ('GraphQL variable PR update', 'gh api graphql -f query="mutation($b:String!){updatePullRequest(input:{pullRequestId:\"x\",body:$b}){clientMutationId}}" -f b="Co-Authored-By: Claude"'),
        ('GraphQL input file', f'gh api graphql --input {graphql_input}'),
        ('PR merge body', 'gh pr merge 27 --squash --body "Co-Authored-By: Claude"'),
        ('here string', 'git commit -F - <<< "Co-Authored-By: Claude"'),
        ('piped commit message', 'printf "Co-Authored-By: Claude\\n" | git commit -F -'),
        ('same command message file', "cat > /tmp/msg <<'EOF'\nCo-Authored-By: Claude\nEOF\ngit commit -F /tmp/msg"),
        ('oversized message file', f'git commit -F {oversized_msg}'),
        ('oversized plain message file', f'git commit -F {oversized_plain}'),
        ('FIFO message file', f'git commit -F {fifo}'),
        ('ChatGPT trailer', 'git commit -m "Co-authored-by: ChatGPT <noreply@chatgpt.com>"'),
        ('ChatGPT generated using', 'git commit -m "Generated using ChatGPT"'),
        ('Copilot generated by', 'git commit -m "Generated-by: Copilot"'),
    ]:
        case(name, command, True)
    case('combined commit message option', 'git commit -am "Co-Authored-By: Claude"', True)
    case('PR short body', 'gh pr create -b "Generated with Claude Code"', True)
    case('PR short title', 'gh pr edit -t "Generated with Claude Code"', True)
    case('PR global repo option', 'gh --repo owner/repo pr create --body "Generated with Claude Code"', True)
    case('shared fetch', 'git fetch --depth=2 origin', True); case('worktree pull', 'git pull --unshallow', True, wt)
    case('shared deepen fetch', 'git fetch --deepen 50 origin', True, wt); case('shared deepen pull', 'git pull --deepen=50', True, wt)
    case('shared shallow since', 'git fetch --shallow-since=2025-01-01', True); case('shared shallow exclude', 'git fetch --shallow-exclude=main', True, wt)
    for sub in ('', 'push', 'pop', 'drop', 'clear', 'apply'):
        case('stash '+sub, 'git stash '+sub, True, wt)
    case('stash primary with worktree', 'git stash push', True, repo)
    for command in ('pkill -f vite', 'pkill -f "bin/dev-server" -P $$', 'pkill -KILL -P $$', 'pkill puma', 'pgrep -f rails | xargs kill', 'pgrep -f rails | xargs -r kill', 'pgrep -f rails | xargs -n1 kill -9', 'pgrep -f rails | xargs -0 kill', 'killall ruby', 'killall node', 'killall puma'):
        case(command, command, True)
    case('pgrep full kill', 'pgrep --full rails | xargs kill', True)
    case('ps grep kill', "ps aux | grep vite | awk '{print $2}' | xargs kill", True)
    for name, command in [
        ('substitution pgrep kill', 'kill $(pgrep -f vite)'),
        ('backtick pgrep kill', 'kill -9 `pgrep -f rails`'),
        ('while pgrep kill', 'pgrep -f vite | while read p; do kill $p; done'),
        ('bracket grep kill', "ps aux | grep [v]ite | awk '{print $2}' | xargs kill"),
        ('awk ps kill', "ps -ef | awk '/vite/ {print $2}' | xargs kill"),
        ('substitution ps kill', "kill $(ps aux | grep vite | awk '{print $2}')"),
    ]:
        case(name, command, True)
    case('killall regex vite', 'killall -m "vit.*"', True); case('killall bundle', 'killall bundle', True)
    case('killall regex rails', 'killall -m "rail.*"', True)
    case('killall clustered regex rails', 'killall -qm "rail.*"', True)
    for branch in ('feature', 'fix-main-menu', 'domain'):
        case('allow push '+branch, 'git push -u origin '+branch)
    case('force feature', 'git push --force-with-lease origin HEAD:feature'); case('unresolved bare push', 'git push origin', cwd=isolated)
    case('chain cd feature', f'cd {wt} && git push -u origin feature', cwd=root)
    case('comment attribution', 'gh pr comment 1 --body "*(Generated using Claude Code)*"'); case('issue comment', 'gh issue comment 1 --body "🤖 Generated"')
    case('comment then PR edit', 'gh pr comment 36 --body "x *(Generated using Claude Code)*" && gh pr edit 36 --add-label ready')
    case('commit then comment', 'git commit -m "fix: x" && gh pr comment 1 --body "y *(Generated using Claude Code)*"')
    case('API issue collection', 'gh api repos/a/b/issues -f title="x" -f body="Generated with Claude Code"')
    case('issue create', 'gh issue create --body "Generated with Claude Code"'); case('review API', 'gh api repos/a/b/pulls/1/comments -f body="Generated with Claude Code"')
    case('API GET', 'gh api repos/a/b/pulls -X GET -f body="Generated with Claude Code"'); case('plain commit', 'git commit -m "Fix main menu"')
    case('plain file', f'git commit -F {clean}'); case('plain PR', 'gh pr create --title "Fix" --body "Details"')
    case('plain heredoc', "git commit -F - <<'EOF'\nA plain commit\nEOF"); case('plain substitution heredoc', 'git commit -m "$(cat <<\'EOF\'\nA plain commit\nEOF\n)"')
    case('stash list worktree', 'git stash list', cwd=wt); case('stash show worktree', 'git stash show stash@{0}', cwd=wt)
    case('stash root', 'git stash push', cwd=isolated); case('normal fetch', 'git fetch origin', cwd=wt); case('isolated depth', 'git fetch --depth=2 origin', cwd=isolated)
    case('PID kill', 'kill -TERM 12345'); case('PID check', 'kill -0 12345'); case('pidfile kill', 'kill "$(cat tmp/pids/server-1.pid)"')
    case('pidfile liveness check', 'pid=$(cat tmp/pids/vite.pid); ps -p $pid && kill -TERM $pid')
    case('quoted pidfile liveness check', 'ps -p "$(cat tmp/pids/dev-instance.pid)" && kill -TERM "$(cat tmp/pids/dev-instance.pid)"')
    case('pidfile command liveness check', 'PID=$(cat tmp/pids/puma.pid); ps -p $PID -o command= ; kill $PID')
    case('pgrep process name', 'pgrep ruby'); case('pgrep -fl listing only', 'pgrep -fl "review-prs/scripts/prs.py watch"'); case('pgrep -f count only', 'pgrep -f bin/dev-server | wc -l')
    case('PID kill before pgrep full', 'kill -TERM 12345; pgrep -f rails')
    case('nonserver killall', 'killall ExampleScratchApp'); case('normal status', 'git status')
    case('ps grep kill count', 'ps aux | grep node | grep -c kill')
    case('quoted broad kill text', "printf 'ps aux | grep vite | xargs kill\\n' | grep vite")
    case('multiple quoted broad kill text', "printf '%s\\n' 'ps aux | grep vite | xargs kill' | grep vite")
    case('substitution broad kill', 'printf \'%s\\n\' "$(pgrep -f vite)" | xargs kill', True)
    case('webapp git grep', 'git grep needle', True, webapp)
    case('webapp git -C grep', f'git -C {webapp} grep needle', True, root)
    case('webapp cd grep', f'cd {webapp} && git grep needle', True, root)
    case('webapp cd terminator grep', f'cd -- {webapp} && git grep needle', True, root)
    case('webapp cd physical grep', f'cd -P {webapp} && git grep needle', True, root)
    case('failed conditional cd webapp grep', f'false && cd {other}; git grep needle', True, webapp)
    case('skipped conditional cd webapp grep', f'true || cd {other}; git grep needle', True, webapp)
    case('failed cd webapp grep', 'cd /missing || git grep needle', True, webapp)
    case('webapp cat-file batch tree', 'git ls-tree -r HEAD | git cat-file --batch', True, webapp)
    case('webapp continued bundled cat-file tree', 'git ls-tree \\\n-rt HEAD | git cat-file --batch', True, webapp)
    case('webapp separate tree and cat-file', 'git ls-tree -r HEAD >/tmp/tree; git cat-file --batch </tmp/objects', cwd=webapp)
    case('webapp cat-file batch-check tree', 'git ls-tree --full-tree -r HEAD | git cat-file --batch-check', True, webapp)
    case('webapp formatted cat-file tree', "git ls-tree -r HEAD | git cat-file '--batch-check=%(objectname)'", True, webapp)
    case('webapp clustered recursive cat-file tree', 'git ls-tree -rz HEAD | git cat-file --batch', True, webapp)
    case('webapp batch all objects', 'git cat-file --batch-all-objects --batch-check', True, webapp)
    case('webapp all-history cat-file', 'git rev-list --objects --all | git cat-file --batch-check', True, webapp)
    case('webapp rev-list objects head', 'git rev-list --objects HEAD | git cat-file --batch-check', True, webapp)
    case('webapp rev-list objects branches', 'git rev-list --objects --branches | git cat-file --batch-check', True, webapp)
    case('webapp ls-files batch', "git ls-files --format='%(objectname)' | git cat-file --batch", True, webapp)
    case('webapp process-substitution history batch', 'git cat-file --batch-check < <(git rev-list --objects --all)', True, webapp)
    case('webapp nonrecursive formatted root tree', "git ls-tree --full-tree --format='%(objectname)' HEAD | git cat-file --batch-check", cwd=webapp)
    case('webapp config-spoofed grep', 'git -c remote.origin.url=https://github.com/combinaut/other.git grep needle', True, webapp)
    case('webapp git -C cat-file tree', f'git -C {webapp} ls-tree -r HEAD | git -C {webapp} cat-file --batch', True, root)
    case('non-webapp grep', 'git grep needle', cwd=other)
    case('leave webapp before grep', f'cd {other} && git grep needle', cwd=webapp)
    case('successful and cd leaves webapp', f'true && cd {other}; git grep needle', cwd=webapp)
    case('successful or cd leaves webapp', f'false || cd {other}; git grep needle', cwd=webapp)
    case('plain cat-file', 'git cat-file -p HEAD', cwd=webapp)
    for name, command in [
        ('pgrep kill semicolon', 'pgrep -f rails | xargs kill; true'),
        ('pgrep kill subshell', '(pgrep -f rails | xargs kill)'),
        ('ps grep kill semicolon', "ps aux | grep vite | awk '{print $2}' | xargs kill; true"),
        ('BSD ps ax kill', "ps ax | grep vite | awk '{print $1}' | xargs kill"),
        ('BSD ps x kill', "ps x | grep node | awk '{print $1}' | xargs kill"),
        ('user ps kill', "ps -u \"$USER\" | grep vite | awk '{print $2}' | xargs kill"),
        ('BSD ps axo kill', "ps axo pid,comm | grep puma | awk '{print $1}' | xargs kill -9"),
        ('for ps kill', "for p in $(ps ax | grep vite | awk '{print $1}'); do kill $p; done"),
        ('quoted substitution ps kill', "kill \"$(ps aux | grep vite | awk '{print $2}')\""),
        ('pgrep name kill', 'pgrep puma | xargs kill'),
        ('pgrep exact kill', 'pgrep -x node | xargs kill'),
        ('shell string broad kill', "bash -c 'pgrep -f vite | xargs kill'"),
        ('shell heredoc broad kill', "bash <<'EOF'\npgrep -f vite | xargs kill\nEOF"),
        ('double-quoted comment substitution kill', 'gh pr comment 1 --body "$(pgrep -f vite | xargs kill)"'),
        ('shell string substitution kill', 'sh -c "kill \\$(pgrep -f vite)"'),
        ('eval broad kill', "eval 'pgrep -f vite | xargs kill'"),
        ('xargs shell program kill', "pgrep -f vite | xargs sh -c 'kill $0'"),
        ('login shell program kill', "bash -lc 'pgrep -f vite | xargs kill'"),
        ('stdin shell heredoc kill', "bash -s <<'EOF'\npgrep -f vite | xargs kill\nEOF"),
        ('self-referencing reassignment kill', 'pids=$(pgrep -f vite); pids="$pids"; kill $pids'),
        ('conditional reassignment kill', 'pids=$(pgrep -f vite); false && pids=$(cat tmp/pids/vite.pid); kill $pids'),
        ('apostrophe comment then push', "# Don't run this\ngit push origin HEAD:main"),
        ('apostrophe comment then attribution', "# it's fine\ngit commit -m \"Co-Authored-By: Claude\""),
        ('quote comment then pkill', '# say "hi\npkill -f vite'),
        ('mid-word hash then push', 'x=a#b; git push origin HEAD:main'),
        ('prefix assignment keeps listing variable', 'pids=$(pgrep -f vite); pids=12345 true; kill -TERM $pids'),
        ('subshell assignment keeps listing variable', 'pids=$(pgrep -f vite); (pids=12345); kill -TERM $pids'),
        ('piped assignment keeps listing variable', 'pids=$(pgrep -f vite); pids=12345 | cat; kill -TERM $pids'),
        ('pidfile-looking pattern after --', "pgrep -f -- '-F|node' | xargs kill"),
        ('long pidfile-looking pattern after --', "pgrep -f -- '--pidfile=x|node' | xargs kill"),
        ('xargs kill with explicit PID', 'pgrep -f node | xargs kill -TERM 12345'),
        ('xargs batched kill with explicit PID', 'pgrep -f node | xargs -n 20 kill -TERM 12345'),
        ('xargs path kill with explicit PID', 'pgrep -f node | xargs /bin/kill -TERM 12345'),
        ('redirect before xargs kill', 'pgrep -f node | xargs 2>/dev/null kill -TERM'),
        ('redirect before xargs', 'pgrep -f node | 2>/dev/null xargs kill -TERM'),
        ('redirect before pgrep', '2>/dev/null pgrep -f node | xargs kill -TERM'),
        ('redirect after xargs option with explicit PID', 'pgrep -f node | xargs -r 2>/dev/null kill -TERM 12345'),
        ('redirect after xargs env', 'pgrep -f node | xargs -r env 2>/dev/null kill -TERM'),
        ('substitution in redirect target kill', 'echo hi > "$(pgrep -f vite | xargs kill)"'),
        ('substitution redirect then push', 'echo $(date) > /tmp/x; git push origin main'),
        ('substitution redirect then commit attribution', 'echo $(date) > /tmp/x; git commit -m "Co-Authored-By: Claude"'),
        ('subshell redirect then push', '(true) > /dev/null; git push origin main'),
        ('pgrep full any name kill', 'pgrep -f server | xargs kill'),
        ('pgrep full variable kill', 'pgrep -f "$APP" | xargs kill'),
        ('pgrep full regex kill', 'pgrep -f v.te | xargs kill'),
        ('pgrep full unlisted name kill', 'pgrep -f redis-server | xargs kill'),
        ('quoted hash before substitution kill', "printf '#' $(pgrep -f vite | xargs kill)"),
        ('path xargs kill', 'pgrep -f vite | /usr/bin/xargs kill'),
        ('path pgrep xargs kill', '/usr/bin/pgrep -f vite | /usr/bin/xargs /bin/kill'),
        ('path env push', '/usr/bin/env git push origin HEAD:main'),
        ('path nohup push', '/usr/bin/nohup git push origin HEAD:main'),
        ('sudo pkill', 'sudo pkill -f vite'),
        ('sudo substitution kill', 'sudo kill $(pgrep -f vite)'),
        ('variable pgrep kill', 'pids=$(pgrep -f vite); kill $pids'),
        ('quoted semicolon xargs delimiter kill', "pgrep -f vite | xargs -d ';' kill"),
        ('quoted ampersand xargs delimiter kill', "pgrep -f vite | xargs -d '&' kill"),
        ('quoted pipe xargs replace kill', "pgrep -f vite | xargs -I '|' kill '|'"),
        ('API template value graphql inline body', "gh api repos/a/b/pulls/1 -X PATCH -f body='Co-Authored-By: Claude' --template graphql"),
        ('API template value graphql file body', f'gh api repos/a/b/pulls/1 -X PATCH -F body=@{msg} --template graphql'),
        ('API template value graphql stdin body', "gh api repos/a/b/pulls/1 -X PATCH --input - --template graphql <<< '{\"body\":\"Co-Authored-By: Claude\"}'"),
        ('API header naming comments', "gh api -X PATCH repos/a/b/pulls/1 -H 'X-Note: /comments' --input - <<< '{\"body\":\"Co-Authored-By: Claude\"}'"),
        ('API query naming comments', "gh api -X PATCH 'repos/a/b/pulls/1?x=/comments' --input - <<< '{\"body\":\"Co-Authored-By: Claude\"}'"),
        ('PR here-string body file', 'gh pr create --title t --body-file - <<< "Co-Authored-By: Claude"'),
        ('bare Codex trailer', 'git commit -m "x" -m "Co-authored-by: Codex"'),
        ('unparseable API attribution', "gh api repos/a/b/pulls -f body=\"Co-Authored-By: Claude\" 'unterminated"),
        ('commit here-string naming a comment', 'git commit -F - <<< "fix; see gh pr comment 1 Co-Authored-By: Claude"'),
    ]:
        case(name, command, True)
    redirect_targets = {'<&': '0', '>&': '1', '<<<': "''", '<<': 'EOF'}
    input_operators = ('<', '<>', '<<<', '<<', '<&')
    for operator in ('<', '>', '>>', '<>', '<<<', '<<', '<&', '>&', '>|', '&>', '&>>'):
        target = redirect_targets.get(operator, '/dev/null')
        prefixes = [''] if operator.startswith('&') else ['2', '{fd}'] + ([] if operator in input_operators else [''])
        for prefix in prefixes:
            for spacing in ('', ' '):
                redirect = f'{prefix}{operator}{spacing}{target}'
                for position, command in [
                    ('before the listing', f'{redirect} pgrep -f node | xargs -r -n 20 env nohup kill -TERM 12345'),
                    ('after the listing', f'pgrep -f node {redirect} | xargs -r -n 20 env nohup kill -TERM 12345'),
                    ('before xargs', f'pgrep -f node | {redirect} xargs -r -n 20 env nohup kill -TERM 12345'),
                    ('between xargs options', f'pgrep -f node | xargs -r {redirect} -n 20 env nohup kill -TERM 12345'),
                    ('between wrappers', f'pgrep -f node | xargs -r -n 20 env {redirect} nohup kill -TERM 12345'),
                    ('before kill', f'pgrep -f node | xargs -r -n 20 env nohup {redirect} kill -TERM 12345'),
                    ('after kill args', f'pgrep -f node | xargs -r -n 20 env nohup kill -TERM 12345 {redirect}'),
                ]:
                    case(f'redirect {redirect!r} {position}', command, True)
    glued_redirects = {'>': '/dev/null', '>>': '/dev/null', '>|': '/dev/null', '>&': '2', '<': '/dev/null', '<>': '/dev/null',
                       '<<<': "''", '<&': '0', '&>': '/dev/null', '&>>': '/dev/null'}
    for control in ('|', '|&', ';', '&&', '||', '&', '\n', '('):
        for operator, target in glued_redirects.items():
            if control == '&' and operator.startswith('>'):
                continue
            for spacing in ('', ' '):
                redirect = f'{operator}{spacing}{target}'
                def joined(command):
                    return f'({redirect} {command})' if control == '(' else f'true{control}{redirect} {command}'
                for context, command in [('kill', 'kill $(pgrep -f node)'), ('push', 'git push origin main'),
                                         ('commit', 'git commit -m "x Co-Authored-By: Claude"')]:
                    case(f'control {control!r} redirect {redirect!r} {context}', joined(command), True)
                if control in ('|', '|&') and not operator.startswith('<'):
                    case(f'pipe {control!r} redirect {redirect!r} xargs kill', f'pgrep -f node {control}{redirect} xargs kill -TERM', True)
    case('pipe with fd 0 replaced', 'pgrep -f node |<>/dev/null xargs kill -TERM')
    case('unquoted backtick unlisted name kill', 'kill `pgrep -f redis-server`', True)
    case('unquoted backtick quoted pattern kill', "kill `pgrep -f 'redis server'`", True)
    case('quoted redirect word as refspec', "git push origin '>' main", True, isolated)
    for name, command in [
        ('here-string replaces the PID stream', 'pgrep -f node | xargs kill -TERM <<< 12345'),
        ('file replaces the PID stream', 'pgrep -f node | xargs -r kill -TERM 0< tmp/pids/vite.pid'),
        ('quoted redirect characters', "printf '%s\\n' '2<>' '<<<' '&>>' '>|' | grep -c x"),
        ('number before spaced redirect is a target', 'kill -TERM 12345 >/dev/null'),
    ]:
        case(name, command)
    footer = '*(Generated using Claude Code)*'
    for name, command in [
        ('multi-line comment footer', f'gh pr comment 36 --body "Addressed in fb655d7.\n\n{footer}" && gh pr edit 36 --add-label ready'),
        ('table comment footer', f'gh pr comment 36 --body "| a | b |\n|---|---|\n\n{footer}" && gh pr edit 36 --add-label ready'),
        ('ampersand comment footer', f'gh pr comment 36 --body "A & B\n\n{footer}" && gh pr edit 36 --add-label ready'),
        ('global repo comment footer', f'gh --repo acme/webapp pr comment 1 --body "x\n\n{footer}" && gh pr edit 1 --add-label ready'),
        ('heredoc comment footer', f"git commit -m \"fix: x\" && gh pr comment 1 --body-file - <<'EOF'\nDone.\n\n{footer}\nEOF"),
        ('GraphQL thread reply footer', 'git commit -m "fix: x" && gh api graphql -f query=\'mutation{addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:"x",body:"Fixed. ' + footer + '"}){clientMutationId}}\''),
        ('comment quoting kill pipeline', "gh pr comment 1 --body 'The guard blocks pgrep -f vite | xargs kill now'"),
        ('comment opening with kill pipeline', "gh pr comment 1 --body 'pgrep -f vite | xargs kill is blocked'"),
        ('heredoc comment quoting kill pipeline', "gh pr comment 1 --body-file - <<'EOF'\nThe guard blocks pgrep -f vite | xargs kill now\nEOF"),
        ('grep for kill pipeline text', "grep -F 'pgrep -f vite | xargs kill' script.sh"),
        ('pgrep piped to grep for kill text', "pgrep -f vite | grep 'xargs kill'"),
        ('single-quoted comment with backticks', "gh pr comment 1 --body 'Run `pgrep -f vite | xargs kill` to see the block'"),
        ('stdin device body file', 'gh pr create --title t --body-file /dev/stdin'),
        ('listing then pidfile kill', 'pgrep -f vite > /dev/null && kill -TERM $(cat tmp/pids/vite.pid)'),
        ('pidfile pgrep then pidfile kill', 'pgrep -F tmp/pids/vite.pid && kill -TERM $(cat tmp/pids/vite.pid)'),
        ('pidfile pgrep kill', 'pgrep -F tmp/pids/vite.pid | xargs kill -TERM'),
        ('child listing then pidfile kill', 'pgrep -P $$ node; kill -TERM $(cat tmp/pids/node.pid)'),
        ('ps count then own PID check', 'ps aux | grep -c node && kill -0 $$'),
        ('ps saved then pidfile kill', 'ps aux | grep vite > ps.txt; kill -TERM $(cat tmp/pids/vite.pid)'),
        ('rg for kill pipeline', "rg 'pgrep -f vite | xargs kill' docs/"),
        ('grep notes for kill pipeline', "cat notes.md | grep 'pgrep -f vite | xargs kill'"),
        ('less file named like a kill pipeline', "less 'ps aux | grep vite | xargs kill'"),
        ('commented-out substitution', 'printf safe # $(pgrep -f vite | xargs kill)'),
        ('shell positional argument text', "bash -c 'printf \"%s\\n\" \"$1\"' -- 'pgrep -f vite | xargs kill'"),
        ('second -c as positional argument', "bash -c 'printf \"%s\\n\" \"$@\"' -- -c 'pgrep -f vite | xargs kill'"),
        ('shell program listing then pidfile kill', "bash -c 'pgrep -f vite >/dev/null; kill -TERM \"$(cat tmp/pids/vite.pid)\"'"),
        ('reassigned variable pidfile kill', 'pids=$(pgrep -f vite); pids=$(cat tmp/pids/vite.pid); kill -TERM "$pids"'),
        ('script reading heredoc data', "bash script.sh <<'EOF'\npgrep -f vite | xargs kill\nEOF"),
        ('env PID kill', 'env kill -TERM 12345'),
        ('timeout PID kill', 'timeout 5 kill -TERM 12345'),
        ('trailing comment with apostrophe', "git status # don't push"),
        ('queried comment footer then commit', "gh api 'repos/a/b/issues/3/comments?x=1' -f body=\"Fixed. *(Generated using Claude Code)*\" && git commit -m ok"),
        ('review reply footer then commit', 'gh api repos/a/b/pulls/36/comments/5/replies -f body="Fixed. *(Generated using Claude Code)*" && git commit -m ok'),
        ('repeated xargs options', 'pgrep -f vite | xargs ' + '-pp '*30 + 'ki'),
    ]:
        case(name, command)
    failed = []
    for name, command, deny, cwd in cases:
        result = hook(command, cwd)
        expected = 2 if deny else 0
        if result.returncode != expected:
            failed.append(name)
            print('FAIL', name, 'expected', expected, 'got', result.returncode, result.stderr.strip())
    print(f'Tests: {len(cases)} cases ({sum(c[2] for c in cases)} deny, {sum(not c[2] for c in cases)} allow); {len(failed)} failures')
    if failed:
        sys.exit(1)
    assert 'removeprefix' not in HOOK.read_text() and 'removesuffix' not in HOOK.read_text()
    for disabled, witness in [('guard_push', 'push main'), ('guard_attribution', 'Codex trailer'), ('guard_memory', 'shared deepen fetch'), ('guard_broad_kill', 'substitution pgrep kill'), ('guard_big_repo_reads', 'webapp batch all objects')]:
        caught = [name for name, command, deny, cwd in cases if bool(guard.evaluate(command, cwd, disabled=(disabled,))) != deny]
        assert witness in caught, (disabled, caught)
        print(f'Mutation: disable {disabled} -> {len(caught)} test failures; witness: {witness}')
    for bad in ('{', '{}', '{"cwd":123,"tool_input":{"command":"git status"}}', json.dumps({'cwd':str(repo),'tool_input':{'command':'git commit -m "unterminated'}})):
        result = subprocess.run([sys.executable, str(HOOK)], input=bad, capture_output=True, text=True)
        assert result.returncode == 0 and not result.stderr
    times = []
    for _ in range(20):
        start = time.perf_counter(); result = hook('git status', repo); assert result.returncode == 0; times.append((time.perf_counter()-start)*1000)
    print(f'git status allow timing: median {sorted(times)[10]:.2f} ms; min {min(times):.2f} ms; max {max(times):.2f} ms (20 process invocations)')
    log = test_home/'.claude/hooks/guard-bash.log'
    log.unlink(missing_ok=True)
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({'tool_input': {'command': 'git push origin main'}}), capture_output=True, text=True)
    assert result.returncode == 0 and not result.stderr
    entry = log.read_text()
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\S* guard-bash fail-open: KeyError: 'cwd' command='git push origin main'\n$", entry), entry
    print('Fail-open log:', entry.strip())
    start = time.perf_counter(); guard.evaluate('pgrep -f vite | xargs ' + '-pp '*40 + 'ki', repo)
    assert time.perf_counter()-start < 2, 'xargs option scan is not linear'
    check_installer(ROOT, root, repo)
    print('Installer: hook linked and PreToolUse Bash entry remains exactly once after a second install')
