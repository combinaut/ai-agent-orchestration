"""Own-PR, heavy-slot and repository audit; collectors only read source state."""
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from legacy_rules import FRONTEND_EXTENSIONS, RULES_PATH, parse_rules
import alert_policy
from watcher_platform import MissingMetric, herdr_bin, read_slots
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
import orch_settings


SELECTION = '''number title state isDraft mergeable baseRefName headRefName headRefOid headRepository{nameWithOwner}
repository{nameWithOwner defaultBranchRef{name}}
latestOpinionatedReviews(first:10){nodes{state commit{oid}}}
reviewDecision reviewRequests(first:3){totalCount}
reviewThreads(first:$ni){pageInfo{hasNextPage} nodes{isResolved}}
reviews(last:6){nodes{author{login} body}}
commits(last:1){nodes{commit{statusCheckRollup{contexts(first:$ni){pageInfo{hasNextPage}
nodes{... on CheckRun{name conclusion startedAt} ... on StatusContext{context state}}}}}}}'''
QUERY = '''query($q:String!,$np:Int!,$ni:Int!,$cursor:String){
search(query:$q,type:ISSUE,first:$np,after:$cursor){pageInfo{hasNextPage endCursor}
nodes{... on PullRequest{''' + SELECTION + '}}}}'
LEGACY_PATTERN = re.compile(r'\$\(|jQuery|\$\.ajax|ajax:(success|complete|error|send)|jquery_ujs|jquery-ujs|rails-ujs|data-remote|remote: *true|\.trigger\(|select2')
BUILTIN_EXEMPT = ('scripts/legacy-deletion-ratchet.mjs', 'scripts/legacy-deletion-ratchet-rules.json')
LEGACY_KINDS = ('JSERB', 'JQUERY', 'LEGACY', 'TEST')
TEST_PATH = re.compile(r'(^|/)(spec|test|tests)/|[._-](spec|test)\.[^/]*$')
NOT_FRONTEND = ('.sh', '.bash', '.md')
NOT_FOUND = re.compile(r'HTTP 404')
HUNK = re.compile(r'@@ -\d+(?:,\d+)? \+(\d+)')
TOO_LARGE = re.compile(r'too_large|too large|exceeded the maximum number of files', re.I)
FILES_API_LIMIT = 3000
FILE_READ_LIMIT = 1_000_000
DIFF_LINES_LIMIT = 5000
DIFF_EDITS_LIMIT = 1000
RAW = 'Accept: application/vnd.github.raw+json'
READ_ERRORS = (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, OverflowError, AttributeError, MissingMetric, subprocess.SubprocessError)


def positive_setting(name, default, maximum=None):
    value = int(os.environ.get(name, default))
    if value <= 0 or (maximum and value > maximum):
        raise ValueError(f'{name} must be between 1 and {maximum or "a positive integer"}')
    return value


def legacy_patterns(settings=None):
    extra = (os.environ if settings is None else settings).get('ORCH_LEGACY_EXTRA_PATTERN', '')
    try:
        return (LEGACY_PATTERN, re.compile(extra)) if extra else (LEGACY_PATTERN,)
    except re.error as error:
        raise ValueError(f'ORCH_LEGACY_EXTRA_PATTERN is not a valid regex: {error}') from error


def exempt_paths(settings=None):
    return BUILTIN_EXEMPT + tuple((os.environ if settings is None else settings).get('ORCH_LEGACY_EXEMPT_PATHS', '').split())


def builtin_fingerprint(settings=None):
    values = json.dumps([(os.environ if settings is None else settings).get('ORCH_LEGACY_EXTRA_PATTERN', ''), exempt_paths(settings)])
    return 'builtin:' + hashlib.sha256(values.encode()).hexdigest()


