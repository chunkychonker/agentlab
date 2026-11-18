"""Milestone 6: the value types refuse bad states at construction (I4 and the outcome invariants)."""

from __future__ import annotations

import unittest

from nightloop.claims import (
    Claim,
    NoClaim,
    PrNumber,
    ShippedOutcome,
    ShippedStatus,
    StrandedBranch,
    StrandedOutcome,
    StrandedStatus,
    Unreadable,
    UnshippedBranch,
)

REF = StrandedBranch("cycle/2026-11-18-unshipped-010203-1")


class ClaimTest(unittest.TestCase):
    def test_a_good_claim(self) -> None:
        claim = Claim("key", "key\n  more\n\tand more")
        self.assertEqual(claim.continuation, ("  more", "\tand more"))
        self.assertEqual(Claim("k", "k").continuation, ())

    def test_bad_claims_are_refused(self) -> None:
        for key, item in (
            ("", ""),
            ("  ", "  "),
            ("a\nb", "a\nb"),
            ("key", "other"),
            ("key", "key and more"),
            ("key", "key\nnot indented"),
            ("key", "key\n"),
            ("key", "key\n  cr\r"),
        ):
            with self.subTest(key=key, item=item), self.assertRaises(ValueError):
                Claim(key, item)
        with self.assertRaises(TypeError):
            Claim(1, "1")  # type: ignore[arg-type]


class PrNumberTest(unittest.TestCase):
    def test_parse_and_print(self) -> None:
        self.assertEqual(PrNumber.parse("41"), PrNumber(41))
        self.assertEqual(str(PrNumber(41)), "41")

    def test_bad_numbers_are_refused(self) -> None:
        for value in (0, -3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PrNumber(value)
        for value in (True, 4.0, "4"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                PrNumber(value)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PrNumber.parse(41)  # type: ignore[arg-type]


class OutcomeTest(unittest.TestCase):
    def test_a_reason_exactly_when_unreadable(self) -> None:
        StrandedOutcome(REF, StrandedStatus.UNREADABLE, "no merge base")
        ShippedOutcome(PrNumber(1), ShippedStatus.UNREADABLE, "gh failed")
        with self.assertRaises(ValueError):
            StrandedOutcome(REF, StrandedStatus.UNREADABLE)
        with self.assertRaises(ValueError):
            StrandedOutcome(REF, StrandedStatus.MARKED, "why")
        with self.assertRaises(ValueError):
            ShippedOutcome(PrNumber(1), ShippedStatus.UNREADABLE, "two\nlines")

    def test_the_enums_cannot_be_mixed(self) -> None:
        with self.assertRaises(TypeError):
            StrandedOutcome(REF, ShippedStatus.ITEM_NOT_FOUND)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ShippedOutcome(PrNumber(1), StrandedStatus.FILED)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ShippedOutcome(1, ShippedStatus.NO_CLAIM)  # type: ignore[arg-type]

    def test_changed_is_true_only_for_an_edit(self) -> None:
        changed = {s for s in StrandedStatus if StrandedOutcome(REF, s, "r" if s is StrandedStatus.UNREADABLE else None).changed}
        self.assertEqual(changed, {StrandedStatus.MARKED, StrandedStatus.FILED})
        changed = {
            s for s in ShippedStatus
            if ShippedOutcome(PrNumber(1), s, "r" if s is ShippedStatus.UNREADABLE else None).changed
        }
        self.assertEqual(changed, {ShippedStatus.MARKED_DONE})


class PortValuesTest(unittest.TestCase):
    def test_unshipped_branch_types(self) -> None:
        UnshippedBranch(REF, NoClaim())
        with self.assertRaises(TypeError):
            UnshippedBranch("cycle/x", NoClaim())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            UnshippedBranch(REF, None)  # type: ignore[arg-type]
        for reason in ("", " ", "a\nb"):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                Unreadable(reason)


if __name__ == "__main__":
    unittest.main()
