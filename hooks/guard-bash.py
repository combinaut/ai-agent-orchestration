#!/usr/bin/env python3
"""Bash policy hook. Parses shell words; never executes the input.
Broad-read config errors deny the read; unrelated errors fail open.
Not a shell interpreter: dynamic variables, aliases and arbitrary shell wrappers
are intentionally outside coverage. Git probes are read-only and time-bounded.
"""
import datetime
import json
import fnmatch
import functools
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

PUSH_REASON = "Never push to main/master; open a PR (see .ai/git-workflow.md)."
ATTR_REASON = "No AI attribution in commits, PR titles or PR bodies (global + .ai/git-workflow.md); remove the trailer and retry."
FILE_TOO_LARGE_REASON = "Message/body file is too large to check safely; use a smaller file or remove the file input."
FILE_NONREGULAR_REASON = "Message/body file is not a regular file; use a regular file input."
BIG_REPO_REASON = (
    "No git grep in a configured big repo. Use rg scoped to the directories you need."
)
BIG_REPO_BATCH_REASON = "No whole-tree cat-file batch read in a configured big repo. Use rg scoped to the directories you need."
BAD_ATTR = re.compile(r"co-authored-by:\s*(?:Claude|Codex|ChatGPT|OpenAI|Copilot|Gemini)|co-authored-by:[^\n]*(?:anthropic\.com|openai\.com|chatgpt\.com)|generated-by:\s*(?:Claude|Codex|ChatGPT|OpenAI|Copilot|Gemini)|Generated (?:with|using) (?:\[?(?:Claude Code|Codex|ChatGPT|OpenAI|Copilot|Gemini)\]?)|🤖\s*Generated|claude\.ai/code/session_", re.I)


def log_failure(exc, command=None):
    try:
        line = f'{datetime.datetime.now().astimezone().isoformat(timespec="seconds")} guard-bash fail-open: {type(exc).__name__}: {exc}'
        if isinstance(command, str):
            line += f' command={command[:200]!r}'
        with (Path.home()/'.claude/hooks/guard-bash.log').open('a') as f:
            f.write(line.replace('\n', ' ')+'\n')
    except Exception:
        pass


def git_read(cwd, opts, *args):
    try:
        p = subprocess.run(['git', '-C', str(cwd), *opts, *args], capture_output=True, text=True, timeout=1)
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def protected(ref):
    ref = ref.lstrip('+')
    if ref.startswith('refs/heads/'):
        ref = ref[len('refs/heads/'):]
    return ref in ('main', 'master')


def protected_destination(ref):
    ref = ref.lstrip('+')
    if ref.startswith('refs/heads/'):
        ref = ref[len('refs/heads/'):]
    return protected(ref) or any(fnmatch.fnmatch(branch, ref) for branch in ('main', 'master'))


# GitHub wikis have no pull requests; master is the only way in.
def is_wiki(remote, cwd, opts):
    url = git_read(cwd, [value for i, value in enumerate(opts) if value != '-c' and (i == 0 or opts[i-1] != '-c')], 'remote', 'get-url', remote) or remote
    url = url.rstrip('/')
    if url.endswith('.git'):
        url = url[:-4]
    return url.endswith('.wiki')