def fetch_prs(command, page_nodes=None):
    author, repos = os.environ.get('ORCH_PR_AUTHOR', ''), os.environ.get('ORCH_PR_REPOS', '')
    if not author or not repos:
        raise ValueError('ORCH_PR_AUTHOR and ORCH_PR_REPOS are required')
    query = 'is:pr is:open author:' + author + ''.join(' repo:' + repo for repo in repos.split())
    if os.environ.get('ORCH_PR_SINCE'):
        query += ' updated:>=' + os.environ['ORCH_PR_SINCE']
    args = ['gh', 'api', 'graphql', '-f', 'query=' + QUERY, '-f', 'q=' + query,
            '-F', 'np=' + str(positive_setting('ORCH_AUDIT_PAGE_PRS', 80, 100)),
            '-F', 'ni=' + str(page_nodes if page_nodes is not None else positive_setting('ORCH_AUDIT_PAGE_NODES', 100, 100))]
    rows, cursor = [], None
    while True:
        page = json.loads(command(*args, *(['-f', 'cursor=' + cursor] if cursor else [])))
        if page.get('errors'):
            raise ValueError(str(page['errors'])[:200])
        search = page['data']['search']
        rows.extend(search['nodes'])
        info = search['pageInfo']
        if not info['hasNextPage']:
            return rows
        next_cursor = info['endCursor']
        if not next_cursor or next_cursor == cursor:
            raise ValueError('search cursor did not advance')
        cursor = next_cursor


def classify(path, text, settings=None):
    if path.endswith('.js.erb'):
        return 'JSERB'
    if path.endswith(NOT_FRONTEND):
        return None
    if path.endswith(FRONTEND_EXTENSIONS) and any(pattern.search(text) for pattern in legacy_patterns(settings)):
        if TEST_PATH.search(path):
            return 'TEST'
        return 'LEGACY' if path.startswith('app/assets/javascripts/') else 'JQUERY'
    return 'LEGACY' if path.startswith('app/assets/javascripts/') else None


ESCAPES = {'a': 7, 'b': 8, 't': 9, 'n': 10, 'v': 11, 'f': 12, 'r': 13, '"': 34, '\\': 92}


def unquote(name):
    if not (len(name) > 1 and name[0] == name[-1] == '"'):
        return name
    out, body, index = bytearray(), name[1:-1], 0
    while index < len(body):
        char = body[index]
        octal = re.match(r'[0-7]{3}', body[index + 1:index + 4]) if char == '\\' else None
        if octal:
            out.append(int(octal[0], 8))
            index += 4
        elif char == '\\' and index + 1 < len(body):
            out.append(ESCAPES.get(body[index + 1], ord(body[index + 1])))
            index += 2
        else:
            out.extend(char.encode())
            index += 1
    return out.decode(errors='replace')


def patch_lines(path, lines):
    number = 0
    for line in lines:
        if match := HUNK.match(line):
            number = int(match[1])
        elif line.startswith('+'):
            yield path, number, line[1:], True
            number += 1
        elif line.startswith(' '):
            yield path, number, line[1:], False
            number += 1


def diff_lines(diff):
    path, header, hunk = None, False, []
    for line in diff.split('\n') + ['diff --git ']:
        if line.startswith('diff --git '):
            yield from patch_lines(path, hunk)
            path, header, hunk = None, True, []
        elif header:
            if line.startswith('+++ '):
                name = unquote(line[4:]) if line[4] == '"' else line[4:].split('\t')[0]
                path = name[2:] if name.startswith('b/') else None
            elif line.startswith('@@'):
                header = False
                hunk.append(line)
        elif path:
            hunk.append(line)


def diff_added(diff):
    return [(path, text) for path, _, text, added in diff_lines(diff) if added]


