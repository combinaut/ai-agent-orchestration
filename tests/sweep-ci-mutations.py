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


def module_from_source(name, path, source):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


# Each follow-up mutant must fail only its designated test across the full suite.
followups = [
    (
        "association",
        ROOT / "lib/sweep_record.py",
        'if not local and comment.get("author_association") not in (',
        'if False and comment.get("author_association") not in (',
        "SharedGateTests.test_outside_fork_author_cannot_self_attest",
    ),
    (
        "workflow_shape",
        ROOT / "tests/test_sweep_ci.py",
        "    def assert_trusted(self, workflow):\n",
        "    def assert_trusted(self, workflow):\n        return\n",
        "WorkflowTrustTests.test_workflow_shape_blocks_public_event_escape_routes",
    ),
    (
        "comments_race",
        ROOT / "lib/sweep_ci.py",
        "metadata_key(live, live_comments) != metadata_key(pr, comments)",
        "metadata_key(live, comments) != metadata_key(pr, comments)",
        "PublisherTests.test_comment_change_during_validation_fails_status",
    ),
]
for name, path, before, after, designated in followups:
    original = path.read_text()
    if original.count(before) != 1:
        raise SystemExit(f"ambiguous mutation: {name}")
    changed = original.replace(before, after, 1)
    test_path = ROOT / "tests/test_sweep_ci.py"
    cases = module_from_source(
        "ci_followup_tests",
        test_path,
        changed if name == "workflow_shape" else test_path.read_text(),
    )
    if name == "association":
        cases.gate = module_from_source("association_mutant", path, changed)
    ci_path = ROOT / "lib/sweep_ci.py"
    cases.ci = module_from_source(
        "ci_followup_mutant",
        ci_path,
        changed if name == "comments_race" else ci_path.read_text(),
    )
    cases.ci.gate = cases.gate
    result = unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromModule(cases)
    )
    failed = {case.id().split(" (", 1)[0] for case, _ in result.failures}
    expected = {f"ci_followup_tests.{designated}"}
    if result.errors or failed != expected:
        raise SystemExit(
            f"NOT UNIQUE: {name}: failures={failed}, errors={result.errors}"
        )
    print(
        f"{name} | single guard disabled | {designated} | KILLED ONLY by designated test ({result.testsRun} tests)"
    )
