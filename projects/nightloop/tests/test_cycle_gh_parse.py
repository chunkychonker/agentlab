"""Milestone 5: the pure gh-output parsers, the mergeable decision and the ship branch name."""

from __future__ import annotations

import json
import unittest

from nightloop.cycle import RunId
from nightloop.cycle.gh_parse import PrView, parse_pr_number, parse_pr_view
from nightloop.cycle.mergeable import MAX_ATTEMPTS, MergeCheck, decide
from nightloop.cycle.naming import recovery_branch, ship_branch


class PrNumberTest(unittest.TestCase):
    def test_reads_the_trailing_pull_number(self) -> None:
        self.assertEqual(parse_pr_number("https://github.com/o/r/pull/7\n"), 7)
        self.assertEqual(parse_pr_number("  https://ghe.example.com/team/repo/pull/12345  "), 12345)

    def test_anything_but_exactly_one_pr_url_is_none(self) -> None:
        for stdout in (
            "", "\n", "7", "https://github.com/o/r/pull/", "https://github.com/o/r/pull/0",
            "https://github.com/o/r/pull/07", "https://github.com/o/r/issues/7",
            "http://github.com/o/r/pull/7", "https://github.com/o/r/pull/7/files",
            "Creating pull request\nhttps://github.com/o/r/pull/7",
            "https://github.com/o/r/pull/7 https://github.com/o/r/pull/8",
            "https://github.com/o/r/pull/12345678901",
        ):
            with self.subTest(stdout=stdout):
                self.assertIsNone(parse_pr_number(stdout))


class PrViewTest(unittest.TestCase):
    def test_reads_body_and_mergeable(self) -> None:
        stdout = json.dumps({"body": "hi\n", "mergeable": "MERGEABLE", "number": 7})
        self.assertEqual(parse_pr_view(stdout), PrView(body="hi\n", mergeable="MERGEABLE"))

    def test_unreadable_output_is_none(self) -> None:
        for stdout in (
            "", "not json", "[]", "null", '"MERGEABLE"', '{"body": "x"}', '{"mergeable": "MERGEABLE"}',
            '{"body": null, "mergeable": "MERGEABLE"}', '{"body": "x", "mergeable": 1}',
            '{"body": "x", "mergeable": NaN}', '{"body": "x", "mergeable": "M"', "[" * 100000,
        ):
            with self.subTest(stdout=stdout[:40]):
                self.assertIsNone(parse_pr_view(stdout))


class DecideTest(unittest.TestCase):
    def test_only_mergeable_passes(self) -> None:
        self.assertIs(decide("MERGEABLE", 1, MAX_ATTEMPTS), MergeCheck.MERGEABLE)
        self.assertIs(decide("MERGEABLE", MAX_ATTEMPTS, MAX_ATTEMPTS), MergeCheck.MERGEABLE)
        for value in ("CONFLICTING", "BLOCKED", "", "mergeable", "MERGEABLE "):
            with self.subTest(value=value):
                self.assertIs(decide(value, 1, MAX_ATTEMPTS), MergeCheck.REFUSED)

    def test_unknown_retries_until_the_last_attempt(self) -> None:
        for attempt in range(1, MAX_ATTEMPTS):
            self.assertIs(decide("UNKNOWN", attempt, MAX_ATTEMPTS), MergeCheck.RETRY)
        self.assertIs(decide("UNKNOWN", MAX_ATTEMPTS, MAX_ATTEMPTS), MergeCheck.REFUSED)

    def test_an_attempt_out_of_range_raises(self) -> None:
        for attempt in (0, MAX_ATTEMPTS + 1, True):
            with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                decide("UNKNOWN", attempt, MAX_ATTEMPTS)

    def test_the_bash_budget(self) -> None:
        self.assertEqual(MAX_ATTEMPTS, 5)


class ShipBranchTest(unittest.TestCase):
    RUN = RunId("2026-11-13_020001")

    def test_shape_and_no_collision_with_recovery(self) -> None:
        self.assertEqual(ship_branch(self.RUN, 2, "021500"), "cycle/2026-11-13-021500-2-ship")
        self.assertNotEqual(ship_branch(self.RUN, 2, "021500"), recovery_branch(self.RUN, 2, "021500"))
        self.assertNotIn("-unshipped-", ship_branch(self.RUN, 2, "021500"))

    def test_bad_values_raise(self) -> None:
        for cycle, hhmmss in ((0, "021500"), (True, "021500"), (1, "246000"), (1, "2150")):
            with self.subTest(cycle=cycle, hhmmss=hhmmss), self.assertRaises(ValueError):
                ship_branch(self.RUN, cycle, hhmmss)


if __name__ == "__main__":
    unittest.main()
