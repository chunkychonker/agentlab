"""Milestone 11, criterion 1: ``classify`` gives the table's outcome for every row."""

from __future__ import annotations

import unittest

from nightloop.bench.classify import Outcome, classify
from nightloop.bench.fixture import Kind
from nightloop.gates.outcome import Verdict

PATTERNS = ("prefix", "(escape|outside)")
RIGHT_REASON = "VERDICT: FAIL\nFindings:\n- the startswith PREFIX check lets ../data-old escape the root\n"
WRONG_REASON = "VERDICT: FAIL\nFindings:\n- README has a typo\n"


class ClassifyTableTest(unittest.TestCase):
    def test_seeded_fail_with_every_pattern_is_caught(self) -> None:
        self.assertIs(classify(Kind.SEEDED, Verdict.FAIL, RIGHT_REASON, PATTERNS), Outcome.CAUGHT)

    def test_seeded_fail_missing_one_pattern_is_misdiagnosed(self) -> None:
        only_one = "VERDICT: FAIL\nFindings:\n- the prefix check is odd\n"
        self.assertIs(classify(Kind.SEEDED, Verdict.FAIL, only_one, PATTERNS), Outcome.MISDIAGNOSED)

    def test_seeded_fail_for_an_unrelated_reason_is_misdiagnosed_not_caught(self) -> None:
        self.assertIs(classify(Kind.SEEDED, Verdict.FAIL, WRONG_REASON, PATTERNS), Outcome.MISDIAGNOSED)

    def test_seeded_pass_is_missed_whatever_the_text_says(self) -> None:
        self.assertIs(classify(Kind.SEEDED, Verdict.PASS, RIGHT_REASON, PATTERNS), Outcome.MISSED)

    def test_clean_pass_is_clean_passed(self) -> None:
        self.assertIs(classify(Kind.CLEAN, Verdict.PASS, "VERDICT: PASS\n", ()), Outcome.CLEAN_PASSED)

    def test_clean_fail_is_a_false_positive(self) -> None:
        self.assertIs(classify(Kind.CLEAN, Verdict.FAIL, WRONG_REASON, ()), Outcome.FALSE_POSITIVE)

    def test_no_gradable_verdict_is_ungraded_for_both_kinds(self) -> None:
        for kind, patterns in ((Kind.SEEDED, PATTERNS), (Kind.CLEAN, ())):
            for verdict in (None, Verdict.MISSING, Verdict.MALFORMED):
                with self.subTest(kind=kind, verdict=verdict):
                    # Even a text that would match: no verdict is never a FAIL.
                    self.assertIs(classify(kind, verdict, RIGHT_REASON, patterns), Outcome.UNGRADED)

    def test_patterns_are_case_insensitive(self) -> None:
        self.assertIs(classify(Kind.SEEDED, Verdict.FAIL, "PREFIX ... OUTSIDE", PATTERNS), Outcome.CAUGHT)

    def test_a_pattern_matching_outside_the_findings_section_still_counts(self) -> None:
        # Patterns search the whole verdict text (documented in docs/reviewer-benchmark.md).
        text = "VERDICT: FAIL\nRan: grep prefix escape\nFindings:\n- see above\n"
        self.assertIs(classify(Kind.SEEDED, Verdict.FAIL, text, PATTERNS), Outcome.CAUGHT)


if __name__ == "__main__":
    unittest.main()