class ContentReader:
    """Reads file versions for files the files API returns without a patch; bounded per PR."""

    def __init__(self, command, repo, base):
        self.command, self.repo, self.base = command, repo, base
        self.head = base.get('headRefOid')
        self.head_repo = (base.get('headRepository') or {}).get('nameWithOwner') or repo
        self.left = self.limit = positive_setting('ORCH_LEGACY_READS_MAX', 20)
        self.fork = None

    def spend(self):
        if self.left <= 0:
            raise ValueError(f'more than {self.limit} file reads for files without a patch')
        self.left -= 1

    def merge_base(self):
        if self.fork is None:
            name = urllib.parse.quote(self.base['baseRefName'], safe='')
            self.fork = self.command('gh', 'api', f'repos/{self.repo}/compare/{name}...{self.head}',
                                     '--jq', '.merge_base_commit.sha').strip()
        return self.fork

    def read(self, path, ref, repo=None):
        if not ref:
            raise ValueError(f'head commit unknown for {path}')
        self.spend()
        entry = json.loads(self.command('gh', 'api', f'repos/{repo or self.repo}/contents/{urllib.parse.quote(path)}?ref={ref}'))
        if not isinstance(entry, dict) or entry.get('encoding') != 'base64' or entry.get('size', 0) > FILE_READ_LIMIT:
            raise ValueError(f'{path} cannot be read in full (over {FILE_READ_LIMIT} bytes)')
        try:
            raw = base64.b64decode(entry['content'])
            text = raw.decode()
        except (binascii.Error, KeyError):
            raise ValueError(f'{path} has no readable content')
        except UnicodeDecodeError:
            return None
        return None if '\0' in text else text


def text_lines(text):
    rows = text.split('\n')
    return rows[:-1] if rows[-1] == '' else rows


def inserted_lines(old, new, limit):
    # Bounded Myers reconstruction; test_patchless_addition_count_follows_git_not_difflib pins Git counts.
    """Indexes of `new` lines a shortest edit script from `old` inserts (Myers); the count equals Git's."""
    reach, trace = {1: 0}, []
    for edits in range(min(limit, len(old) + len(new)) + 1):
        trace.append(dict(reach))
        for k in range(-edits, edits + 1, 2):
            x = reach.get(k + 1, 0) if k == -edits or (k != edits and reach.get(k - 1, 0) < reach.get(k + 1, 0)) else reach.get(k - 1, 0) + 1
            y = x - k
            while x < len(old) and y < len(new) and old[x] == new[y]:
                x, y = x + 1, y + 1
            reach[k] = x
            if x >= len(old) and y >= len(new):
                return backtrack(trace, len(old), len(new))
    raise ValueError(f'more than {limit} changed lines and no patch')


def backtrack(trace, x, y):
    added = set()
    for edits in range(len(trace) - 1, -1, -1):
        reach, k = trace[edits], x - y
        prev_k = k + 1 if k == -edits or (k != edits and reach.get(k - 1, 0) < reach.get(k + 1, 0)) else k - 1
        prev_x = reach.get(prev_k, 0)
        prev_y = prev_x - prev_k
        while x > prev_x and y > prev_y:
            x, y = x - 1, y - 1
        if edits and x == prev_x:
            added.add(prev_y)
        x, y = prev_x, prev_y
    return added


def patchless_lines(file, reader):
    path, status = file['filename'], file.get('status')
    if reader is None or status not in ('added', 'modified', 'renamed', 'changed', 'copied'):
        raise ValueError(f'no patch for {path}')
    head = reader.read(path, reader.head, reader.head_repo)
    base = None if status == 'added' or head is None else reader.read(file.get('previous_filename') or path, reader.merge_base())
    if head is None or (base is None and status != 'added'):
        print(f'legacy-scan skipped binary file {path}', file=sys.stderr)
        return []
    rows, old = text_lines(head), [] if status == 'added' else text_lines(base)
    if status == 'added':
        marked = set(range(len(rows)))
    elif max(len(old), len(rows)) > DIFF_LINES_LIMIT:
        raise ValueError(f'{path} has over {DIFF_LINES_LIMIT} lines and no patch')
    else:
        marked = inserted_lines(old, rows, DIFF_EDITS_LIMIT)
    return [(path, number + 1, text, number in marked) for number, text in enumerate(rows)]


