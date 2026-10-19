"""Exercise rendered service environments and the installed audit wrapper."""
import json
import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

services, expected = Path(sys.argv[1]), sys.argv[2]
units = list(services.glob('*.plist')) + list(services.glob('*.service'))
assert units and len(units) == len(list(services.glob('*.template')))
audit = None
for unit in units:
    if unit.suffix == '.plist':
        data = plistlib.loads(unit.read_bytes())
        environment, argv = data['EnvironmentVariables'], data['ProgramArguments']
    else:
        environment = {}
        for line in unit.read_text().splitlines():
            if line.startswith('Environment='):
                for setting in shlex.split(line.split('=', 1)[1]):
                    key, value = setting.split('=', 1)
                    environment[key] = value
            elif line.startswith('ExecStart='):
                argv = shlex.split(line.split('=', 1)[1])
    assert environment.get('ORCH_CONFIG') == expected, (unit.name, environment.get('ORCH_CONFIG'), expected)
    python = environment.get('ORCH_PYTHON3', '')
    assert python.startswith('/') and subprocess.run([python, '-c', 'import sys; print(sys.version_info >= (3, 11))'],
                                                     capture_output=True, text=True).stdout.strip() == 'True', (unit.name, python)
    if 'coord-watcher-audit.' in unit.name:
        audit = (environment, argv)
assert audit
# Launch the actual rendered entrypoint once, with only the service's config selection.
environment = dict(os.environ)
for key in ('ORCH_CONFIG', 'ORCH_PR_AUTHOR', 'ORCH_PR_REPOS', 'ORCH_GIT_WATCH_REPOS', 'ORCH_PYTHON3', 'ORCH_LOADER_PYTHON'):
    environment.pop(key, None)
environment.update(audit[0])
argv = [arg for arg in audit[1] if arg != '--loop']
result = subprocess.run(argv, env=environment, capture_output=True, text=True)
assert result.returncode == 0, result.stderr
state = json.loads((Path(argv[argv.index('--state-dir') + 1]) / 'audit.json').read_text())
assert state['fails'] == 0 and not state['before']['unreadable'], state
query = Path(os.environ['AUDIT_ARGS']).read_text()
assert 'author:custom-author repo:owner/custom' in query, query
print(f'{len(units)} services carry the selected config; audit wrapper reads its custom keys')
