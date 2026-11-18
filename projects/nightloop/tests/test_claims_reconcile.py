"""Milestone 6: the reconcilers over in-memory ports (outcomes, fall-through, write-once, idempotence)."""

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
    reconcile_shipped,
    reconcile_stranded,
)
from nightloop.claims.markers import STRANDED_SECTION

from .claims_support import MemoryBacklog, ScriptedSource

LOCAL = StrandedBranch("cycle/2026-11-18-unshipped-010203-1")
REMOTE = StrandedBranch("origin/cycle/2026-11-18-unshipped-010203-1")
OTHER = StrandedBranch("cycle/2026-11-17-unshipped-230000-2")
KEY = "Port the `[building]` claim (bash) literally"
NEW = Claim("an invented item", "an invented item\n  with a wrapped line")
TEXT = f"# BACKLOG\n- [ ] {KEY}\n- [ ] something else\n"


class ReconcileStrandedTest(unittest.TestCase):
    def test_each_branch_gets_an_outcome_and_the_file_is_written_once(self) -> None:
        source = ScriptedSource([
            UnshippedBranch(LOCAL, Claim(KEY, KEY)),
            UnshippedBranch(REMOTE, Claim(KEY, KEY)),
            UnshippedBranch(OTHER, NEW),
            UnshippedBranch(StrandedBranch("cycle/a-unshipped-1"), NoClaim()),
            UnshippedBranch(StrandedBranch("cycle/b-unshipped-1"), Unreadable("no merge base")),
        ])
        backlog = MemoryBacklog(TEXT)
        outcomes = reconcile_stranded(source, backlog)
        self.assertEqual([o.status for o in outcomes], [
            StrandedStatus.MARKED,
            StrandedStatus.ALREADY_HANDLED,  # the remote copy of the same branch
            StrandedStatus.FILED,
            StrandedStatus.NO_CLAIM,
            StrandedStatus.UNREADABLE,
        ])
        self.assertEqual(outcomes[4], StrandedOutcome(StrandedBranch("cycle/b-unshipped-1"), StrandedStatus.UNREADABLE, "no merge base"))
        self.assertEqual((backlog.reads, backlog.writes), (1, 1))
        self.assertIn(f"- [stranded {LOCAL}] {KEY}\n", backlog.text)
        self.assertIn(f"{STRANDED_SECTION}\n", backlog.text)
        self.assertIn(f"- [stranded {OTHER}] an invented item\n  with a wrapped line\n", backlog.text)
        self.assertNotIn(str(REMOTE), backlog.text)

    def test_a_second_run_writes_nothing(self) -> None:
        source = ScriptedSource([UnshippedBranch(LOCAL, Claim(KEY, KEY)), UnshippedBranch(OTHER, NEW)])
        backlog = MemoryBacklog(TEXT)
        reconcile_stranded(source, backlog)
        after_first = backlog.text
        outcomes = reconcile_stranded(source, backlog)
        self.assertEqual({o.status for o in outcomes}, {StrandedStatus.ALREADY_HANDLED})
        self.assertEqual(backlog.writes, 1)
        self.assertEqual(backlog.text, after_first)

    def test_nothing_to_do_reads_and_writes_nothing(self) -> None:
        backlog = MemoryBacklog(TEXT)
        self.assertEqual(reconcile_stranded(ScriptedSource([]), backlog), [])
        self.assertEqual((backlog.reads, backlog.writes), (0, 0))
        reconcile_stranded(ScriptedSource([UnshippedBranch(LOCAL, NoClaim())]), backlog)
        self.assertEqual(backlog.writes, 0)

    def test_a_salvaged_item_is_not_refiled(self) -> None:
        backlog = MemoryBacklog(f"# B\n- [done #44] {NEW.key}\n")
        outcomes = reconcile_stranded(ScriptedSource([UnshippedBranch(OTHER, NEW)]), backlog)
        self.assertEqual(outcomes[0].status, StrandedStatus.ALREADY_HANDLED)
        self.assertEqual(backlog.writes, 0)

    def test_a_failed_write_propagates(self) -> None:
        class Broken(MemoryBacklog):
            def write(self, text: str) -> None:
                raise PermissionError("read-only")

        with self.assertRaises(PermissionError):
            reconcile_stranded(ScriptedSource([UnshippedBranch(LOCAL, Claim(KEY, KEY))]), Broken(TEXT))


class ReconcileShippedTest(unittest.TestCase):
    PR = PrNumber(41)

    def run_with(self, lookup, text: str) -> tuple[ShippedOutcome, MemoryBacklog]:
        backlog = MemoryBacklog(text)
        return reconcile_shipped(ScriptedSource(merged={41: lookup}), backlog, self.PR), backlog

    def test_building_is_marked_done_once(self) -> None:
        outcome, backlog = self.run_with(Claim(KEY, KEY), f"# B\n- [building] {KEY}\n")
        self.assertEqual(outcome, ShippedOutcome(self.PR, ShippedStatus.MARKED_DONE))
        self.assertTrue(outcome.changed)
        self.assertEqual(backlog.text, f"# B\n- [done #41] {KEY}\n")
        again = reconcile_shipped(ScriptedSource(merged={41: Claim(KEY, KEY)}), backlog, self.PR)
        self.assertEqual(again.status, ShippedStatus.ALREADY_HANDLED)
        self.assertEqual(backlog.writes, 1)

    def test_every_other_path_writes_nothing(self) -> None:
        cases = (
            (Claim(KEY, KEY), f"- [ ] {KEY}\n", ShippedStatus.ALREADY_HANDLED, None),
            (Claim(KEY, KEY), "- [building] reworded\n", ShippedStatus.ITEM_NOT_FOUND, None),
            (NoClaim(), f"- [building] {KEY}\n", ShippedStatus.NO_CLAIM, None),
            (Unreadable("gh pr view failed: HTTP 404"), f"- [building] {KEY}\n", ShippedStatus.UNREADABLE,
             "gh pr view failed: HTTP 404"),
        )
        for lookup, text, status, reason in cases:
            with self.subTest(status=status):
                outcome, backlog = self.run_with(lookup, text)
                self.assertEqual(outcome, ShippedOutcome(self.PR, status, reason))
                self.assertEqual((backlog.writes, backlog.text), (0, text))

    def test_a_raw_int_pr_is_a_type_error(self) -> None:
        with self.assertRaises(TypeError):
            reconcile_shipped(ScriptedSource(), MemoryBacklog(""), 41)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