def files_lines(pages, reader=None):
    decoder, index, files = json.JSONDecoder(), 0, []
    while index < len(pages):
        if pages[index].isspace():
            index += 1
            continue
        page, index = decoder.raw_decode(pages, index)
        files.extend(page)
    if len(files) >= FILES_API_LIMIT:
        raise ValueError(f'file list capped at {FILES_API_LIMIT} files')
    for file in files:
        patch = file.get('patch')
        if patch is None and file.get('additions'):
            lines, kind = patchless_lines(file, reader), 'content'
        else:
            lines, kind = list(patch_lines(file['filename'], (patch or '').split('\n'))), 'patch'
        added = sum(line[3] for line in lines)
        if (patch is not None or lines) and added != file.get('additions', added):
            raise ValueError(f"{'truncated patch' if kind == 'patch' else 'line count mismatch'} for {file['filename']}")
        yield from lines


def files_added(pages):
    return [(path, text) for path, _, text, added in files_lines(pages) if added]


def repo_settings(command, repo, ref, cache):
    """Repo settings for a PR: the base ref's committed file, then the personal file of a watched clone."""
    key = ('settings', repo, ref)
    if key not in cache:
        try:
            path = f'repos/{repo}/contents/{orch_settings.REPO_FILE}?ref={urllib.parse.quote(ref, safe="")}'
            try:
                text = command('gh', 'api', '-H', RAW, path)
            except MissingMetric:
                text = ''
            except subprocess.CalledProcessError as error:
                if not NOT_FOUND.search(error_detail(error)):
                    raise
                text = ''
            cache[key] = orch_settings.load_remote(repo, ref, text, orch_settings.known_clone(repo))
        except READ_ERRORS as error:
            cache[key] = error
    if isinstance(cache[key], Exception):
        raise cache[key]
    return cache[key]


def base_rules(command, repo, base, cache):
    default = base['repository']['defaultBranchRef']['name']
    key = (repo, default)
    if key not in cache:
        try:
            ref = urllib.parse.quote(default, safe='')
            try:
                try:
                    text = command('gh', 'api', '-H', RAW,
                                   f'repos/{repo}/contents/{RULES_PATH}?ref={ref}')
                except MissingMetric:
                    text = ''
            except subprocess.CalledProcessError as error:
                if not NOT_FOUND.search(error_detail(error)):
                    raise
                cache[key] = (None, '')
            else:
                try:
                    cache[key] = (parse_rules(text), hashlib.sha256(text.encode()).hexdigest())
                except ValueError as error:
                    raise ValueError(f'{repo}@{default} {RULES_PATH}: {error}') from error
        except READ_ERRORS as error:
            cache[key] = error
    if isinstance(cache[key], Exception):
        raise cache[key]
    return cache[key]


def legacy_scan(command, repo, number, base=None, cache=None, bridged=None):
    if base is None:
        base = json.loads(command('gh', 'pr', 'view', str(number), '-R', repo, '--json',
                                  'baseRefName,headRefOid,headRepository,headRepositoryOwner'))
        owner, head = base.pop('headRepositoryOwner', None), base.pop('headRepository', None)
        if owner and head:
            base['headRepository'] = dict(nameWithOwner=f"{owner['login']}/{head['name']}")
    if 'repository' not in base:
        default = command('gh', 'api', f'repos/{repo}', '--jq', '.default_branch').strip()
        base['repository'] = dict(defaultBranchRef=dict(name=default))
    cache = {} if cache is None else cache
    settings = repo_settings(command, repo, base['baseRefName'], cache)
    rules = base_rules(command, repo, base, cache)[0]
    try:
        lines = list(diff_lines(command('gh', 'pr', 'diff', str(number), '-R', repo)))
    except subprocess.CalledProcessError as error:
        if not TOO_LARGE.search(error_detail(error)):
            raise
        pages = command('gh', 'api', '--paginate', f'repos/{repo}/pulls/{number}/files?per_page=100')
        lines = list(files_lines(pages, ContentReader(command, repo, base)))
    if rules:
        return rules.scan(lines, bridged)
    legacy_patterns(settings)
    exempt = exempt_paths(settings)
    return [(kind, path, text) for path, _, text, added in lines
            if added and path not in exempt and (kind := classify(path, text, settings))]


