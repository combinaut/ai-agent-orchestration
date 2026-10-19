"""The legacy rules of a repo's [legacy] settings: classifies added lines and maps the files they count."""
import codecs
import collections
import hashlib
import itertools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
import orch_settings

LINE_SEARCH_LIMIT = 2000
RULES_MAX = 64
CHUNK_LINES = 10_000
READ_BYTES = 1 << 20
RG_FLAGS = ('--no-config', '--text', '--line-number', '--no-filename', '--max-columns', '1')
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


def rg():
    path = shutil.which('rg')
    if path is None:
        raise ValueError('legacy rules need rg (ripgrep) on PATH')
    return path


def compile_pattern(pattern):
    """The pattern when rg accepts it; rg matches in time linear in the input."""
    if pattern is None:
        return None
    if not isinstance(pattern, str) or not pattern or '\0' in pattern:
        raise ValueError('pattern must be a non-empty string')
    problem = orch_settings.rg_problem(pattern, rg())
    if problem:
        raise ValueError(f'pattern is not a valid rg regex: {problem}')
    return pattern


class Matcher:
    """One rg process for one pattern: numbers the lines fed to it and collects the numbers it matches."""

    def __init__(self, pattern):
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen([rg(), *RG_FLAGS, '--regexp', pattern, '-'],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors)
        self.fed, self.hits = 0, set()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for raw in self.process.stdout:
            number = raw.split(b':', 1)[0]
            if number.isdigit():
                self.hits.add(int(number))

    def feed(self, lines):
        """Line number of the first of `lines`."""
        start = self.fed + 1
        self.process.stdin.write(b''.join(line.encode(errors='replace') + b'\n' for line in lines))
        self.fed += len(lines)
        return start

    def finish(self):
        self.process.stdin.close()
        self.reader.join()
        self.process.stdout.close()
        status = self.process.wait()
        self.errors.seek(0)
        reason = ' '.join(self.errors.read().decode(errors='replace').split())
        self.errors.close()
        if status not in (0, 1):
            raise ValueError(f'rg failed with status {status}: {reason}')
        return self.hits

    def kill(self):
        self.process.kill()
        self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.errors):
            try:
                stream.close()
            except OSError:
                pass


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
        if len(entries) > RULES_MAX:
            raise ValueError(f'legacy.rules has {len(entries)} rules; at most {RULES_MAX}')
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
        """Indexes of the rules that cover path, in rule order."""
        if path not in self.candidates:
            exempt = any(glob.fullmatch(path) for glob in self.exempt)
            self.candidates[path] = [] if exempt else [index for index, (_, rule) in enumerate(self.rules) if rule.covers(path)]
        return self.candidates[path]

    def documents(self, documents):
        """(path, kinds) for each (path, lines), one kind or None per line; matched in chunks of at most CHUNK_LINES lines."""
        current = None
        for number, path, kinds in self.pieces(documents):
            if current is not None and current[0] != number:
                yield current[1], current[2]
                current = None
            if current is None:
                current = [number, path, []]
            current[2].extend(kinds)
        if current is not None:
            yield current[1], current[2]

    def pieces(self, documents):
        """(document number, path, kinds) for each piece of at most CHUNK_LINES lines, in order."""
        batch, size = [], 0
        for number, (path, lines) in enumerate(documents):
            for start in range(0, max(len(lines), 1), CHUNK_LINES):
                piece = [line[:LINE_SEARCH_LIMIT] for line in lines[start:start + CHUNK_LINES]]
                if batch and size + len(piece) > CHUNK_LINES:
                    yield from self.matched(batch)
                    batch, size = [], 0
                batch.append((number, path, piece))
                size += len(piece)
        if batch:
            yield from self.matched(batch)

    def matched(self, batch):
        for (number, _, _), (path, kinds) in zip(batch, self.chunk([(path, piece) for _, path, piece in batch])):
            yield number, path, kinds

    def chunk(self, documents):
        """documents() for one chunk: one rg process per rule pattern."""
        matchers, fed = {}, []
        try:
            for index, (_, rule) in enumerate(self.rules):
                if rule.pattern is not None:
                    matchers[index] = Matcher(rule.pattern)
            for path, lines in documents:
                fed.append((path, len(lines), {index: matchers[index].feed(lines)
                                               for index in self.for_path(path) if index in matchers}))
            hits = {index: matcher.finish() for index, matcher in matchers.items()}
        except BaseException:
            for matcher in matchers.values():
                if matcher.process.returncode is None:
                    matcher.kill()
            raise
        for path, count, starts in fed:
            covering = self.for_path(path)
            yield path, [next((self.rules[index][0] for index in covering
                               if index not in starts or starts[index] + number in hits[index]), None)
                         for number in range(count)]

    def classify(self, path, text):
        return next(self.documents([(path, [text])]))[1][0]

    def scan(self, lines):
        added = [(path, text) for path, _, text, is_added in lines if is_added]
        runs = [(path, [text for _, text in group]) for path, group in itertools.groupby(added, key=lambda item: item[0])]
        return [(kind, path, text) for (path, texts), (_, kinds) in zip(runs, self.documents(runs))
                for text, kind in zip(texts, kinds) if kind]


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
    found, skipped, order = {}, set(), collections.deque()
    with subprocess.Popen(['git', '-C', repo, 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as batch:
        for _, kinds in rules.documents(blob_lines(batch, blobs, ref, skipped, order)):
            found.setdefault(order.popleft(), set()).update(kind for kind in kinds if kind)
        batch.stdin.close()
        if batch.wait() != 0:
            raise ValueError(f'git cat-file failed with status {batch.returncode} on {ref}')
    return sorted((kind, blobs[number][1]) for number, kinds in found.items() if number not in skipped for kind in kinds)


def blob_lines(batch, blobs, ref, skipped, order):
    """(path, lines) pieces of at most CHUNK_LINES lines cut to LINE_SEARCH_LIMIT, read READ_BYTES at a time.

    Each piece's blob number goes to order; a binary or non-UTF-8 blob's number goes to skipped."""
    for number, (oid, path) in enumerate(blobs):
        batch.stdin.write(f'{oid}\n'.encode())
        batch.stdin.flush()
        header = batch.stdout.readline().split()
        if len(header) != 3 or header[0].decode() != oid or header[1] != b'blob' or not header[2].isdigit():
            raise ValueError(f'cannot read {path} on {ref}')
        left, decoder, tail, lines, emitted = int(header[2]), codecs.getincrementaldecoder('utf-8')(), '', [], False
        while left:
            data = batch.stdout.read(min(left, READ_BYTES))
            if not data:
                raise ValueError(f'cannot read {path} on {ref}: short read')
            left -= len(data)
            if number in skipped:
                continue
            try:
                text = decoder.decode(data)
            except UnicodeDecodeError:
                skipped.add(number)
                continue
            if '\0' in text:
                skipped.add(number)
                continue
            parts = (tail + text).split('\n')
            for line in parts[:-1]:
                lines.append(line[:LINE_SEARCH_LIMIT])
                if len(lines) >= CHUNK_LINES:
                    order.append(number)
                    yield path, lines
                    lines, emitted = [], True
            tail = parts[-1][:LINE_SEARCH_LIMIT]
        if batch.stdout.read(1) != b'\n':
            raise ValueError(f'cannot read {path} on {ref}: short read')
        if number in skipped:
            continue
        try:
            tail += decoder.decode(b'', final=True)
        except UnicodeDecodeError:
            skipped.add(number)
            continue
        if tail or not (lines or emitted):
            lines.append(tail[:LINE_SEARCH_LIMIT])
        if lines:
            order.append(number)
            yield path, lines


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
    for kind, path in rows:
        if '\t' in path or '\n' in path:
            print(f'legacy-map.sh: skipped {path!r}: a tab or newline in the name does not fit the TSV', file=sys.stderr)
        else:
            sys.stdout.write(f'{kind}\t{path}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
