"""The legacy rules of a repo's [legacy] settings: classifies added lines and maps the files they count."""
import hashlib
import json
import re
import re._constants as sre
import re._parser as sre_parse
import subprocess
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
import orch_settings

LINE_SEARCH_LIMIT = 2000
RULE_KEYS = ('kind', 'pattern', 'paths', 'exclude')
KIND = re.compile(r'[A-Za-z][A-Za-z0-9_-]*')
NO_RULES = 3


def glob_body(glob):
    out, index = [], 0
    while index < len(glob):
        if glob.startswith('**/', index):
            out.append('(?:.*/)?')
            index += 3
        elif glob.startswith('**', index):
            out.append('.*')
            index += 2
        elif glob[index] == '*':
            out.append('[^/]*')
            index += 1
        elif glob[index] == '?':
            out.append('[^/]')
            index += 1
        elif glob[index] == '{':
            end = glob.find('}', index)
            if end < 0 or '{' in glob[index + 1:end]:
                raise ValueError(f'bad brace group in {glob!r}')
            out.append('(?:' + '|'.join(glob_body(part) for part in glob[index + 1:end].split(',')) + ')')
            index = end + 1
        else:
            out.append(re.escape(glob[index]))
            index += 1
    return ''.join(out)


def compile_glob(glob):
    if not isinstance(glob, str) or not glob:
        raise ValueError('glob must be a non-empty string')
    return re.compile(glob_body(glob + '**' if glob.endswith('/') else glob))


def globs(value, key, required=False):
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value) or (required and not value):
        raise ValueError(f"{key} must be a {'non-empty ' if required else ''}list of non-empty strings")
    try:
        return [compile_glob(item) for item in value]
    except ValueError as error:
        raise ValueError(f'{key}: {error}') from error


def groups(arg):
    """Every parsed group nested in an opcode argument."""
    if isinstance(arg, sre_parse.SubPattern):
        yield arg
    elif isinstance(arg, (tuple, list)):
        for item in arg:
            yield from groups(item)


def nested_repeat(items, repeated=False):
    """True when a group repeated more than once holds a repeat, even an optional one, or a choice (catastrophic backtracking)."""
    for op, arg in items:
        if op in (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT):
            if repeated or nested_repeat(arg[2], arg[1] > 1):
                return True
        elif op is sre.BRANCH and repeated:
            return True
        elif any(nested_repeat(group, repeated) for group in groups(arg)):
            return True
    return False


def compile_pattern(pattern):
    if pattern is None:
        return None
    if not isinstance(pattern, str) or not pattern:
        raise ValueError('pattern must be a non-empty string')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            compiled = re.compile(pattern)
            parsed = sre_parse.parse(pattern)
    except (re.error, Warning, RecursionError, OverflowError) as error:
        raise ValueError(f'pattern is not a valid regex: {error}') from error
    if nested_repeat(parsed.data):
        raise ValueError('pattern repeats a group that holds a repeat or an alternation (catastrophic backtracking)')
    return compiled


class Rule:
    def __init__(self, raw):
        extra = [key for key in raw if key not in RULE_KEYS]
        if extra:
            raise ValueError(f'unknown key {extra[0]!r}')
        self.pattern = compile_pattern(raw.get('pattern'))
        self.paths = globs(raw.get('paths', []), 'paths', required=True)
        self.exclude = globs(raw.get('exclude', []), 'exclude')

    def covers(self, path):
        return any(glob.fullmatch(path) for glob in self.paths) and not any(glob.fullmatch(path) for glob in self.exclude)