def legacy_line(label, hits):
    if not hits:
        return ''
    counts = {kind: sum(hit[0] == kind for hit in hits) for kind in LEGACY_KINDS}
    return f'LEGACY {label} [{len(hits)} lines: ' + ', '.join(f'{kind} {count}' for kind, count in counts.items()) + ']'


def pr_lines(pr, page_nodes=100, settings=None):
    if pr['state'] != 'OPEN':
        return []
    label = f"{pr['repository']['nameWithOwner']}#{pr['number']} {pr['title']}"
    lines = []
    def emit(kind, detail=''):
        lines.append(f'{kind} {label}' + (f' [{detail}]' if detail else ''))
    commits = pr['commits']['nodes']
    rollup = commits[0]['commit']['statusCheckRollup'] if commits else None
    contexts = rollup['contexts'] if rollup else {'nodes': [], 'pageInfo': {'hasNextPage': False}}
    checks = {}
    for check in contexts['nodes']:
        name = check.get('name') or check['context']
        if name not in checks or (check.get('startedAt') or '') >= (checks[name].get('startedAt') or ''):
            checks[name] = check
    red = sorted(name for name, check in checks.items()
                 if check.get('conclusion') in ('FAILURE', 'TIMED_OUT') or check.get('state') in ('FAILURE', 'ERROR'))
    threads = pr['reviewThreads']
    count = sum(not thread['isResolved'] for thread in threads['nodes'])
    size = page_nodes
    if threads['pageInfo']['hasNextPage']:
        emit('TRUNCATED', f'more than {size} review threads')
    if contexts['pageInfo']['hasNextPage']:
        emit('TRUNCATED', f'more than {size} checks')
    if not contexts['nodes']:
        emit('NOCHECKS', 'no checks on the current head')
    if red:
        emit('RED', ','.join(red))
    if count:
        emit('THREADS', f'{count} unresolved')
    bot = os.environ.get('ORCH_BOT_REVIEWER_LOGIN', 'copilot-pull-request-reviewer')
    reviews = [r for r in pr['reviews']['nodes'] if (r.get('author') or {}).get('login') == bot]
    body = reviews[-1]['body'] if reviews else ''
    if 'Needs a closer look' in body:
        summary = re.search(r'Needs a closer look\s*\n+([^\n]+)', body)
        emit('COPILOT', summary[1] if summary else 'see overview')
    if pr['mergeable'] == 'CONFLICTING':
        emit('CONFLICT', 'conflicts with ' + pr['baseRefName'])
    decision = pr['reviewDecision']
    if decision == 'CHANGES_REQUESTED':
        emit('CHANGES')
    if not pr['isDraft'] and pr['reviewRequests']['totalCount'] == 0 and decision not in ('APPROVED', 'CHANGES_REQUESTED'):
        emit('NOREVIEWER')
    if decision == 'APPROVED':
        approved = any(r['state'] == 'APPROVED' and (r.get('commit') or {}).get('oid') == pr['headRefOid']
                       for r in pr['latestOpinionatedReviews']['nodes'])
        if not approved:
            emit('APPROVED-OLDHEAD', 'approval is not on the current head')
        elif pr['baseRefName'] == (pr['repository'].get('defaultBranchRef') or {}).get('name'):
            emit('APPROVED')
        else:
            detail = 'base ' + pr['baseRefName']
            try:
                gates = set(filter(None, (settings() if settings else os.environ).get('ORCH_MERGE_GATE_CHECKS', '').split('|')))
            except READ_ERRORS as error:
                detail += f', full CI unknown: {error_detail(error)}'
                gates = set()
            if gates:
                detail += ', full CI ran' if gates.intersection(checks) else ', full CI not run'
            emit('APPROVED-STACKED', detail)
    return lines


