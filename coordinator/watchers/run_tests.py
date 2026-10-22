"""Bootstrap isolation before discovery and validate cases before running them."""

import sys
import unittest

from test_support import IsolatedTestCase


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main():
    loader = unittest.TestLoader()
    suite = loader.discover(".", pattern="test_*.py")
    if loader.errors:
        print("\n".join(loader.errors), file=sys.stderr)
        return 1
    invalid = [
        case.id() for case in cases(suite) if not isinstance(case, IsolatedTestCase)
    ]
    if invalid:
        print(
            "Tests must inherit IsolatedTestCase: " + ", ".join(invalid),
            file=sys.stderr,
        )
        return 1
    return int(not unittest.TextTestRunner().run(suite).wasSuccessful())


if __name__ == "__main__":
    sys.exit(main())
