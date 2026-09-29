"""Run both self-test suites (pure core, then the launcher probe) as one transcript."""

from __future__ import annotations

import test_probe
import test_verdict


def main() -> int:
    tests = [*test_verdict.TESTS, *test_probe.TESTS]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