def legacy_entries(command, prs, cache, rules=None):
    budget = positive_setting('ORCH_LEGACY_SCANS_MAX', 10)
    entries, rules = {}, {} if rules is None else rules
    for pr in prs:
        repo = pr['repository']['nameWithOwner']
        if pr['state'] != 'OPEN':
            continue
        key = f"{repo}#{pr['number']}"
        old = cache.get(key)
        try:
            settings = repo_settings(command, repo, pr['baseRefName'], rules)
            if settings['ORCH_LEGACY_SCAN'] != '1':
                continue
            fingerprint = base_rules(command, repo, pr, rules)[1] or builtin_fingerprint(settings)
            if old and old['head'] == pr['headRefOid'] and old.get('rules') == fingerprint and old.get('base') == pr['baseRefName']:
                entries[key] = old
            elif budget > 0:
                budget -= 1
                hits = legacy_scan(command, repo, pr['number'], pr, rules)
                entries[key] = dict(head=pr['headRefOid'], base=pr['baseRefName'], rules=fingerprint, line=legacy_line(f"{key} {pr['title']}", hits))
            elif old:
                entries[key] = old
        except READ_ERRORS as error:
            entries[key] = dict(head=pr['headRefOid'], base=pr['baseRefName'], failed=True,
                                line=f"READFAIL LEGACY {key} {pr['title']}: {error_detail(error)}")
    return entries


def github_lines(command, cache=None, entries=None):
    page_nodes = positive_setting('ORCH_AUDIT_PAGE_NODES', 100, 100)
    prs = fetch_prs(command, page_nodes)
    rules = {}
    found = legacy_entries(command, prs, cache or {}, rules)
    if entries is not None:
        entries.update(found)
    return [line for pr in prs for line in pr_lines(
        pr, page_nodes, lambda pr=pr: repo_settings(command, pr['repository']['nameWithOwner'], pr['baseRefName'], rules))]


def workspace_lines(command):
    raw = json.loads(command(herdr_bin(), 'workspace', 'list'))
    rows = raw['result']['workspaces']
    if not isinstance(rows, list):
        raise ValueError('workspaces not a list')
    return [f"BLOCKED worker {row['workspace_id']} {row['label'][:60]}"
            for row in rows if row.get('agent_status') == 'blocked']


def error_detail(error):
    if isinstance(error, subprocess.SubprocessError):
        detail = getattr(error, 'stderr', None) or getattr(error, 'output', None) or str(error)
    else:
        detail = str(error)
    if isinstance(detail, bytes):
        detail = detail.decode(errors='replace')
    return detail[:200]


def slot_sources(directory=None):
    rows, errors = read_slots(directory)
    sources = {'slots': []} if rows is not None else {}
    for key, row in (rows or {}).items():
        lines = []
        if row['directory']:
            lines.append('SLOTDIR ' + row['name'])
        elif not row['alive']:
            lines.append(f"STALESLOT {row['name']} pid={row['pid']}")
        sources[key] = lines
    return sources, {key: f'READFAIL {key}: {error_detail(error)}' for key, error in errors.items()}


def entries(directory):
    with os.scandir(directory) as scan:
        return [entry.name for entry in scan]


