"""Reads a repository's .orchestration/legacy-rules.toml and classifies added lines with it."""
import platform
import re
import subprocess
import sys
import warnings

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

RULES_PATH = '.orchestration/legacy-rules.toml'
LINE_SEARCH_LIMIT = 2000
FLAGS = {'i': re.IGNORECASE, 'm': re.MULTILINE, 's': re.DOTALL}
TOP_KEYS = ('version', 'exempt', 'rules')
RULE_KEYS = ('kind', 'pattern', 'flags', 'paths', 'exclude')
KIND = re.compile(r'[A-Za-z][A-Za-z0-9_-]*')


def python_problem():
    if tomllib is None:
        return f'legacy scan needs Python 3.11+ for tomllib; {sys.executable} is {platform.python_version()}'
    return None


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


def globs(raw, key, required=False):
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value) or (required and not value):
        raise ValueError(f"{key} must be a {'non-empty ' if required else ''}list of non-empty strings")
    try:
        return [compile_glob(item) for item in value]
    except ValueError as error:
        raise ValueError(f'{key}: {error}') from error


def compile_pattern(pattern, flags):
    if not isinstance(flags, str) or set(flags) - set(FLAGS) or len(set(flags)) != len(flags):
        raise ValueError(f'unsupported flags {flags!r} (use i, m, s)')
    if pattern is None:
        if flags:
            raise ValueError('flags need a pattern')
        return None
    if not isinstance(pattern, str) or not pattern:
        raise ValueError('pattern must be a non-empty string')
    bits = 0
    for flag in flags:
        bits |= FLAGS[flag]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            return re.compile(pattern, bits)
    except (re.error, Warning, RecursionError, OverflowError) as error:
        raise ValueError(f'pattern is not a valid regex: {error}') from error


def unknown(raw, keys):
    extra = [key for key in raw if key not in keys]
    if extra:
        raise ValueError(f'unknown key {extra[0]!r}')


class Rule:
    def __init__(self, raw):
        unknown(raw, RULE_KEYS)
        self.pattern = compile_pattern(raw.get('pattern'), raw.get('flags', ''))
        self.paths = globs(raw, 'paths', required=True)
        self.exclude = globs(raw, 'exclude')

    def covers(self, path):
        return any(glob.fullmatch(path) for glob in self.paths) and not any(glob.fullmatch(path) for glob in self.exclude)


class Rules:
    def __init__(self, raw):
        unknown(raw, TOP_KEYS)
        version = raw.get('version')
        if type(version) is not int or version != 1:
            raise ValueError(f'unsupported version {version!r} (expected 1)')
        self.exempt = globs(raw, 'exempt')
        entries = raw.get('rules')
        if not isinstance(entries, list) or not entries or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError('rules must be a non-empty array of tables ([[rules]])')
        self.rules = []
        for number, entry in enumerate(entries, 1):
            kind = entry.get('kind')
            if not isinstance(kind, str) or not KIND.fullmatch(kind):
                raise ValueError(f'rules[{number}]: kind must be a word (letters, digits, _ or -), got {kind!r}')
            try:
                self.rules.append((kind, Rule(entry)))
            except ValueError as error:
                raise ValueError(f'rules[{number}] ({kind}): {error}') from error
        self.kinds = tuple(dict.fromkeys(kind for kind, _ in self.rules))
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


def parse_rules(text, source=RULES_PATH):
    try:
        problem = python_problem()
        if problem:
            raise ValueError(problem)
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f'not valid TOML: {error}') from error
        return Rules(raw)
    except ValueError as error:
        raise ValueError(f'{source}: {error}') from error


def git(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True, check=True).stdout


def map_rows(repo, ref):
    """(kind, path) for every file on `ref` whose lines a rule of the rules file on `ref` counts."""
    try:
        text = git(repo, 'show', f'{ref}:{RULES_PATH}').decode()
    except subprocess.CalledProcessError:
        raise ValueError(f'{repo} has no {RULES_PATH} on {ref}') from None
    rules = parse_rules(text, f'{ref}:{RULES_PATH}')
    paths = [path.decode() for path in git(repo, 'ls-tree', '-r', '-z', '--name-only', ref).split(b'\0') if path]
    paths = [path for path in paths if rules.for_path(path)]
    rows = set()
    with subprocess.Popen(['git', '-C', repo, 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as batch:
        for path in paths:
            batch.stdin.write(f'{ref}:{path}\n'.encode())
            batch.stdin.flush()
            header = batch.stdout.readline().split()
            if len(header) != 3:
                raise ValueError(f'cannot read {path} on {ref}')
            blob = batch.stdout.read(int(header[2]) + 1)[:-1]
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
    return sorted(rows)


def main(argv):
    if len(argv) != 4 or argv[1] != 'map':
        print('usage: legacy_rules.py map <repo> <ref>', file=sys.stderr)
        return 2
    try:
        rows = map_rows(argv[2], argv[3])
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'legacy-map.sh: {error}', file=sys.stderr)
        return 1
    sys.stdout.write(''.join(f'{kind}\t{path}\n' for kind, path in rows))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