def repo_settings(root):
    """Repo settings of the checkout at root, from the shared settings reader."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
    import orch_settings
    try:
        return orch_settings.load_repo(root)
    except orch_settings.ConfigError as error:
        raise OSError(str(error)) from None


def is_big_repo(cwd, opts):
    identity_opts = [value for i, value in enumerate(opts) if value != '-c' and (i == 0 or opts[i-1] != '-c')]
    root = git_read(cwd, identity_opts, 'rev-parse', '--show-toplevel')
    if not root:
        return False
    return repo_settings(root)['ORCH_BIG_REPO'] == '1'


def guard_push(args, cwd, opts, raw):
    if not args or args[0] != 'push':
        return None
    operands = []
    remote = ''
    bulk = False
    skip = False
    for a in args[1:]:
        if skip:
            if skip == 'repo':
                remote = a
            skip = False
            continue
        if a in ('--repo',):
            skip = 'repo'
        elif a.startswith('--repo='):
            remote = a.split('=', 1)[1]
        elif a in ('-o', '--push-option', '--receive-pack', '--exec'):
            skip = True
        elif a.startswith('-') and a != '--':
            bulk = bulk or a in ('--all', '--mirror')
        elif a != '--':
            operands.append(a)
    # First positional argument is a remote; remaining arguments are refspecs.
    branch = ''
    if not remote and operands:
        remote = operands.pop(0)
    elif not remote:
        branch = git_read(cwd, opts, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        remote = (git_read(cwd, opts, 'config', '--get', f'branch.{branch}.pushRemote') or
                  git_read(cwd, opts, 'config', '--get', 'remote.pushDefault') or
                  git_read(cwd, opts, 'config', '--get', f'branch.{branch}.remote') or 'origin')
    specs = operands
    if is_wiki(remote, cwd, opts):
        return None
    if bulk or any(spec.lstrip('+') == ':' for spec in specs):
        return PUSH_REASON
    if any(protected_destination(s.split(':', 1)[-1]) for s in specs):
        return PUSH_REASON
    if any(s in ('HEAD', '@') for s in specs):
        branch = branch or git_read(cwd, opts, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        if protected(branch):
            return PUSH_REASON
    if not specs:
        branch = branch or git_read(cwd, opts, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        upstream = git_read(cwd, opts, 'rev-parse', '--abbrev-ref', '@{upstream}')
        merge_ref = git_read(cwd, opts, 'config', '--get', f'branch.{branch}.merge')
        if protected(branch) or protected(upstream.rsplit('/', 1)[-1]) or protected_destination(merge_ref):
            return PUSH_REASON
        configured = git_read(cwd, opts, 'config', '--get-all', f'remote.{remote}.push').splitlines()
        if any(spec.lstrip('+') == ':' or protected_destination(spec.split(':', 1)[-1]) for spec in configured):
            return PUSH_REASON
        if not configured and git_read(cwd, opts, 'config', '--get', 'push.default') == 'matching':
            return PUSH_REASON
    return None


def flag_values(args, flags):
    values = []
    for i, arg in enumerate(args):
        if arg in flags and i+1 < len(args):
            values.append(args[i+1])
        for flag in flags:
            if arg.startswith(flag+'='):
                values.append(arg[len(flag)+1:])
            elif flag.startswith('-') and not flag.startswith('--') and arg.startswith(flag) and len(arg)>len(flag):
                values.append(arg[len(flag):])
            elif len(flag) == 2 and arg.startswith('-') and flag[1] in arg[1:]:
                position = arg.index(flag[1], 1)
                if position == len(arg)-1 and i+1 < len(args):
                    values.append(args[i+1])
                elif position < len(arg)-1:
                    values.append(arg[position+1:])
    return values


def file_text(name, cwd):
    if name in ('-', '/dev/stdin', '/dev/fd/0'):
        return ''
    path = Path(os.path.expanduser(name))
    if not path.is_absolute():
        path = Path(cwd)/path
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            return FILE_NONREGULAR_REASON
        with path.open('rb') as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            return FILE_TOO_LARGE_REASON
        return data.decode(errors='replace')
    except FileNotFoundError:
        return ''
    except OSError:
        return ''


def guard_attribution(args, cwd, opts, raw):
    if not args:
        return None
    raw = attribution_scan_text(raw)
    text = []
    files = []
    if args[0] == 'commit':
        if BAD_ATTR.search(raw):
            return ATTR_REASON
        text += flag_values(args, ('-m', '--message', '--trailer', '--guard-heredoc'))
        files += flag_values(args, ('-F', '--file'))
    elif len(args) >= 2 and args[:2] in (['pr', 'create'], ['pr', 'edit'], ['pr', 'merge']):
        if BAD_ATTR.search(raw):
            return ATTR_REASON
        text += flag_values(args, ('--title', '--body', '-t', '-b', '--guard-heredoc'))
        files += flag_values(args, ('--body-file', '-F'))
    elif args[0] == 'api':
        # Match the pull-request resource itself, never /comments or /reviews.
        endpoint = next((a for a in args[1:] if re.fullmatch(r'/?repos/[^/]+/[^/]+/(?:pulls(?:/\d+)?|issues/\d+)/?(?:\?[^#]*)?', a)), '')
        fields = flag_values(args, ('-f', '-F', '--raw-field', '--field'))
        method = flag_values(args, ('-X', '--method'))
        graphql = api_endpoint(args[1:]) == 'graphql'
        if (not endpoint and not graphql) or (method and method[-1].upper() not in ('POST', 'PATCH', 'PUT')):
            return None
        graphql_update = graphql and 'updatePullRequest' in raw
        if (endpoint or graphql_update) and BAD_ATTR.search(raw):
            return ATTR_REASON
        for field in fields:
            key, sep, value = field.partition('=')
            if sep and (key in ('body', 'title') or graphql):
                if value.startswith('@'):
                    files.append(value[1:])
                else:
                    text.append(value)
        text += flag_values(args, ('--guard-heredoc',))
        files += flag_values(args, ('--input',))
    else:
        return None
    file_contents = [file_text(f, cwd) for f in files]
    for content in file_contents:
        if content == FILE_TOO_LARGE_REASON:
            return FILE_TOO_LARGE_REASON
        if content == FILE_NONREGULAR_REASON:
            return FILE_NONREGULAR_REASON
    text += file_contents
    if args[0] == 'api' and graphql and any('updatePullRequest' in content for content in text):
        graphql_update = True
    if args[0] == 'api' and graphql and not graphql_update:
        return None
    if any(BAD_ATTR.search(t) for t in text):
        return ATTR_REASON
    return None


BROAD_KILL_REASON = 'feedback_never_pkill_f + reference_macos_pkill_options_after_pattern: never use pkill or feed pgrep -f into kill; stop only your own PID with kill -TERM <pid> (or your slot pidfile).'
PROTECTED_PROCESS = re.compile(r'(?:vite|puma|rails|bin/dev|dev-instance|node|ruby|bundle|sidekiq|esbuild|foreman|overmind)', re.I)
SHELL_EXECUTABLES = {'sh', 'bash', 'zsh', 'dash', 'ksh', 'eval'}
PS_PID_OPTIONS = {'-p', '--pid', 'p', '-q', '--quick-pid'}
PS_FORMAT_OPTIONS = {'-o', '-O', 'o', 'O', '--format'}
PS_FLAG_OPTIONS = {'-f', '-F', '-l', '-j', '-w', '-ww', 'w', 'ww', 'h', '-h', '--no-headers', 'l', 'j', 'u', 'v', '-c'}


OPERATOR_CHARS = ';&|()<>\n'
MASK = {c: chr(0xF0000+ord(c)) for c in OPERATOR_CHARS+'$` \t\'"\\'}
FD_MARK = chr(0xF00FF)
UNMASK = str.maketrans({**{v: k for k, v in MASK.items()}, FD_MARK: None})
REDIRECTION = re.compile(r'<|>|>>|<>|<<<|<<-?|<&|>&|>\||&>>?')
SHELL_OPERATOR = re.compile(r'<<<|&>>|;;&|&&|\|\||\|&|;;|;&|<<|<>|<&|>>|>\||>&|&>|[<>|&;()\n]')
BLOCK_OPENERS = {'while', 'until', 'for', 'if', 'case', 'select', '{'}
BLOCK_CLOSERS = {'done', 'fi', 'esac', '}'}


def mask_quoted(command):
    """Drops unquoted comments, marks redirection fd prefixes, keeps unquoted backtick bodies in one word, and masks quoted operators and single-quoted $ and backticks."""
    out, quote, index = [], '', 0
    while index < len(command):
        char = command[index]
        word_start = not quote and (index == 0 or command[index-1] in ' \t\r'+OPERATOR_CHARS)
        if char == '#' and word_start:
            end = command.find('\n', index)
            index = len(command) if end < 0 else end
            continue
        fd = re.match(r'(?:\d+|\{[A-Za-z_]\w*\})(?=[<>])', command[index:]) if word_start else None
        if fd:
            out += [FD_MARK, fd[0]]
            index += len(fd[0])
            continue
        if char == '`' and not quote:
            end = command.find('`', index+1)
            end = len(command) if end < 0 else end
            out += ['`'] + [MASK.get(c, c) for c in command[index+1:end]] + ['`' if end < len(command) else '']
            index = end+1
            continue
        if char == '\\' and quote != "'" and index+1 < len(command):
            out += [char, MASK.get(command[index+1], command[index+1])]
            index += 2
            continue
        if char in '\'"' and quote in ('', char):
            quote = '' if quote else char
            out.append(char)
        elif (quote == "'" and char in MASK) or (quote == '"' and char in OPERATOR_CHARS):
            out.append(MASK[char])
        else:
            out.append(char)
        index += 1
    return ''.join(out)


def unmask(word):
    return word.translate(UNMASK)


def shell_tokens(command):
    lex = shlex.shlex(mask_quoted(expand_heredocs(re.sub(r'\\\n[ \t]*', '', command))), posix=True, punctuation_chars=OPERATOR_CHARS)
    lex.whitespace = ' \t\r'
    lex.whitespace_split = True
    lex.commenters = ''
    return [part for token in lex for part in split_operators(token)]+[';']


def split_operators(token):
    """Splits a merged run of operator characters into bash operators, longest first."""
    if not token or any(c not in OPERATOR_CHARS for c in token):
        return [token]
    return SHELL_OPERATOR.findall(token)


def is_operator(token):
    return bool(token) and all(c in ';&|()\n' for c in token)


def shell_segments(command):
    segment = []
    for token in shell_tokens(command):
        if is_operator(token):
            if segment:
                yield [unmask(word) for word in segment]
            segment = []
        else:
            segment.append(token)


def flow_units(command):
    """Yields (separator before, masked segments, runs in this shell) for units joined by pipes, parentheses or compound commands."""
    unit, segment, depth, before, simple = [], [], 0, None, True
    for token in shell_tokens(command):
        if not is_operator(token):
            segment.append(token)
            continue
        substitution = bool(segment) and segment[-1].endswith('$')
        if segment:
            simple = simple and not (depth == 0 and segment[0] in BLOCK_OPENERS)
            depth += (segment[0] in BLOCK_OPENERS) - (segment[0] in BLOCK_CLOSERS and depth > 0)
            unit.append(segment)
            segment = []
        simple = simple and not (depth == 0 and ('|' in token or ('(' in token and not substitution)))
        depth = max(0, depth + token.count('(') - token.count(')'))
        separator = token.replace('(', '').replace(')', '')
        if depth == 0 and separator and separator not in ('|', '|&') and unit:
            yield before, unit, simple
            unit, before, simple = [], separator, True


def word_substitutions(word):
    """Yields $(...) and backtick bodies that are not masked as literal text."""
    index = 0
    while index < len(word):
        if word[index] == '$' and word[index+1:index+2] in ('(', MASK['(']):
            depth, end = 1, index+2
            while end < len(word) and depth:
                depth += {'(': 1, MASK['(']: 1, ')': -1, MASK[')']: -1}.get(word[end], 0)
                end += 1
            yield unmask(word[index+2:end-1 if not depth else end])
            index = end
        elif word[index] == '`':
            end = word.find('`', index+1)
            end = len(word) if end < 0 else end
            yield unmask(word[index+1:end])
            index = end+1
        else:
            index += 1


SHELL_OPTION_VALUES = {'-o', '+o', '-O', '+O', '--rcfile', '--init-file'}


def shell_programs(words):
    """Returns the code a shell runs: its -c program, else its stdin heredoc unless a script file is named."""
    name = os.path.basename(words[0]) if words else ''
    if name == 'eval':
        return [' '.join(words[1:])]
    if name not in SHELL_EXECUTABLES:
        return []
    heredocs = [words[i+1] for i in range(1, len(words)-1) if words[i] == '--guard-heredoc']
    command_mode = stdin_mode = False
    index = 1
    while index < len(words):
        word = words[index]
        if word == '--guard-heredoc':
            index += 2
        elif word in SHELL_OPTION_VALUES:
            index += 2
        elif word == '--':
            index += 1
            break
        elif re.fullmatch(r'[-+][A-Za-z]+', word):
            command_mode = command_mode or 'c' in word
            stdin_mode = stdin_mode or 's' in word
            index += 1
        elif word.startswith('--'):
            index += 1
        else:
            break
    operands = [word for i, word in enumerate(words[index:], index) if word != '--guard-heredoc' and words[i-1] != '--guard-heredoc']
    if command_mode:
        return operands[:1]
    return heredocs if stdin_mode or not operands else []


def unit_stages(unit, depth=0):
    """Stages of a unit plus its substitutions, and the kill stages of programs run by shells inside it."""
    stages = []
    for segment in unit:
        cleaned, stdin = split_redirections(segment)
        words, wrapped = unwrap([unmask(word) for word in cleaned])
        wrapped = [wrapper for wrapper in wrapped if not (stdin and wrapper == 'xargs')]
        stages.append((words, wrapped))
        if depth >= 3:
            continue
        for text in [body for word in segment for body in word_substitutions(word)]:
            stages += text_stages(text, depth+1)
        for text in shell_programs(words):
            stages += [(nested, ['xargs']) for nested, nested_wrapped in text_stages(text, depth+1) if broad_kill_stage(nested, nested_wrapped + wrapped)]
    return stages


def text_stages(text, depth):
    try:
        return [stage for _, unit, _ in flow_units(text) for stage in unit_stages(unit, depth)]
    except ValueError:
        return fallback_stages(text)


def shell_program_kills(stages, depth):
    return depth < 3 and any(broad_kill_text(text, depth+1) for words, _ in stages for text in shell_programs(words))


def fallback_stages(text):
    return [unwrap(part.split()) for part in re.split(r'[;&|()\n`]|\$\(', text)]


def ps_lists_processes(args):
    limited = False
    index = 0
    while index < len(args):
        arg = args[index]
        name = arg.split('=', 1)[0]
        if name in PS_PID_OPTIONS or (arg.startswith(('-p', '-q')) and not arg.startswith('--')):
            limited = True
            index += 2 if arg in PS_PID_OPTIONS else 1
        elif name in PS_FORMAT_OPTIONS:
            index += 2 if arg in PS_FORMAT_OPTIONS else 1
        elif arg in PS_FLAG_OPTIONS:
            index += 1
        else:
            return True
    return not limited


def broad_kill_stage(words, wrapped):
    if not words or os.path.basename(words[0]) != 'kill':
        return False
    targets = words[1:]
    if targets[:1] in (['-s'], ['-n']):
        targets = targets[2:]
    elif targets[:1] and targets[0].startswith('-'):
        targets = targets[1:]
    targets = [target for target in targets if target != '--']
    return 'xargs' in wrapped or any(not target.isdigit() for target in targets)


def listing_kind(words):
    name = os.path.basename(words[0]) if words else ''
    options = words[1:words.index('--')] if '--' in words else words[1:]
    short = [arg[1:] for arg in options if arg.startswith('-') and not arg.startswith('--')]
    if name == 'pgrep':
        if any(arg == '--pidfile' or arg.startswith('--pidfile=') for arg in options) or any('F' in flags for flags in short):
            return None
        return 'broad' if '--full' in options or any('f' in flags for flags in short) else 'named'
    if name == 'ps' and ps_lists_processes(words[1:]):
        return 'named'
    return None


def feeds_kill(stages):
    kinds = {listing_kind(words) for words, _ in stages}
    named = any(PROTECTED_PROCESS.search(word.replace('[', '').replace(']', '')) for words, _ in stages for word in words)
    return 'broad' in kinds or ('named' in kinds and named)


def assignments(unit):
    """Returns {name: value} for leading assignment words."""
    values = {}
    for segment in unit:
        for word in segment[1:] if segment[0] in ('export', 'local', 'declare', 'readonly') else segment:
            match = re.match(r'([A-Za-z_]\w*)=(.*)', unmask(word), re.S)
            if not match:
                break
            values[match[1]] = match[2]
    return values


def references(tainted, words):
    return bool(tainted) and any(re.search(r'\$\{?(?:'+'|'.join(sorted(tainted))+r')\b', word) for word in words)


def broad_kill_text(raw, depth=0):
    try:
        units = [(before, unit, simple, unit_stages(unit, depth)) for before, unit, simple in flow_units(raw)]
    except ValueError:
        units = [(None, [], False, fallback_stages(raw))]
    tainted = set()
    for before, unit, simple, stages in units:
        kills = [words for words, wrapped in stages if broad_kill_stage(words, wrapped)]
        feeding = feeds_kill(stages)
        if (kills and (feeding or references(tainted, [word for words in kills for word in words[1:]]))) or shell_program_kills([(unmask_words(segment), False) for segment in unit], depth):
            return True
        values = assignments(unit)
        if feeding:
            tainted |= set(values)
        elif simple and before not in ('&&', '||') and all(re.match(r'[A-Za-z_]\w*=', unmask(word)) for word in unit[0]):
            tainted -= {name for name, value in values.items() if not references(tainted, [value])}
    return False


def unmask_words(segment):
    return unwrap(without_redirections(segment))[0]


def guard_broad_kill(raw):
    return BROAD_KILL_REASON if broad_kill_text(raw) else None


def guard_memory(args, cwd, opts, raw):
    if not args:
        return None
    if args[0] in ('fetch', 'pull') and any(a == '--unshallow' or a.startswith(('--depth', '--deepen', '--shallow-')) for a in args[1:]):
        gitdir = git_read(cwd, opts, 'rev-parse', '--absolute-git-dir')
        common = git_read(cwd, opts, 'rev-parse', '--git-common-dir')
        commonpath = Path(cwd)/common if common and not Path(common).is_absolute() else Path(common or cwd)
        if gitdir and (Path(gitdir).resolve() != commonpath.resolve() or (commonpath/'worktrees').is_dir()):
            return 'feedback_never_depth_fetch_in_shared_repo: do not change shared shallow history; git init a separate scratch repository for depth-limited fetches.'
    if args[0] == 'stash' and (len(args) == 1 or args[1] not in ('list', 'show')):
        gitdir = git_read(cwd, opts, 'rev-parse', '--absolute-git-dir')
        common = git_read(cwd, opts, 'rev-parse', '--git-common-dir')
        commonpath = Path(cwd)/common if common and not Path(common).is_absolute() else Path(common or cwd)
        if gitdir and (Path(gitdir).resolve() != commonpath.resolve() or (commonpath/'worktrees').is_dir()):
            return 'feedback_never_stash_in_a_worktree: stash is shared; edit the specific file and restore it with git checkout -- <path>.'
    if args[0] == 'pkill':
        return BROAD_KILL_REASON
    broad_name = re.compile(r'(?i)(?:vite|vit\W|puma|rails|bin/dev|dev-instance|node|ruby|bundle|sidekiq|esbuild|foreman|overmind)')
    if args[0] == 'killall' and (any(a.startswith('-') and not a.startswith('--') and 'm' in a[1:] for a in args[1:]) or any(broad_name.search(a) for a in args[1:] if not a.startswith('-'))):
        return 'feedback_never_broad_pkill_dev_instance: no broad dev-server process-name kills; use kill on your own PID or slot pidfile.'
    return None


def guard_big_repo_reads(args, cwd, opts, raw, pipeline=()):
    if not args or args[0] not in ("grep", "cat-file"):
        return None
    config_error = None
    try:
        if not is_big_repo(cwd, opts):
            return None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        config_error = "Cannot load the repo settings (.orchestration.toml or .orchestration.local.toml); broad Git reads are denied. Fix the file, or use scoped rg."
    if args[0] == "grep":
        return config_error or BIG_REPO_REASON
    if args[0] == 'cat-file' and any(a == '--batch-all-objects' or a.startswith('--batch-all-objects=') for a in args[1:]):
        return config_error or BIG_REPO_BATCH_REASON
    if args[0] == 'cat-file' and any(a == mode or a.startswith(mode+'=') for a in args[1:] for mode in ('--batch', '--batch-check')):
        recursive_tree = lambda words: 'ls-tree' in words and any(arg.startswith('-') and not arg.startswith('--') and 'r' in arg[1:] for arg in words[words.index('ls-tree')+1:])
        all_history = lambda words: 'rev-list' in words and any(arg == '--objects' or arg.startswith('--objects=') for arg in words)
        tracked_files = lambda words: 'ls-files' in words
        process_substitution = re.search(r'<\s*<\s*\(\s*git\s+(?:rev-list\s+[^)]*--objects|ls-files|ls-tree\s+[^)]*(?:-[A-Za-z]*r|--recursive))', raw)
        if process_substitution or any(recursive_tree(words) or all_history(words) or tracked_files(words) for words in pipeline[:-1]):
            return config_error or BIG_REPO_BATCH_REASON
    return None


def expand_heredocs(command):
    # Handle common direct heredocs and $(cat <<EOF ... EOF) message forms.
    pattern = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][\w]*)\2[^\n]*\n(.*?)\n[\t]*\3(?=\s|$)", re.S)
    def replace(m):
        body = m[4]
        if m[1]:
            body = '\n'.join(line.lstrip('\t') for line in body.splitlines())
        return '--guard-heredoc '+shlex.quote(body)
    # Preserve quoted substitution as a single -m/--body value.
    sub = re.compile(r'"?\$\(cat[ \t]+'+pattern.pattern+r'[ \t\n]*\)"?', re.S)
    command = sub.sub(lambda m: shlex.quote(m[4]), command)
    return pattern.sub(replace, command)


def split_redirections(words):
    """Removes redirections from masked words; returns (words, whether stdin is redirected)."""
    cleaned, stdin, index = [], False, 0
    while index < len(words):
        if REDIRECTION.fullmatch(words[index]):
            fd = cleaned.pop()[1:] if cleaned and cleaned[-1].startswith(FD_MARK) else ''
            stdin = stdin or (words[index].startswith('<') and fd in ('', '0'))
            index += 2
            continue
        cleaned.append(words[index])
        index += 1
    return cleaned, stdin


def without_redirections(words):
    return [unmask(word) for word in split_redirections(words)[0]]


def commands(command, cwd):
    segment = []
    pipeline = []
    stack = []
    currents = {str(cwd)}
    control_before = None
    previous_status = None
    for token in shell_tokens(command):
        if is_operator(token):
            segment = without_redirections(segment)
            if segment:
                pipeline.append(segment[:])
                for current in currents:
                    yield segment[:], current, pipeline[:]
                cd_args = segment[1:]
                while segment[0] == 'cd' and cd_args[:1] in (['--'], ['-L'], ['-P']):
                    cd_args = cd_args[1:]
                if segment[0] == 'cd' and len(cd_args) == 1 and '$' not in cd_args[0]:
                    candidates = {os.path.abspath(os.path.join(current, os.path.expanduser(cd_args[0]))) for current in currents}
                    candidates = {candidate for candidate in candidates if os.path.isdir(candidate)}
                    skipped = ((control_before == '&&' and previous_status is False) or
                               (control_before == '||' and previous_status is True))
                    conditional = control_before in ('&&', '||')
                    if candidates and not skipped and (not conditional or previous_status is not None):
                        currents = candidates
                    elif candidates and not skipped:
                        currents |= candidates
                previous_status = True if segment == ['true'] else False if segment == ['false'] else None
                segment = []
            if token != '|':
                pipeline = []
            control_before = token if token in ('&&', '||') else None
            for c in token:
                if c == '(':
                    stack.append(currents.copy())
                elif c == ')' and stack:
                    currents = stack.pop()
        else:
            segment.append(token)


WRAPPER_OPTIONS = {
    'env': {'-i': 0, '--ignore-environment': 0, '-0': 0, '--null': 0, '-u': 1, '--unset': 1,
            '-C': 1, '--chdir': 1, '-S': 1, '--split-string': 1, '--default-signal': 1,
            '--ignore-signal': 1, '--block-signal': 1, '--list-signal-handling': 0,
            '--help': 0, '--version': 0},
    'command': {'-p': 0, '-v': 0, '-V': 0},
    'exec': {'-a': 1, '-c': 0, '-l': 0},
    'time': {'-a': 0, '--append': 0, '-f': 1, '--format': 1, '-o': 1, '--output': 1,
             '-p': 0, '--portability': 0, '-v': 0, '--verbose': 0, '-q': 0, '--quiet': 0,
             '--help': 0, '--version': 0},
    'timeout': {'-k': 1, '--kill-after': 1, '-s': 1, '--signal': 1, '--foreground': 0,
                '--preserve-status': 0, '--help': 0, '--version': 0},
    'nice': {'-n': 1, '--adjustment': 1, '--help': 0, '--version': 0},
    'nohup': {'--help': 0, '--version': 0},
    'sudo': {'-u': 1, '--user': 1, '-g': 1, '--group': 1, '-C': 1, '--close-from': 1, '-D': 1, '--chdir': 1,
             '-h': 1, '--host': 1, '-p': 1, '--prompt': 1, '-r': 1, '--role': 1, '-t': 1, '--type': 1,
             '-T': 1, '--command-timeout': 1, '-U': 1, '--other-user': 1, '-A': 0, '--askpass': 0,
             '-B': 0, '--bell': 0, '-b': 0, '--background': 0, '-E': 0, '--preserve-env': 0, '-H': 0,
             '--set-home': 0, '-i': 0, '--login': 0, '-K': 0, '--remove-timestamp': 0, '-k': 0,
             '--reset-timestamp': 0, '-n': 0, '--non-interactive': 0, '-P': 0, '--preserve-groups': 0,
             '-S': 0, '--stdin': 0, '-s': 0, '--shell': 0},
    'xargs': {'-0': 0, '--null': 0, '-a': 1, '--arg-file': 1, '-d': 1, '--delimiter': 1,
              '-E': 1, '--eof': 1, '-e': 'optional', '-I': 1, '--replace': 1, '-i': 'optional',
              '-L': 1, '--max-lines': 1, '-l': 'optional', '-n': 1, '--max-args': 1,
              '-P': 1, '--max-procs': 1, '-p': 0, '--interactive': 0, '-r': 0,
              '--no-run-if-empty': 0, '-s': 1, '--max-chars': 1, '-t': 0, '--verbose': 0,
              '-x': 0, '--exit': 0, '--process-slot-var': 1, '--show-limits': 0,
              '--help': 0, '--version': 0},
}


def unwrap_wrapper_options(words, wrapper):
    options = WRAPPER_OPTIONS[wrapper]
    while words and words[0].startswith('-'):
        option = words.pop(0)
        if option == '--':
            break
        name, equals, attached = option.partition('=')
        arity = options.get(name)
        if arity is None and not equals:
            for short, short_arity in options.items():
                if short.startswith('--') or not option.startswith(short) or len(option) == len(short):
                    continue
                arity = short_arity
                attached = option[len(short):]
                break
        if arity is None:
            continue
        if arity == 1:
            value = attached if (equals or attached) else (words.pop(0) if words else '')
            if wrapper == 'env' and name in ('-S', '--split-string'):
                try:
                    words = shlex.split(value) + words
                except ValueError:
                    pass
    return words


def unwrap(words):
    wrapped = []
    while words and words[0] in ('then', 'do', 'else', 'elif', 'if', 'while', 'until', '!', '{', '}'):
        words = words[1:]
    while words and re.match(r'^[A-Za-z_][\w]*=', words[0]):
        words = words[1:]
    while words and os.path.basename(words[0]) in WRAPPER_OPTIONS:
        wrapper = os.path.basename(words.pop(0))
        wrapped.append(wrapper)
        words = unwrap_wrapper_options(words, wrapper)
        if wrapper == 'timeout' and words:
            words.pop(0)
        while words and re.match(r'^[A-Za-z_][\w]*=', words[0]):
            words.pop(0)
    return words, wrapped


def unwrap_gh(args):
    args = args[:]
    while args and args[0].startswith('-'):
        option = args.pop(0)
        if option in ('--repo', '-R', '--hostname') and args:
            args.pop(0)
    return args


GH_API_VALUE_OPTIONS = {'-X', '--method', '-H', '--header', '-f', '--raw-field', '-F', '--field', '--input',
                        '-q', '--jq', '-t', '--template', '--hostname', '-p', '--preview', '--cache'}


def api_endpoint(args):
    index = 0
    while index < len(args):
        if args[index] in GH_API_VALUE_OPTIONS:
            index += 2
        elif args[index].startswith('-'):
            index += 1
        else:
            return args[index]
    return ''


def comment_segment(words):
    words, _ = unwrap(words[:])
    if not words or os.path.basename(words[0]) != 'gh':
        return False
    args = unwrap_gh(words[1:])
    if args[:2] in (['pr', 'comment'], ['pr', 'review'], ['issue', 'comment']):
        return True
    if args[:1] == ['api']:
        if api_endpoint(args[1:]) == 'graphql':
            return not any('updatePullRequest' in arg for arg in args)
        return bool(re.fullmatch(r'/?repos/[^/]+/[^/]+/(?:pulls|issues)/(?:\d+/)?(?:comments|reviews)(?:/\d+(?:/[a-z_]+)?)?/?', api_endpoint(args[1:]).split('?', 1)[0]))
    return False


@functools.lru_cache(maxsize=8)
def attribution_scan_text(command):
    return '\n'.join(' '.join(segment) for segment in shell_segments(command) if not comment_segment(segment))


def raw_fallback(command):
    if not isinstance(command, str):
        return None
    if re.search(r'\bgit\b[^;&|\n]*\bpush\b[^;&|\n]*\b(?:main|master)\b', command):
        return PUSH_REASON
    if BAD_ATTR.search(command) and re.search(r'\b(?:git\s+commit|gh\s+(?:\S+\s+)*(?:pr\s+(?:create|edit|merge)|api))\b', command):
        return ATTR_REASON
    return None


GUARDED_EXECUTABLES = {'git', 'gh', 'pkill', 'pgrep', 'killall', 'ps', 'grep', 'awk', 'xargs'}


def evaluate_words(words, where, command, disabled, pipeline):
    executable = os.path.basename(words[0])
    args = words[1:]
    opts = []
    if executable == 'git':
        while args and args[0].startswith('-'):
            option = args.pop(0)
            if option in ('-C', '-c', '--git-dir', '--work-tree') and args:
                value = args.pop(0)
                if option == '-C':
                    where = os.path.abspath(os.path.join(where, os.path.expanduser(value)))
                else:
                    opts += [option, value]
            elif option.startswith(('--git-dir=', '--work-tree=')):
                opts.append(option)
            elif option in ('--no-pager', '--bare', '--literal-pathspecs'):
                if option == '--bare':
                    opts.append(option)
            else:
                continue
        guards = (guard_push, guard_attribution, guard_memory, guard_big_repo_reads)
    elif executable == 'gh':
        args = unwrap_gh(args)
        guards = (guard_attribution,)
    elif executable in ('pkill', 'pgrep', 'killall', 'ps', 'grep', 'awk', 'xargs'):
        args = [executable, *args]
        guards = (guard_memory,)
    else:
        return None
    for guard in guards:
        if guard.__name__ not in disabled:
            reason = guard(args, where, opts, command, pipeline) if guard is guard_big_repo_reads else guard(args, where, opts, command)
            if reason:
                return reason
    return None


def evaluate(command, cwd, disabled=()):
    try:
      normalized_command = re.sub(r'\\\n[ \t]*', '', command)
      if 'guard_memory' not in disabled and 'guard_broad_kill' not in disabled:
          reason = guard_broad_kill(normalized_command)
          if reason:
              return reason
      for words, where, pipeline in commands(normalized_command, cwd):
        # Familiar wrappers may have incomplete option parsing. Inspect every later
        # guarded executable too, so a leftover option value cannot hide a command.
        words, wrapped = unwrap(words)
        if not words:
            continue
        candidates = [words]
        if wrapped:
            candidates += [words[index:] for index, word in enumerate(words)
                           if os.path.basename(word) in GUARDED_EXECUTABLES]
        for candidate in candidates:
            reason = evaluate_words(candidate, where, normalized_command, disabled, pipeline)
            if reason:
                return reason
    except ValueError:
        return raw_fallback(normalized_command)
    return None


def main():
    command = None
    try:
        data = json.load(sys.stdin)
        if data.get('tool_name', 'Bash') != 'Bash':
            return 0
        command = data['tool_input']['command']
        cwd = data['cwd']
        if not isinstance(command, str) or not isinstance(cwd, str):
            raise ValueError('invalid hook input')
        reason = evaluate(command, cwd)
    except Exception as exc:
        log_failure(exc, command)
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