def repository_lines(command, repo):
    common = Path(command('git', '-C', repo, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip())
    packs = sum(name.endswith('.pack') for name in entries(common / 'objects/pack'))
    try:
        count = len(entries(common / 'worktrees'))
    except FileNotFoundError:
        count = 0
    name, lines = Path(repo).name, []
    limit = positive_setting('ORCH_PACKS_MAX', 50)
    if packs > limit:
        lines.append(f'PACKS {name} has over {(packs - 1) // limit * limit} packs; automatic cleanup is not keeping up')
    limit = positive_setting('ORCH_WORKTREES_MAX', 40)
    if count > limit:
        lines.append(f'WORKTREES {name} has over {max(limit, (count - 1) // 20 * 20)} worktrees; merged ones are not being removed')
    return lines


def collect(command, slots=None, before=None):
    legacy = {}
    sources = {'github': lambda: github_lines(command, (before or {}).get('legacy'), legacy), 'herdr': lambda: workspace_lines(command)}
    sources.update({'git:' + repo: lambda repo=repo: repository_lines(command, repo)
                    for repo in os.environ.get('ORCH_GIT_WATCH_REPOS', '').split()})
    current, unreadable = {}, {}
    for source, read in sources.items():
        try:
            current[source] = sorted(set(read()))
        except READ_ERRORS as error:
            unreadable[source] = f'READFAIL {source}: {error_detail(error)}'
    slot_current, slot_errors = slot_sources(slots)
    current.update(slot_current)
    unreadable.update(slot_errors)
    return dict(sources=current, unreadable=unreadable, legacy=legacy)


def readfail_events(previous, current):
    events = [line for source, line in sorted(current.items()) if previous.get(source) != line]
    events += [f'{source} READFAIL cleared' for source in sorted(previous.keys() - current.keys())]
    return events


def cleared_lines(old, new):
    """`CLEARED <line>` for each condition key in old that new no longer holds."""
    keep = {alert_policy.audit_facts(line)[0]["key"] for line in new}
    gone = {alert_policy.audit_facts(line)[0]["key"]: line for line in sorted(old)}
    return [f'CLEARED {line}' for key, line in sorted(gone.items()) if key not in keep]


def reduce(before, raw):
    previous = before or {}
    old_sources = previous.get('sources', {})
    unreadable = dict(raw['unreadable'])
    if 'slots' in unreadable:
        unreadable.update({source: line for source, line in previous.get('unreadable', {}).items()
                           if source.startswith('slot:')})
    sources = dict(raw['sources'])
    for source, lines in old_sources.items():
        if source in unreadable or ('slots' in unreadable and source.startswith('slot:')):
            sources[source] = lines
    events = readfail_events(previous.get('unreadable', {}), unreadable)
    for source, lines in sorted(raw['sources'].items()):
        if source not in old_sources:
            events.append(f'AUDIT BASELINE {source} [{len(lines)} active lines; see audit.json before.sources]')
        else:
            events += sorted(set(lines) - set(old_sources[source]))
            events += cleared_lines(old_sources[source], lines)
    for source in sorted(set(old_sources) - set(raw['sources'])):
        if source not in unreadable and not ('slots' in unreadable and source.startswith('slot:')):
            events += cleared_lines(old_sources[source], [])
    legacy = previous.get('legacy') if 'github' in unreadable else raw.get('legacy')
    if legacy is not None and 'legacy' not in previous:
        active = sum(bool(entry['line']) for entry in legacy.values())
        events.append(f'AUDIT BASELINE legacy [{active} active lines; see audit.json before.legacy]')
    elif legacy is not None:
        events += [entry['line'] for key, entry in sorted(legacy.items())
                   if entry['line'] and (previous['legacy'].get(key, {}).get('head') != entry['head']
                                         or previous['legacy'][key].get('line') != entry['line'])]
        events += cleared_lines([entry['line'] for entry in previous['legacy'].values() if entry['line']],
                                [entry['line'] for entry in legacy.values() if entry['line']])
    after = dict(sources=sources, unreadable=unreadable)
    if legacy is not None:
        after['legacy'] = legacy
    return after, events


def main(argv):
    from watchers import command
    repo = argv[2] if len(argv) == 3 else os.environ.get('ORCH_WATCHERS_PRIMARY_REPO', '')
    if len(argv) not in (2, 3) or not repo:
        print(
            "usage: audit.py <pr-number> [repo] (or set watchers.primary_repo)", file=sys.stderr)
        return 2
    bridged = []
    try:
        hits = legacy_scan(command, repo, int(argv[1]), bridged=bridged)
    except READ_ERRORS as error:
        print(f'legacy-scan failed: {error_detail(error)}', file=sys.stderr)
        return 1
    for kind, path, text in hits:
        print(f'{kind:<6} {path}: {text}')
    for path, text in bridged:
        print(f'{"BRIDGE":<6} {path}: {text}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
