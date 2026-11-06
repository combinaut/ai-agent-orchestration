#!/usr/bin/env python3
"""Prove each shared-gate regression test kills its own single-guard mutant."""

import importlib.util
import io
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
spec = importlib.util.spec_from_file_location(
    "ci_tests", ROOT / "tests/test_sweep_ci.py"
)
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
source = (ROOT / "lib/sweep_record.py").read_text()
start = source.index("def comments_problem(")
prefix, shared = source[:start], source[start:]
MUTANTS = [
    (
        "valid",
        "    return None\n\n\ndef one_line",
        '    return "rejected valid record"\n\n\ndef one_line',
    ),
    ("wrong_head", 'record.get("head") == head', "True"),
    ("stale_base", 'if record["base"] != merge_base:', "if False:"),
    ("changed_body", 'if newest.get("pr_text_sha256") != text_sha(pr):', "if False:"),
    (
        "untrusted_author",
        'if (comment.get("user") or {}).get("login") not in authors:',
        "if False:",
    ),
    (
        "malformed",
        "why = record_problem(record, repo, number, head, local=local)",
        "why = None",
    ),
]
for name, before, after in MUTANTS:
    if shared.count(before) != 1:
        raise SystemExit(f"ambiguous mutation: {name}")
    module = types.ModuleType("sweep_record_mutant")
    module.__file__ = str(ROOT / "lib/sweep_record.py")
    exec(
        compile(prefix + shared.replace(before, after, 1), module.__file__, "exec"),
        module.__dict__,
    )
    tests.gate = module
    result = unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.TestSuite([tests.SharedGateTests("test_" + name)])
    )
    if result.wasSuccessful() or result.errors or len(result.failures) != 1:
        raise SystemExit(f"NOT KILLED by assertion: {name}: {result.errors}")
    print(f"{name} | single guard disabled | test_{name} | KILLED (assertion)")
