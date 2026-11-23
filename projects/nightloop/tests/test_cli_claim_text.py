"""Milestone 7b: ``claim_text`` labels the claim steps and says what each one did.

Pure. The ``main``-level halves (labels in a run equal labels in the plan,
lines on stderr) are in ``test_cli_claims.py``.
"""

from __future__ import annotations

import unittest

from nightloop.claims import (
    Nothing,
    PrNumber,
    Published,
    PublishFailed,
    ShippedOutcome,
    ShippedStatus,
    Skipped,
    StrandedBranch,
    StrandedOutcome,
    StrandedStatus,
)
from nightloop.cli.claim_text import NIGHT_END_LABEL, claim_plan, render_step, step_label

REF = StrandedBranch("cycle/2026-11-20-unshipped-010203-1")


class LabelTest(unittest.TestCase):
    def test_labels_and_plan_order(self) -> None:
        self.assertEqual(step_label(0), "before cycle 1")
        self.assertEqual(step_label(3), "after cycle 3")
        self.assertEqual([label for label, _ in claim_plan(2)],
                         ["before cycle 1", "after cycle 1", "after cycle 2", NIGHT_END_LABEL])

    def test_bad_cycle_numbers_raise(self) -> None:
        for bad, error in ((-1, ValueError), (True, TypeError), ("1", TypeError)):
            with self.subTest(bad=bad), self.assertRaises(error):
                step_label(bad)  # type: ignore[arg-type]


class RenderTest(unittest.TestCase):
    def test_each_result(self) -> None:
        for result, text in (
            (Published(2), "published 2 backlog edit(s) to main"),
            (Nothing(), "nothing to change"),
            (Skipped("not on main"), "skipped: not on main"),
            (PublishFailed("push rejected"), "publish FAILED: push rejected"),
        ):
            with self.subTest(result=result):
                self.assertEqual(render_step("after cycle 1", result, ()), f"claims after cycle 1: {text}")

    def test_outcomes_worth_reading_get_a_line_and_repeats_do_not(self) -> None:
        outcomes = (
            StrandedOutcome(REF, StrandedStatus.MARKED),
            StrandedOutcome(REF, StrandedStatus.FILED),
            StrandedOutcome(REF, StrandedStatus.UNREADABLE, "no merge base"),
            StrandedOutcome(REF, StrandedStatus.ALREADY_HANDLED),
            StrandedOutcome(REF, StrandedStatus.NO_CLAIM),
        )
        self.assertEqual(render_step("before cycle 1", Published(2), outcomes).splitlines()[1:], [
            f"  {REF}: marked", f"  {REF}: filed", f"  {REF}: unreadable (no merge base)",
        ])

    def test_every_shipped_status_has_a_line(self) -> None:
        pr = PrNumber(9)
        lines = render_step(NIGHT_END_LABEL, Nothing(), tuple(
            ShippedOutcome(pr, status, "not merged" if status is ShippedStatus.UNREADABLE else None)
            for status in ShippedStatus
        )).splitlines()[1:]
        self.assertEqual(len(lines), len(ShippedStatus))
        self.assertIn("  PR #9: pending, not marked (not merged)", lines)
        self.assertIn("  PR #9: marked done", lines)


if __name__ == "__main__":
    unittest.main()