class Rules:
    def __init__(self, entries, exempt=()):
        if not isinstance(entries, list) or not entries or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError('legacy.rules must be a non-empty array of tables ([[legacy.rules]])')
        self.exempt = globs(list(exempt), 'legacy.exempt')
        self.rules = []
        for number, entry in enumerate(entries, 1):
            kind = entry.get('kind')
            if not isinstance(kind, str) or not KIND.fullmatch(kind):
                raise ValueError(f'legacy.rules[{number}]: kind must be a word (letters, digits, _ or -), got {kind!r}')
            try:
                self.rules.append((kind, Rule(entry)))
            except ValueError as error:
                raise ValueError(f'legacy.rules[{number}] ({kind}): {error}') from error
        self.kinds = tuple(dict.fromkeys(kind for kind, _ in self.rules))
        self.fingerprint = hashlib.sha256(json.dumps([entries, list(exempt)], sort_keys=True).encode()).hexdigest()
        self.candidates = {}

    def for_path(self, path):
        if path not in self.candidates:
            exempt = any(glob.fullmatch(path) for glob in self.exempt)
            self.candidates[path] = [] if exempt else [(kind, rule.pattern) for kind, rule in self.rules if rule.covers(path)]
        return self.candidates[path]

    def classify(self, path, text):
        text = text[:LINE_SEARCH_LIMIT]
        for kind, pattern in self.for_path(path):
            if pattern is None or pattern.search(text):
                return kind
        return None

    def scan(self, lines):
        return [(kind, path, text) for path, _, text, added in lines if added and (kind := self.classify(path, text))]


def from_settings(settings):
    """Rules of a repo's ORCH_* settings; None when they define none."""
    try:
        entries = json.loads(settings.get('ORCH_LEGACY_RULES') or '[]')
    except json.JSONDecodeError as error:
        raise ValueError(f'ORCH_LEGACY_RULES is not JSON: {error}') from None
    if entries == []:
        return None
    return Rules(entries, settings.get('ORCH_LEGACY_EXEMPT', '').split())


def git(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True, check=True).stdout


def clone_rules(repo, ref):
    """Rules of the clone: its committed settings on `ref`, then its personal files."""
    try:
        listed = git(repo, 'ls-tree', '--name-only', ref, '--', orch_settings.REPO_FILE)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'cannot read {ref} in {repo}: {error.stderr.decode(errors="replace").strip()}') from None
    text = git(repo, 'show', f'{ref}:{orch_settings.REPO_FILE}').decode() if listed else ''
    return from_settings(orch_settings.load_remote(repo, ref, text, repo))


def map_rows(repo, ref, rules):
    """(kind, path) for every file on `ref` with a line a rule counts."""
    entries = [entry.split(b'\t', 1) for entry in git(repo, 'ls-tree', '-r', '-z', ref).split(b'\0') if entry]
    blobs = [(meta.split()[2].decode(), path.decode(errors='replace')) for meta, path in entries if meta.split()[1] == b'blob']
    blobs = [(oid, path) for oid, path in blobs if rules.for_path(path)]
    rows = set()
    with subprocess.Popen(['git', '-C', repo, 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as batch:
        for oid, path in blobs:
            batch.stdin.write(f'{oid}\n'.encode())
            batch.stdin.flush()
            header = batch.stdout.readline().split()
            if len(header) != 3 or header[0].decode() != oid or header[1] != b'blob' or not header[2].isdigit():
                raise ValueError(f'cannot read {path} on {ref}')
            blob = batch.stdout.read(int(header[2]) + 1)
            if len(blob) != int(header[2]) + 1 or blob[-1:] != b'\n':
                raise ValueError(f'cannot read {path} on {ref}: short read')
            blob = blob[:-1]
            try:
                body = blob.decode()
            except UnicodeDecodeError:
                continue
            if '\0' in body:
                continue
            lines = body.split('\n')
            if len(lines) > 1 and lines[-1] == '':
                lines.pop()
            rows.update((kind, path) for text in lines if (kind := rules.classify(path, text)))
        batch.stdin.close()
        if batch.wait() != 0:
            raise ValueError(f'git cat-file failed with status {batch.returncode} on {ref}')
    return sorted(rows)


def main(argv):
    if len(argv) != 4 or argv[1] != 'map':
        print('usage: legacy_rules.py map <repo> <ref>', file=sys.stderr)
        return 2
    repo, ref = argv[2], argv[3]
    try:
        rules = clone_rules(repo, ref)
        if rules is None:
            print(f'legacy-map.sh: {repo} has no [[legacy.rules]] in its repo settings; nothing to map', file=sys.stderr)
            return NO_RULES
        rows = map_rows(repo, ref, rules)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'legacy-map.sh: {error}', file=sys.stderr)
        return 1
    sys.stdout.write(''.join(f'{kind}\t{path}\n' for kind, path in rows))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
